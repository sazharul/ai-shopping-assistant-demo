"""
app/api/routes.py

All FastAPI endpoints. Clean and thin — business logic lives in graph.py.

Endpoints:
  POST /chat          — full JSON response
  POST /chat/stream   — Server-Sent Events streaming
  POST /index/build   — rebuild FAISS index from faq.json
  GET  /index/status  — index health
  GET  /health        — liveness probe
  POST /debug/retrieve — raw retrieval without LLM (dev only)

SSE protocol (chat/stream):
  Normal token  →  data: {"token": "..."}
  Product data  →  data: {"product_data": [...]}   ← only when search_products fires
  Both events arrive on the same stream; the frontend handles each type.
"""

from __future__ import annotations

import json
import logging
import base64
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import StreamingResponse, FileResponse

from app.agent.graph import run_agent, stream_agent
from app.databases.admin_store import get_conversation_by_session
from app.models import (
    ChatRequest, ChatResponse, ChatUser, ChatUserRequest,
    HealthResponse, IndexResponse, ChatHistoryResponse,
    ImageIndexResponse, ImageSearchResponse, ImageSearchResult,
    ImageSearchB64Request
)
from app.rag.engine import rag_engine
from app.databases.chat_store import get_history, get_or_create_user
from app.rag.product_image_engine import product_image_engine
from app.utils.chat_images import get_chat_image_path
from app.deps import require_admin_key

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from PIL import Image
from io import BytesIO
from app.config import get_settings


logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


# ---------------------------------------------------------------------------
# Chat — JSON
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Send a message and receive a full JSON response.
    """
    conv = get_conversation_by_session(request.session_id)
    if conv and conv.get("status") in ("queued", "with_agent"):
        raise HTTPException(
            status_code=409,
            detail="Session is in human handoff mode. Use /handoff/message instead.",
        )
    try:
        result = await run_agent(
            message=request.message,
            session_id=request.session_id,
        )
        return ChatResponse(
            session_id=request.session_id,
            answer=result["answer"],
            tool_calls=result["tool_calls"],
        )
    except Exception as exc:
        logger.exception("Error in /chat | session=%s", request.session_id)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.") from exc


# ---------------------------------------------------------------------------
# Chat — SSE streaming
# ---------------------------------------------------------------------------

@router.post("/chat/stream", tags=["Chat"])
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    conv = get_conversation_by_session(request.session_id)
    if conv and conv.get("status") in ("queued", "with_agent"):
        raise HTTPException(
            status_code=409,
            detail="Session is in human handoff mode. Use /handoff/message instead.",
        )
    logger.info(
        "Streaming request | session=%s message_len=%s has_image=%s",
        request.session_id,
        len(request.message),
        bool(request.image_base64),
    )
    async def event_stream():
        try:
            async for chunk in stream_agent(request.message, request.session_id, request.image_base64):
                if isinstance(chunk, str):
                    # Normal text token
                    yield f"data: {json.dumps({'token': chunk})}\n\n"

                elif isinstance(chunk, dict) and "__product_response__" in chunk:
                    resp = chunk["__product_response__"]
                    yield f"data: {json.dumps({'product_message': resp['message'], 'product_data': resp['product_data']})}\n\n"

        except Exception as exc:
            logger.exception("Streaming error | session=%s", request.session_id)
            yield f"data: {json.dumps({'error': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

@router.post("/index/build", response_model=IndexResponse, tags=["Index"], dependencies=[Depends(require_admin_key)])
async def build_index() -> IndexResponse:
    """Rebuild the FAISS + BM25 index from the current faq.json."""
    try:
        from app.config import get_settings
        settings = get_settings()
        rag_engine.load_faq_data(settings.faq_data_path)
        rag_engine.build_index()
    except Exception as exc:
        logger.exception("Index build failed")
        raise HTTPException(status_code=500, detail="Index build failed.") from exc

    return IndexResponse(
        status="rebuilt",
        total_documents=rag_engine.total_docs,
        categories=rag_engine.categories,
    )


@router.get("/index/status", response_model=IndexResponse, tags=["Index"])
async def index_status() -> IndexResponse:
    return IndexResponse(
        status="ready" if rag_engine.is_ready else "not_loaded",
        total_documents=rag_engine.total_docs,
        categories=rag_engine.categories,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        index_loaded=rag_engine.is_ready,
        total_docs=rag_engine.total_docs,
    )


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------

@router.post("/debug/retrieve", tags=["Debug"], dependencies=[Depends(require_admin_key)])
async def debug_retrieve(request: ChatRequest) -> dict:
    """
    Returns raw retrieval results with similarity scores — no LLM involved.
    Useful for tuning BM25/semantic weights. Do not expose in production.
    """
    if not rag_engine.is_ready:
        raise HTTPException(status_code=503, detail="Index not loaded.")

    results = rag_engine.retrieve_with_scores(
        query=request.message,
        category_filter=request.category_filter,
    )
    return {
        "query": request.message,
        "results": [
            {
                "score": round(score, 4),
                "id": doc.metadata.get("id"),
                "category": doc.metadata.get("category"),
                "action_type": doc.metadata.get("action_type"),
                "question": doc.metadata.get("question"),
                "answer": doc.metadata.get("answer"),
            }
            for doc, score in results
        ],
    }


# ---------------------------------------------------------------------------
# User / History
# ---------------------------------------------------------------------------

@router.post("/chat/user", response_model=ChatUser, tags=["Chat"])
async def user(request: ChatUserRequest) -> ChatUser:
    """Create or retrieve a user session before starting a chat."""
    result = get_or_create_user(name=request.name, email=request.email)
    return ChatUser(
        id=result["id"],
        name=result["name"],
        email=result["email"],
        session_id=result["session_id"],
    )


@router.get("/chat/history", response_model=ChatHistoryResponse, tags=["Chat"])
async def get_chat_history(
    user_id: int = Query(..., description="User ID"),
    session_id: str = Query(..., description="User session ID"),
    page: int = Query(1, ge=1, description="Page number starting from 1"),
) -> ChatHistoryResponse:
    result = get_history(user_id=user_id, page=page, session_id=session_id)
    return ChatHistoryResponse(
        user=result["user"],
        data=result["data"],
        pagination=result["pagination"],
    )


@router.get("/chat/uploads/{session_id}/{filename}", tags=["Chat"])
async def get_chat_upload(session_id: str, filename: str):
    """Serve a previously uploaded chat image by its stored path."""
    image_path = f"{session_id}/{filename}"
    filepath = get_chat_image_path(image_path)
    if not filepath:
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(filepath, media_type="image/jpeg")




# ---------------------------------------------------------------------------
# Image RAG Routes
# ---------------------------------------------------------------------------

@router.post("/image-index/build", response_model=ImageIndexResponse, tags=["Image Search"], dependencies=[Depends(require_admin_key)])
async def build_image_index(limit: Optional[int] = None) -> ImageIndexResponse:
    """Rebuild the CLIP image FAISS index from product_images.json."""
    try:
        result = product_image_engine.build_index(
            limit=limit,
        )
    except Exception as exc:
        logger.exception("Image index build failed")
        raise HTTPException(status_code=500, detail="Image index build failed.") from exc
 
    return ImageIndexResponse(
        status="rebuilt",
        total_products=result["indexed"],
        failed=result["failed"],
        failed_details=result["failed_details"],
    )

@router.get("/image-index/status", tags=["Image Search"])
async def image_index_status():
    return {
        "ready": product_image_engine.is_ready,
        "total_products": product_image_engine.total_products,
    }



@router.post("/image-search", response_model=ImageSearchResponse, tags=["Image Search"])
async def image_search(
    file: UploadFile = File(...),
    request_by: str = Form(default="api"),
    top_k: int = Form(default=0),
):
    """
    Upload a product photo (file) → returns visually similar products.
    Used by frontend image-upload feature in the chat widget.
    """
    settings = get_settings()
    effective_top_k = top_k or settings.image_top_k_results
 
    if not product_image_engine.is_ready:
        raise HTTPException(
            status_code=400,
            detail="Image index not built yet. Call POST /image-index/build first.",
        )
 
    contents = await file.read()
    if len(contents) > settings.max_image_upload_bytes:
        raise HTTPException(status_code=413, detail="Image file is too large.")
    try:
        image = Image.open(BytesIO(contents)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid image file.") from exc
 
    try:
        results = product_image_engine.apiSearch(
            pil_image=image,
            top_k=effective_top_k,
        )
    except Exception as exc:
        logger.exception("Image search failed")
        raise HTTPException(status_code=500, detail="Image search failed.") from exc
 
    return ImageSearchResponse(
        top_k=effective_top_k,
        results=results,
    )


@router.post("/image-search/base64", response_model=ImageSearchResponse, tags=["Image Search"])
async def image_search_base64(body: ImageSearchB64Request):
    """
    Same as /image-search but accepts base64 JSON — used by the agent/tool layer
    when the frontend sends image_base64 directly in the chat payload.
    """
    settings = get_settings()
    effective_top_k = body.top_k or settings.image_top_k_results
 
    if not product_image_engine.is_ready:
        raise HTTPException(
            status_code=400,
            detail="Image index not built yet. Call POST /image-index/build first.",
        )
 
    b64 = body.image_base64
    if "," in b64:
        b64 = b64.split(",")[1]
 
    try:
        image_bytes = base64.b64decode(b64)
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 image.") from exc
 
    try:
        results = product_image_engine.apiSearch(
            pil_image=image,
            top_k=effective_top_k,
        )
    except Exception as exc:
        logger.exception("Image search failed")
        raise HTTPException(status_code=500, detail="Image search failed.") from exc
 
    return ImageSearchResponse(top_k=effective_top_k, results=results)
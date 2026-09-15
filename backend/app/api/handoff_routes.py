"""Public handoff and feedback endpoints."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.databases.admin_store import (
    get_handoff_status,
    is_within_business_hours,
    request_handoff,
    resolve_handoff,
    reset_conversation_to_bot,
    save_feedback,
    get_business_setting,
    DEFAULT_BUSINESS_HOURS,
    get_conversation_by_session,
)
from app.databases.chat_store import save_message
from app.models import FeedbackRequest, HandoffMessageBody, HandoffRequestBody
from app.handoff.offline import build_off_hours_handoff_response
from app.handoff.rate_limit import check_handoff_rate_limit
from app.ws.events import emit_handoff_requested
from app.ws.handoff import handoff_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Handoff"])


@router.post("/handoff/request")
async def handoff_request(body: HandoffRequestBody):
    check_handoff_rate_limit(body.session_id)

    if not is_within_business_hours():
        return build_off_hours_handoff_response(body.session_id, body.reason)

    conv = request_handoff(body.session_id, body.reason)
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found. Register via POST /chat/user first.")
    status = get_handoff_status(body.session_id)
    await emit_handoff_requested(body.session_id)
    return status


@router.get("/handoff/stream/{session_id}")
async def handoff_customer_stream(session_id: str):
    queue = handoff_manager.subscribe_customer_sse(session_id)

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            handoff_manager.unsubscribe_customer_sse(session_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/handoff/status/{session_id}")
async def handoff_status(session_id: str):
    status = get_handoff_status(session_id)
    if not status:
        raise HTTPException(status_code=404, detail="Session not found")
    return status


@router.post("/handoff/message")
async def handoff_customer_message(body: HandoffMessageBody):
    conv = get_conversation_by_session(body.session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found")
    if conv["status"] not in ("queued", "with_agent"):
        raise HTTPException(status_code=400, detail="Not in handoff mode")
    save_message(body.session_id, "user", body.message, sender_type="user")
    await handoff_manager.broadcast_message(
        session_id=body.session_id,
        conversation_id=conv["id"],
        sender_type="user",
        message=body.message,
    )
    return {"status": True}


@router.post("/handoff/resolve")
async def handoff_customer_resolve(body: HandoffRequestBody):
    conv = get_conversation_by_session(body.session_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found")
    resolved = resolve_handoff(conv["id"], resolved_by="customer")
    reset_conversation_to_bot(conv["id"])
    await handoff_manager.notify_customer(
        body.session_id,
        "conversation_resolved",
        {"conversation_id": conv["id"]},
    )
    return resolved


@router.post("/feedback")
async def submit_feedback(body: FeedbackRequest):
    ok = save_feedback(body.session_id, body.rating, body.comment)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": True, "message": "Thank you for your feedback!"}

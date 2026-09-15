"""Admin REST API — JWT protected."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.auth.admin_auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_admin,
    get_current_admin_from_token,
    require_roles,
)
from app.databases.admin_store import (
    claim_handoff,
    create_admin_user,
    export_conversations_csv,
    get_analytics_overview,
    get_conversation_by_id,
    get_conversation_messages,
    get_csat_analytics,
    get_active_handoffs,
    get_handoff_queue,
    get_handoff_status,
    get_inquiry_by_id,
    get_notification_summary,
    get_tool_analytics,
    list_admin_users,
    list_chat_users,
    get_chat_user,
    list_conversations,
    list_inquiries,
    release_handoff,
    resolve_handoff,
    set_agent_presence,
    set_conversation_tags,
    get_business_setting,
    set_business_setting,
    DEFAULT_BUSINESS_HOURS,
    get_admin_user_by_email,
    verify_password,
    update_inquiry_status,
)
from app.databases.chat_store import save_message
from app.models import (
    AdminLoginRequest,
    AdminRefreshRequest,
    AdminTokenResponse,
    AdminUserResponse,
    AgentMessageBody,
    AgentPresenceBody,
    BusinessHoursBody,
    ConversationExportBody,
    ConversationTagsBody,
    InquiryStatusBody,
)
from app.ws.handoff import handoff_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


def _admin_response(user: dict) -> AdminUserResponse:
    return AdminUserResponse(
        id=user["id"],
        name=user["name"],
        email=user["email"],
        role=user["role"],
        is_online=user.get("is_online", 0),
        last_seen_at=user.get("last_seen_at"),
        created_at=user.get("created_at"),
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/auth/login", response_model=AdminTokenResponse)
async def admin_login(body: AdminLoginRequest) -> AdminTokenResponse:
    user = get_admin_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    public = {k: v for k, v in user.items() if k != "password_hash"}
    return AdminTokenResponse(
        access_token=create_access_token(user["id"], user["role"]),
        refresh_token=create_refresh_token(user["id"]),
        user=_admin_response(public),
    )


@router.post("/auth/refresh", response_model=AdminTokenResponse)
async def admin_refresh(body: AdminRefreshRequest) -> AdminTokenResponse:
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    from app.databases.admin_store import get_admin_user_by_id

    user = get_admin_user_by_id(int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="Admin not found")
    return AdminTokenResponse(
        access_token=create_access_token(user["id"], user["role"]),
        refresh_token=create_refresh_token(user["id"]),
        user=_admin_response(user),
    )


@router.get("/auth/me", response_model=AdminUserResponse)
async def admin_me(admin: dict = Depends(get_current_admin)) -> AdminUserResponse:
    return _admin_response(admin)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def admin_list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    email: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
):
    return list_conversations(
        page=page,
        page_size=page_size,
        status=status,
        email=email,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/conversations/search")
async def admin_search_conversations(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    admin: dict = Depends(get_current_admin),
):
    return list_conversations(page=page, search=q)


@router.get("/conversations/{conversation_id}")
async def admin_conversation_detail(
    conversation_id: int,
    admin: dict = Depends(get_current_admin),
):
    conv = get_conversation_by_id(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = get_conversation_messages(conversation_id)
    return {"conversation": conv, "messages": messages}


@router.post("/conversations/{conversation_id}/tags")
async def admin_set_tags(
    conversation_id: int,
    body: ConversationTagsBody,
    admin: dict = Depends(require_roles("owner", "agent", "developer")),
):
    conv = set_conversation_tags(conversation_id, body.tags)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.post("/conversations/export")
async def admin_export_conversations(
    body: ConversationExportBody,
    admin: dict = Depends(require_roles("owner", "developer")),
):
    csv_data = export_conversations_csv(body.conversation_ids)
    return PlainTextResponse(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=conversations.csv"},
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users")
async def admin_list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
):
    return list_chat_users(page=page, page_size=page_size, search=search)


@router.get("/users/{user_id}")
async def admin_get_user(
    user_id: int,
    conversation_id: Optional[int] = Query(None),
    admin: dict = Depends(get_current_admin),
):
    data = get_chat_user(user_id, conversation_id=conversation_id)
    if not data:
        raise HTTPException(status_code=404, detail="User not found")
    return data


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.get("/analytics/overview")
async def admin_analytics_overview(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
):
    return get_analytics_overview(date_from=date_from, date_to=date_to)


@router.get("/analytics/tools")
async def admin_analytics_tools(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
):
    return {"tools": get_tool_analytics(date_from=date_from, date_to=date_to)}


@router.get("/analytics/csat")
async def admin_analytics_csat(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
):
    return get_csat_analytics(date_from=date_from, date_to=date_to)


@router.get("/analytics/performance")
async def admin_analytics_performance(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    admin: dict = Depends(get_current_admin),
):
    from app.databases.admin_store import get_performance_metrics
    return get_performance_metrics(date_from=date_from, date_to=date_to)


# ---------------------------------------------------------------------------
# Handoff (admin)
# ---------------------------------------------------------------------------

@router.get("/handoff/queue")
async def admin_handoff_queue(admin: dict = Depends(get_current_admin)):
    return {"queue": get_handoff_queue()}


@router.get("/handoff/active")
async def admin_active_handoffs(
    admin: dict = Depends(get_current_admin),
    mine: bool = Query(False),
):
    agent_id = admin["id"] if mine else None
    return {"active": get_active_handoffs(agent_id)}


@router.get("/handoff/stream")
async def admin_handoff_stream(admin: dict = Depends(get_current_admin_from_token)):
    queue = handoff_manager.subscribe_agent_sse()

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            handoff_manager.unsubscribe_agent_sse(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/handoff/{conversation_id}/claim")
async def admin_claim_handoff(
    conversation_id: int,
    admin: dict = Depends(require_roles("owner", "agent")),
):
    conv = claim_handoff(conversation_id, admin["id"])
    if not conv:
        raise HTTPException(status_code=409, detail="Conversation not available to claim")
    await handoff_manager.notify_customer(
        conv["session_id"],
        "agent_joined",
        {"agent_name": admin["name"], "conversation_id": conversation_id},
    )
    await handoff_manager.notify_agents(
        "queue_update",
        {"action": "claimed", "conversation_id": conversation_id},
    )
    return conv


@router.post("/handoff/{conversation_id}/release")
async def admin_release_handoff(
    conversation_id: int,
    admin: dict = Depends(require_roles("owner", "agent")),
):
    conv = release_handoff(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    status = get_handoff_status(conv["session_id"]) or {}
    await handoff_manager.notify_customer(
        conv["session_id"],
        "queue_update",
        status,
    )
    await handoff_manager.notify_agents(
        "queue_update",
        {"action": "released", "conversation_id": conversation_id},
    )
    return conv


@router.post("/handoff/{conversation_id}/message")
async def admin_send_handoff_message(
    conversation_id: int,
    body: AgentMessageBody,
    admin: dict = Depends(require_roles("owner", "agent")),
):
    conv = get_conversation_by_id(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv["status"] != "with_agent":
        raise HTTPException(status_code=400, detail="Conversation is not with an agent")
    save_message(
        conv["session_id"],
        "agent",
        body.message,
        sender_type="agent",
        metadata={"agent_id": admin.get("id"), "agent_name": admin.get("name")},
    )
    await handoff_manager.broadcast_message(
        session_id=conv["session_id"],
        conversation_id=conversation_id,
        sender_type="agent",
        message=body.message,
        agent_name=admin["name"],
    )
    return {"status": True, "message": body.message}


@router.post("/handoff/{conversation_id}/resolve")
async def admin_resolve_handoff(
    conversation_id: int,
    admin: dict = Depends(require_roles("owner", "agent")),
):
    conv = resolve_handoff(conversation_id, resolved_by="agent")
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await handoff_manager.notify_customer(
        conv["session_id"],
        "conversation_resolved",
        {"conversation_id": conversation_id},
    )
    await handoff_manager.notify_agents(
        "queue_update",
        {"action": "resolved", "conversation_id": conversation_id},
    )
    return conv


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@router.get("/agents")
async def admin_list_agents(admin: dict = Depends(get_current_admin)):
    return {"agents": list_admin_users()}


@router.post("/agents/presence")
async def admin_set_presence(
    body: AgentPresenceBody,
    admin: dict = Depends(require_roles("owner", "agent")),
):
    user = set_agent_presence(admin["id"], body.is_online)
    return user


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.get("/settings/business-hours")
async def get_business_hours(admin: dict = Depends(get_current_admin)):
    return get_business_setting("business_hours", DEFAULT_BUSINESS_HOURS)


@router.put("/settings/business-hours")
async def update_business_hours(
    body: BusinessHoursBody,
    admin: dict = Depends(require_roles("owner", "developer")),
):
    return set_business_setting("business_hours", body.model_dump())


# ---------------------------------------------------------------------------
# Inquiries
# ---------------------------------------------------------------------------

@router.get("/inquiries")
async def admin_list_inquiries(
    admin: dict = Depends(get_current_admin),
    page: int = Query(1, ge=1),
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
):
    return list_inquiries(page=page, status=status, category=category, search=search)


@router.get("/inquiries/{inquiry_id}")
async def admin_get_inquiry(
    inquiry_id: int,
    admin: dict = Depends(get_current_admin),
):
    inquiry = get_inquiry_by_id(inquiry_id)
    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return {"inquiry": inquiry}


@router.patch("/inquiries/{inquiry_id}/status")
async def admin_update_inquiry_status(
    inquiry_id: int,
    body: InquiryStatusBody,
    admin: dict = Depends(require_roles("owner", "agent")),
):
    try:
        inquiry = update_inquiry_status(
            inquiry_id,
            status=body.status,
            agent_id=admin.get("id"),
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not inquiry:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return {"inquiry": inquiry}


@router.get("/notifications/summary")
async def admin_notification_summary(admin: dict = Depends(get_current_admin)):
    return get_notification_summary()


# ---------------------------------------------------------------------------
# Bootstrap (owner only, first-time setup)
# ---------------------------------------------------------------------------

@router.post("/bootstrap")
async def bootstrap_admin(
    body: AdminLoginRequest,
    admin: dict = Depends(require_roles("owner")),
):
    """Create additional admin users (owner only)."""
    try:
        user = create_admin_user(
            name=body.email.split("@")[0],
            email=body.email,
            password=body.password,
            role="agent",
        )
        return _admin_response(user)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

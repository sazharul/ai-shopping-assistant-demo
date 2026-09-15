"""Shared realtime event helpers for handoff."""

from __future__ import annotations

from app.databases.admin_store import get_conversation_by_session, get_handoff_status
from app.ws.handoff import handoff_manager


async def emit_handoff_requested(session_id: str) -> None:
    conv = get_conversation_by_session(session_id)
    if not conv:
        return
    status = get_handoff_status(session_id) or {}
    await handoff_manager.notify_agents(
        "handoff_requested",
        {"conversation": conv, "queue_position": status.get("queue_position", 0)},
    )
    await handoff_manager.notify_customer(
        session_id,
        "queue_update",
        status,
    )


async def emit_inquiry_created(inquiry: dict) -> None:
    await handoff_manager.notify_agents(
        "inquiry_created",
        {"inquiry": inquiry},
    )

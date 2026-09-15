"""Off-hours handoff fallback — creates a local inquiry when agents are unavailable."""

from __future__ import annotations

import logging
from typing import Optional

from app.databases.admin_store import DEFAULT_BUSINESS_HOURS, create_inquiry, get_business_setting, log_event
from app.databases.config import get_connection
from app.ws.events import emit_inquiry_created

logger = logging.getLogger(__name__)


def _get_user_by_session(session_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, email FROM users WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_off_hours_support_ticket(
    session_id: str,
    reason: str = "Customer requested live support outside business hours",
) -> dict:
    user = _get_user_by_session(session_id)
    if not user:
        return {"created": False, "error": "Session not found"}

    hours = get_business_setting("business_hours", DEFAULT_BUSINESS_HOURS)
    offline_message = hours.get(
        "offline_message",
        "Our agents are currently offline. We have created a support ticket for you.",
    )

    try:
        inquiry = create_inquiry(
            name=user["name"],
            email=user["email"],
            phone="Not provided",
            category="live_support_request",
            subject="Live support request (outside business hours)",
            message=reason or "Customer requested to speak with a human agent while agents were offline.",
            session_id=session_id,
            metadata={"type": "off_hours_handoff"},
        )
        log_event(
            "off_hours_support_ticket",
            session_id=session_id,
            payload={"reason": reason, "inquiry_id": inquiry.get("id")},
        )
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(emit_inquiry_created(inquiry))
        except Exception:
            logger.exception("Failed to emit off-hours inquiry notification")

        return {
            "created": True,
            "message": offline_message,
            "ticket": inquiry,
        }
    except Exception:
        logger.exception("Failed to create off-hours support ticket | session=%s", session_id)
        return {
            "created": False,
            "message": offline_message,
            "error": "Could not create support ticket",
        }


def build_off_hours_handoff_response(session_id: str, reason: Optional[str] = None) -> dict:
    ticket_result = create_off_hours_support_ticket(session_id, reason or "")
    message = ticket_result.get("message") or "Agents are currently offline."
    if ticket_result.get("created"):
        message = (
            f"{message} Our team has opened a support ticket and will follow up by email."
        )
    return {
        "session_id": session_id,
        "status": "offline",
        "handoff_available": False,
        "queue_position": 0,
        "message": message,
        "support_ticket_created": bool(ticket_result.get("created")),
    }

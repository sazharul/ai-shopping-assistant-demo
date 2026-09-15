"""Background jobs for operational alerts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from app.config import get_settings
from app.databases.admin_store import log_event
from app.databases.config import get_connection

logger = logging.getLogger(__name__)


def _get_queued_over_threshold(minutes: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.session_id, c.queued_at, c.handoff_reason,
                   u.name AS user_name, u.email AS user_email,
                   CAST((julianday('now') - julianday(c.queued_at)) * 1440 AS INTEGER) AS wait_minutes
            FROM conversations c
            JOIN users u ON u.id = c.user_id
            WHERE c.status = 'queued'
              AND c.queued_at IS NOT NULL
              AND (julianday('now') - julianday(c.queued_at)) * 1440 >= ?
            ORDER BY c.queued_at ASC
            """,
            (minutes,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _already_alerted(conversation_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM analytics_events
            WHERE event_type = 'queue_wait_alert' AND conversation_id = ?
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


async def _send_webhook(payload: dict) -> None:
    settings = get_settings()
    url = (settings.alert_webhook_url or "").strip()
    if not url:
        logger.warning(
            "Queue wait alert for conversation %s (no ALERT_WEBHOOK_URL configured)",
            payload.get("conversation_id"),
        )
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
    except Exception:
        logger.exception("Failed to send queue alert webhook")


async def check_queue_wait_alerts() -> None:
    settings = get_settings()
    threshold = max(1, settings.queue_alert_minutes)
    queued = _get_queued_over_threshold(threshold)

    for item in queued:
        conversation_id = item["id"]
        if _already_alerted(conversation_id):
            continue

        payload = {
            "alert_type": "queue_wait_exceeded",
            "conversation_id": conversation_id,
            "session_id": item["session_id"],
            "customer_name": item["user_name"],
            "customer_email": item["user_email"],
            "wait_minutes": item["wait_minutes"],
            "handoff_reason": item.get("handoff_reason"),
            "queued_at": item.get("queued_at"),
            "threshold_minutes": threshold,
            "sent_at": datetime.utcnow().isoformat(),
        }
        await _send_webhook(payload)
        log_event(
            "queue_wait_alert",
            session_id=item["session_id"],
            conversation_id=conversation_id,
            payload=payload,
        )
        logger.info(
            "Queue wait alert sent | conversation_id=%s wait_minutes=%s",
            conversation_id,
            item["wait_minutes"],
        )


async def run_queue_alert_loop() -> None:
    settings = get_settings()
    interval = max(30, settings.queue_alert_check_seconds)
    while True:
        try:
            await check_queue_wait_alerts()
        except Exception:
            logger.exception("Queue alert check failed")
        await asyncio.sleep(interval)

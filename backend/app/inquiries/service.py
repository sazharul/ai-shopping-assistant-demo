"""Create and validate inquiries handled in enox-admin."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.agent.context import current_session_id
from app.config import resolve_enox_api_key, resolve_enox_api_url
from app.databases.admin_store import (
    INQUIRY_PREFIX,
    create_inquiry,
    has_pending_cancel_inquiry,
)
from app.utils.utils import post_to_api
from app.ws.events import emit_inquiry_created

logger = logging.getLogger(__name__)

_CANCEL_BLOCK_STATUSES = {
    "delivered": (
        "Your order has already been delivered. You cannot cancel it now. "
        "You may request a return or exchange after delivery."
    ),
    "shipped": (
        "Your order has already been shipped. You cannot cancel it now. "
        "You may request a return or exchange after delivery."
    ),
    "processing": (
        "Your order is currently processing. You cannot cancel it now. "
        "You may request a return or exchange after delivery."
    ),
}


def _schedule_inquiry_notification(inquiry: dict) -> None:
    if not inquiry:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(emit_inquiry_created(inquiry))
    except Exception:
        logger.exception("Failed to schedule inquiry notification | id=%s", inquiry.get("id"))


def _api(endpoint: str, payload: dict) -> dict:
    return post_to_api(
        endpoint,
        payload,
        {
            "X-INTERNAL-KEY": resolve_enox_api_key(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        logger,
        base_url=resolve_enox_api_url(),
    )


def _customer_name_from_order(data: dict) -> str:
    customer = (data.get("data") or {}).get("customer") or {}
    name = (customer.get("name") or "").strip()
    return name or "Guest Customer"


def validate_cancel_order(order_id: int, email: str) -> dict:
    """Validate order cancellation eligibility via StyleHub order APIs."""
    if has_pending_cancel_inquiry(order_id, email):
        return {
            "status": False,
            "message": (
                "A cancellation request for this order is already pending review. "
                "Our team will contact you by email with updates."
            ),
        }

    try:
        order_resp = _api("/api/orders", {"order_id": order_id, "email": email.strip()})
    except Exception:
        logger.exception("Cancel validation failed | order_id=%s", order_id)
        return {
            "status": False,
            "message": "Something went wrong. Please try again later.",
        }

    if not order_resp.get("status"):
        return {
            "status": False,
            "message": order_resp.get("message") or "Order could not be verified.",
        }

    try:
        status_resp = _api(
            "/api/order/status",
            {"order_id": order_id, "email": email.strip()},
        )
    except Exception:
        logger.exception("Cancel status check failed | order_id=%s", order_id)
        return {
            "status": False,
            "message": "Something went wrong. Please try again later.",
        }

    if not status_resp.get("status"):
        return {
            "status": False,
            "message": status_resp.get("message") or "Order status could not be verified.",
        }

    current_status = ((status_resp.get("data") or {}).get("status") or "").lower()
    if current_status == "canceled":
        status_date = (status_resp.get("data") or {}).get("status_date") or "a previous date"
        return {
            "status": False,
            "message": f"Your order was already canceled on {status_date}. You cannot cancel it again.",
        }

    if current_status in _CANCEL_BLOCK_STATUSES:
        return {"status": False, "message": _CANCEL_BLOCK_STATUSES[current_status]}

    return {
        "status": True,
        "customer_name": _customer_name_from_order(order_resp),
        "phone": ((order_resp.get("data") or {}).get("customer") or {}).get("phone") or "",
    }


def submit_cancel_order_inquiry(order_id: int, email: str, cancel_reason: str) -> dict:
    validation = validate_cancel_order(order_id, email)
    if not validation.get("status"):
        return validation

    inquiry = create_inquiry(
        name=validation["customer_name"],
        email=email.strip(),
        phone=validation.get("phone") or "",
        category="order_cancel_request",
        subject=f"Order cancellation request — #{order_id}",
        message=cancel_reason.strip(),
        order_id=str(order_id),
        session_id=current_session_id.get(),
        metadata={"type": "cancel_order", "order_id": order_id},
    )
    _schedule_inquiry_notification(inquiry)

    return {
        "status": True,
        "message": (
            "Your order cancellation request has been successfully submitted. "
            "You will receive email notification and refund updates after approval. "
            "Refund amount will appear in your bank within 10–30 business days."
        ),
        "data": {"inquiry_id": inquiry.get("reference")},
    }


def submit_support_inquiry(
    *,
    name: str,
    email: str,
    phone: str,
    category: str,
    subject: str,
    message: str,
    order_id: Optional[str] = None,
) -> dict:
    inquiry = create_inquiry(
        name=name,
        email=email,
        phone=phone,
        category=category,
        subject=subject,
        message=message,
        order_id=order_id,
        session_id=current_session_id.get(),
        metadata={"type": "support_ticket"},
    )
    _schedule_inquiry_notification(inquiry)

    return {
        "status": True,
        "message": (
            f"Your inquiry has been submitted successfully. "
            f"Inquiry ID: {inquiry.get('reference', INQUIRY_PREFIX + str(inquiry.get('id', '')))}"
        ),
        "data": inquiry,
    }

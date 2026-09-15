"""Canned API responses for DEMO_MODE — no live Laravel backend required."""

from __future__ import annotations


def demo_api_response(endpoint: str, payload: dict) -> dict:
    ep = endpoint.lower()

    if "order/status" in ep or "order-status" in ep:
        order_id = payload.get("order_id") or payload.get("order_number") or "SH-10492"
        return {
            "status": True,
            "message": "Order found.",
            "data": {
                "order_id": order_id,
                "status": "shipped",
                "tracking": "TRK-DEMO-12345",
                "estimated_delivery": "2026-03-20",
            },
        }

    if "order/details" in ep or "order-details" in ep:
        return {
            "status": True,
            "message": "Order details retrieved.",
            "data": {
                "order_id": payload.get("order_id", "SH-10492"),
                "items": [{"name": "Classic Blue Denim Jacket", "qty": 1, "price": 59.99}],
                "total": 59.99,
                "currency": "GBP",
            },
        }

    if "cancel" in ep:
        return {"status": True, "message": "Order cancelled successfully.", "data": []}

    if "shipping" in ep and "update" in ep:
        return {"status": True, "message": "Shipping address updated.", "data": []}

    if "shipping" in ep:
        return {
            "status": True,
            "message": "Shipping address retrieved.",
            "data": {"address": "123 Demo Street, London, SW1A 1AA, UK"},
        }

    if "return" in ep and "status" in ep:
        return {
            "status": True,
            "message": "Return status retrieved.",
            "data": {"return_id": "RET-DEMO-001", "status": "approved"},
        }

    if "return" in ep:
        return {
            "status": True,
            "message": "Return request created.",
            "data": {"return_id": "RET-DEMO-001"},
        }

    if "discount" in ep or "coupon" in ep:
        code = payload.get("discount_code") or payload.get("code") or "WELCOME10"
        return {
            "status": True,
            "message": "Discount code is valid.",
            "data": {"code": code, "discount_percent": 10},
        }

    if "ticket" in ep or "support" in ep:
        return {
            "status": True,
            "message": "Support ticket created.",
            "data": {"ticket_id": "TKT-DEMO-789"},
        }

    if "invoice" in ep:
        return {"status": True, "message": "Invoice sent to your email.", "data": []}

    if "incident" in ep:
        return {"status": True, "message": "Incident logged for review.", "data": []}

    return {
        "status": True,
        "message": "Demo mode response.",
        "data": {"note": "This is a simulated response in DEMO_MODE."},
    }

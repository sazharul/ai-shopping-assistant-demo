"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Header, HTTPException

from app.config import get_settings


async def require_admin_key(x_admin_key: str = Header(default="")) -> None:
    """Protect admin/debug/index routes when ADMIN_API_KEY is configured."""
    settings = get_settings()
    if settings.strict_security_enabled() and not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_API_KEY must be configured in production.",
        )
    if not settings.admin_api_key:
        return
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

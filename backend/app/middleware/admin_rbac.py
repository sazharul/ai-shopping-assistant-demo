"""Production-only admin RBAC middleware (defense in depth)."""

from __future__ import annotations

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.admin_auth import decode_token
from app.config import get_settings
from app.security.startup import enforce_admin_route_role


class AdminRBACMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path

        if not settings.strict_security_enabled() or not path.startswith("/api/v1/admin"):
            return await call_next(request)

        if path in {"/api/v1/admin/auth/login", "/api/v1/admin/auth/refresh"}:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return await call_next(request)

        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = decode_token(token)
            if payload.get("type") != "access":
                return await call_next(request)
            role = payload.get("role")
            if role:
                enforce_admin_route_role(request, role)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        return await call_next(request)

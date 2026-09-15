"""Production security checks and helpers."""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_INSECURE_JWT_SECRETS = frozenset({
    "",
    "changeme",
    "change-me-in-production",
    "change-me-in-production-use-long-random-string",
    "your-secret-key",
    "secret",
})

_INSECURE_ADMIN_KEYS = frozenset({
    "",
    "changeme",
    "your-internal-key-here",
})

# method -> path pattern -> allowed roles (production middleware backup)
_ADMIN_ROLE_RULES: list[tuple[str, re.Pattern[str], tuple[str, ...]]] = [
    ("POST", re.compile(r"^/api/v1/admin/conversations/export$"), ("owner", "developer")),
    ("PUT", re.compile(r"^/api/v1/admin/settings/business-hours$"), ("owner", "developer")),
    ("POST", re.compile(r"^/api/v1/admin/bootstrap$"), ("owner",)),
    ("POST", re.compile(r"^/api/v1/admin/handoff/\d+/claim$"), ("owner", "agent")),
    ("POST", re.compile(r"^/api/v1/admin/handoff/\d+/release$"), ("owner", "agent")),
    ("POST", re.compile(r"^/api/v1/admin/handoff/\d+/message$"), ("owner", "agent")),
    ("POST", re.compile(r"^/api/v1/admin/handoff/\d+/resolve$"), ("owner", "agent")),
]

_PUBLIC_ADMIN_PATHS = frozenset({
    "/api/v1/admin/auth/login",
    "/api/v1/admin/auth/refresh",
})


def is_insecure_jwt_secret(secret: str) -> bool:
    value = (secret or "").strip()
    if value.lower() in _INSECURE_JWT_SECRETS:
        return True
    return len(value) < 32


def is_insecure_admin_key(key: str) -> bool:
    value = (key or "").strip()
    return value.lower() in _INSECURE_ADMIN_KEYS


def validate_security_settings(settings) -> None:
    """Fail fast in strict production; warn only in development/debug."""
    if not settings.strict_security_enabled():
        warnings: list[str] = []
        if is_insecure_jwt_secret(settings.jwt_secret_key):
            warnings.append("JWT_SECRET_KEY is using a development default.")
        if is_insecure_admin_key(settings.admin_api_key):
            warnings.append("ADMIN_API_KEY is not set (index/debug routes are open).")
        for warning in warnings:
            logger.warning("[Development] %s", warning)
        return

    errors: list[str] = []
    if is_insecure_jwt_secret(settings.jwt_secret_key):
        errors.append(
            "JWT_SECRET_KEY must be set to a random string of at least 32 characters in production."
        )
    if is_insecure_admin_key(settings.admin_api_key):
        errors.append(
            "ADMIN_API_KEY must be set to a strong secret in production (protects index/debug routes)."
        )
    if errors:
        raise RuntimeError("Security configuration error:\n- " + "\n- ".join(errors))

    logger.info("Production security checks passed.")


def required_roles_for_admin_request(method: str, path: str) -> Optional[tuple[str, ...]]:
    for rule_method, pattern, roles in _ADMIN_ROLE_RULES:
        if rule_method == method.upper() and pattern.match(path):
            return roles
    return None


def enforce_admin_route_role(request: Request, role: str) -> None:
    if request.url.path in _PUBLIC_ADMIN_PATHS:
        return
    required = required_roles_for_admin_request(request.method, request.url.path)
    if required and role not in required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for this admin action.",
        )

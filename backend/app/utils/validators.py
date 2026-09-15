"""Shared input validators."""

from __future__ import annotations

import re

SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
_NAME_UNSAFE_RE = re.compile(r"[*_`#\[\]<>]")


def validate_session_id(session_id: str) -> str:
    if not session_id or ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError("Invalid session_id")
    if not SESSION_ID_RE.match(session_id):
        raise ValueError("Invalid session_id format")
    return session_id


def sanitize_display_name(name: str) -> str:
    cleaned = _NAME_UNSAFE_RE.sub("", (name or "").strip())
    return (cleaned[:100] or "there")

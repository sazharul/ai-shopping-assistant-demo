"""Request-scoped context for tools that need the active chat session."""

from __future__ import annotations

from contextvars import ContextVar

current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")

"""Persistent LangGraph checkpoint storage (async for streaming)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import get_settings

_settings = get_settings()
_checkpoint_path = Path(_settings.chat_store_path).resolve().parent / "agent_checkpoints.db"
_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

agent_memory: AsyncSqliteSaver | None = None
_connection: aiosqlite.Connection | None = None


async def init_agent_memory() -> None:
    """Initialize AsyncSqliteSaver during FastAPI startup."""
    global agent_memory, _connection

    _connection = await aiosqlite.connect(str(_checkpoint_path))
    await _connection.execute("PRAGMA journal_mode=WAL")
    await _connection.execute("PRAGMA busy_timeout=5000")
    await _connection.execute("PRAGMA synchronous=NORMAL")
    agent_memory = AsyncSqliteSaver(_connection)
    await agent_memory.setup()


async def close_agent_memory() -> None:
    """Close checkpoint connection on app shutdown."""
    global agent_memory, _connection

    if _connection is not None:
        await _connection.close()

    agent_memory = None
    _connection = None

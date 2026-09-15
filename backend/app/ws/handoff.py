"""WebSocket and SSE connection manager for live handoff."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class HandoffConnectionManager:
    def __init__(self) -> None:
        self._customer_connections: dict[str, set[WebSocket]] = {}
        self._agent_connections: dict[int, set[WebSocket]] = {}
        self._global_agents: set[WebSocket] = set()
        self._customer_sse: dict[str, set[asyncio.Queue]] = {}
        self._agent_sse: set[asyncio.Queue] = set()

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def connect_customer(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._customer_connections.setdefault(session_id, set()).add(websocket)

    async def connect_agent(self, agent_id: int, websocket: WebSocket, global_queue: bool = True) -> None:
        await websocket.accept()
        self._agent_connections.setdefault(agent_id, set()).add(websocket)
        if global_queue:
            self._global_agents.add(websocket)

    def disconnect_customer(self, session_id: str, websocket: WebSocket) -> None:
        conns = self._customer_connections.get(session_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                self._customer_connections.pop(session_id, None)

    def disconnect_agent(self, agent_id: int, websocket: WebSocket) -> None:
        conns = self._agent_connections.get(agent_id)
        if conns:
            conns.discard(websocket)
            if not conns:
                self._agent_connections.pop(agent_id, None)
        self._global_agents.discard(websocket)

    # ------------------------------------------------------------------
    # SSE subscriptions
    # ------------------------------------------------------------------

    def subscribe_customer_sse(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._customer_sse.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe_customer_sse(self, session_id: str, queue: asyncio.Queue) -> None:
        queues = self._customer_sse.get(session_id)
        if queues:
            queues.discard(queue)
            if not queues:
                self._customer_sse.pop(session_id, None)

    def subscribe_agent_sse(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._agent_sse.add(queue)
        return queue

    def unsubscribe_agent_sse(self, queue: asyncio.Queue) -> None:
        self._agent_sse.discard(queue)

    # ------------------------------------------------------------------
    # Publish helpers
    # ------------------------------------------------------------------

    async def _send_ws(self, connections: set[WebSocket], event: str, data: dict) -> None:
        payload = json.dumps({"event": event, "data": data})
        dead: list[WebSocket] = []
        for ws in list(connections):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            connections.discard(ws)

    def _push_sse(self, queues: set[asyncio.Queue], event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        for queue in list(queues):
            try:
                queue.put_nowait(payload)
            except Exception:
                pass

    async def publish(
        self,
        event: str,
        data: dict,
        *,
        session_id: str | None = None,
        to_agents: bool = False,
    ) -> None:
        if session_id:
            await self.notify_customer(session_id, event, data)
        if to_agents:
            await self.notify_agents(event, data)

    async def notify_customer(self, session_id: str, event: str, data: dict) -> None:
        conns = self._customer_connections.get(session_id, set())
        await self._send_ws(conns, event, data)
        self._push_sse(self._customer_sse.get(session_id, set()), event, data)

    async def notify_agents(self, event: str, data: dict) -> None:
        await self._send_ws(self._global_agents, event, data)
        self._push_sse(self._agent_sse, event, data)

    async def notify_agent(self, agent_id: int, event: str, data: dict) -> None:
        conns = self._agent_connections.get(agent_id, set())
        await self._send_ws(conns, event, data)

    async def broadcast_message(
        self,
        *,
        session_id: str,
        conversation_id: int,
        sender_type: str,
        message: str,
        agent_name: str | None = None,
    ) -> None:
        payload = {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "sender_type": sender_type,
            "message": message,
            "agent_name": agent_name,
        }
        await self.notify_customer(session_id, "new_message", payload)
        await self.notify_agents("new_message", payload)


handoff_manager = HandoffConnectionManager()

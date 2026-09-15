"""WebSocket endpoints for live handoff."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.auth.admin_auth import decode_token
from app.ws.handoff import handoff_manager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/handoff/{session_id}")
async def customer_handoff_ws(session_id: str, websocket: WebSocket):
    await handoff_manager.connect_customer(session_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        handoff_manager.disconnect_customer(session_id, websocket)


@router.websocket("/ws/admin/queue")
async def admin_queue_ws(websocket: WebSocket, token: str = Query(...)):
    try:
        payload = decode_token(token)
        agent_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=4001)
        return

    await handoff_manager.connect_agent(agent_id, websocket, global_queue=True)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        handoff_manager.disconnect_agent(agent_id, websocket)

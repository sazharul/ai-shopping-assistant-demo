"""Per-session rate limiting for handoff requests."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException

from app.config import get_settings

_hits: dict[str, deque[float]] = defaultdict(deque)


def check_handoff_rate_limit(session_id: str) -> None:
    settings = get_settings()
    max_requests = settings.handoff_rate_limit_max()
    window = settings.handoff_rate_limit_window_seconds
    now = time.time()

    key = f"handoff:{session_id}"
    hits = _hits[key]
    while hits and hits[0] < now - window:
        hits.popleft()

    if len(hits) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many handoff requests for this session. "
                "Please wait before trying again."
            ),
        )

    hits.append(now)

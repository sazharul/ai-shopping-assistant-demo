"""
Admin, conversation, analytics, handoff, and feedback persistence.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

import bcrypt

from app.databases.config import get_connection

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
CONVERSATION_STATUSES = ("bot", "queued", "with_agent", "resolved")
ADMIN_ROLES = ("owner", "agent", "developer")


def _now() -> str:
    return datetime.utcnow().isoformat()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Schema migrations (called from init_db)
# ---------------------------------------------------------------------------

def migrate_admin_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'agent',
            is_online INTEGER NOT NULL DEFAULT 0,
            last_seen_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'bot',
            assigned_agent_id INTEGER,
            handoff_reason TEXT,
            handoff_summary TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            tags TEXT DEFAULT '[]',
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_message_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME,
            queued_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (assigned_agent_id) REFERENCES admin_users(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            session_id TEXT,
            conversation_id INTEGER,
            payload_json TEXT DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS business_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS inquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT DEFAULT '',
            category TEXT NOT NULL,
            subject TEXT DEFAULT '',
            message TEXT NOT NULL,
            order_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            session_id TEXT,
            source TEXT NOT NULL DEFAULT 'enox_ai',
            metadata_json TEXT DEFAULT '{}',
            assigned_agent_id INTEGER,
            resolved_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (assigned_agent_id) REFERENCES admin_users(id)
        )
    """)

    msg_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
    }
    migrations = {
        "conversation_id": "INTEGER",
        "sender_type": "TEXT DEFAULT 'user'",
        "tool_calls_json": "TEXT",
        "metadata_json": "TEXT",
        "latency_ms": "INTEGER",
    }
    for col, col_type in migrations.items():
        if col not in msg_cols:
            conn.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} {col_type}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversations_last_message ON conversations(last_message_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_event_type ON analytics_events(event_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inquiries_created ON inquiries(created_at)"
    )


def seed_default_admin(email: str, password: str, name: str = "Admin") -> None:
    if not email or not password:
        logger.warning(
            "DATABASE | admin seed skipped — set ADMIN_DEFAULT_EMAIL and "
            "ADMIN_DEFAULT_PASSWORD in backend/.env"
        )
        return
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM admin_users WHERE email = ?", (email.lower(),)
        ).fetchone()
        if row:
            return
        create_admin_user(name=name, email=email, password=password, role="owner")
        logger.info("DATABASE | default admin user seeded | email=%s", email)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Admin users
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def create_admin_user(
    *,
    name: str,
    email: str,
    password: str,
    role: str = "agent",
) -> dict:
    if role not in ADMIN_ROLES:
        raise ValueError(f"Invalid role: {role}")
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO admin_users (name, email, password_hash, role)
            VALUES (?, ?, ?, ?)
            """,
            (name, email.lower(), hash_password(password), role),
        )
        conn.commit()
        return get_admin_user_by_id(cursor.lastrowid)  # type: ignore[arg-type]
    finally:
        conn.close()


def get_admin_user_by_id(user_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, email, role, is_online, last_seen_at, created_at "
            "FROM admin_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_admin_user_by_email(email: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_admin_users() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, email, role, is_online, last_seen_at, created_at "
            "FROM admin_users ORDER BY name"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_agent_presence(agent_id: int, is_online: bool) -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE admin_users SET is_online = ?, last_seen_at = ? WHERE id = ?",
            (1 if is_online else 0, _now(), agent_id),
        )
        conn.commit()
        return get_admin_user_by_id(agent_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def resolve_conversation_id(conn, session_id: str) -> Optional[int]:
    """Get or create a conversation using an existing connection (no commit)."""
    row = conn.execute(
        "SELECT id FROM conversations WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row:
        return row["id"]

    user_row = conn.execute(
        "SELECT id FROM users WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not user_row:
        return None

    now = _now()
    cursor = conn.execute(
        """
        INSERT INTO conversations (session_id, user_id, status, started_at, last_message_at)
        VALUES (?, ?, 'bot', ?, ?)
        """,
        (session_id, user_row["id"], now, now),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def touch_conversation(conversation_id: int, conn=None) -> None:
    if conn is not None:
        conn.execute(
            "UPDATE conversations SET last_message_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        return

    db = get_connection()
    try:
        db.execute(
            "UPDATE conversations SET last_message_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        db.commit()
    finally:
        db.close()


def get_or_create_conversation(session_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        conversation_id = resolve_conversation_id(conn, session_id)
        if conversation_id is None:
            return None
        conn.commit()
        if not existing:
            log_event("chat_started", session_id=session_id, conversation_id=conversation_id)
        row = conn.execute(
            "SELECT id, session_id, user_id, status FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_conversation_by_id(conversation_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT c.*, u.name AS user_name, u.email AS user_email,
                   a.name AS agent_name
            FROM conversations c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN admin_users a ON a.id = c.assigned_agent_id
            WHERE c.id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        data = _row_to_dict(row)
        data["tags"] = json.loads(data.get("tags") or "[]")
        return data
    finally:
        conn.close()


def get_conversation_by_session(session_id: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
        return get_conversation_by_id(row["id"]) if row else None
    finally:
        conn.close()


def list_conversations(
    *,
    page: int = 1,
    page_size: int = PAGE_SIZE,
    status: Optional[str] = None,
    email: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    conn = get_connection()
    try:
        where = ["1=1"]
        params: list[Any] = []

        if status:
            where.append("c.status = ?")
            params.append(status)
        if email:
            where.append("LOWER(u.email) LIKE ?")
            params.append(f"%{email.lower()}%")
        if search:
            where.append(
                "(LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ? OR "
                "EXISTS (SELECT 1 FROM chat_messages cm WHERE cm.conversation_id = c.id "
                "AND LOWER(cm.message) LIKE ?))"
            )
            q = f"%{search.lower()}%"
            params.extend([q, q, q])
        if date_from:
            where.append("c.last_message_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("c.last_message_at <= ?")
            params.append(date_to)

        where_sql = " AND ".join(where)
        total = conn.execute(
            f"""
            SELECT COUNT(*) AS cnt FROM conversations c
            JOIN users u ON u.id = c.user_id
            WHERE {where_sql}
            """,
            params,
        ).fetchone()["cnt"]

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT c.id, c.session_id, c.status, c.handoff_reason, c.priority,
                   c.started_at, c.last_message_at, c.resolved_at, c.queued_at,
                   c.assigned_agent_id, c.tags,
                   u.id AS user_id, u.name AS user_name, u.email AS user_email,
                   a.name AS agent_name,
                   (SELECT COUNT(*) FROM chat_messages cm WHERE cm.conversation_id = c.id) AS message_count,
                   (SELECT cm.message FROM chat_messages cm WHERE cm.conversation_id = c.id
                    ORDER BY cm.id DESC LIMIT 1) AS last_message
            FROM conversations c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN admin_users a ON a.id = c.assigned_agent_id
            WHERE {where_sql}
            ORDER BY c.last_message_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()

        items = []
        for row in rows:
            item = _row_to_dict(row)
            item["tags"] = json.loads(item.get("tags") or "[]")
            items.append(item)

        return {
            "data": items,
            "pagination": {
                "total_items": total,
                "total_pages": max(1, math.ceil(total / page_size)),
                "current_page": page,
                "page_size": page_size,
            },
        }
    finally:
        conn.close()


def get_conversation_messages(conversation_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, role, sender_type, message, image_path, tool_calls_json,
                   metadata_json, latency_ms, timestamp
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = _row_to_dict(row)
            if item.get("tool_calls_json"):
                try:
                    item["tool_calls"] = json.loads(item["tool_calls_json"])
                except json.JSONDecodeError:
                    item["tool_calls"] = []
            if item.get("metadata_json"):
                try:
                    item["metadata"] = json.loads(item["metadata_json"])
                except json.JSONDecodeError:
                    item["metadata"] = {}
            result.append(item)
        return result
    finally:
        conn.close()


def list_chat_users(
    page: int = 1,
    page_size: int = PAGE_SIZE,
    search: Optional[str] = None,
) -> dict:
    conn = get_connection()
    try:
        where = ["1=1"]
        params: list[Any] = []
        if search:
            where.append("(LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ?)")
            q = f"%{search.lower().strip()}%"
            params.extend([q, q])
        where_sql = " AND ".join(where)

        total = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM users u WHERE {where_sql}",
            params,
        ).fetchone()["cnt"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT u.id, u.name, u.email, u.session_id, u.created_at,
                   (SELECT COUNT(*) FROM chat_messages cm
                    JOIN conversations c ON c.id = cm.conversation_id
                    WHERE c.user_id = u.id) AS message_count,
                   (SELECT c.last_message_at FROM conversations c
                    WHERE c.user_id = u.id ORDER BY c.last_message_at DESC LIMIT 1) AS last_active,
                   (SELECT c.id FROM conversations c
                    WHERE c.user_id = u.id ORDER BY c.last_message_at DESC LIMIT 1) AS latest_conversation_id
            FROM users u
            WHERE {where_sql}
            ORDER BY COALESCE(last_active, '') DESC, u.created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
        return {
            "data": [_row_to_dict(r) for r in rows],
            "pagination": {
                "total_items": total,
                "total_pages": max(1, math.ceil(total / page_size) if total else 1),
                "current_page": page,
                "page_size": page_size,
            },
        }
    finally:
        conn.close()


def get_chat_user(user_id: int, conversation_id: Optional[int] = None) -> Optional[dict]:
    conn = get_connection()
    try:
        user_row = conn.execute(
            "SELECT id, name, email, session_id, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user_row:
            return None
        user = _row_to_dict(user_row)
        conv_rows = conn.execute(
            """
            SELECT c.id, c.session_id, c.status, c.last_message_at, c.started_at,
                   (SELECT COUNT(*) FROM chat_messages cm WHERE cm.conversation_id = c.id) AS message_count
            FROM conversations c
            WHERE c.user_id = ?
            ORDER BY c.last_message_at DESC
            """,
            (user_id,),
        ).fetchall()
        conversations = [_row_to_dict(row) for row in conv_rows]
    finally:
        conn.close()

    selected = None
    if conversation_id:
        selected = next((c for c in conversations if int(c.get("id") or 0) == int(conversation_id)), None)
    if selected is None and conversations:
        selected = conversations[0]

    messages = get_conversation_messages(selected["id"]) if selected else []
    full = get_conversation_by_id(selected["id"]) if selected else None

    return {
        "user": user,
        "conversations": conversations,
        "conversation": full or selected,
        "messages": messages,
    }


def set_conversation_tags(conversation_id: int, tags: list[str]) -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE conversations SET tags = ? WHERE id = ?",
            (json.dumps(tags), conversation_id),
        )
        conn.commit()
        return get_conversation_by_id(conversation_id)
    finally:
        conn.close()


def export_conversations_csv(conversation_ids: Optional[list[int]] = None) -> str:
    conn = get_connection()
    try:
        if conversation_ids:
            placeholders = ",".join("?" * len(conversation_ids))
            convs = conn.execute(
                f"""
                SELECT c.id, c.session_id, c.status, u.name, u.email
                FROM conversations c JOIN users u ON u.id = c.user_id
                WHERE c.id IN ({placeholders})
                """,
                conversation_ids,
            ).fetchall()
        else:
            convs = conn.execute(
                """
                SELECT c.id, c.session_id, c.status, u.name, u.email
                FROM conversations c JOIN users u ON u.id = c.user_id
                ORDER BY c.id
                """
            ).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "conversation_id", "session_id", "status", "user_name", "user_email",
            "role", "sender_type", "message", "timestamp",
        ])
        for conv in convs:
            msgs = conn.execute(
                """
                SELECT role, sender_type, message, timestamp
                FROM chat_messages WHERE conversation_id = ? ORDER BY id
                """,
                (conv["id"],),
            ).fetchall()
            for msg in msgs:
                writer.writerow([
                    conv["id"], conv["session_id"], conv["status"],
                    conv["name"], conv["email"],
                    msg["role"], msg["sender_type"], msg["message"], msg["timestamp"],
                ])
        return output.getvalue()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def log_event(
    event_type: str,
    *,
    session_id: Optional[str] = None,
    conversation_id: Optional[int] = None,
    payload: Optional[dict] = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO analytics_events (event_type, session_id, conversation_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_type, session_id, conversation_id, json.dumps(payload or {}), _now()),
        )
        conn.commit()
    except Exception:
        logger.exception("DATABASE | log_event failed | type=%s", event_type)
    finally:
        conn.close()


def _parse_bound(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    if len(text) == 10:
        return text + " 00:00:00"
    if len(text) == 16:
        return text + ":00"
    return text[:19]


def _as_dt(value: str) -> datetime:
    return datetime.fromisoformat(_parse_bound(value).replace(" ", "T"))


def _and_range(column: str, date_from: Optional[str], date_to: Optional[str]) -> tuple[str, list]:
    start = _parse_bound(date_from)
    end = _parse_bound(date_to)
    clauses: list[str] = []
    params: list[Any] = []
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{column} <= ?")
        params.append(end)
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def get_analytics_overview(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    conn = get_connection()
    try:
        conv_range, conv_params = _and_range("started_at", date_from, date_to)
        event_range, event_params = _and_range("created_at", date_from, date_to)
        msg_range, msg_params = _and_range("timestamp", date_from, date_to)
        fb_range, fb_params = _and_range("created_at", date_from, date_to)

        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        total_conversations = conn.execute(
            f"SELECT COUNT(*) AS c FROM conversations WHERE 1=1{conv_range}",
            conv_params,
        ).fetchone()["c"]
        today = datetime.utcnow().date().isoformat()
        today_conversations = conn.execute(
            "SELECT COUNT(*) AS c FROM conversations WHERE DATE(started_at) = ?",
            (today,),
        ).fetchone()["c"]
        handoff_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM analytics_events WHERE event_type = 'handoff_requested'{event_range}",
            event_params,
        ).fetchone()["c"]
        resolved_handoffs = conn.execute(
            f"SELECT COUNT(*) AS c FROM analytics_events WHERE event_type = 'handoff_resolved'{event_range}",
            event_params,
        ).fetchone()["c"]
        total_messages = conn.execute(
            f"SELECT COUNT(*) AS c FROM chat_messages WHERE 1=1{msg_range}",
            msg_params,
        ).fetchone()["c"]
        avg_messages = (
            round(total_messages / total_conversations, 1) if total_conversations else 0
        )
        handoff_rate = (
            round(handoff_count / total_conversations * 100, 1)
            if total_conversations else 0
        )
        containment_rate = round(max(0, 100 - handoff_rate), 1)
        csat = conn.execute(
            f"SELECT AVG(rating) AS avg_rating, COUNT(*) AS count FROM message_feedback WHERE 1=1{fb_range}",
            fb_params,
        ).fetchone()
        online_agents = conn.execute(
            "SELECT COUNT(*) AS c FROM admin_users WHERE is_online = 1"
        ).fetchone()["c"]
        queued = conn.execute(
            "SELECT COUNT(*) AS c FROM conversations WHERE status = 'queued'"
        ).fetchone()["c"]

        return {
            "total_users": total_users,
            "total_conversations": total_conversations,
            "conversations_today": today_conversations,
            "conversations_in_period": total_conversations,
            "total_messages": total_messages,
            "avg_messages_per_conversation": avg_messages,
            "handoff_requests": handoff_count,
            "handoff_resolved": resolved_handoffs,
            "handoff_rate_percent": handoff_rate,
            "containment_rate_percent": containment_rate,
            "csat_average": round(csat["avg_rating"], 2) if csat["avg_rating"] else None,
            "csat_count": csat["count"],
            "online_agents": online_agents,
            "queued_conversations": queued,
            "trend": _conversation_trend(conn, date_from, date_to),
            "status_mix": _status_mix(conn, date_from, date_to),
            **get_performance_metrics(conn, date_from=date_from, date_to=date_to),
        }
    finally:
        conn.close()


def _conversation_trend(
    conn,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    end = _as_dt(date_to) if date_to else datetime.utcnow()
    start = _as_dt(date_from) if date_from else end - timedelta(days=13)
    if start > end:
        start, end = end, start
    hourly = (end - start) <= timedelta(hours=48)
    start_bound = _parse_bound(date_from) or start.strftime("%Y-%m-%d %H:%M:%S")
    end_bound = _parse_bound(date_to) or end.strftime("%Y-%m-%d %H:%M:%S")

    if hourly:
        key_sql = "strftime('%Y-%m-%d %H:00:00', {col})"
        conv_rows = conn.execute(
            f"""
            SELECT {key_sql.format(col='started_at')} AS bucket, COUNT(*) AS conversations
            FROM conversations
            WHERE started_at >= ? AND started_at <= ?
            GROUP BY bucket
            """,
            (start_bound, end_bound),
        ).fetchall()
        handoff_rows = conn.execute(
            f"""
            SELECT {key_sql.format(col='created_at')} AS bucket, COUNT(*) AS handoffs
            FROM analytics_events
            WHERE event_type = 'handoff_requested' AND created_at >= ? AND created_at <= ?
            GROUP BY bucket
            """,
            (start_bound, end_bound),
        ).fetchall()
        conv_map = {row["bucket"]: row["conversations"] for row in conv_rows if row["bucket"]}
        handoff_map = {row["bucket"]: row["handoffs"] for row in handoff_rows if row["bucket"]}
        series = []
        cursor = start.replace(minute=0, second=0, microsecond=0)
        last = end.replace(minute=0, second=0, microsecond=0)
        while cursor <= last:
            key = cursor.strftime("%Y-%m-%d %H:00:00")
            series.append({
                "day": key,
                "conversations": int(conv_map.get(key, 0)),
                "handoffs": int(handoff_map.get(key, 0)),
            })
            cursor += timedelta(hours=1)
        return series

    conv_rows = conn.execute(
        """
        SELECT DATE(started_at) AS day, COUNT(*) AS conversations
        FROM conversations
        WHERE started_at >= ? AND started_at <= ?
        GROUP BY DATE(started_at)
        """,
        (start_bound, end_bound),
    ).fetchall()
    handoff_rows = conn.execute(
        """
        SELECT DATE(created_at) AS day, COUNT(*) AS handoffs
        FROM analytics_events
        WHERE event_type = 'handoff_requested' AND created_at >= ? AND created_at <= ?
        GROUP BY DATE(created_at)
        """,
        (start_bound, end_bound),
    ).fetchall()
    conv_map = {row["day"]: row["conversations"] for row in conv_rows if row["day"]}
    handoff_map = {row["day"]: row["handoffs"] for row in handoff_rows if row["day"]}
    series = []
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        key = cursor.isoformat()
        series.append({
            "day": key,
            "conversations": int(conv_map.get(key, 0)),
            "handoffs": int(handoff_map.get(key, 0)),
        })
        cursor += timedelta(days=1)
    return series


def _status_mix(
    conn,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    extra, params = _and_range("started_at", date_from, date_to)
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM conversations WHERE 1=1{extra} GROUP BY status",
        params,
    ).fetchall()
    counts = {row["status"] or "unknown": row["count"] for row in rows}
    order = ["bot", "queued", "with_agent", "resolved"]
    labels = {
        "bot": "Handled by AI",
        "queued": "Waiting",
        "with_agent": "With agent",
        "resolved": "Resolved",
    }
    mix = [{"status": key, "label": labels[key], "count": int(counts.get(key, 0))} for key in order]
    extras = [k for k in counts if k not in order]
    for key in extras:
        mix.append({"status": key, "label": str(key).replace("_", " ").title(), "count": int(counts[key])})
    return mix


def get_performance_metrics(
    conn=None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        msg_range, msg_params = _and_range("timestamp", date_from, date_to)
        queued_range, queued_params = _and_range("c.queued_at", date_from, date_to)

        ai_latency = conn.execute(
            f"""
            SELECT AVG(latency_ms) AS avg_ms, COUNT(*) AS count
            FROM chat_messages
            WHERE role = 'ai' AND latency_ms IS NOT NULL AND latency_ms > 0{msg_range}
            """,
            msg_params,
        ).fetchone()

        agent_reply = conn.execute(
            f"""
            SELECT AVG(
                (julianday(first_agent.ts) - julianday(c.queued_at)) * 86400
            ) AS avg_sec
            FROM conversations c
            INNER JOIN (
                SELECT conversation_id, MIN(timestamp) AS ts
                FROM chat_messages
                WHERE sender_type = 'agent'
                GROUP BY conversation_id
            ) first_agent ON first_agent.conversation_id = c.id
            WHERE c.queued_at IS NOT NULL
              AND julianday(first_agent.ts) >= julianday(c.queued_at)
              {queued_range}
            """,
            queued_params,
        ).fetchone()

        started_range, started_params = _and_range("started_at", date_from, date_to)
        repeat_visitors = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM (
                SELECT user_id FROM conversations
                WHERE 1=1{started_range}
                GROUP BY user_id HAVING COUNT(*) > 1
            )
            """,
            started_params,
        ).fetchone()["c"]

        tool_turns = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM chat_messages
            WHERE tool_calls_json IS NOT NULL AND tool_calls_json != '' AND tool_calls_json != '[]'
            {msg_range}
            """,
            msg_params,
        ).fetchone()["c"]

        ai_turns = conn.execute(
            f"SELECT COUNT(*) AS c FROM chat_messages WHERE role = 'ai'{msg_range}",
            msg_params,
        ).fetchone()["c"]

        tool_success_rate = (
            round(tool_turns / ai_turns * 100, 1) if ai_turns else 0
        )

        return {
            "avg_ai_response_ms": round(ai_latency["avg_ms"]) if ai_latency["avg_ms"] else None,
            "avg_ai_response_samples": ai_latency["count"],
            "avg_agent_first_reply_sec": round(agent_reply["avg_sec"], 1) if agent_reply["avg_sec"] else None,
            "repeat_visitors": repeat_visitors,
            "tool_success_rate_percent": tool_success_rate,
            "top_topics": get_top_topics(conn, date_from=date_from, date_to=date_to),
        }
    finally:
        if owns_conn:
            conn.close()


def get_top_topics(
    conn=None,
    limit: int = 8,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        extra, params = _and_range("COALESCE(queued_at, started_at)", date_from, date_to)
        rows = conn.execute(
            f"""
            SELECT handoff_summary FROM conversations
            WHERE handoff_summary IS NOT NULL AND handoff_summary != ''{extra}
            """,
            params,
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            summary = row["handoff_summary"] or ""
            if "Detected intent:" not in summary:
                continue
            intent = summary.split("Detected intent:", 1)[1].split("\n", 1)[0].strip()
            if intent:
                counts[intent] = counts.get(intent, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: -item[1])[:limit]
        return [{"topic": topic, "count": count} for topic, count in ranked]
    finally:
        if owns_conn:
            conn.close()


def get_tool_analytics(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    conn = get_connection()
    try:
        extra, params = _and_range("timestamp", date_from, date_to)
        rows = conn.execute(
            f"""
            SELECT tool_calls_json FROM chat_messages
            WHERE tool_calls_json IS NOT NULL AND tool_calls_json != ''{extra}
            """,
            params,
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            try:
                tools = json.loads(row["tool_calls_json"])
                for tool in tools:
                    counts[tool] = counts.get(tool, 0) + 1
            except json.JSONDecodeError:
                continue
        return [{"tool": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]
    finally:
        conn.close()


def get_csat_analytics(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    conn = get_connection()
    try:
        extra, params = _and_range("created_at", date_from, date_to)
        rows = conn.execute(
            f"""
            SELECT rating, COUNT(*) AS count
            FROM message_feedback
            WHERE 1=1{extra}
            GROUP BY rating ORDER BY rating
            """,
            params,
        ).fetchall()
        distribution = {str(r["rating"]): r["count"] for r in rows}
        avg = conn.execute(
            f"SELECT AVG(rating) AS a FROM message_feedback WHERE 1=1{extra}",
            params,
        ).fetchone()["a"]
        recent = conn.execute(
            f"""
            SELECT mf.rating, mf.comment, mf.created_at, u.name, u.email
            FROM message_feedback mf
            JOIN conversations c ON c.id = mf.conversation_id
            JOIN users u ON u.id = c.user_id
            WHERE 1=1{extra.replace('created_at', 'mf.created_at')}
            ORDER BY mf.id DESC LIMIT 20
            """,
            params,
        ).fetchall()
        return {
            "average": round(avg, 2) if avg else None,
            "distribution": distribution,
            "recent": [_row_to_dict(r) for r in recent],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def save_feedback(
    session_id: str,
    rating: int,
    comment: Optional[str] = None,
) -> bool:
    conv = get_conversation_by_session(session_id)
    if not conv:
        return False
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO message_feedback (conversation_id, session_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv["id"], session_id, rating, comment, _now()),
        )
        conn.commit()
        log_event("csat_submitted", session_id=session_id, conversation_id=conv["id"],
                  payload={"rating": rating})
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Business settings
# ---------------------------------------------------------------------------

DEFAULT_BUSINESS_HOURS = {
    "enabled": False,
    "timezone": "Europe/London",
    "days": {
        "mon": {"open": "09:00", "close": "17:00"},
        "tue": {"open": "09:00", "close": "17:00"},
        "wed": {"open": "09:00", "close": "17:00"},
        "thu": {"open": "09:00", "close": "17:00"},
        "fri": {"open": "09:00", "close": "17:00"},
        "sat": None,
        "sun": None,
    },
    "offline_message": "Our agents are available Mon–Fri 9am–5pm. We'll create a support ticket for you.",
}


def get_business_setting(key: str, default: Any = None) -> Any:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value_json FROM business_settings WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        return json.loads(row["value_json"])
    finally:
        conn.close()


def set_business_setting(key: str, value: Any) -> dict:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO business_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                                            updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), _now()),
        )
        conn.commit()
        return {"key": key, "value": value}
    finally:
        conn.close()


def is_within_business_hours() -> bool:
    from app.config import get_settings

    if not get_settings().handoff_business_hours_enforced:
        return True

    settings = get_business_setting("business_hours", DEFAULT_BUSINESS_HOURS)
    if not settings.get("enabled", True):
        return True
    now = datetime.utcnow()
    day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    day_hours = settings.get("days", {}).get(day_key)
    if not day_hours:
        return False
    current = now.strftime("%H:%M")
    return day_hours["open"] <= current <= day_hours["close"]


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------

def _detect_handoff_intent(tools_used: list[str], messages: list[dict]) -> str:
    tool_set = {t.lower() for t in tools_used}
    text = " ".join((m.get("message") or "").lower() for m in messages)

    if "get_order_status" in tool_set or "get_order_details" in tool_set or "order" in text:
        return "order_inquiry"
    if "create_return_request" in tool_set or "return" in text:
        return "returns"
    if "search_products" in tool_set or "find_product_category" in tool_set or "product" in text:
        return "product_inquiry"
    if "create_support_ticket" in tool_set or "complaint" in text:
        return "complaint"
    if "validate_discount_code" in tool_set or "discount" in text or "coupon" in text:
        return "billing"
    return "general_support"


def _extract_json_message(text: str) -> str | None:
    token = '"message": "'
    idx = text.find(token)
    if idx < 0:
        token = '"message":"'
        idx = text.find(token)
        if idx < 0:
            return None
    i = idx + len(token)
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": " ", "r": "", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    value = "".join(out).strip()
    return value or None


def _preview_message_text(msg: dict) -> str:
    text = msg.get("message") or ""
    image_path = msg.get("image_path")
    prefix = "[Image attached] " if image_path else ""
    text = re.sub(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+", "[Image attached]", text)
    stripped = text.strip()
    for _ in range(6):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I).strip()
        parsed = None
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("message"), str) and parsed["message"].strip():
            stripped = parsed["message"].strip()
            continue
        extracted = _extract_json_message(stripped)
        if extracted and extracted != stripped:
            stripped = extracted
            continue
        break
    stripped = stripped.replace("\\n", " ").replace('\\"', '"')
    stripped = re.sub(r"[A-Za-z0-9+/=]{80,}", "", stripped)
    stripped = re.sub(r"```(?:json)?", "", stripped, flags=re.I)
    stripped = re.sub(r"[{}\[\]]+", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip(" \t\n\r\"'`")
    return (prefix + stripped).strip()[:220]


def build_handoff_summary(session_id: str) -> str:
    conv = get_conversation_by_session(session_id)
    if not conv:
        return ""
    messages = get_conversation_messages(conv["id"])[-10:]
    tools_used: list[str] = []
    lines = []
    for msg in messages:
        role = msg.get("sender_type") or msg.get("role")
        text = _preview_message_text(msg)
        lines.append(f"[{role}] {text}")
        for tool in msg.get("tool_calls") or []:
            if tool not in tools_used:
                tools_used.append(tool)

    intent = _detect_handoff_intent(tools_used, messages)
    summary = f"Detected intent: {intent}\n\nRecent messages:\n" + "\n".join(lines)
    if tools_used:
        summary += "\n\nTools used: " + ", ".join(tools_used)
    return summary


def request_handoff(session_id: str, reason: Optional[str] = None) -> Optional[dict]:
    conv = get_or_create_conversation(session_id)
    if not conv:
        return None

    existing = get_conversation_by_id(conv["id"])
    if existing and existing.get("status") in ("queued", "with_agent"):
        return existing

    summary = build_handoff_summary(session_id)
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'queued', handoff_reason = ?, handoff_summary = ?,
                queued_at = ?, last_message_at = ?
            WHERE id = ?
            """,
            (reason, summary, _now(), _now(), conv["id"]),
        )
        conn.commit()
        log_event("handoff_requested", session_id=session_id, conversation_id=conv["id"],
                  payload={"reason": reason})
        return get_conversation_by_id(conv["id"])
    finally:
        conn.close()


def get_handoff_status(session_id: str) -> Optional[dict]:
    conv = get_conversation_by_session(session_id)
    if not conv:
        return None
    queue_position = 0
    if conv["status"] == "queued":
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS pos FROM conversations
                WHERE status = 'queued' AND queued_at <= ?
                """,
                (conv.get("queued_at") or conv["started_at"],),
            ).fetchone()
            queue_position = row["pos"]
        finally:
            conn.close()
    return {
        "session_id": session_id,
        "conversation_id": conv["id"],
        "status": conv["status"],
        "queue_position": queue_position,
        "agent_name": conv.get("agent_name"),
        "handoff_reason": conv.get("handoff_reason"),
    }


def get_handoff_queue() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.session_id, c.status, c.handoff_reason, c.handoff_summary,
                   c.queued_at, c.priority, u.name AS user_name, u.email AS user_email
            FROM conversations c
            JOIN users u ON u.id = c.user_id
            WHERE c.status = 'queued'
            ORDER BY c.priority DESC, c.queued_at ASC
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_active_handoffs(agent_id: Optional[int] = None) -> list[dict]:
    conn = get_connection()
    try:
        query = """
            SELECT c.id, c.session_id, c.status, c.handoff_reason, c.handoff_summary,
                   c.queued_at, c.last_message_at, c.assigned_agent_id,
                   u.name AS user_name, u.email AS user_email,
                   a.name AS agent_name
            FROM conversations c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN admin_users a ON a.id = c.assigned_agent_id
            WHERE c.status = 'with_agent'
        """
        params: tuple = ()
        if agent_id is not None:
            query += " AND c.assigned_agent_id = ?"
            params = (agent_id,)
        query += " ORDER BY c.last_message_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def claim_handoff(conversation_id: int, agent_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not row or row["status"] not in ("queued", "with_agent"):
            return None
        conn.execute(
            """
            UPDATE conversations
            SET status = 'with_agent', assigned_agent_id = ?, last_message_at = ?
            WHERE id = ?
            """,
            (agent_id, _now(), conversation_id),
        )
        conn.commit()
        conv = get_conversation_by_id(conversation_id)
        if conv:
            log_event("handoff_claimed", session_id=conv["session_id"],
                      conversation_id=conversation_id, payload={"agent_id": agent_id})
        return conv
    finally:
        conn.close()


def release_handoff(conversation_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'queued', assigned_agent_id = NULL, queued_at = ?
            WHERE id = ?
            """,
            (_now(), conversation_id),
        )
        conn.commit()
        return get_conversation_by_id(conversation_id)
    finally:
        conn.close()


def resolve_handoff(conversation_id: int, resolved_by: str = "agent") -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'resolved', resolved_at = ?, assigned_agent_id = NULL
            WHERE id = ?
            """,
            (_now(), conversation_id),
        )
        conn.commit()
        conv = get_conversation_by_id(conversation_id)
        if conv:
            log_event("handoff_resolved", session_id=conv["session_id"],
                      conversation_id=conversation_id, payload={"resolved_by": resolved_by})
        return conv
    finally:
        conn.close()


def reset_conversation_to_bot(conversation_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'bot', assigned_agent_id = NULL, resolved_at = NULL
            WHERE id = ?
            """,
            (conversation_id,),
        )
        conn.commit()
        return get_conversation_by_id(conversation_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Inquiries (cancel order, support tickets, off-hours handoff)
# ---------------------------------------------------------------------------

INQUIRY_STATUSES = ("pending", "in_progress", "resolved", "rejected")
INQUIRY_PREFIX = "ENOI-"


def _format_inquiry(row) -> Optional[dict]:
    if not row:
        return None
    data = _row_to_dict(row)
    data.setdefault("reference", f"{INQUIRY_PREFIX}{data['id']}")
    try:
        data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
    except json.JSONDecodeError:
        data["metadata"] = {}
    return data


def create_inquiry(
    *,
    name: str,
    email: str,
    category: str,
    message: str,
    phone: str = "",
    subject: str = "",
    order_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source: str = "enox_ai",
    metadata: Optional[dict] = None,
) -> dict:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO inquiries (
                reference, name, email, phone, category, subject, message,
                order_id, status, session_id, source, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                f"tmp-{secrets.token_hex(8)}",
                name.strip(),
                email.strip().lower(),
                (phone or "").strip(),
                category.strip(),
                (subject or "").strip(),
                message.strip(),
                str(order_id).strip() if order_id else None,
                session_id,
                source,
                json.dumps(metadata or {}),
            ),
        )
        inquiry_id = cursor.lastrowid
        reference = f"{INQUIRY_PREFIX}{inquiry_id}"
        conn.execute(
            "UPDATE inquiries SET reference = ?, updated_at = ? WHERE id = ?",
            (reference, _now(), inquiry_id),
        )
        conn.commit()
        inquiry = get_inquiry_by_id(inquiry_id)  # type: ignore[arg-type]
        if inquiry:
            log_event(
                "inquiry_created",
                session_id=session_id,
                payload={"inquiry_id": inquiry_id, "category": category, "reference": reference},
            )
        return inquiry or {}
    finally:
        conn.close()


def get_inquiry_by_id(inquiry_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM inquiries WHERE id = ?", (inquiry_id,)).fetchone()
        return _format_inquiry(row)
    finally:
        conn.close()


def get_inquiry_by_reference(reference: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM inquiries WHERE reference = ?",
            (reference.strip().upper() if reference.upper().startswith(INQUIRY_PREFIX) else f"{INQUIRY_PREFIX}{reference}",),
        ).fetchone()
        return _format_inquiry(row)
    finally:
        conn.close()


def has_pending_cancel_inquiry(order_id: int | str, email: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id FROM inquiries
            WHERE category = 'order_cancel_request'
              AND order_id = ?
              AND email = ?
              AND status IN ('pending', 'in_progress')
            LIMIT 1
            """,
            (str(order_id), email.strip().lower()),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def list_inquiries(
    *,
    page: int = 1,
    status: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE
    clauses: list[str] = []
    params: list[Any] = []

    if status and status in INQUIRY_STATUSES:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if search:
        like = f"%{search.strip()}%"
        clauses.append(
            "(email LIKE ? OR name LIKE ? OR subject LIKE ? OR message LIKE ? OR reference LIKE ? OR order_id LIKE ?)"
        )
        params.extend([like, like, like, like, like, like])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_connection()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM inquiries {where}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT * FROM inquiries {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, PAGE_SIZE, offset],
        ).fetchall()
        items = [_format_inquiry(row) for row in rows]
        total_pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
        return {
            "data": items,
            "pagination": {
                "current_page": page,
                "total_pages": total_pages,
                "total_items": total,
                "page_size": PAGE_SIZE,
            },
        }
    finally:
        conn.close()


def update_inquiry_status(
    inquiry_id: int,
    *,
    status: str,
    agent_id: Optional[int] = None,
    note: Optional[str] = None,
) -> Optional[dict]:
    if status not in INQUIRY_STATUSES:
        raise ValueError(f"Invalid inquiry status: {status}")

    conn = get_connection()
    try:
        existing = get_inquiry_by_id(inquiry_id)
        if not existing:
            return None

        metadata = existing.get("metadata") or {}
        if note:
            metadata["resolution_note"] = note
        resolved_at = _now() if status in ("resolved", "rejected") else None
        conn.execute(
            """
            UPDATE inquiries
            SET status = ?,
                assigned_agent_id = COALESCE(?, assigned_agent_id),
                resolved_at = COALESCE(?, resolved_at),
                metadata_json = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                agent_id,
                resolved_at,
                json.dumps(metadata),
                _now(),
                inquiry_id,
            ),
        )
        conn.commit()
        inquiry = get_inquiry_by_id(inquiry_id)
        if inquiry:
            log_event(
                "inquiry_status_updated",
                session_id=inquiry.get("session_id"),
                payload={"inquiry_id": inquiry_id, "status": status},
            )
        return inquiry
    finally:
        conn.close()


def count_pending_inquiries() -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM inquiries WHERE status = 'pending'"
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def get_notification_summary() -> dict:
    conn = get_connection()
    try:
        pending_inquiries = conn.execute(
            "SELECT COUNT(*) FROM inquiries WHERE status = 'pending'"
        ).fetchone()[0]
        in_progress_inquiries = conn.execute(
            "SELECT COUNT(*) FROM inquiries WHERE status = 'in_progress'"
        ).fetchone()[0]
        queued_handoffs = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE status = 'queued'"
        ).fetchone()[0]
        return {
            "pending_inquiries": int(pending_inquiries or 0),
            "in_progress_inquiries": int(in_progress_inquiries or 0),
            "queued_handoffs": int(queued_handoffs or 0),
            "total_actionable": int(pending_inquiries or 0) + int(queued_handoffs or 0),
        }
    finally:
        conn.close()

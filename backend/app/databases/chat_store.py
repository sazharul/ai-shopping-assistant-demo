from datetime import datetime
import json
from app.databases.config import get_connection
from app.databases.admin_store import migrate_admin_schema, resolve_conversation_id, touch_conversation
import logging
import math
from uuid import uuid4
import random

from app.utils.validators import sanitize_display_name

logger = logging.getLogger(__name__)
# ---------------------------
# CREATE TABLE (run once at startup)
# ---------------------------
def init_db():
    conn = None
    try:
        conn = get_connection()

        # Enable foreign keys and WAL mode for better concurrency
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

        # Users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                session_id TEXT UNIQUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Chat messages table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                image_path TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Migrate existing tables that pre-date image_path column
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        if "image_path" not in columns:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN image_path TEXT")

        migrate_admin_schema(conn)

        conn.commit()
        logger.info("DATABASE | initialized successfully")

        _seed_admin_from_settings()

    except Exception:
        logger.exception("DATABASE | initialization failed")

    finally:
        if conn:
            conn.close()


def _seed_admin_from_settings() -> None:
    """Create default admin user when ADMIN_DEFAULT_* env vars are set."""
    try:
        from app.config import get_settings
        from app.databases.admin_store import seed_default_admin

        settings = get_settings()
        seed_default_admin(
            settings.admin_default_email,
            settings.admin_default_password,
            settings.admin_default_name,
        )
    except Exception:
        logger.exception("DATABASE | admin seed failed")

# ---------------------------
# SAVE or GET USER
# ---------------------------


def get_welcome_message(user_name: str) -> str:
    safe_name = sanitize_display_name(user_name)
    messages = [
        f"Hi **{safe_name}**! I'm **StyleHub AI**, your assistant from **StyleHub (portfolio demo)**. How may I help you today?",
        f"Welcome back, **{safe_name}**! I'm **StyleHub AI** from **StyleHub (portfolio demo)**. Got a question about your order or anything else? I'm here!",
        f"Hey **{safe_name}**! Great to have you here. I'm **StyleHub AI**, StyleHub's virtual assistant. What can I do for you today?",
        f"Hello **{safe_name}**! I'm **StyleHub AI** from **StyleHub (portfolio demo)**. Whether it's orders, returns, or products — I've got you covered. What do you need?",
        f"Hi there, **{safe_name}**! I'm **StyleHub AI**, your dedicated assistant at **StyleHub (portfolio demo)**. How can I assist you today?",
    ]
    return random.choice(messages)


def get_or_create_user(name: str, email: str) -> dict:
    conn = None
    greeting_session_id: str | None = None
    greeting_message: str | None = None
    result: dict | None = None

    try:
        conn = get_connection()

        cursor = conn.execute(
            """
            SELECT id, name, email, session_id
            FROM users
            WHERE email = ?
            """,
            (email,)
        )

        row = cursor.fetchone()

        if row:
            logger.info(
                "DATABASE | existing user found | email=%s user_id=%s",
                email,
                row["id"]
            )
            user_id = row["id"]

            last_msg_cursor = conn.execute(
                """
                SELECT timestamp
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,)
            )

            last_msg = last_msg_cursor.fetchone()

            if last_msg is None:
                needs_greeting = True
            else:
                last_time = datetime.fromisoformat(str(last_msg["timestamp"]))
                hours_since = (datetime.utcnow() - last_time).total_seconds() / 3600
                needs_greeting = hours_since >= 24

            if needs_greeting:
                greeting_session_id = row["session_id"]
                greeting_message = get_welcome_message(name)

            logger.info(
                "DATABASE | existing user found | email=%s user_id=%s needs_greeting=%s",
                email, row["id"], bool(greeting_message),
            )

            result = {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "session_id": row["session_id"]
            }
            conn.commit()

        else:
            session_id = str(uuid4())

            cursor = conn.execute(
                """
                INSERT INTO users (name, email, session_id)
                VALUES (?, ?, ?)
                """,
                (name, email, session_id)
            )

            conn.commit()

            user_id = cursor.lastrowid
            greeting_session_id = session_id
            greeting_message = get_welcome_message(name)

            logger.info(
                "DATABASE | new user created | user_id=%s email=%s",
                user_id,
                email
            )

            result = {
                "id": user_id,
                "name": name,
                "email": email,
                "session_id": session_id
            }

    except Exception:
        logger.exception(
            "DATABASE | get_or_create_user failed | email=%s",
            email
        )
        raise

    finally:
        if conn:
            conn.close()

    if greeting_session_id and greeting_message:
        save_message(greeting_session_id, "ai", greeting_message)

    return result  # type: ignore[return-value]


# ---------------------------
# SAVE MESSAGE
# ---------------------------
def _sender_type_for_role(role: str) -> str:
    if role == "ai":
        return "ai"
    if role == "agent":
        return "agent"
    if role == "system":
        return "system"
    return "user"


def save_message(
    session_id: str,
    role: str,
    message: str,
    image_path: str | None = None,
    *,
    sender_type: str | None = None,
    tool_calls: list[str] | None = None,
    metadata: dict | None = None,
    latency_ms: int | None = None,
):
    conn = None

    try:
        conn = get_connection()

        cursor = conn.execute(
            "SELECT id FROM users WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()

        if not row:
            logger.error(
                "DATABASE | user not found | session_id=%s",
                session_id,
            )
            return False

        user_id = row["id"]
        conversation_id = resolve_conversation_id(conn, session_id)
        effective_sender = sender_type or _sender_type_for_role(role)

        conn.execute(
            """
            INSERT INTO chat_messages (
                user_id,
                conversation_id,
                role,
                sender_type,
                message,
                image_path,
                tool_calls_json,
                metadata_json,
                latency_ms,
                timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                conversation_id,
                role,
                effective_sender,
                message,
                image_path,
                json.dumps(tool_calls) if tool_calls else None,
                json.dumps(metadata) if metadata else None,
                latency_ms,
                datetime.utcnow(),
            ),
        )

        if conversation_id:
            touch_conversation(conversation_id, conn)

        conn.commit()

        logger.info(
            "DATABASE | message saved | user_id=%s role=%s length=%s has_image=%s tools=%s",
            user_id,
            role,
            len(message),
            bool(image_path),
            tool_calls or [],
        )

        return True

    except Exception:
        logger.exception(
            "DATABASE | save message failed | session_id=%s role=%s",
            session_id,
            role,
        )
        raise

    finally:
        if conn:
            conn.close()


# ---------------------------
# GET HISTORY
# ---------------------------
PAGE_SIZE = 20
AGENT_HISTORY_LIMIT = 40

def get_recent_messages(session_id: str, limit: int = AGENT_HISTORY_LIMIT) -> list[dict]:
    """Return recent chat rows oldest-first for agent memory hydration."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.execute(
            """
            SELECT cm.role, cm.message
            FROM chat_messages cm
            JOIN users u ON u.id = cm.user_id
            WHERE u.session_id = ?
            ORDER BY cm.id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = cursor.fetchall()
        return [
            {"role": row["role"], "message": row["message"]}
            for row in reversed(rows)
        ]
    except Exception:
        logger.exception(
            "DATABASE | get_recent_messages failed | session_id=%s",
            session_id,
        )
        return []
    finally:
        if conn:
            conn.close()


def get_history(user_id: int, page: int = 1, session_id: str | None = None):
    conn = None

    try:
        conn = get_connection()

        # ------------------------
        # 1. Get user
        # ------------------------
        user_cursor = conn.execute(
            """
            SELECT id, name, email, session_id
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        )

        user = user_cursor.fetchone()

        if not user:
            logger.warning(
                "DATABASE | user not found | user_id=%s",
                user_id
            )

            return {
                "user": None,
                "data": [],
                "pagination": {
                    "total_items": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": PAGE_SIZE,
                }
            }

        if session_id and user["session_id"] != session_id:
            logger.warning(
                "DATABASE | session mismatch | user_id=%s",
                user_id
            )
            return {
                "user": None,
                "data": [],
                "pagination": {
                    "total_items": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": PAGE_SIZE,
                }
            }

        # ------------------------
        # 2. Count total messages
        # ------------------------
        count_cursor = conn.execute(
            """
            SELECT COUNT(*) as total
            FROM chat_messages
            WHERE user_id = ?
            """,
            (user_id,)
        )

        total_items = count_cursor.fetchone()["total"]
        total_pages = max(1, math.ceil(total_items / PAGE_SIZE))

        # ------------------------
        # 3. Pagination calculation
        # ------------------------
        offset = (page - 1) * PAGE_SIZE

        # ------------------------
        # 4. Fetch messages
        # ------------------------
        cursor = conn.execute(
            """
            SELECT role, message, image_path, timestamp
            FROM chat_messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, PAGE_SIZE, offset)
        )

        rows = cursor.fetchall()

        logger.info(
            "DATABASE | history fetched | user_id=%s page=%s count=%s",
            user_id,
            page,
            len(rows)
        )

        return {
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "session_id": user["session_id"]
            },
            "data": [
                {
                    "role": row["role"],
                    "message": row["message"],
                    "image_path": row["image_path"],
                    "timestamp": row["timestamp"]
                }
                for row in rows
            ],
            "pagination": {
                "total_items": total_items,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": PAGE_SIZE,
            }
        }

    except Exception:
        logger.exception(
            "DATABASE | get history failed | user_id=%s",
            user_id
        )

        return {
            "user": None,
            "data": [],
            "pagination": {
                "total_items": 0,
                "total_pages": 0,
                "current_page": page,
                "page_size": PAGE_SIZE,
            }
        }

    finally:
        if conn:
            conn.close()
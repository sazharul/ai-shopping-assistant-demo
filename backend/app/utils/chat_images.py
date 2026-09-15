from __future__ import annotations

import base64
import logging
import re
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from app.config import get_settings
from app.utils.validators import validate_session_id

logger = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _open_image(image_bytes: bytes) -> Image.Image:
    settings = get_settings()
    if len(image_bytes) > settings.max_image_upload_bytes:
        raise ValueError("Image file is too large")

    Image.MAX_IMAGE_PIXELS = 20_000_000
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    return image


def save_chat_image(session_id: str, image_base64: str) -> str:
    """
    Decode base64 image data, save as JPEG, and return a relative path
    suitable for storing in chat_messages.image_path (session_id/filename).
    """
    settings = get_settings()
    validate_session_id(session_id)

    b64 = image_base64
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    image_bytes = base64.b64decode(b64, validate=True)
    image = _open_image(image_bytes)

    upload_dir = Path(settings.chat_uploads_path) / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}.jpg"
    filepath = upload_dir / filename
    image.save(filepath, "JPEG", quality=92, optimize=True)

    relative_path = f"{session_id}/{filename}"
    logger.info("CHAT-IMAGE | saved | path=%s", relative_path)
    return relative_path


def get_chat_image_path(image_path: str) -> Path | None:
    """Resolve a stored image_path to an absolute file path, or None if invalid."""
    if not image_path or ".." in image_path:
        return None

    parts = image_path.split("/")
    if len(parts) != 2:
        return None

    session_id, filename = parts
    if not _FILENAME_RE.match(session_id) or not _FILENAME_RE.match(filename):
        return None

    try:
        validate_session_id(session_id)
    except ValueError:
        return None

    settings = get_settings()
    filepath = Path(settings.chat_uploads_path) / session_id / filename
    return filepath if filepath.is_file() else None

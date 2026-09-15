"""
app/utils/utils.py

Shared helpers used across all tools:
  - sanitize_optional_str  : normalises LLM-hallucinated null strings → None
  - call_api               : single async-safe HTTP POST with consistent error handling
  - error_response         : builds the standard failure dict without repeating it everywhere
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional, get_args
import sys

import requests

from app.models import CATEGORY_DESCRIPTIONS

# ---------------------------------------------------------------------------
# Logging — configure once here; every other module just calls getLogger()
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"app_{datetime.now().strftime('%Y-%m-%d')}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),   # explicit utf-8
            logging.StreamHandler(stream=open(            
                sys.stdout.fileno(),
                mode="w",
                encoding="utf-8",
                closefd=False,
            )),
        ],
    )


# ---------------------------------------------------------------------------
# LLM null-string sanitiser
# ---------------------------------------------------------------------------

# Strings that LLMs emit instead of a real null / empty value
_LLM_NULL_STRINGS: frozenset[str] = frozenset(
    {"null", "none", "nil", "undefined", "n/a", "na", ""}
)


def sanitize_optional_str(value: Any) -> Optional[str]:
    """
    Convert LLM-hallucinated null-like strings into Python ``None``.

    LLMs sometimes emit literal strings like "null", "none", or "undefined"
    instead of omitting optional fields. This helper normalises them so they
    never corrupt an API payload.

    Args:
        value: Raw value received from the LLM (any type accepted).

    Returns:
        Stripped string if the value is meaningful, ``None`` otherwise.

    Examples:
        >>> sanitize_optional_str("null")
        None
        >>> sanitize_optional_str("  hello@example.com  ")
        'hello@example.com'
        >>> sanitize_optional_str(None)
        None
    """
    if value is None:
        return None

    normalized = str(value).strip()

    if normalized.lower() in _LLM_NULL_STRINGS:
        return None

    return normalized


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT_SECONDS = 10.0


def error_response(message: str) -> dict:
    """
    Return a standardised failure dict that mirrors the backend's error shape.

    Centralising this means every tool returns the same structure on failure,
    making it trivial for the LLM to detect and relay errors.
    """
    return {"status": False, "message": message, "data": []}


def post_to_api(
    endpoint: str,
    payload: dict,
    headers: dict,
    logger: logging.Logger,
    *,
    base_url: str,
) -> dict:
    if os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes"):
        from app.utils.demo_stubs import demo_api_response

        logger.info("DEMO_MODE | stub response for %s", endpoint)
        return demo_api_response(endpoint, payload)

    """
    POST ``payload`` to ``{base_url}{endpoint}`` and return the parsed JSON.

    On any failure (network, HTTP error, non-JSON body) this logs the problem
    and returns a standardised error dict instead of raising — keeping the
    LangChain agent loop alive.

    Args:
        endpoint:  Path portion of the URL, e.g. ``"/api/order/status"``.
        payload:   JSON-serialisable request body.
        headers:   HTTP headers (auth key, content type, etc.).
        logger:    Caller's logger so log lines show the right module name.
        base_url:  Base URL, injected rather than imported to stay testable.

    Returns:
        Parsed response dict, or a standardised error dict on failure.
    """
    url = f"{base_url.rstrip('/')}{endpoint}"
    api_key = headers.get("X-INTERNAL-KEY", "")
    logger.info(
        "POST %s | payload_keys=%s | key_prefix=%s",
        url,
        list(payload.keys()),
        api_key[:6] if api_key else "MISSING",
    )

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        # exc.response is Optional[Response] in requests' type stubs — guard before accessing it.
        # In practice raise_for_status() always attaches the response, but the type checker
        # can't prove that, so we narrow explicitly and fall back to a generic message.
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        error_details = ""
        if exc.response is not None:
            try:
                error_details = exc.response.json() # Try to grab Laravel's JSON error array
            except ValueError:
                error_details = exc.response.text[:200] # Fallback to raw text string

        logger.error("HTTP error | status=%s url=%s details=%s", status_code, url, error_details)
        return error_response(f"The server returned HTTP {status_code}: {error_details}")
    except requests.exceptions.RequestException as exc:
        logger.critical("Network error | url=%s error=%s", url, exc)
        return error_response("Could not reach the server. Please try again later.")
    except Exception as exc:
        # CRITICAL: Catch serialization errors or programming bugs!
        logger.info("API CALL ERROR | Unexpected error in post_to_api | error=%s", exc)
        return error_response("Internal tool serialization error.")

    try:
        data = response.json()
    except ValueError:
        logger.info("Non-JSON response | url=%s body_preview=%s", url, response.text[:200])
        return error_response("The server returned an unreadable response.")

    # Guarantee the 'data' key is always present so callers never KeyError
    data.setdefault("data", [])

    logger.info("Response OK | status=%s url=%s", data.get("status"), url)
    #logger.debug("Response body | %s", data)

    return data


# ---------------------------------------------------------------------------
# Support ticket doc string
# ---------------------------------------------------------------------------

CATEGORY_GUIDANCE = "\n".join(
    f"- {category.value}: {description}"
    for category, description in CATEGORY_DESCRIPTIONS.items()
)

CREATE_SUPPORT_TICKET_DOC = f"""Create a customer support ticket for issues that require review or action
    by a human support team.

    IMPORTANT RULES:

    1. NEVER call this tool without the customer's permission.
       Always ask first:
       "Would you like me to create a support ticket for you?"

    2. Before calling this tool, collect ALL required information:
       - name (required)
       - email (required)
       - phone (required)
       - message (required)

    3. The category MUST be selected from the supported categories below
       based on the customer's problem statement.

    4. If the issue is related to a specific order, ALWAYS request the
       order ID when reasonably available.

       Examples:
       - order cancellation
       - delivery delay
       - delivery failure
       - reshipment request
       - damaged shipment
       - missing order
       - wrong item
       - refund issue
       - product issue

    5. Subject may be generated automatically from the customer's issue
       if not explicitly provided.

       Example:
       Category: delivery_delay
       Subject: "Delivery Delay for Order"

    6. Message should contain a clear summary of the customer's issue.
       If the customer already explained the problem, summarize it
       instead of asking them to repeat everything.

    7. Only call this tool after:
       - customer agrees to create a ticket
       - required information has been collected

    Supported categories:

    {CATEGORY_GUIDANCE}

    Returns:
        JSON containing the created ticket ID and confirmation details.
    """
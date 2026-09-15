"""
app/agent/graph.py

The single LangGraph ReAct agent for the StyleHub ecommerce assistant.

All 14 tools are registered here — including search_knowledge_base (RAG).
The LLM decides in one reasoning loop which tool(s) to call, in what order,
and when it has enough information to give a final answer.

No router. No classifier. No separate code paths.

Product search special handling:
  - When search_products fires, its raw JSON result is captured.
  - The SSE stream emits an extra event:  data: {"product_data": [...]}
  - The LLM is instructed to respond with a compact JSON envelope:
      {"products": ["Title A", "Title B"]}
    so the frontend can match titles to the rich product_data payload.
"""

from __future__ import annotations

import logging
import json
import base64
import re
import time
from PIL import Image
from io import BytesIO
from typing import AsyncIterator, Optional

from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain.globals import set_verbose, set_debug
from app.rag.product_image_engine import product_image_engine
from app.models import ImageSearchResult

from app.config import get_settings
from app.tools.tools import ALL_TOOLS
from app.agent.prompt import _SYSTEM_PROMPT
from app.agent import memory as agent_memory_module
from app.databases.chat_store import save_message, get_recent_messages, AGENT_HISTORY_LIMIT
from app.utils.chat_images import save_chat_image
from app.agent.context import current_session_id

settings = get_settings()
logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Agent Configuration
# ---------------------------------------------------------------------------

set_verbose(settings.debug_mode)
#set_debug(True)

_llm = ChatOpenAI(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    temperature=0.2,
    streaming=True,
    model_kwargs={"stream_options": {"include_usage": True}}
)

_agent = None


def init_agent() -> None:
    """Build the LangGraph agent after async checkpoint storage is ready."""
    global _agent

    checkpointer = agent_memory_module.agent_memory
    if checkpointer is None:
        raise RuntimeError("Agent memory not initialized. Call init_agent_memory() first.")

    _agent = create_react_agent(
        model=_llm,
        tools=ALL_TOOLS,
        state_modifier=_SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )


def get_agent():
    if _agent is None:
        raise RuntimeError("Agent not initialized. Call init_agent() first.")
    return _agent


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def image_handler(image_base64: str, session_id: str, message: str) -> tuple[list[dict], str]:

    image_products: list[dict] | None = None

    augmented_message = message

    try:
        image_products = product_image_engine.agentSearch(
            pil_image=image_base64,
            top_k=settings.image_top_k_results or 5,
        )
        logger.info(
            "IMAGE-SEARCH | found %d matches for session=%s request=%s",
            len(image_products), session_id, image_products
        )
    except Exception:
        logger.exception("IMAGE-SEARCH | failed for session=%s", session_id)
        image_products = []
    
    if image_products and len(image_products) > 0:

        product_lines = "\n".join(
            f"- {p['product_id']}: {p['product_name']} (£{p['price']}, "
            f"colors: {', '.join(p.get('colors') or p.get('color') or [])})"
            for p in image_products
        )
        augmented_message = (
            f"{message}\n\n"
            f"[SYSTEM CONTEXT: The user uploaded a image. Visual search found these "
            f"matching products in our catalog, ranked by similarity:\n{product_lines}\n"
            f"Respond ONLY with a compact JSON object in this exact shape: "
            f'{{"message": "<A short, friendly message mentioning how many similar products you found based on the image and whether the top result is an exact match or only a similar match. always try to provide category url with text>", "products": ["<exact product_name 1>", "<exact product_name 2>", ...]}}\n'
            f"and note if it's an exact match or just similar. Don't call search_products again.]"
        )
    else:
        augmented_message = (
            f"{message}\n\n"
            f"[SYSTEM CONTEXT: The user uploaded a image but no visually similar "
            f"products were found in our catalog. Let them know and offer to help "
            f"Respond ONLY with a compact JSON object in this exact shape: "
            f'{{"message": "A short, friendly message explaining that no similar products were found and encouraging the user to search another image or by description>", "products": []}}\n'
            f"search by description instead.]"
        )

    return image_products, augmented_message


def build_ai_saved_message(agent_response: str, product_data: list[dict] | None) -> str:
    """Persist AI response with product_data for history reload; never store augmented prompts."""
    if not product_data:
        return agent_response

    try:
        payload = json.loads(agent_response)
        if isinstance(payload, dict):
            payload["product_data"] = product_data
            return json.dumps(payload)
    except json.JSONDecodeError:
        pass

    return json.dumps({"message": agent_response, "product_data": product_data})



def extract_display_message(agent_response: str) -> str:
    """Pull the friendly message string from the LLM's product JSON envelope."""
    if not agent_response.strip():
        return ""

    text = agent_response.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            msg = payload.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    except json.JSONDecodeError:
        pass

    return agent_response.strip()


def build_history_messages(session_id: str) -> list:
    """Rebuild LangGraph message history from persisted chat store."""
    rows = get_recent_messages(session_id, limit=AGENT_HISTORY_LIMIT)
    messages = []
    for row in rows:
        role = row["role"]
        text = row["message"]
        if role == "user":
            messages.append(HumanMessage(content=text))
        elif role == "ai":
            messages.append(AIMessage(content=extract_display_message(text)))
    return messages


async def build_agent_input(session_id: str, user_message: str, config: RunnableConfig) -> list:
    """
    Return messages for the next agent turn.

    Uses the LangGraph checkpoint when available; otherwise hydrates from SQLite
    so conversations survive server restarts.
    """
    agent = get_agent()
    state = await agent.aget_state(config)
    checkpoint_messages = []
    if state and state.values:
        checkpoint_messages = state.values.get("messages") or []

    if checkpoint_messages:
        return [HumanMessage(content=user_message)]

    history_messages = build_history_messages(session_id)
    if history_messages and isinstance(history_messages[-1], HumanMessage):
        history_messages[-1] = HumanMessage(content=user_message)
        return history_messages

    return [HumanMessage(content=user_message)]


def product_json_handler(product_data_to_emit: list[dict] | None, image_products: list[dict] | None, agent_response: str) -> list[dict] | None:

    if product_data_to_emit is not None or image_products is not None:
        product_titles = []
        try:
            response_json = json.loads(agent_response)
            if isinstance(response_json, dict) and "products" in response_json:
                product_titles = response_json["products"]
        except:
            pass

        if product_data_to_emit is None:
            product_data_to_emit = image_products
        
        if product_titles:
            filtered_products   = []
            used_ids            = set()
            for title in product_titles:
                clean_title = title.lower().strip()

                for product in product_data_to_emit: # type: ignore
                    product_name = product.get("product_name", "").lower().strip()

                    if (product_name == clean_title or product_name in clean_title or clean_title in product_name):
                        pid = product.get("product_id")
                        if pid and pid not in used_ids:
                            filtered_products.append(product)
                            used_ids.add(pid)
                            break
            # Update agent_response with filtered products
            try:
                response_json = json.loads(agent_response)
                if isinstance(response_json, dict):
                    response_json["product_data"] = filtered_products
                    agent_response = json.dumps(response_json)
            except:
                pass

            return filtered_products 
        else:
            return product_data_to_emit

    



async def stream_agent(message: str, session_id: str, image_base64: Optional[str] = None,) -> AsyncIterator[str | dict]:
    logger.info(
        "USER MESSAGE:stream_agent()  | session=%s message=%s has_image=%s",
        session_id, message, bool(image_base64),
    )

    # ── NEW: Image search runs BEFORE the agent ──────────────────────
    image_products: list[dict] | None   = None
    augmented_message                   = message

    if image_base64:
        image_products, augmented_message = image_handler(image_base64, session_id, message)

    user_image_path = None
    if image_base64:
        try:
            user_image_path = save_chat_image(session_id, image_base64)
        except Exception:
            logger.exception("CHAT-IMAGE | failed to save upload for session=%s", session_id)

    save_message(session_id, "user", message, image_path=user_image_path)

    config: RunnableConfig      = {"configurable": {"thread_id": session_id}}
    input_messages = await build_agent_input(session_id, augmented_message, config)
    tool_calls: list[str]       = []
    agent_response              = ""
    tool_name                   = None
    product_data_to_emit: list[dict] | None = None
    final_product_data: list[dict] | None = None
    is_product_turn = bool(image_base64)
    started_at = time.perf_counter()
    token = current_session_id.set(session_id)

    try:
        async for msg_chunk, metadata in get_agent().astream(
            {"messages": input_messages},
            config=config,
            stream_mode="messages",
        ):
            # ── Tool result interception ──────────────────────────────────
            if isinstance(msg_chunk, ToolMessage):
                tool_name = msg_chunk.name
                if tool_name == "search_products":
                    try:
                        payload = json.loads(msg_chunk.content)  # type: ignore
                        if payload.get("status") and payload.get("products"):
                            product_data_to_emit = payload["products"]
                            is_product_turn = True
                            logger.info(
                                "PRODUCT-STREAM | captured %d products for session=%s",
                                len(product_data_to_emit), session_id, # type: ignore
                            )
                    except (json.JSONDecodeError, AttributeError):
                        logger.warning(
                            "PRODUCT-STREAM | failed to parse search_products result for session=%s",
                            session_id,
                        )

            # ── AI text tokens ────────────────────────────────────────────
            if isinstance(msg_chunk, AIMessageChunk):
                if (msg_chunk.content and not msg_chunk.tool_calls and not msg_chunk.tool_call_chunks):
                    content_str = msg_chunk.content
                    if isinstance(content_str, str) and content_str:
                        agent_response += content_str
                        # Hold back raw JSON during product/image turns; send message at end
                        if not is_product_turn:
                            yield content_str

                if msg_chunk.tool_calls:
                    for tc in msg_chunk.tool_calls:
                        name = tc.get("name", "unknown")
                        tool_calls.append(name)
                        from app.databases.admin_store import log_event
                        log_event("tool_used", session_id=session_id, payload={"tool": name})

        # ── After stream ends, emit friendly message + product cards ──────
        if is_product_turn:
            logger.info("AGENT PRODUCT RESPONSE: %s", agent_response)
            final_product_data = product_json_handler(
                product_data_to_emit, image_products, agent_response
            ) or []
            display_message = extract_display_message(agent_response)
            if not display_message and not final_product_data:
                display_message = (
                    "I couldn't find matching products right now, "
                    "but I'd be happy to help you search another way."
                )
            yield {
                "__product_response__": {
                    "message": display_message,
                    "product_data": final_product_data,
                }
            }

    except Exception:
        logger.exception("AGENT FAILED:stream_agent()  | session=%s", session_id)
        yield "\nSorry, something went wrong."
    finally:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if agent_response.strip():
            saved_message = build_ai_saved_message(agent_response, final_product_data)
            metadata = {}
            if final_product_data:
                metadata["product_count"] = len(final_product_data)
            save_message(
                session_id,
                "ai",
                saved_message,
                sender_type="ai",
                tool_calls=tool_calls or None,
                metadata=metadata or None,
                latency_ms=latency_ms,
            )
        current_session_id.reset(token)

        logger.info(
            "AGENT RESPONSE:stream_agent()  | session=%s response=%s",
            session_id, agent_response,
        )
        logger.info(
            "AGENT TOOLS USED:stream_agent()  | session=%s tools=%s",
            session_id, tool_calls,
        )
        if "request_human_handoff" in tool_calls:
            try:
                from app.ws.events import emit_handoff_requested
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(emit_handoff_requested(session_id))
            except Exception:
                logger.exception("Failed to emit handoff notification for session=%s", session_id)


async def run_agent(message: str, session_id: str) -> dict:
    """
    Run one agent turn and return a structured result dict.

    Returns:
        {
            "answer":     str  — final response to show the customer,
            "tool_calls": list — names of every tool that fired this turn,
        }
    """
    logger.info(
        "USER MESSAGE:run_agent()  | session=%s message=%s",
        session_id, message[:80],
    )
    save_message(session_id, "user", message)
    config: RunnableConfig = {"configurable": {"thread_id": session_id}}
    input_messages = await build_agent_input(session_id, message, config)

    answer = ""
    tool_calls: list[str] = []
    started_at = time.perf_counter()
    token = current_session_id.set(session_id)

    try:
        result = await get_agent().ainvoke(
            {"messages": input_messages},
            config=config,
        )
        for msg in result.get("messages", []):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_calls.append(tc.get("name", "unknown"))

            if (
                hasattr(msg, "content")
                and msg.content
                and not getattr(msg, "tool_calls", None)
            ):
                answer = msg.content

    except Exception:
        logger.exception("AGENT FAILED:run_agent()  | session=%s", session_id)
        answer = "Sorry, something went wrong."
    finally:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if answer.strip():
            save_message(
                session_id,
                "ai",
                answer,
                sender_type="ai",
                tool_calls=tool_calls or None,
                latency_ms=latency_ms,
            )
        logger.info(
            "AGENT RESPONSE:run_agent()  | session=%s response=%s",
            session_id, answer,
        )
        logger.info(
            "AGENT TOOLS USED:run_agent()  | session=%s tools=%s",
            session_id, tool_calls,
            )
        if "request_human_handoff" in tool_calls:
            try:
                from app.ws.events import emit_handoff_requested
                await emit_handoff_requested(session_id)
            except Exception:
                logger.exception("Failed to emit handoff notification for session=%s", session_id)
        current_session_id.reset(token)

    return {"answer": answer, "tool_calls": tool_calls}
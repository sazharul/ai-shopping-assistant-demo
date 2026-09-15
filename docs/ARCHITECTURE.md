# Architecture

## Overview

StyleHub AI is a two-path conversational commerce assistant:

| Path | Trigger | How it works |
|------|---------|--------------|
| **RAG** | FAQ / policy questions | Hybrid FAISS + BM25 retrieval → LLM answer |
| **Agent** | Orders, returns, product search | LangGraph ReAct agent with backend tools |

## Request Flow

```
User message (React widget)
    → POST /api/v1/chat/stream (SSE)
    → LangGraph agent (GPT-4o)
        → search_knowledge_base (FAQ RAG)
        → search_products (product RAG)
        → get_store_catalog (demo JSON)
        → order/return tools (DEMO_MODE stubs)
    → SSE events: token, product_data, done
    → Widget renders streaming text + product cards
```

## Backend Modules

| Module | Purpose |
|--------|---------|
| `app/agent/graph.py` | LangGraph ReAct agent wiring |
| `app/agent/prompt.py` | System prompt and tool-routing rules |
| `app/rag/engine.py` | FAQ hybrid retrieval (FAISS + BM25) |
| `app/rag/product_engine.py` | Product semantic + keyword search |
| `app/rag/product_image_engine.py` | Optional CLIP image search |
| `app/tools/tools.py` | Agent tools + DEMO_MODE API stubs |
| `app/api/routes.py` | Chat, streaming, index management |
| `app/databases/chat_store.py` | Message persistence (SQLite) |
| `app/databases/admin_store.py` | Admin users, handoff queue |

## DEMO_MODE

When `DEMO_MODE=true` (default in `.env.example`):

- `post_to_api()` in `app/utils/utils.py` returns canned JSON from `app/utils/demo_stubs.py`
- No outbound calls to a Laravel commerce backend
- Fictional order IDs like `SH-10492` work in demos

## Index Build

On startup (or via `scripts/build_demo_indexes.py`):

1. Load `demo_faq.json` → embed with `text-embedding-3-small` → FAISS index
2. Load `demo_products.json` → embed `rag_text_blob` fields → product FAISS index
3. BM25 indexes built in-memory alongside FAISS

## Frontend Widget

The React app compiles to an embeddable chat widget:

- `useChat` hook manages SSE streaming and message state
- `ProductCards` renders inline carousel from `product_data` SSE events
- `parseMessageContent` normalises agent JSON product responses

## Optional: CLIP Image Search

Requires PyTorch and a pre-built FAISS index from `backend/data/demo_images/`.

Build via `POST /api/v1/image-index/build` or `backend/image_train/train_image_index.py`.

Skipped gracefully on startup if dependencies or index are missing.

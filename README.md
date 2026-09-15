# StyleHub AI Shopping Assistant — Demo

A portfolio demo of an AI shopping assistant built with **FastAPI**, **LangGraph**, and a **React** embeddable chat widget. Rebranded as **StyleHub** with synthetic catalog data only.

> **Portfolio demonstration only.** This repository is an independent showcase for recruiters and engineers.
> It uses the same technologies and architectural patterns from my production work, but it is **not**
> the source code of any client, employer, or live product. Fictional branding and synthetic data only.
> See [DISCLAIMER.md](DISCLAIMER.md).
>
> Production experience reference: [enorsia.com](https://enorsia.com/) (code not published).

## Features

- LangGraph ReAct agent with 13+ commerce tools (orders, returns, shipping, discounts)
- Hybrid **FAISS + BM25** RAG for FAQ and product search
- Server-Sent Events (SSE) streaming chat responses
- Inline product cards with colour/size selectors
- Human handoff queue UI (demo-only — no live agents)
- Optional CLIP image search (synthetic demo catalog)
- `DEMO_MODE=true` — all Laravel commerce API calls return canned JSON stubs

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, LangGraph, LangChain |
| AI | OpenAI GPT-4o, text-embedding-3-small |
| Retrieval | FAISS + BM25 hybrid search |
| Frontend | React 19, Vite 8, embeddable `<enox-chat>` widget |
| Optional | CLIP image search (PyTorch) |

## Architecture

```
React Widget  →  FastAPI  →  LangGraph Agent
                    │              ├── search_products (RAG)
                    │              ├── search_knowledge_base (FAQ RAG)
                    │              └── order/return tools (DEMO_MODE stubs)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Quick Start (Docker)

```bash
git clone https://github.com/sazharul/ai-shopping-assistant-demo.git
cd ai-shopping-assistant-demo
export OPENAI_API_KEY=your-openai-key
docker compose up --build
```

| Service | URL |
|---------|-----|
| Chat widget | http://localhost:5173 |
| API + docs | http://localhost:8001/docs |

On first run the backend builds FAISS indexes from `backend/data/demo_*.json` (requires `OPENAI_API_KEY`).

### Example prompts

- "Find me a blue dress under £50"
- "What's your return policy?"
- "Check order status for SH-10492"
- "Show me men's hoodies"

### Demo accounts

See [docs/DEMO_ACCOUNTS.md](docs/DEMO_ACCOUNTS.md).

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@stylehub.demo` | `DemoAdmin123!` |

## Local Development (without Docker)

### Backend

```bash
cd backend
cp .env.example .env
# Set OPENAI_API_KEY in .env
pip install -r requirements.txt
python scripts/build_demo_indexes.py
uvicorn main:app --reload --port 8001
```

### Frontend

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open http://localhost:5173

## Synthetic Data

All catalog data is fictional:

- `backend/data/demo_products.json` — 18 StyleHub fashion items
- `backend/data/demo_faq.json` — 15 generic ecommerce FAQs
- `backend/data/demo_catalog.json` — category tree for `get_store_catalog`
- `backend/data/demo_images/` — placeholder product images (SVG)

## License

MIT — see [LICENSE](LICENSE).

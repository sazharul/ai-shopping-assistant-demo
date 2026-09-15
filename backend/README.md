# Backend

FastAPI + LangGraph AI shopping assistant. See the [root README](../README.md) for setup instructions.

```bash
cp .env.example .env
pip install -r requirements.txt
python scripts/build_demo_indexes.py
uvicorn main:app --reload --port 8001
```

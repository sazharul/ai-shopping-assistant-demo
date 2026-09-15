#!/usr/bin/env python3
"""Build FAQ and product FAISS indexes from demo JSON data."""

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env.example")

from app.config import reload_settings
from app.rag.engine import rag_engine
from app.rag.product_engine import product_rag_engine


def main() -> None:
    settings = reload_settings()
    print("[build] Loading FAQ data...")
    rag_engine.load_faq_data(settings.faq_data_path)
    print("[build] Building FAQ index...")
    rag_engine.build_index()
    print(f"[build] FAQ index ready ({rag_engine.total_docs} docs)")

    print("[build] Loading product data...")
    product_rag_engine.load_product_data(settings.product_data_path)
    print("[build] Building product index...")
    product_rag_engine.build_index()
    print(f"[build] Product index ready ({product_rag_engine.total_products} products)")


if __name__ == "__main__":
    main()

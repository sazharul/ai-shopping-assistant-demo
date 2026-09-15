"""
app/rag/product_engine.py

Product catalogue RAG engine: semantic (FAISS) + keyword (BM25) hybrid retrieval.

At startup, each product's rag_text_blob is auto-generated if blank, giving the
LLM dense natural-language context for semantic matching.

Flow:
    startup → load_product_data() → load_index() or build_index()
    tool    → retrieve(query, filters?) → list[dict]   (plain dicts, not Documents)
"""

from __future__ import annotations

import json
import os
import re
import logging
from pathlib import Path
from typing import Optional

from langchain.schema import Document
from langchain.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

from app.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Blob generator — produces rich text for embeddings
# ---------------------------------------------------------------------------

def _make_rag_blob(p: dict) -> str:
    """
    Auto-generate a natural-language embedding blob from a product dict.
    Used when the stored rag_text_blob is empty or missing.
    """
    attrs = p.get("attributes", {})

    colors   = ", ".join(attrs.get("colors", [])) or "not specified"
    sizes    = ", ".join(str(s) for s in attrs.get("sizes", [])) or "not specified"
    occasions = ", ".join(attrs.get("occasion", [])) or "not specified"

    price_text = f"£{p['price']}"
    if p.get("has_discount") and p.get("discount_price"):
        price_text += f" (on sale: £{p['discount_price']}, {p.get('discount_percent', '')}% off)"

    stock = "in stock" if p.get("in_stock") else "out of stock"

    parts = [
        f"{p['product_name']} by {p.get('brand', 'StyleHub')}.",
        f"Category: {p.get('category', '')} | Department: {p.get('department', '')}.",
        f"Fabric: {attrs.get('fabric', 'not specified')}.",
        f"Fit: {attrs.get('fit', 'not specified')}.",
        f"Sleeve: {attrs.get('sleeve', 'not specified')}.",
        f"Neckline: {attrs.get('neckline', 'not specified')}.",
        f"Season: {attrs.get('season', 'not specified')}.",
        f"Available colors: {colors}.",
        f"Available sizes: {sizes}.",
        f"Occasions: {occasions}.",
        f"Price: {price_text}. Currently {stock}.",
    ]
    if p.get("rating") and p["rating"] > 0:
        parts.append(f"Rating: {p['rating']} from {p.get('total_reviews', 0)} reviews.")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ProductRAGEngine:
    """
    Hybrid FAISS + BM25 retrieval over the StyleHub product catalogue.
    Returns plain dicts (original product records) so tools can serialise
    directly to JSON for the LLM.
    """

    # How many results each sub-retriever fetches before fusion
    _K = settings.top_k_results or 5
    _MIN_RELEVANCE_SCORE = settings.product_min_relevance_score or 0.8

    def __init__(self) -> None:
        self._embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
        self._vectorstore: Optional[FAISS] = None
        self._bm25_retriever: Optional[BM25Retriever] = None
        self._ensemble_retriever: Optional[EnsembleRetriever] = None
        self._products: list[dict] = []          # raw product dicts
        self._id_map: dict[str, dict] = {}       # product_id → dict

    # -----------------------------------------------------------------------
    # Startup helpers
    # -----------------------------------------------------------------------

    def load_product_data(self, path: str) -> None:
        """Load products from JSON and pre-fill empty rag_text_blob fields."""
        with open(path, "r", encoding="utf-8") as f:
            raw: list[dict] = json.load(f)

        for p in raw:
            if not p.get("rag_text_blob"):
                p["rag_text_blob"] = _make_rag_blob(p)

        self._products = raw
        self._id_map = {p["product_id"]: p for p in raw}
        print(f"[ProductRAGEngine] Loaded {len(raw)} products.")

    def build_index(self) -> None:
        """Embed all products and persist FAISS index to disk."""
        docs = self._build_documents()
        self._vectorstore = FAISS.from_documents(docs, self._embeddings)

        index_path = settings.product_faiss_index_path   # add to config — see Step 4
        os.makedirs(index_path, exist_ok=True)
        self._vectorstore.save_local(index_path)

        self._build_bm25(docs)
        self._build_ensemble()
        print(f"[ProductRAGEngine] Index built: {len(docs)} products indexed.")

    def load_index(self) -> bool:
        """Load saved FAISS index. Returns False if none exists."""
        index_path = settings.product_faiss_index_path
        if not Path(index_path).exists():
            return False

        self._vectorstore = FAISS.load_local(
            index_path,
            self._embeddings,
            allow_dangerous_deserialization=True,
        )
        docs = self._build_documents()   # BM25 is never persisted
        self._build_bm25(docs)
        self._build_ensemble()
        print("[ProductRAGEngine] Product index loaded from disk.")
        return True

    # -----------------------------------------------------------------------
    # Retrieval
    # -----------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        department: Optional[str] = None,
        category: Optional[str] = None,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
        in_stock_only: bool = False,
        color: Optional[str] = None,
        size: Optional[str] = None,
        occasion: Optional[str] = None,
        top_k: int = 5,
        min_relevance_score: Optional[float] = None,
    ) -> list[dict]:
        if not self.is_ready:
            return []

        threshold = (
            min_relevance_score
            if min_relevance_score is not None
            else self._MIN_RELEVANCE_SCORE
        )

        def _norm(s: str) -> str:
            # normalize for comparison: lowercase, strip, collapse separators
            return re.sub(r"[\s\-_]+", " ", s.strip().lower())

        def _department_matches(filter_department: str, product_department: str) -> bool:
            return _norm(filter_department) == _norm(product_department)

        def _category_matches(filter_category: str, product_category: str) -> bool:
            # Exact match only — substring checks (e.g. "shirts" in "sweatshirts")
            # cause false positives across sibling categories.
            return _norm(filter_category) == _norm(product_category)

        def _title_matches_query(product_name: str, search_query: str) -> bool:
            name_norm = _norm(product_name)
            query_norm = _norm(search_query)
            if not query_norm:
                return False
            if query_norm == name_norm:
                return True
            # Whole-token match so "shirt" does not match "sweatshirt".
            tokens = [re.escape(token) for token in query_norm.split() if token]
            if not tokens:
                return False
            pattern = r"(?<!\w)" + r"\s+".join(tokens) + r"(?!\w)"
            return bool(re.search(pattern, name_norm))

        results: list[dict] = []
        seen: set[str] = set()

        # ── PASS 0: Deterministic department/category/title match — bypasses semantic threshold ──
        if category or query:
            for pid, p in self._id_map.items():
                if department and not _department_matches(department, p.get("department", "")):
                    continue

                category_hit = bool(
                    category and _category_matches(category, p.get("category", ""))
                )
                title_hit = bool(query and _title_matches_query(p.get("product_name", ""), query))

                if category and not category_hit:
                    continue
                if not category and query and not title_hit:
                    continue

                if in_stock_only and not p.get("in_stock", True):
                    continue

                # apply remaining attribute filters
                effective_price = p.get("discount_price") or p.get("price", 0)
                if max_price is not None and effective_price > max_price:
                    continue
                if min_price is not None and effective_price < min_price:
                    continue
                if color:
                    colors = [c.lower() for c in p.get("attributes", {}).get("colors", [])]
                    if color.lower() not in colors:
                        continue
                if size:
                    sizes = [str(s).lower() for s in p.get("attributes", {}).get("sizes", [])]
                    if str(size).lower() not in sizes:
                        continue
                if occasion:
                    occasions = [o.lower() for o in p.get("attributes", {}).get("occasion", [])]
                    if occasion.lower() not in occasions:
                        continue

                if pid not in seen:
                    seen.add(pid)
                    results.append(p)
                    if len(results) >= top_k:
                        return results

        # ── PASS 1: existing semantic + BM25 ensemble (unchanged, fills remaining slots) ──
        candidates = self._ensemble_retriever.invoke(query)  # type: ignore[union-attr] 
        scored_docs = self._vectorstore.similarity_search_with_relevance_scores(query, k=self._K * 3)  # type: ignore[union-attr]
        score_by_pid = {
            doc.metadata.get("product_id"): score
            for doc, score in scored_docs
            if doc.metadata.get("product_id")
        }

        for doc in candidates:
            pid = doc.metadata.get("product_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)

            p = self._id_map.get(pid)
            if not p:
                continue

            score = score_by_pid.get(pid)
            if score is not None and score < threshold:
                continue

            if department and not _department_matches(department, p.get("department", "")):
                continue
            if category and not _category_matches(category, p.get("category", "")):
                continue
            if in_stock_only and not p.get("in_stock", True):
                continue
            if max_price is not None:
                effective_price = p.get("discount_price") or p.get("price", 0)
                if effective_price > max_price:
                    continue
            if min_price is not None:
                effective_price = p.get("discount_price") or p.get("price", 0)
                if effective_price < min_price:
                    continue
            if color:
                colors = [c.lower() for c in p.get("attributes", {}).get("colors", [])]
                if color.lower() not in colors:
                    continue
            if size:
                sizes = [str(s).lower() for s in p.get("attributes", {}).get("sizes", [])]
                if str(size).lower() not in sizes:
                    continue
            if occasion:
                occasions = [o.lower() for o in p.get("attributes", {}).get("occasion", [])]
                if occasion.lower() not in occasions:
                    continue

            results.append(p)
            if len(results) >= top_k:
                break

        return results

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _build_documents(self) -> list[Document]:
        docs = []
        for p in self._products:
            docs.append(
                Document(
                    page_content=p["rag_text_blob"],
                    metadata={
                        "product_id":   p["product_id"],
                        "product_name": p["product_name"],
                        "department":   p.get("department", ""),
                        "category":     p.get("category", ""),
                        "price":        p.get("price", 0),
                        "in_stock":     p.get("in_stock", True),
                    },
                )
            )
        return docs

    def _build_bm25(self, docs: list[Document]) -> None:
        self._bm25_retriever = BM25Retriever.from_documents(docs)
        self._bm25_retriever.k = self._K

    def _build_ensemble(self) -> None:
        if self._bm25_retriever is None:
            raise ValueError("BM25 retriever must be initialized first.")
        semantic = self._vectorstore.as_retriever(  # type: ignore[union-attr]
            search_type="similarity",
            search_kwargs={"k": self._K},
        )
        self._ensemble_retriever = EnsembleRetriever(
            retrievers=[semantic, self._bm25_retriever],
            weights=[0.6, 0.4],
        )

    # -----------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._vectorstore is not None and self._ensemble_retriever is not None

    @property
    def total_products(self) -> int:
        return len(self._products)


# Singleton — imported everywhere
product_rag_engine = ProductRAGEngine()
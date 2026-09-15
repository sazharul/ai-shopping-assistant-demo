"""
app/rag/engine.py

Hybrid RAG engine: semantic (FAISS) + keyword (BM25) retrieval.

The singleton ``rag_engine`` is imported by the lifespan hook in main.py
and by the router in app/agent/router.py.

Flow:
    startup → load_faq_data() → load_index() or build_index()
    request → retrieve(query, category_filter?)  →  list[Document]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from langchain.schema import Document
from langchain.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

from app.config import get_settings
from app.models import FAQItem

settings = get_settings()


class RAGEngine:
    """
    Wraps FAISS (semantic) + BM25 (keyword) into a single hybrid retriever.

    Attributes are intentionally kept private-ish (prefix _) except for the
    three read-only properties used by the health/status endpoints.
    """

    def __init__(self) -> None:
        self._embeddings = OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
        self._vectorstore: Optional[FAISS] = None
        self._bm25_retriever: Optional[BM25Retriever] = None
        self._ensemble_retriever: Optional[EnsembleRetriever] = None
        self._documents: list[Document] = []
        self._faq_items: list[FAQItem] = []

    # -----------------------------------------------------------------------
    # Startup helpers (called from main.py lifespan)
    # -----------------------------------------------------------------------

    def load_faq_data(self, path: str) -> None:
        """Load and validate FAQ JSON against the FAQItem schema."""
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._faq_items = [FAQItem(**item) for item in raw]

    def build_index(self) -> None:
        """
        Embed all FAQ documents and persist the FAISS index to disk.
        Rebuilds both FAISS and BM25 from scratch.
        Called when no saved index is found on startup, or via POST /index/build.
        """
        docs = self._build_documents()

        # Semantic index — calls OpenAI embeddings API
        self._vectorstore = FAISS.from_documents(docs, self._embeddings)

        index_path = settings.faiss_index_path
        os.makedirs(index_path, exist_ok=True)
        self._vectorstore.save_local(index_path)

        self._build_bm25(docs)
        self._build_ensemble()

        print(f"[RAGEngine] Index built: {len(docs)} documents indexed.")

    def load_index(self) -> bool:
        """
        Load a previously saved FAISS index from disk.
        Returns False when no saved index exists (triggers build_index instead).
        """
        index_path = settings.faiss_index_path
        if not Path(index_path).exists():
            return False

        self._vectorstore = FAISS.load_local(
            index_path,
            self._embeddings,
            allow_dangerous_deserialization=True,
        )

        # BM25 is not persisted — rebuild from raw documents
        docs = self._build_documents()
        self._build_bm25(docs)
        self._build_ensemble()

        print("[RAGEngine] Index loaded from disk.")
        return True

    # -----------------------------------------------------------------------
    # Retrieval (called from app/agent/router.py)
    # -----------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        category_filter: Optional[str] = None,
        k: Optional[int] = None,
    ) -> list[Document]:
        """
        Return top-k documents for a query using hybrid retrieval.

        When category_filter is set, falls back to semantic-only search
        because BM25 does not support metadata filtering.
        """
        k = k or settings.top_k_results

        if category_filter:
            # Semantic-only when we need metadata filtering
            return self._vectorstore.similarity_search(  # type: ignore[union-attr]
                query, k=k, filter={"category": category_filter}
            )

        return self._ensemble_retriever.invoke(query)  # type: ignore[union-attr]

    def retrieve_with_scores(
        self,
        query: str,
        category_filter: Optional[str] = None,
        k: Optional[int] = None,
    ) -> list[tuple[Document, float]]:
        """
        Semantic retrieval with similarity scores.
        Used by the debug endpoint — not called in the main chat flow.
        """
        k = k or settings.top_k_results
        kwargs: dict = {"k": k}
        if category_filter:
            kwargs["filter"] = {"category": category_filter}
        return self._vectorstore.similarity_search_with_score(query, **kwargs)  # type: ignore[union-attr]

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _build_documents(self) -> list[Document]:
        """Convert FAQItems into LangChain Documents and cache them."""
        docs = []
        for item in self._faq_items:
            docs.append(
                Document(
                    page_content=item.embedding_text,
                    metadata={
                        "id": item.id,
                        "category": item.category,
                        "action_type": item.action_type,
                        "tool_name": getattr(item, "tool_name", None),
                        "question": item.question,
                        "answer": item.answer,
                        "keywords": ", ".join(item.keywords),
                        "source": item.metadata.source,
                        "updated_at": item.metadata.updated_at,
                    },
                )
            )
        self._documents = docs
        return docs

    def _build_bm25(self, docs: list[Document]) -> None:
        self._bm25_retriever = BM25Retriever.from_documents(docs)
        self._bm25_retriever.k = settings.top_k_results

    def _build_ensemble(self) -> None:
        # Assert or check that the retriever isn't None
        if self._bm25_retriever is None:
            raise ValueError("BM25 retriever must be initialized before building the ensemble.")
        
        semantic = self._vectorstore.as_retriever(  # type: ignore[union-attr]
            search_type="similarity",
            search_kwargs={"k": settings.top_k_results},
        )
        self._ensemble_retriever = EnsembleRetriever(
            retrievers=[semantic, self._bm25_retriever],
            weights=[settings.semantic_weight, settings.bm25_weight],
        )

    # -----------------------------------------------------------------------
    # Read-only properties used by health / status endpoints
    # -----------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        return self._vectorstore is not None and self._ensemble_retriever is not None

    @property
    def total_docs(self) -> int:
        return len(self._documents)

    @property
    def categories(self) -> list[str]:
        return list({item.category for item in self._faq_items})


# Singleton — imported everywhere that needs retrieval
rag_engine = RAGEngine()

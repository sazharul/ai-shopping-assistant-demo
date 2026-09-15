"""
app/rag/product_image_engine.py
================================
CLIP-based product image similarity search.
Mirrors the structure of your existing rag_engine / product_rag_engine.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
import pickle
import logging
import base64
from pathlib import Path
from io import BytesIO
from typing import Optional

import numpy as np
import faiss
import torch
import requests
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from app.config import get_settings
from app.models import ImageSearchResult
from app.tools.tools import _to_json

logger = logging.getLogger(__name__)
settings = get_settings()

class ProductImageRAGEngine:
    """
    CLIP-based image similarity engine.
    Loaded once at app startup (see main.py lifespan), mirrors rag_engine pattern.
    """

    def __init__(self) -> None:
        self.clip_model: Optional[CLIPModel] = None
        self.clip_processor: Optional[CLIPProcessor] = None

        self.index: Optional[faiss.Index] = None
        self.index_ids: list[str] = []
        self.products_map: dict = {}

        self._model_loaded = False

    # ────────────────────────────────────────────────
    # Model loading (call once at startup)
    # ────────────────────────────────────────────────
    def load_model(self) -> None:
        if self._model_loaded:
            return
        
        model_name = settings.image_clip_model
        if not model_name:
            logger.warning("No CLIP model specified in settings")
            return
        logger.info("Loading CLIP model: %s", model_name)
        self.clip_model = CLIPModel.from_pretrained(model_name)  # type: ignore
        self.clip_processor = CLIPProcessor.from_pretrained(model_name)  # type: ignore
        self.clip_model.eval()  # type: ignore[attr-defined]
        self._model_loaded = True
        logger.info("CLIP model loaded ✅")

    # ────────────────────────────────────────────────
    # Product data
    # ────────────────────────────────────────────────
    def load_product_data(self, json_path: str) -> None:
        path = Path(json_path)
        if not path.exists():
            logger.warning("Product image JSON not found at %s", json_path)
            self.products_map = {}
            return

        with open(path, "r") as f:
            products = json.load(f)

        if isinstance(products, dict):
            products = list(products.values())

        self.products_map = {p["product_id"]: p for p in products}
        logger.info("Loaded %d products for image indexing", len(self.products_map))

    # ────────────────────────────────────────────────
    # Index load / save
    # ────────────────────────────────────────────────
    def load_index(self) -> bool:
        index_path = settings.image_index_path
        ids_path = settings.image_ids_path

        if not index_path or not ids_path:
            logger.warning("No image index or id paths specified in settings")
            return False

        if Path(index_path).exists() and Path(ids_path).exists():
            self.index = faiss.read_index(index_path)
            with open(ids_path, "rb") as f:
                self.index_ids = pickle.load(f)
            logger.info("Image index loaded ✅ (%d products)", len(self.index_ids))
            return True
        logger.warning("No saved image index found at %s", index_path)
        return False

    def build_index(
        self,
        limit: Optional[int] = None,
    ) -> dict:
        """Fetch product images, embed with CLIP, build + persist FAISS index.

        Each product now has a `product_image` list (multiple images per
        product). Every image is embedded and added as its own vector in the
        FAISS index, but `indexed_ids` maps each vector back to the same
        `product_id` -- so a search can return the same product multiple
        times (once per matching image) or you can dedupe at query time.
        """
        image_base_url  = settings.image_base_url
        index_path      = settings.image_index_path
        ids_path        = settings.image_ids_path
        json_path       = settings.image_json_path

        if not index_path or not ids_path or not json_path or not image_base_url:
            logger.warning("No image index or id, json or image paths specified in settings")
            raise ValueError("No image index or id, json or image paths specified in settings")

        self.load_product_data(json_path)
        products = list(self.products_map.values())
        if limit:
            products = products[:limit]

        # Flatten to (product_id, image_filename) pairs so progress logging
        # reflects total images, not total products.
        image_tasks: list[tuple[str, str]] = []
        for i, product in enumerate(products):
            product_id = product.get("product_id", f"unknown_{i}")
            images = product.get("product_image", [])

            if isinstance(images, str):
                # Backward-compat: old data had a single filename string.
                images = [images] if images else []

            if not images:
                continue

            for image_filename in images:
                image_tasks.append((product_id, image_filename))

        embeddings: list[np.ndarray] = []
        indexed_ids: list[str] = []
        failed: list[dict] = []

        total = len(image_tasks)
        for i, (product_id, image_filename) in enumerate(image_tasks):
            try:
                url = image_base_url + image_filename
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                image = Image.open(BytesIO(response.content)).convert("RGB")
                emb = self._get_clip_embedding(image)

                embeddings.append(emb)
                indexed_ids.append(product_id)
                logger.info("[%d/%d] ✅ %s (%s)", i + 1, total, product_id, image_filename)

            except Exception as e:
                failed.append({
                    "product_id": product_id,
                    "image": image_filename,
                    "reason": str(e),
                })
                logger.warning(
                    "[%d/%d] ❌ %s (%s): %s", i + 1, total, product_id, image_filename, e
                )

        if not embeddings:
            raise ValueError("No images could be indexed.")

        matrix = np.array(embeddings).astype("float32")
        dimension = matrix.shape[1]  # 512 for CLIP ViT-B/32
        index = faiss.IndexFlatIP(dimension)  # cosine similarity on normalized vectors
        index.add(matrix)  # type: ignore[call-arg]

        Path(index_path).parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, index_path)
        with open(ids_path, "wb") as f:
            pickle.dump(indexed_ids, f)

        self.index = index
        self.index_ids = indexed_ids

        return {
            "indexed": len(indexed_ids),
            "indexed_products": len({pid for pid in indexed_ids}),
            "failed": len(failed),
            "failed_details": failed[:10],
            "dimension": dimension,
        }
    # ────────────────────────────────────────────────
    # Embedding helper
    # ────────────────────────────────────────────────
    def _get_clip_embedding(self, pil_image: Image.Image) -> np.ndarray:
        if not self._model_loaded:
            raise RuntimeError("CLIP model not loaded. Call load_model() first.")

        inputs = self.clip_processor(images=pil_image, return_tensors="pt")  # type: ignore
        with torch.no_grad():
            emb = self.clip_model.get_image_features(**inputs)  # type: ignore
        emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb.squeeze().numpy().astype("float32")

    # ────────────────────────────────────────────────
    # Search
    # ────────────────────────────────────────────────
    def apiSearch(
        self,
        pil_image: Image.Image,
        top_k: int = 5,
    ) -> list[ImageSearchResult]:
        
        image_base_url = settings.image_base_url
        min_score = settings.image_min_similarity
        
        if not image_base_url:
            logger.warning("No image base url specified in settings")
            raise ValueError("No image base url specified in settings")

        if self.index is None or len(self.index_ids) == 0:
            raise RuntimeError("No image index built yet. Call build_index() first.")

        query_vector = self._get_clip_embedding(pil_image).reshape(1, -1)
        scores, indices = self.index.search(query_vector, top_k)  # type: ignore[call-arg]

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            score = float(score)
            if score < min_score:
                continue
            product_id = self.index_ids[idx]
            product = self.products_map.get(product_id, {})

            attrs = product.get("attributes", {})
            results.append(ImageSearchResult(
                rank=len(results) + 1,
                score=round(float(score), 4),
                product_id=product_id,
                product_name=product.get("product_name"),
                product_url=product.get("product_url"),
                product_image=product.get("product_image", ""),
                department=product.get("department"),
                category=product.get("category"),
                price=product.get("price"),
                currency=product.get("currency", "GBP"),
                discount_price=product.get("discount_price"),
                discount_percent=product.get("discount_percent"),
                has_discount=product.get("has_discount", False),
                in_stock=product.get("in_stock", True),
                rating=product.get("rating"),
                total_reviews=product.get("total_reviews"),
                colors=attrs.get("colors", []),
                sizes=attrs.get("sizes", []),
                fabric=attrs.get("fabric"),
                fit=attrs.get("fit"),
                sleeve=attrs.get("sleeve"),
                neckline=attrs.get("neckline"),
                season=attrs.get("season"),
                occasion=attrs.get("occasion", []),
            ))
        return results

    def _filter_and_format(self, scores, indices, top_k, anchor_category: bool = True) -> list[dict]:
        """
        Formats raw FAISS hits into product dicts, sorted by score (descending,
        already guaranteed by FAISS).

        Category filtering strategy
        ----------------------------
        The #1 result is treated as the "anchor" -- we assume it's the closest
        visual match and therefore the correct category. Everything else is
        preferred to match that category; results from a different category
        are pushed to the back and only used to backfill if we don't have
        enough same-category matches to fill top_k.

        This is a soft filter, not a hard drop -- we never return fewer than
        top_k results just because of category mismatch (as long as the wider
        candidate pool has enough entries). Each result gets a
        "category_match" flag so callers/agents can see which ones were
        backfilled from a different category if it matters downstream.
        """
        min_score = settings.image_min_similarity
        candidates = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            score = float(score)
            if score < min_score:
                continue
            product_id = self.index_ids[idx]
            product = self.products_map.get(product_id)
            if not product:
                logger.warning("Indexed product_id %s missing from products_map", product_id)
                continue
            attrs = product.get("attributes", {})
            candidates.append({
                "score": round(score, 4),
                "product_id": product["product_id"],
                "product_name": product.get("product_name"),
                "product_url": product.get("product_url"),
                "product_image": product.get("product_image", ""),
                "category": product.get("category"),
                "department": product.get("department"),
                "price": product.get("price"),
                "currency": product.get("currency", "GBP"),
                "discount_price": product.get("discount_price"),
                "discount_percent": product.get("discount_percent"),
                "has_discount": product.get("has_discount", False),
                "in_stock": product.get("in_stock", True),
                "rating": product.get("rating"),
                "total_reviews": product.get("total_reviews"),
                "colors": attrs.get("colors", []),
                "sizes": attrs.get("sizes", []),
                "fabric": attrs.get("fabric"),
                "fit": attrs.get("fit"),
                "sleeve": attrs.get("sleeve"),
                "season": attrs.get("season"),
                "occasion": attrs.get("occasion", []),
                "neckline": attrs.get("neckline"),
            })

        if not candidates:
            return []

        if not anchor_category:
            for rank, item in enumerate(candidates[:top_k], start=1):
                item["rank"] = rank
                item["category_match"] = True
            return candidates[:top_k]

        anchor = candidates[0]
        anchor_category = anchor.get("category")
        anchor_department = anchor.get("department")

        seen = set()
        final = []

        for item in candidates:
            # Must match both category and department
            if (
                item.get("category") != anchor_category
                or item.get("department") != anchor_department
            ):
                continue

            # Keep only one result per product
            if item["product_id"] in seen:
                continue

            seen.add(item["product_id"])
            item["category_match"] = True
            item["rank"] = len(final) + 1
            final.append(item)

            if len(final) == top_k:
                break
        return final

    def agentSearch(self, pil_image: str, top_k: int = 5) -> list[dict]:
        image_base_url = settings.image_base_url
        if not image_base_url:
            raise ValueError("No image base url specified in settings")
        if self.index is None or len(self.index_ids) == 0:
            raise RuntimeError("No image index built yet. Call build_index() first.")

        b64 = pil_image.split(",")[1] if "," in pil_image else pil_image
        image = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
        query_vector = self._get_clip_embedding(image).reshape(1, -1)

        # search wider than top_k so category filtering + backfill doesn't starve you of results
        scores, indices = self.index.search(query_vector, top_k * 5)  # type: ignore[call-arg]
        results = self._filter_and_format(scores, indices, top_k, anchor_category=True)
        return results
    
    # ────────────────────────────────────────────────
    # Status helpers (mirrors rag_engine.total_docs style)
    # ────────────────────────────────────────────────
    @property
    def total_products(self) -> int:
        return len(self.index_ids)

    @property
    def is_ready(self) -> bool:
        return self.index is not None and len(self.index_ids) > 0


# Singleton instance — imported across the app, same pattern as rag_engine
product_image_engine = ProductImageRAGEngine()
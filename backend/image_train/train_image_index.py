"""
train_image_index.py
=====================
Standalone, resumable, multithreaded CLIP indexing script.
Uses your existing app.config settings for every path -- nothing hardcoded.

Run from your project root:

    tmux new -s imgindex
    python3 train_image_index.py
    # Ctrl+b, d to detach -- survives you closing your laptop / SSH session

Writes to settings.image_index_path / settings.image_ids_path, same paths
your ProductImageRAGEngine.load_index() already reads. Search code (apiSearch
/ agentSearch) needs zero changes.

If killed partway through, just run it again -- it resumes from the
checkpoint files in your data/ folder instead of starting over.

CHANGE LOG (this version)
--------------------------
- Images are now read from a local folder (IMAGE_DIR) instead of being
  downloaded over HTTP. No more `requests` calls, no network timeouts.
- Embedding is now BATCHED: instead of calling model.get_image_features()
  once per image, we load a batch of images (fast, local disk, many
  threads) and run ONE forward pass per batch. This is the main speed win
  once the network download step is gone -- the CPU-bound embedding call
  was always the real bottleneck, not the I/O.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
import pickle
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import faiss
import torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel

from app.config import get_settings

settings = get_settings()

# ────────────────────────────────────────────────
# Config -- straight from your app.config, nothing hardcoded
# ────────────────────────────────────────────────
IMAGE_JSON_PATH = settings.image_json_path      # data/product_images.json
IMAGE_DIR = Path(settings.image_local_dir)           # local folder holding all product images now
IMAGE_INDEX_PATH = settings.image_index_path      # data/product_image_index.faiss
IMAGE_IDS_PATH = settings.image_ids_path          # data/product_image_index_ids.pkl
CLIP_MODEL_NAME = settings.image_clip_model       # openai/clip-vit-base-patch32

# Checkpoint files live in the same folder as your index (i.e. data/)
DATA_DIR = Path(IMAGE_INDEX_PATH).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)
CKPT_EMBEDDINGS_PATH = DATA_DIR / "train_progress_embeddings.npy"
CKPT_IDS_PATH = DATA_DIR / "train_progress_ids.pkl"
CKPT_DONE_PATH = DATA_DIR / "train_progress_done.json"
CKPT_FAILED_PATH = DATA_DIR / "train_progress_failed.json"

CHECKPOINT_EVERY = 500        # local reads are fast -- checkpoint less often to cut disk-write overhead
LOAD_BATCH_SIZE = 64          # how many images we read from disk + embed together in one forward pass
LOAD_WORKERS = 12             # thread pool for reading/decoding images off local disk (cheap, can be generous)

# ────────────────────────────────────────────────
# Logging -- terminal AND file this time
# ────────────────────────────────────────────────

# Project root (parent of image_train)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

LOG_DIR = PROJECT_ROOT / "data"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "train_image_index.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def load_clip():
    logger.info("Loading CLIP model: %s", CLIP_MODEL_NAME)
    model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)  # type: ignore
    processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)  # type: ignore
    model.eval()  # type: ignore[attr-defined]
    torch.set_num_threads(max(1, os.cpu_count() - 2))  #type: ignore # no download threads competing for CPU anymore
    logger.info("CLIP model loaded OK")
    return model, processor


def get_clip_embeddings_batch(model, processor, pil_images: list[Image.Image]) -> np.ndarray:
    """One forward pass for a whole batch of images -- this is the speedup vs. one-at-a-time."""
    inputs = processor(images=pil_images, return_tensors="pt")  # type: ignore
    with torch.no_grad():
        emb = model.get_image_features(**inputs)  # type: ignore
    emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.numpy().astype("float32")


def load_product_data() -> dict:
    path = Path(IMAGE_JSON_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Product image JSON not found at {IMAGE_JSON_PATH}")
    with open(path, "r") as f:
        products = json.load(f)
    if isinstance(products, dict):
        products = list(products.values())
    products_map = {p["product_id"]: p for p in products}
    logger.info("Loaded %d products from %s", len(products_map), IMAGE_JSON_PATH)
    return products_map


def load_checkpoint():
    embeddings: list[np.ndarray] = []
    indexed_ids: list[str] = []
    done: set[str] = set()
    failed: list[dict] = []

    if CKPT_EMBEDDINGS_PATH.exists() and CKPT_IDS_PATH.exists():
        try:
            arr = np.load(CKPT_EMBEDDINGS_PATH)
            embeddings = [row for row in arr]
            with open(CKPT_IDS_PATH, "rb") as f:
                indexed_ids = pickle.load(f)
            logger.info("Resuming: %d images already embedded", len(indexed_ids))
        except Exception as e:
            logger.warning("Could not load embedding checkpoint, starting fresh: %s", e)
            embeddings, indexed_ids = [], []

    if CKPT_DONE_PATH.exists():
        try:
            done = set(json.loads(CKPT_DONE_PATH.read_text()))
        except Exception as e:
            logger.warning("Could not load done-set checkpoint: %s", e)

    if CKPT_FAILED_PATH.exists():
        try:
            failed = json.loads(CKPT_FAILED_PATH.read_text())
        except Exception:
            pass

    return embeddings, indexed_ids, done, failed


def save_checkpoint(embeddings, indexed_ids, done, failed):
    np.save(CKPT_EMBEDDINGS_PATH, np.array(embeddings).astype("float32"))
    with open(CKPT_IDS_PATH, "wb") as f:
        pickle.dump(indexed_ids, f)
    CKPT_DONE_PATH.write_text(json.dumps(list(done)))
    CKPT_FAILED_PATH.write_text(json.dumps(failed[:200]))


def clear_checkpoint():
    for p in (CKPT_EMBEDDINGS_PATH, CKPT_IDS_PATH, CKPT_DONE_PATH, CKPT_FAILED_PATH):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def load_image_local(product_id: str, image_filename: str):
    """Runs in a worker thread. Reads + decodes a single image from local disk. No network."""
    try:
        path = IMAGE_DIR / image_filename
        if not path.exists():
            return product_id, image_filename, None, f"file not found: {path}"
        image = Image.open(path).convert("RGB")
        # CLIP only ever looks at 224x224 -- shrink now so we never hold a
        # full-res product photo in memory during the batch.
        image.thumbnail((384, 384))
        return product_id, image_filename, image, None
    except Exception as e:
        return product_id, image_filename, None, str(e)


def build_index() -> dict:
    model, processor = load_clip()
    products_map = load_product_data()
    products = list(products_map.values())

    # Flatten to (product_id, image_filename) pairs.
    image_tasks: list[tuple[str, str]] = []
    for i, product in enumerate(products):
        product_id = product.get("product_id", f"unknown_{i}")
        images = product.get("product_image", [])
        if isinstance(images, str):
            images = [images] if images else []
        if not images:
            continue
        for image_filename in images:
            image_tasks.append((product_id, image_filename))

    total = len(image_tasks)
    logger.info("Total images to process: %d", total)
    logger.info("Reading images from local folder: %s", IMAGE_DIR.resolve())

    embeddings, indexed_ids, done, failed = load_checkpoint()

    remaining_tasks = [
        (pid, fname) for (pid, fname) in image_tasks
        if f"{pid}::{fname}" not in done
    ]
    logger.info("Remaining after resume: %d (skipping %d already done)",
                len(remaining_tasks), total - len(remaining_tasks))

    lock = threading.Lock()
    processed_since_checkpoint = 0
    completed_count = len(done)

    with ThreadPoolExecutor(max_workers=LOAD_WORKERS) as executor:
        for batch_start in range(0, len(remaining_tasks), LOAD_BATCH_SIZE):
            batch = remaining_tasks[batch_start:batch_start + LOAD_BATCH_SIZE]

            # 1) Load every image in this batch off local disk, in parallel.
            futures = [executor.submit(load_image_local, pid, fname) for (pid, fname) in batch]
            loaded = []  # list of (product_id, image_filename, PIL.Image)
            for future in as_completed(futures):
                product_id, image_filename, image, error = future.result()
                key = f"{product_id}::{image_filename}"
                if error is not None:
                    with lock:
                        failed.append({"product_id": product_id, "image": image_filename, "reason": error})
                        done.add(key)
                        completed_count += 1
                    logger.warning("[%d/%d] FAIL %s (%s): %s", completed_count, total, product_id, image_filename, error)
                    continue
                loaded.append((product_id, image_filename, image))

            if not loaded:
                continue

            # 2) One batched forward pass for everything that loaded successfully.
            pil_images = [img for (_, _, img) in loaded]
            try:
                batch_embs = get_clip_embeddings_batch(model, processor, pil_images)
            except Exception as e:
                # Batch-level failure (e.g. a corrupt image poisoning the batch) --
                # fall back to embedding one-by-one so we don't lose the whole batch.
                logger.warning("Batch embed failed (%s), falling back to per-image for this batch", e)
                batch_embs = []
                for (product_id, image_filename, image) in loaded:
                    try:
                        single = get_clip_embeddings_batch(model, processor, [image])
                        batch_embs.append(single[0])
                    except Exception as e2:
                        key = f"{product_id}::{image_filename}"
                        with lock:
                            failed.append({"product_id": product_id, "image": image_filename, "reason": str(e2)})
                            done.add(key)
                            completed_count += 1
                        logger.warning("[%d/%d] FAIL embed failed %s (%s): %s", completed_count, total, product_id, image_filename, e2)
                        batch_embs.append(None)
                batch_embs = [e for e in batch_embs if e is not None]

            # 3) Record results, close images, checkpoint periodically.
            with lock:
                for (product_id, image_filename, image), emb in zip(loaded, batch_embs):
                    key = f"{product_id}::{image_filename}"
                    embeddings.append(emb)
                    indexed_ids.append(product_id)
                    done.add(key)
                    completed_count += 1
                    processed_since_checkpoint += 1
                    image.close()
                    logger.info("[%d/%d] OK %s (%s)", completed_count, total, product_id, image_filename)

                if processed_since_checkpoint >= CHECKPOINT_EVERY:
                    save_checkpoint(embeddings, indexed_ids, done, failed)
                    processed_since_checkpoint = 0
                    logger.info("Checkpoint saved at %d/%d", completed_count, total)

    save_checkpoint(embeddings, indexed_ids, done, failed)

    if not embeddings:
        raise ValueError("No images could be indexed.")

    matrix = np.array(embeddings).astype("float32")
    dimension = matrix.shape[1]  # 512 for fashion-clip / CLIP ViT-B/32
    index = faiss.IndexFlatIP(dimension)
    index.add(matrix)  # type: ignore[call-arg]

    Path(IMAGE_INDEX_PATH).parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, IMAGE_INDEX_PATH)
    with open(IMAGE_IDS_PATH, "wb") as f:
        pickle.dump(indexed_ids, f)

    clear_checkpoint()  # only needed while a run is in progress

    return {
        "indexed": len(indexed_ids),
        "indexed_products": len({pid for pid in indexed_ids}),
        "failed": len(failed),
        "failed_details": failed[:10],
        "dimension": dimension,
    }


if __name__ == "__main__":
    result = build_index()
    logger.info(
        "DONE. indexed=%d indexed_products=%d failed=%d dimension=%d",
        result["indexed"], result["indexed_products"], result["failed"], result["dimension"],
    )
    if result["failed_details"]:
        logger.info("Sample failures: %s", result["failed_details"])
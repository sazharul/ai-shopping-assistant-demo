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
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import json
import pickle
import logging
import threading
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import faiss
import torch
import requests
from PIL import Image
from transformers import CLIPProcessor, CLIPModel

from app.config import get_settings

settings = get_settings()

# ────────────────────────────────────────────────
# Config -- straight from your app.config, nothing hardcoded
# ────────────────────────────────────────────────
IMAGE_JSON_PATH = settings.image_json_path      # data/product_images.json
IMAGE_BASE_URL = settings.image_base_url          # https://stylehub.com/upload/ecom_products/
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

CHECKPOINT_EVERY = 100
BATCH_SIZE = 100             # max images downloaded-but-not-yet-embedded at once
MAX_WORKERS = 8              # lowered from 20 -- reduce further if you still see OOM kills
DOWNLOAD_TIMEOUT = 15

# ────────────────────────────────────────────────
# Logging -- terminal AND file this time
# ────────────────────────────────────────────────
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
    torch.set_num_threads(max(1, os.cpu_count() - 4))  #type: ignore # no download threads competing for CPU anymore
    logger.info("CLIP model loaded OK")
    return model, processor


def get_clip_embedding(model, processor, pil_image: Image.Image) -> np.ndarray:
    inputs = processor(images=pil_image, return_tensors="pt")  # type: ignore
    with torch.no_grad():
        emb = model.get_image_features(**inputs)  # type: ignore
    emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.squeeze().numpy().astype("float32")


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


def download_image(product_id: str, image_filename: str):
    """Runs in a worker thread. I/O only -- no model access here."""
    try:
        url = IMAGE_BASE_URL + image_filename
        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content)).convert("RGB")
        # CLIP only ever looks at 224x224 -- shrink now so we never hold a
        # full-res product photo (often several thousand px) in memory.
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

    def handle_result(product_id, image_filename, image, error):
        nonlocal processed_since_checkpoint, completed_count
        key = f"{product_id}::{image_filename}"

        if error is not None:
            with lock:
                failed.append({"product_id": product_id, "image": image_filename, "reason": error})
                done.add(key)
                completed_count += 1
            logger.warning("[%d/%d] FAIL %s (%s): %s", completed_count, total, product_id, image_filename, error)
            return

        try:
            emb = get_clip_embedding(model, processor, image)
        except Exception as e:
            with lock:
                failed.append({"product_id": product_id, "image": image_filename, "reason": str(e)})
                done.add(key)
                completed_count += 1
            logger.warning("[%d/%d] FAIL embed failed %s (%s): %s", completed_count, total, product_id, image_filename, e)
            return
        finally:
            image.close()
            del image

        with lock:
            embeddings.append(emb)
            indexed_ids.append(product_id)
            done.add(key)
            completed_count += 1
            processed_since_checkpoint += 1
            logger.info("[%d/%d] OK %s (%s)", completed_count, total, product_id, image_filename)

            if processed_since_checkpoint >= CHECKPOINT_EVERY:
                save_checkpoint(embeddings, indexed_ids, done, failed)
                processed_since_checkpoint = 0
                logger.info("Checkpoint saved at %d/%d", completed_count, total)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Process in bounded batches -- submitting all ~15k tasks at once lets
        # downloads race far ahead of the (slower, CPU-bound) embedding step,
        # piling up finished-but-unprocessed images in memory with no ceiling.
        # Capping batch size keeps memory bounded regardless of that mismatch.
        for batch_start in range(0, len(remaining_tasks), BATCH_SIZE):
            batch = remaining_tasks[batch_start:batch_start + BATCH_SIZE]
            futures = [executor.submit(download_image, pid, fname) for (pid, fname) in batch]
            for future in as_completed(futures):
                product_id, image_filename, image, error = future.result()
                handle_result(product_id, image_filename, image, error)

    save_checkpoint(embeddings, indexed_ids, done, failed)

    if not embeddings:
        raise ValueError("No images could be indexed.")

    matrix = np.array(embeddings).astype("float32")
    dimension = matrix.shape[1]  # 512 for CLIP ViT-B/32
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

"""
app/config.py

Central settings loaded from environment variables / .env file.
All other modules import ``get_settings()`` — never read os.environ directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_DIR / ".env"

_PLACEHOLDER_KEYS = frozenset({"", "your-internal-key-here", "changeme"})


def _read_env_file() -> dict[str, str]:
    """Read backend/.env directly — bypasses stale OS-level environment variables."""
    if not ENV_FILE.is_file():
        return {}
    return {
        key: value.strip() if isinstance(value, str) else ""
        for key, value in dotenv_values(ENV_FILE).items()
        if value is not None
    }


def reload_settings() -> "Settings":
    """Clear cached settings and reload backend/.env on every app start."""
    get_settings.cache_clear()
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=True)
    return get_settings()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    demo_mode: bool = True
    maintenance_mode: bool = False
    environment: str = "development"
    debug_mode: bool = False
    cors_origins: str = "*"
    admin_api_key: str = ""
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 60
    jwt_refresh_token_days: int = 7
    admin_default_email: str = ""
    admin_default_password: str = ""
    admin_default_name: str = "Admin"
    # When false, human handoff is allowed 24/7 (use for local/testing).
    # When true, handoff is restricted by business_hours settings in DB/admin.
    handoff_business_hours_enforced: bool = False
    # Set true in real production deploys to require strong JWT/admin secrets.
    strict_security_enforced: bool = False
    alert_webhook_url: str = ""
    queue_alert_minutes: int = 5
    queue_alert_check_seconds: int = 60
    max_image_upload_bytes: int = 10_485_760  # 10 MB
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    handoff_rate_limit_requests: int | None = None
    handoff_rate_limit_window_seconds: int = 900

    # Optional future database URL (PostgreSQL). SQLite is used when unset.
    database_url: str = ""

    # OpenAI
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"

    # RAG tuning
    faq_data_path: str = "data/demo_faq.json"
    faiss_index_path: str = "data/faiss_index"
    product_faiss_index_path: str = "data/product_faiss_index"
    product_data_path: str = "data/demo_products.json"
    catalog_data_path: str = "data/demo_catalog.json"
    top_k_results: int = 4
    bm25_weight: float = 0.4
    semantic_weight: float = 0.6
    product_min_relevance_score: float = 0.8

    # Product Image RAG
    image_json_path: str = "data/demo_product_images.json"
    image_base_url: str = "data/demo_images/"
    image_index_path: str = "data/product_image_index.faiss"
    image_ids_path: str = "data/product_image_index_ids.pkl"
    image_top_k_results: int = 5
    image_clip_model: str = "openai/clip-vit-base-patch32"
    image_min_similarity: float = 0.75
    image_json_url: str = ""
    image_local_dir: str = "data/demo_images"

    # Chat store
    chat_store_path: str = "data/stylehub_ai.db"
    chat_uploads_path: str = "data/chat_uploads"

    # LangChain / LangSmith Tracing
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "StyleHubAI"

    # Commerce backend API (stubbed when demo_mode=true)
    enox_api_url: str = "http://localhost:8000"
    enox_api_key: str = "demo-key"


    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("production", "prod")

    def is_development(self) -> bool:
        return not self.is_production()

    def strict_security_enabled(self) -> bool:
        """True only when explicitly enabled for a production deploy."""
        return (
            self.strict_security_enforced
            and self.is_production()
            and not self.debug_mode
        )

    def handoff_rate_limit_max(self) -> int:
        if self.handoff_rate_limit_requests is not None:
            return self.handoff_rate_limit_requests
        if self.is_development() or self.debug_mode:
            return 100
        return 3


@lru_cache()
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=True)
    return Settings()


def resolve_enox_api_key() -> str:
    """
    Read ENOX_API_KEY from backend/.env on every call.

    Laravel INTERNAL_API_KEYS may contain multiple comma-separated values.
    FastAPI must send exactly one key per request.
    """
    raw = (_read_env_file().get("ENOX_API_KEY") or "").strip()
    if raw in _PLACEHOLDER_KEYS:
        return ""
    if not raw:
        return ""
    return raw.split(",")[0].strip()


def resolve_enox_api_url() -> str:
    """Read ENOX_API_URL from backend/.env on every call."""
    raw = (_read_env_file().get("ENOX_API_URL") or "").strip()
    if raw:
        return raw
    return get_settings().enox_api_url.strip() or "http://localhost:8000"

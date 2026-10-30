from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from typing import List
import os
import re
from pathlib import Path


_BACKEND_DIR = Path(__file__).resolve().parent


def _mask_db_url(url: str) -> str:
    """Replace password in a DB URL with *** for safe logging."""
    return re.sub(r"(:)[^:@]+(@)", r"\1***\2", url)


def _normalize_database_url(url: str) -> str:
    """Ensure PostgreSQL URLs use the postgresql+psycopg:// dialect scheme for SQLAlchemy 2.0 / Psycopg 3."""
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Keys
    gemini_api_key: str = ""

    # Paths
    upload_dir: str = "uploads"
    chroma_data_path: str = "./chroma_data"

    # File limits
    max_file_size_mb: int = 20
    allowed_extensions: List[str] = ["pdf", "txt", "docx"]
    allowed_mime_types: List[str] = [
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]

    # ChromaDB
    chroma_collection_name: str = "study_material"

    # AI / embeddings
    gemini_model: str = "gemini-2.5-flash"

    # Context budget (characters)
    max_context_chars: int = 12_000

    # Chunking
    chunk_size: int = 1_000
    chunk_overlap: int = 100

    # Quiz limits
    min_question_count: int = 1
    max_question_count: int = 20

    # CORS
    cors_origins: List[str] = ["*"]

    # --- Phase 3 flags ---
    # Run Alembic migrations automatically on startup (dev only; default off for prod)
    auto_migrate: bool = False

    # Legacy AsyncStorage migration endpoint toggle
    enable_legacy_history_migration: bool = False

    # Controlled Simplification: gate deferred legacy quiz, flashcard, and study routers
    enable_legacy_study_modules: bool = False

    # Maximum AI retries for quality validation
    max_quiz_generation_retries: int = 2

    # Phase 6 & Wave 2 Multi-Document RAG limits
    chat_max_documents: int = 50
    chat_max_message_characters: int = 4_000
    chat_max_context_characters: int = 12_000
    chat_max_recent_messages: int = 8
    chat_max_regenerations: int = 3
    chat_retrieval_top_k: int = 12
    # Normalized cosine relevance: (2 - cosine_distance) / 2.
    chat_relevance_threshold: float = 0.10
    chat_max_chunks_per_document: int = 4
    chat_retrieval_timeout_seconds: int = 15
    chat_generation_timeout_seconds: int = 60
    chat_summary_timeout_seconds: int = 15
    # One bounded provider call keeps API and frontend timeouts aligned.
    chat_max_ai_retries: int = 0

    # Wave 2 Multi-Document RAG Configuration
    rag_candidates_per_document: int = 4
    rag_max_candidates: int = 48
    rag_context_token_budget: int = 3_000
    rag_near_duplicate_threshold: float = 0.85
    rag_diversity_margin: float = 0.08
    rag_max_share_per_document: float = 0.50

    # Phase 7 authentication
    environment: str = "development"
    jwt_secret_key: str = "development-only-change-this-secret-before-production"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "studygen-api"
    jwt_audience: str = "studygen-app"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    refresh_inactivity_days: int = 7
    password_min_length: int = 10
    login_max_failed_attempts: int = 5
    login_lock_minutes: int = 15
    legacy_data_owner_email: str = "legacy@local.invalid"

    # --- Phase 8 Document Processing & OCR ---
    ocr_enabled: bool = True
    ocr_min_native_characters: int = 50
    ocr_invalid_character_ratio: float = 0.15
    ocr_max_pages_per_document: int = 100
    ocr_max_page_pixels: int = 25_000_000
    ocr_render_dpi: int = 120
    ocr_page_timeout_seconds: int = 10
    ocr_document_timeout_seconds: int = 60
    chunk_min_characters: int = 50

    # --- Phase 9 Redis & Job Queue ---
    redis_url: str = "redis://localhost:6379"
    redis_queue_prefix: str = "studygen"
    redis_socket_timeout_seconds: int = 10
    redis_connect_timeout_seconds: int = 5
    redis_max_connections: int = 10
    job_result_ttl_seconds: int = 86400   # 24 hours
    job_lock_ttl_seconds: int = 3600      # 1 hour
    job_stale_after_seconds: int = 300    # 5 minutes without heartbeat

    # Phase 9 job-type timeouts
    document_job_timeout_seconds: int = 300   # 5 minutes
    ocr_job_timeout_seconds: int = 600        # 10 minutes
    quiz_job_timeout_seconds: int = 120       # 2 minutes
    flashcard_job_timeout_seconds: int = 120  # 2 minutes
    chat_job_timeout_seconds: int = 90        # 1.5 minutes
    worker_heartbeat_seconds: int = 15

    # Phase 9 job retry limits
    document_job_max_attempts: int = 3
    ai_job_max_attempts: int = 3

    # Phase 9 durable local file storage
    document_storage_root: str = "storage"

    # Phase 9 worker concurrency
    document_worker_concurrency: int = 2
    ai_worker_concurrency: int = 2
    maintenance_worker_concurrency: int = 1

    # --- Phase 10 Database Configuration ---
    # Primary database URL.
    # Development default: SQLite (via env var DATABASE_URL or computed below).
    # Production: must be postgresql+psycopg://user:password@host:5432/dbname
    # Set DATABASE_URL in .env or environment; do NOT hardcode credentials here.
    database_url: str = ""

    # PostgreSQL connection pool settings
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30           # seconds
    database_pool_recycle: int = 1800         # seconds (30 min)
    database_pool_pre_ping: bool = True
    database_echo: bool = False

    # PostgreSQL statement and lock timeouts (0 = disabled)
    database_statement_timeout_ms: int = 0
    database_lock_timeout_ms: int = 0

    # --- Phase 11A Community ---
    enable_community_features: bool = False
    post_caption_max_characters: int = 5_000
    post_max_attachments: int = 5
    post_max_images: int = 5
    post_max_documents: int = 3
    post_max_videos: int = 1
    post_image_max_bytes: int = 10 * 1024 * 1024
    post_document_max_bytes: int = 20 * 1024 * 1024
    post_video_max_bytes: int = 100 * 1024 * 1024
    post_image_max_pixels: int = 25_000_000
    post_video_max_duration_seconds: int = 300
    community_feed_page_size: int = 20
    community_feed_max_page_size: int = 50
    community_username_min_length: int = 3
    community_username_max_length: int = 30
    community_bio_max_characters: int = 500
    community_post_create_rate_per_minute: int = 10
    community_media_upload_rate_per_minute: int = 20
    community_user_search_rate_per_minute: int = 30
    comment_max_characters: int = 2_000
    community_comment_page_size: int = 20
    community_reply_preview_size: int = 3
    community_reaction_rate_per_minute: int = 60
    community_comment_rate_per_minute: int = 20
    community_save_rate_per_minute: int = 60
    community_share_rate_per_minute: int = 10
    community_report_rate_per_minute: int = 10

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Accept comma-separated string or list for CORS origins."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("upload_dir", "chroma_data_path", "document_storage_root", mode="before")
    @classmethod
    def resolve_path(cls, v: str) -> str:
        path = Path(v)
        return str(path if path.is_absolute() else (_BACKEND_DIR / path).resolve())

    @model_validator(mode="after")
    def set_database_url_default(self) -> "Settings":
        """
        If database_url is not explicitly set, compute the SQLite default.
        Normalize postgres:// and postgresql:// to postgresql+psycopg://.
        Production mode (environment != 'development' or 'test') rejects SQLite.
        """
        raw_url = self.database_url or os.getenv("DATABASE_URL", "")
        if raw_url:
            norm_url = _normalize_database_url(raw_url)
            object.__setattr__(self, "database_url", norm_url)
        else:
            default_sqlite = f"sqlite:///{(_BACKEND_DIR / 'studygen.db').as_posix()}"
            object.__setattr__(self, "database_url", default_sqlite)

        url = self.database_url
        env = self.environment.lower()
        testing = os.getenv("TESTING", "").lower() in ("1", "true", "yes")

        if env not in ("development", "test") and not testing:
            if url.startswith("sqlite"):
                raise ValueError(
                    "DATABASE_URL must be a PostgreSQL URL in production mode. "
                    "SQLite is not supported in production. "
                    f"Received URL scheme: sqlite (environment={env})"
                )

        return self

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def database_url_safe(self) -> str:
        """Password-masked URL safe for logging."""
        return _mask_db_url(self.database_url)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgresql(self) -> bool:
        return self.database_url.startswith("postgresql")


settings = Settings()

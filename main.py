"""
main.py — StudyGen API entry point (Phase 2).
"""
import logging
import os
import sys

# Disable ChromaDB telemetry via env var (belt-and-suspenders approach).
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_SERVER_NOFILE"] = "65536"

sys.path.insert(0, os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# Silence ChromaDB / PostHog telemetry
# ChromaDB 0.5.3 calls posthog.capture() with the old positional-argument
# signature, but PostHog >=3 changed capture() to keyword-only arguments,
# causing "capture() takes 1 positional argument but 3 were given" at startup.
# Monkey-patching before chromadb is imported prevents the error entirely.
# ---------------------------------------------------------------------------
try:
    import posthog  # noqa: E402 – must happen before chromadb
    posthog.disabled = True
    # Also silence the Posthog class-level capture in case it was already bound
    posthog.Posthog.capture = lambda *args, **kwargs: None  # type: ignore[method-assign]
except Exception:  # pragma: no cover
    pass  # posthog not installed; nothing to patch

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import google.generativeai as genai
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

from config import settings
from sqlalchemy.engine import make_url
from app.routers import documents as documents_router
from app.routers import folders as folders_router
from app.routers import quizzes as quizzes_router
from app.routers import attempts as attempts_router
from app.routers import study as study_router
from app.routers import review_sessions as review_sessions_router
from app.routers import study_sets as study_sets_router
from app.routers import flashcards as flashcards_router
from app.routers import flashcard_review_sessions as flashcard_review_sessions_router
from app.routers import chat_sessions as chat_sessions_router
from app.routers import ai_scope as ai_scope_router
from app.routers import study_guides as study_guides_router
from app.routers import auth as auth_router
from app.routers import jobs as jobs_router
from app.routers import community as community_router
from app.routers import posts as posts_router
from app.routers import social_interactions as social_interactions_router
from app.routers import moderation as moderation_router
from app.routers import admin as admin_router

# Import all models so SQLAlchemy/Alembic can see them for create_all()
from app.database.base import Base
from app.database.session import engine
import app.database.models  # noqa: F401 — __init__.py registers all models

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# A test database is destructive by design. Refuse to run the normal app in
# its public schema; a dedicated non-public development schema is acceptable.
if os.getenv("TESTING", "").lower() not in {"true", "1"}:
    configured_url = make_url(settings.database_url)
    configured_options = str(configured_url.query.get("options", ""))
    if (configured_url.database or "").endswith("_test") and "search_path=" not in configured_options:
        raise RuntimeError(
            "Refusing to start the application in a PostgreSQL test schema. "
            "Set DATABASE_URL to a development database or dedicated development search_path."
        )

if (
    settings.environment.lower() not in {"development", "test"}
    and (
        not settings.jwt_secret_key
        or settings.jwt_secret_key.startswith("development-only")
        or len(settings.jwt_secret_key) < 32
    )
):
    raise RuntimeError("A strong JWT_SECRET_KEY is required outside development.")


def _looks_like_gemini_api_key(api_key: str) -> bool:
    """Validate the basic Google API key shape before configuring Gemini."""
    key = (api_key or "").strip()
    return key.startswith("AIza") and len(key) >= 35

# ---------------------------------------------------------------------------
# Run Alembic migrations on startup
# ---------------------------------------------------------------------------
def _run_migrations() -> None:
    if os.getenv("TESTING", "").lower() in ("true", "1"):
        logger.info("Test environment detected — skipping Alembic migrations.")
        return
    db_url = os.getenv("DATABASE_URL", "")
    # Skip Alembic when running against an in-memory SQLite DB (test environment).
    # Tests create all tables via Base.metadata.create_all() in conftest.py.
    if ":memory:" in db_url or "file::memory:" in db_url:
        logger.info("In-memory database detected — skipping Alembic migrations.")
        return
    if not settings.auto_migrate:
        logger.info("AUTO_MIGRATE is disabled; database migrations were not run.")
        return
    try:
        alembic_cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "alembic"))
        migration_url = db_url or f"sqlite:///{os.path.join(os.path.dirname(__file__), 'studygen.db')}"
        alembic_cfg.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations applied.")
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        raise

_run_migrations()

# Tests use a disposable in-memory database. Persistent environments are
# migration-managed and are never silently changed by create_all().
if os.getenv("TESTING", "").lower() in ("true", "1"):
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Test schema created from SQLAlchemy metadata.")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="StudyGen API",
    version="2.0.0",
    description="RAG-powered quiz generation with persistence.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------------
chroma_client = chromadb.PersistentClient(
    path=settings.chroma_data_path,
    settings=ChromaSettings(anonymized_telemetry=False),
)
default_ef = embedding_functions.DefaultEmbeddingFunction()

collection = chroma_client.get_or_create_collection(
    name=settings.chroma_collection_name,
    embedding_function=default_ef,
    metadata={"hnsw:space": "cosine"},
)

gemini_available = False
gemini_unavailable_reason = "GEMINI_API_KEY is not set."
if not settings.gemini_api_key:
    logger.warning(gemini_unavailable_reason)
elif not _looks_like_gemini_api_key(settings.gemini_api_key) and os.getenv("TESTING", "").lower() not in ("true", "1"):
    gemini_unavailable_reason = (
        "GEMINI_API_KEY does not look like a valid Google Gemini API key. "
        "Create a key in Google AI Studio and update backend/.env."
    )
    logger.error(gemini_unavailable_reason)
else:
    genai.configure(api_key=settings.gemini_api_key)
    gemini_available = True
    gemini_unavailable_reason = ""
    logger.info("Gemini API key loaded.")

gemini_model = genai.GenerativeModel(settings.gemini_model)

app.state.collection = collection
app.state.gemini_model = gemini_model
app.state.gemini_available = gemini_available
app.state.gemini_unavailable_reason = gemini_unavailable_reason
app.state.settings = settings

os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs(settings.document_storage_root, exist_ok=True)
os.makedirs(settings.chroma_data_path, exist_ok=True)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth_router.router)
app.include_router(documents_router.router)
app.include_router(folders_router.router)
if settings.enable_legacy_study_modules:
    app.include_router(quizzes_router.router)
    app.include_router(attempts_router.router)
    app.include_router(study_router.router)
    app.include_router(review_sessions_router.router)
    app.include_router(study_sets_router.router)
    app.include_router(flashcards_router.router)
    app.include_router(flashcard_review_sessions_router.router)
app.include_router(chat_sessions_router.router)
app.include_router(ai_scope_router.router)
app.include_router(study_guides_router.router)
app.include_router(jobs_router.router)
if settings.enable_community_features:
    app.include_router(community_router.router)
    app.include_router(posts_router.router)
    app.include_router(social_interactions_router.router)
    app.include_router(moderation_router.router)
app.include_router(admin_router.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/", tags=["health"])
def health_check():
    return {"status": "online", "version": "2.0.0", "message": "StudyGen Backend is running"}

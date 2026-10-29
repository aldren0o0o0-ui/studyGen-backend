"""
alembic/env.py — Alembic migration environment (Phase 10: PostgreSQL & SQLite ready).
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the backend root importable so models can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.base import Base  # noqa: E402
# Import all model modules so Alembic detects them for autogenerate
import app.database.models.document  # noqa: F401
import app.database.models.quiz      # noqa: F401
import app.database.models.attempt   # noqa: F401
import app.database.models.auth      # noqa: F401
import app.database.models.study     # noqa: F401
import app.database.models.flashcard # noqa: F401
import app.database.models.chat      # noqa: F401
import app.database.models.job       # noqa: F401
from config import settings          # noqa: E402

# Alembic Config object (provides access to alembic.ini values)
config = context.config

# Use settings.database_url (or env var override)
db_url = os.getenv("DATABASE_URL") or settings.database_url
if db_url:
    # ConfigParser treats percent-encoded URL characters as interpolation.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (requires a live DB connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE support
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

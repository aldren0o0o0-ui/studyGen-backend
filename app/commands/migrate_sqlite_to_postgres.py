"""Strict, typed SQLite-to-PostgreSQL data migration for Phase 10.1."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, MetaData, Numeric, create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.exc import SQLAlchemyError

from app.database.job_status import PROCESSING_JOB_STATUSES
from app.database.types import normalize_json_value
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MIGRATION_ORDER = [
    "users", "refresh_tokens", "documents", "document_processing_runs",
    "document_pages", "quizzes", "questions", "quiz_attempts", "attempt_answers",
    "topic_performance", "review_items", "review_sessions", "review_session_items",
    "study_goals", "study_activities", "study_sets", "flashcards",
    "flashcard_review_states", "flashcard_review_sessions",
    "flashcard_review_session_items", "chat_sessions", "chat_session_documents",
    "chat_messages", "chat_citations", "chat_generation_records", "processing_jobs",
    "worker_heartbeats",
]
JSON_COLUMNS = {
    "documents": {"extraction_warnings_json"},
    "document_processing_runs": {"warnings_json"},
    "quizzes": {"question_types_json", "configuration_json"},
    "questions": {"options_json", "source_chunk_indexes_json", "source_references_json", "accepted_answers_json"},
    "flashcards": {"source_references_json"},
    "chat_messages": {"follow_up_suggestions_json"},
    "processing_jobs": {"payload_json"},
}
BOOLEAN_COLUMNS = {"attempt_answers": {"is_correct"}, "chat_messages": {"insufficient_evidence"}}
STATUS_COLUMNS = {"processing_jobs": {"status": set(PROCESSING_JOB_STATUSES)}}
EXPECTED_REVISION = "0013_phase11b_interactions"


@dataclass(frozen=True)
class ValidationIssue:
    table: str
    row_id: str
    column: str
    code: str
    reason: str

    def __str__(self) -> str:
        return f"Table: {self.table} Row: {self.row_id} Column: {self.column} Error: {self.code} ({self.reason})"


class MigrationValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__(f"Migration validation failed with {len(issues)} issue(s)")


def get_sqlite_conn(sqlite_path: str) -> sqlite3.Connection:
    path = Path(sqlite_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Source SQLite database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def require_postgres(pg_url: str):
    engine = create_engine(pg_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("TARGET_NOT_POSTGRESQL: target dialect must be PostgreSQL")
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def _parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    raise ValueError("expected SQLite boolean 0 or 1")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("expected ISO-8601 datetime")
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _validate_uuid_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(UUID(str(value)))


def transform_row(table_name: str, table, row: sqlite3.Row) -> tuple[dict[str, Any], list[ValidationIssue]]:
    source = dict(row)
    pk_names = [column.name for column in table.primary_key.columns]
    row_id = ",".join(str(source.get(name)) for name in pk_names) or "<unknown>"
    output: dict[str, Any] = {}
    issues: list[ValidationIssue] = []

    for column in table.columns:
        if column.name not in source:
            continue
        value = source[column.name]
        try:
            if column.name in JSON_COLUMNS.get(table_name, set()) or isinstance(column.type, JSONB):
                value = normalize_json_value(value, location=f"{table_name}/{row_id}/{column.name}")
            elif column.name in BOOLEAN_COLUMNS.get(table_name, set()) or isinstance(column.type, Boolean):
                value = _parse_bool(value)
            elif isinstance(column.type, DateTime):
                value = _parse_datetime(value)
            elif isinstance(column.type, PG_UUID):
                value = UUID(str(value)) if value is not None else None
            elif column.name == "id" or column.name.endswith("_id"):
                # The current schema stores UUIDs as VARCHAR(36); validate without changing binding type.
                if value is not None and getattr(column.type, "length", None) == 36:
                    value = _validate_uuid_string(value)
            elif isinstance(column.type, Integer) and value is not None:
                value = int(value)
            elif isinstance(column.type, Numeric) and value is not None:
                value = float(value)

            allowed = STATUS_COLUMNS.get(table_name, {}).get(column.name)
            if allowed is not None and value not in allowed:
                raise ValueError(f"unsupported status {value!r}")
            output[column.name] = value
        except Exception as exc:
            code = "INVALID_VALUE"
            if column.name in JSON_COLUMNS.get(table_name, set()):
                code = "MALFORMED_JSON"
            elif column.name in BOOLEAN_COLUMNS.get(table_name, set()):
                code = "INVALID_BOOLEAN"
            elif column.name == "id" or column.name.endswith("_id"):
                code = "INVALID_UUID"
            elif isinstance(column.type, DateTime):
                code = "INVALID_DATETIME"
            elif column.name == "status":
                code = "INVALID_STATUS"
            issues.append(ValidationIssue(table_name, row_id, column.name, code, str(exc)))
    return output, issues


def _source_rows(conn: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(f'SELECT * FROM "{table_name}"').fetchall()
    except sqlite3.OperationalError:
        return []


def validate_source(sqlite_path: str, pg_engine) -> tuple[dict[str, list[dict[str, Any]]], list[ValidationIssue]]:
    source = get_sqlite_conn(sqlite_path)
    metadata = MetaData()
    metadata.reflect(bind=pg_engine)
    transformed: dict[str, list[dict[str, Any]]] = {}
    issues: list[ValidationIssue] = []
    try:
        for name in MIGRATION_ORDER:
            rows = _source_rows(source, name)
            if name not in metadata.tables:
                if rows:
                    issues.append(ValidationIssue(name, "*", "*", "MISSING_TARGET_TABLE", "target table is absent"))
                continue
            transformed[name] = []
            for row in rows:
                value, row_issues = transform_row(name, metadata.tables[name], row)
                transformed[name].append(value)
                issues.extend(row_issues)
    finally:
        source.close()
    return transformed, issues


def run_dry_run(sqlite_path: str, pg_url: str) -> None:
    engine = require_postgres(pg_url)
    try:
        rows, issues = validate_source(sqlite_path, engine)
        for issue in issues:
            logger.error("%s", issue)
        if issues:
            raise MigrationValidationError(issues)
        logger.info("Dry run passed: %d rows validated", sum(map(len, rows.values())))
    finally:
        engine.dispose()


def run_apply(sqlite_path: str, pg_url: str) -> None:
    engine = require_postgres(pg_url)
    try:
        rows_by_table, issues = validate_source(sqlite_path, engine)
        if issues:
            for issue in issues:
                logger.error("%s", issue)
            raise MigrationValidationError(issues)
        metadata = MetaData()
        metadata.reflect(bind=engine)
        for name, rows in rows_by_table.items():
            table = metadata.tables[name]
            inserted = matching = conflicting = 0
            with engine.begin() as conn:  # one atomic transaction per table
                for row in rows:
                    identity = {c.name: row[c.name] for c in table.primary_key.columns}
                    existing = conn.execute(select(table).filter_by(**identity)).mappings().one_or_none()
                    if existing is not None:
                        comparable = {key: existing[key] for key in row}
                        if comparable == row:
                            matching += 1
                            continue
                        conflicting += 1
                        raise RuntimeError(f"CONFLICTING_ROW: table={name} primary_key={identity}")
                    conn.execute(table.insert().values(**row))
                    inserted += 1
            logger.info("Table %s: inserted=%d matching=%d conflicting=%d", name, inserted, matching, conflicting)
    finally:
        engine.dispose()


def run_verify(sqlite_path: str, pg_url: str) -> None:
    engine = require_postgres(pg_url)
    source = get_sqlite_conn(sqlite_path)
    try:
        inspector = inspect(engine)
        with engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if revision != EXPECTED_REVISION:
                raise RuntimeError(f"ALEMBIC_REVISION_MISMATCH: expected {EXPECTED_REVISION}, got {revision}")
            for name in MIGRATION_ORDER:
                source_count = len(_source_rows(source, name))
                if name not in inspector.get_table_names():
                    raise RuntimeError(f"MISSING_TARGET_TABLE: {name}")
                target_count = conn.execute(select(text("count(*)")).select_from(text(f'"{name}"'))).scalar_one()
                if source_count != target_count:
                    raise RuntimeError(f"ROW_COUNT_MISMATCH: {name} source={source_count} target={target_count}")
        _, issues = validate_source(sqlite_path, engine)
        if issues:
            raise MigrationValidationError(issues)
        logger.info("Verification passed at revision %s", revision)
    finally:
        source.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL safely")
    parser.add_argument("--source", default="studygen.db")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        if args.dry_run:
            run_dry_run(args.source, settings.database_url)
        elif args.apply:
            run_apply(args.source, settings.database_url)
        else:
            run_verify(args.source, settings.database_url)
    except (OSError, ValueError, RuntimeError, SQLAlchemyError) as exc:
        logger.error("MIGRATION_FAILED: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

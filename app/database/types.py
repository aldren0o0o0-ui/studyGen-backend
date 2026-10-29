"""Shared portable database types used by SQLite and PostgreSQL."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


class MalformedJSONValue(ValueError):
    """A legacy persisted JSON string could not be decoded."""


def normalize_json_value(value: Any, *, location: str = "JSON value") -> Any:
    """Return a JSON-compatible Python value, decoding legacy JSON strings."""
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise MalformedJSONValue(
                f"Malformed persisted JSON at {location}: {exc.msg}"
            ) from exc
    raise TypeError(f"Unsupported JSON value at {location}: {type(value).__name__}")


class PortableJSON(TypeDecorator):
    """Native JSONB on PostgreSQL and JSON on SQLite, with legacy text support."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(JSONB() if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value, dialect):
        return normalize_json_value(value)

    def process_result_value(self, value, dialect):
        return normalize_json_value(value)


JSON_VALUE = PortableJSON()


def utc_now() -> datetime:
    """Return the current timezone-aware UTC instant."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Normalize aware values to UTC and interpret legacy naive values as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class UTCDateTime(TypeDecorator):
    """TIMESTAMPTZ on PostgreSQL with a stable aware-UTC Python contract."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(self, value, dialect):
        value = ensure_utc(value)
        if value is not None and dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return ensure_utc(value)


UTC_DATETIME = UTCDateTime()

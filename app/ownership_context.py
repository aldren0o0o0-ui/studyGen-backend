"""Request-local ownership context used by SQLAlchemy and vector services."""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: str) -> None:
    _current_user_id.set(user_id)


def current_user_id() -> str | None:
    return _current_user_id.get()


@contextmanager
def ownership_scope(user_id: str):
    """Temporarily scope ORM reads/writes for maintenance work."""
    token = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)

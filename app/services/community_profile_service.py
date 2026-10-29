"""Privacy-safe community profile operations."""
from __future__ import annotations

import re
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.types import utc_now

USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.]{1,28}[a-z0-9])?$")
RESERVED_USERNAMES = {"admin", "administrator", "moderator", "system", "support", "api"}


class CommunityProfileError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def normalize_username(value: str, minimum: int = 3, maximum: int = 30) -> str:
    normalized = value.strip().lower()
    if not minimum <= len(normalized) <= maximum or not USERNAME_RE.fullmatch(normalized):
        raise CommunityProfileError("INVALID_USERNAME", "Username must be 3-30 lowercase letters, numbers, periods, or underscores.")
    if normalized in RESERVED_USERNAMES:
        raise CommunityProfileError("INVALID_USERNAME", "This username is reserved.")
    return normalized


def update_profile(db: Session, user: User, *, username=None, display_name=None, bio=None, settings=None) -> User:
    if username is not None:
        normalized = normalize_username(username, settings.community_username_min_length, settings.community_username_max_length)
        existing = db.scalar(select(User.id).where(User.normalized_username == normalized, User.id != user.id))
        if existing:
            raise CommunityProfileError("USERNAME_ALREADY_TAKEN", "Username is already taken.")
        user.username = username.strip()
        user.normalized_username = normalized
    if display_name is not None:
        value = display_name.strip()
        if not value:
            raise CommunityProfileError("INVALID_DISPLAY_NAME", "Display name cannot be empty.")
        user.display_name = value
    if bio is not None:
        value = bio.strip()
        if len(value) > settings.community_bio_max_characters:
            raise CommunityProfileError("INVALID_BIO", "Bio is too long.")
        user.bio = value or None
    user.updated_at = utc_now()
    db.commit(); db.refresh(user)
    return user


def public_user(db: Session, user_id: str) -> User | None:
    return db.scalar(select(User).where(User.id == user_id, User.status == "ACTIVE", User.deleted_at.is_(None), User.normalized_username.is_not(None)))


def search_users(db: Session, current_user_id: str, query: str, limit: int = 20) -> list[User]:
    term = query.strip().lower()
    if not term:
        return []
    return list(db.scalars(select(User).where(
        User.id != current_user_id, User.status == "ACTIVE", User.deleted_at.is_(None),
        User.normalized_username.is_not(None),
        or_(User.normalized_username.like(f"{term}%"), User.display_name.ilike(f"%{term}%")),
    ).order_by(User.normalized_username).limit(min(limit, 20))).all())

"""Authentication orchestration and security event logging."""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.services.password_service import (
    hash_password, needs_rehash, verify_dummy, verify_password,
)
from app.services.token_service import (
    create_access_token, issue_refresh_token, revoke_all_user_tokens, utcnow,
)

logger = logging.getLogger("security")


class AuthError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _masked(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _tokens(db, user, settings, device_name=None, device_identifier=None):
    refresh, _ = issue_refresh_token(
        db, user, settings,
        device_name=device_name, device_identifier=device_identifier,
    )
    db.commit()
    return create_access_token(user, settings), refresh


def register(db: Session, payload, settings):
    normalized = normalize_email(payload.email)
    if db.execute(select(User.id).where(User.normalized_email == normalized)).first():
        raise AuthError(409, "An account with this email already exists.")
    now = utcnow()
    user_id = str(uuid4())
    # Community is a core product surface, so a registered account must be
    # post-ready. Keep the human-readable email stem and add an ID fragment
    # to make the generated username concurrency-safe and non-enumerating.
    stem = re.sub(r"[^a-z0-9]+", "_", normalized.partition("@")[0]).strip("_")
    if len(stem) < 3:
        stem = f"{stem or 'user'}_user"
    generated_username = f"{stem[:22]}_{user_id.replace('-', '')[:7]}"
    user = User(
        id=user_id,
        email=payload.email.strip(),
        normalized_email=normalized,
        password_hash=hash_password(payload.password, minimum=settings.password_min_length),
        display_name=payload.display_name,
        username=generated_username,
        normalized_username=generated_username,
        status="ACTIVE",
        password_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
        access, refresh = _tokens(
            db, user, settings, payload.device_name, payload.device_identifier
        )
    except IntegrityError as exc:
        db.rollback()
        raise AuthError(409, "An account with this email already exists.") from exc
    logger.info("user_registered user_id=%s email=%s", user.id, _masked(user.email))
    return user, access, refresh


def login(db: Session, payload, settings):
    normalized = normalize_email(payload.email)
    user = db.execute(
        select(User).where(User.normalized_email == normalized).with_for_update(of=User)
    ).scalar_one_or_none()
    if user is None:
        verify_dummy(payload.password)
        logger.warning("login_failed email=%s", _masked(payload.email))
        raise AuthError(401, "Invalid email or password.")
    now = utcnow()
    if user.locked_until and user.locked_until > now:
        logger.warning("login_locked user_id=%s", user.id)
        raise AuthError(401, "Invalid email or password.")
    if user.status == "LOCKED" and user.locked_until and user.locked_until <= now:
        user.status = "ACTIVE"
        user.failed_login_attempts = 0
        user.locked_until = None
    valid = verify_password(payload.password, user.password_hash)
    if not valid or user.status != "ACTIVE" or user.deleted_at is not None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.login_max_failed_attempts:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.status = "LOCKED" if user.status == "ACTIVE" else user.status
            logger.warning("account_temporarily_locked user_id=%s", user.id)
        user.updated_at = now
        db.commit()
        logger.warning("login_failed email=%s", _masked(payload.email))
        raise AuthError(401, "Invalid email or password.")
    from app.database.models.moderation import UserRestriction
    account_restricted = db.scalar(select(UserRestriction.id).where(
        UserRestriction.user_id == user.id, UserRestriction.restriction_type == "ACCOUNT",
        UserRestriction.status == "ACTIVE", UserRestriction.starts_at <= now,
        or_(UserRestriction.ends_at.is_(None), UserRestriction.ends_at > now),
    ))
    if account_restricted:
        logger.warning("login_account_restricted user_id=%s", user.id)
        raise AuthError(401, "Invalid email or password.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(
            payload.password, minimum=settings.password_min_length
        )
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    user.updated_at = now
    access, refresh = _tokens(
        db, user, settings, payload.device_name, payload.device_identifier
    )
    logger.info("login_succeeded user_id=%s", user.id)
    return user, access, refresh


def update_profile(db: Session, user: User, display_name: str) -> User:
    user.display_name = display_name
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, current: str, new: str, settings) -> None:
    if not verify_password(current, user.password_hash):
        raise AuthError(401, "Current password is incorrect.")
    now = utcnow()
    user.password_hash = hash_password(new, minimum=settings.password_min_length)
    user.session_version += 1
    user.password_changed_at = now
    user.updated_at = now
    db.flush()
    revoke_all_user_tokens(db, user.id, "password_changed")
    logger.info("password_changed user_id=%s", user.id)


def deactivate(db: Session, user: User, current_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise AuthError(401, "Current password is incorrect.")
    now = utcnow()
    user.status = "DELETED"
    user.deleted_at = now
    user.updated_at = now
    db.flush()
    revoke_all_user_tokens(db, user.id, "account_deactivated")
    logger.info("account_deactivated user_id=%s", user.id)

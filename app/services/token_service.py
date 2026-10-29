"""JWT access tokens and hashed, rotating opaque refresh tokens."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models.auth import RefreshToken, User
from app.database.types import utc_now


class TokenError(RuntimeError):
    pass


class TokenReuseError(TokenError):
    pass


def utcnow() -> datetime:
    return utc_now()


def password_marker(user: User) -> int:
    return int(user.password_changed_at.replace(tzinfo=timezone.utc).timestamp())


def create_access_token(user: User, settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "jti": str(uuid4()),
        "pwd": password_marker(user),
        "sv": user.session_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "type", "iat", "exp", "jti", "pwd", "sv"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid access token.") from exc
    if payload.get("type") != "access":
        raise TokenError("Invalid access token type.")
    if type(payload.get("sv")) is not int or payload["sv"] < 1:
        raise TokenError("Invalid access token.")
    return payload


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_refresh_token(
    db: Session,
    user: User,
    settings,
    *,
    family_id: str | None = None,
    parent_token_id: str | None = None,
    device_name: str | None = None,
    device_identifier: str | None = None,
) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(64)
    now = utcnow()
    row = RefreshToken(
        id=str(uuid4()),
        user_id=user.id,
        token_hash=_hash_refresh(raw),
        family_id=family_id or str(uuid4()),
        session_version=user.session_version,
        parent_token_id=parent_token_id,
        device_name=device_name,
        device_identifier=device_identifier,
        created_at=now,
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    db.add(row)
    db.flush()
    return raw, row


def revoke_family(db: Session, family_id: str, reason: str) -> None:
    now = utcnow()
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
    )


def rotate_refresh_token(
    db: Session, raw_token: str, settings
) -> tuple[User, str, RefreshToken]:
    candidate = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_refresh(raw_token))
    ).scalar_one_or_none()
    if candidate is None:
        raise TokenError("Invalid refresh token.")
    # Global security lock order: User first, then refresh-token row.
    user = db.scalar(select(User).where(User.id == candidate.user_id).with_for_update(of=User))
    if not user or user.status != "ACTIVE" or user.deleted_at is not None:
        revoke_family(db, candidate.family_id, "user_inactive")
        db.commit()
        raise TokenError("User is inactive.")
    row = db.scalar(select(RefreshToken).where(RefreshToken.id == candidate.id).with_for_update(of=RefreshToken))
    if row.revoked_at is not None:
        if row.replaced_by_token_id is not None:
            revoke_family(db, row.family_id, "reuse_detected"); db.commit()
            raise TokenReuseError("Refresh token reuse detected.")
        raise TokenError("Refresh token is revoked.")
    if row.session_version != user.session_version:
        revoke_family(db, row.family_id, "session_invalidated"); db.commit()
        raise TokenError("Refresh token is revoked.")
    now = utcnow()
    if row.expires_at <= now:
        row.revoked_at = now; row.revoke_reason = "expired"; db.commit()
        raise TokenError("Refresh token is expired.")
    inactivity = timedelta(days=settings.refresh_inactivity_days)
    if (row.last_used_at or row.created_at) + inactivity <= now:
        row.revoked_at = now; row.revoke_reason = "inactive"; db.commit()
        raise TokenError("Refresh token is inactive.")
    from app.database.models.moderation import UserRestriction
    account_restricted = db.scalar(select(UserRestriction.id).where(
        UserRestriction.user_id == user.id, UserRestriction.restriction_type == "ACCOUNT",
        UserRestriction.status == "ACTIVE", UserRestriction.starts_at <= now,
        (UserRestriction.ends_at.is_(None) | (UserRestriction.ends_at > now)),
    ))
    if account_restricted:
        revoke_family(db, row.family_id, "account_restricted")
        db.commit()
        raise TokenError("User is inactive.")

    raw_new, new_row = issue_refresh_token(
        db,
        user,
        settings,
        family_id=row.family_id,
        parent_token_id=row.id,
        device_name=row.device_name,
        device_identifier=row.device_identifier,
    )
    claimed = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
        .values(
            last_used_at=now,
            revoked_at=now,
            replaced_by_token_id=new_row.id,
            revoke_reason="rotated",
        )
    )
    if claimed.rowcount != 1:
        # Another request rotated this token between our read and write.
        db.rollback()
        revoke_family(db, row.family_id, "reuse_detected")
        db.commit()
        raise TokenReuseError("Refresh token reuse detected.")
    db.commit()
    return user, raw_new, new_row


def revoke_refresh_token(db: Session, raw_token: str, user_id: str) -> bool:
    row = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == _hash_refresh(raw_token),
            RefreshToken.user_id == user_id,
        )
    ).scalar_one_or_none()
    if not row:
        return False
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoke_reason = "logout"
        db.commit()
    return True


def revoke_all_user_tokens(db: Session, user_id: str, reason: str) -> None:
    now = utcnow()
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason)
    )
    db.commit()

"""Central authentication dependencies."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.session import SessionLocal
from app.ownership_context import set_current_user_id
from app.services.token_service import TokenError, decode_access_token, password_marker
from config import settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    display_name: str
    status: str
    role: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_model(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Not authenticated.")
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except TokenError:
        raise HTTPException(401, "Invalid or expired access token.")
    user = db.get(User, claims["sub"])
    if (
        not user or user.status != "ACTIVE" or user.deleted_at is not None
        or int(claims.get("pwd", -1)) != password_marker(user)
        or type(claims.get("sv")) is not int or claims["sv"] != user.session_version
    ):
        raise HTTPException(401, "Invalid or expired access token.")
    from app.database.models.moderation import UserRestriction
    from app.database.types import utc_now
    now = utc_now()
    if db.scalar(select(UserRestriction.id).where(
        UserRestriction.user_id == user.id, UserRestriction.restriction_type == "ACCOUNT",
        UserRestriction.status == "ACTIVE", UserRestriction.starts_at <= now,
        or_(UserRestriction.ends_at.is_(None), UserRestriction.ends_at > now),
    )):
        raise HTTPException(401, "Invalid or expired access token.")
    return user


async def get_current_user(user: User = Depends(get_current_user_model)) -> CurrentUser:
    # Async dependencies run in the request task; the ContextVar is then copied
    # into any worker thread used by legacy synchronous service functions.
    set_current_user_id(user.id)
    return CurrentUser(
        id=user.id, email=user.email, display_name=user.display_name, status=user.status, role=user.role
    )

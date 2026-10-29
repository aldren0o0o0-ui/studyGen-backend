"""Phase 7 authentication and account endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.dependencies import get_current_user_model, get_db
from app.schemas.auth import (
    AccountDeleteRequest, AuthResponse, ChangePasswordRequest, LoginRequest,
    MessageResponse, ProfileUpdateRequest, RefreshRequest, RegisterRequest,
    UserResponse,
)
from app.services import auth_service
from app.services.token_service import (
    TokenError, TokenReuseError, create_access_token, revoke_all_user_tokens,
    revoke_refresh_token, rotate_refresh_token,
)
from config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("security")


def _response(user: User, access: str, refresh: str) -> AuthResponse:
    return AuthResponse(
        access_token=access,
        refresh_token=refresh,
        access_expires_in=settings.access_token_minutes * 60,
        user=UserResponse.model_validate(user),
    )


def _auth_error(exc: auth_service.AuthError):
    raise HTTPException(exc.status_code, exc.detail)


@router.post("/register", response_model=AuthResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user, access, refresh = auth_service.register(db, payload, settings)
        return _response(user, access, refresh)
    except auth_service.AuthError as exc:
        _auth_error(exc)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    try:
        user, access, refresh = auth_service.login(db, payload, settings)
        return _response(user, access, refresh)
    except auth_service.AuthError as exc:
        _auth_error(exc)


@router.post("/refresh", response_model=AuthResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        user, raw, _ = rotate_refresh_token(db, payload.refresh_token, settings)
    except TokenReuseError:
        logger.warning("refresh_token_reuse_detected")
        raise HTTPException(401, "Invalid refresh token.")
    except TokenError:
        raise HTTPException(401, "Invalid refresh token.")
    logger.info("refresh_token_rotated user_id=%s", user.id)
    return _response(user, create_access_token(user, settings), raw)


@router.post("/logout", response_model=MessageResponse)
def logout(
    payload: RefreshRequest,
    user: User = Depends(get_current_user_model),
    db: Session = Depends(get_db),
):
    revoke_refresh_token(db, payload.refresh_token, user.id)
    logger.info("logout user_id=%s", user.id)
    return MessageResponse(status="logged_out")


@router.post("/logout-all", response_model=MessageResponse)
def logout_all(
    user: User = Depends(get_current_user_model),
    db: Session = Depends(get_db),
):
    revoke_all_user_tokens(db, user.id, "logout_all")
    logger.info("logout_all user_id=%s", user.id)
    return MessageResponse(status="logged_out_all")


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user_model)):
    return user


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user_model),
    db: Session = Depends(get_db),
):
    return auth_service.update_profile(db, user, payload.display_name)


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user_model),
    db: Session = Depends(get_db),
):
    try:
        auth_service.change_password(
            db, user, payload.current_password, payload.new_password, settings
        )
    except auth_service.AuthError as exc:
        _auth_error(exc)
    return MessageResponse(status="password_changed_login_required")


@router.delete("/account", response_model=MessageResponse)
def delete_account(
    payload: AccountDeleteRequest,
    user: User = Depends(get_current_user_model),
    db: Session = Depends(get_db),
):
    try:
        auth_service.deactivate(db, user, payload.current_password)
    except auth_service.AuthError as exc:
        _auth_error(exc)
    return MessageResponse(status="account_deactivated")

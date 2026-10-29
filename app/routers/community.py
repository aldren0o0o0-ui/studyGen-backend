"""Phase 11A community profile endpoints."""
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.types import utc_now
from app.dependencies import get_current_user, get_current_user_model, get_db
from app.schemas.social import CommunityDashboardResponse, CommunityProfileResponse, CommunityProfileUpdate, CommunityUserSummary, PostFeedResponse
from app.database.models.social import Post
from app.services import community_profile_service as profiles
from app.services import post_service
from app.services.post_media_service import _classify
from app.services.community_rate_limit import enforce
from app.services.user_restriction_service import assert_can_access_community
from config import settings

def _community_access(db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: assert_can_access_community(db, user.id)
    except post_service.PostError as exc:
        raise HTTPException(403, {"code": exc.code, "message": str(exc)})


router = APIRouter(prefix="/community", tags=["community"], dependencies=[Depends(get_current_user), Depends(_community_access)])


def _error(exc):
    raise HTTPException(409 if exc.code == "USERNAME_ALREADY_TAKEN" else 422, {"code": exc.code, "message": str(exc)})


def _summary(user: User):
    return CommunityUserSummary(user_id=user.id, username=user.username or "", display_name=user.display_name,
                                profile_image_url=f"/community/users/{user.id}/image" if user.profile_image_key else None)


def _profile(user: User):
    return CommunityProfileResponse(**_summary(user).model_dump(), bio=user.bio, joined_at=user.created_at)


@router.get("/me", response_model=CommunityProfileResponse)
def me(user: User = Depends(get_current_user_model)):
    return _profile(user)


@router.get("/me/dashboard", response_model=CommunityDashboardResponse)
def my_dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    """Private author summary over currently visible, public, canonical posts."""
    row = db.execute(
        select(
            func.count(Post.id),
            func.coalesce(func.sum(Post.reaction_count), 0),
            func.coalesce(func.sum(Post.comment_count), 0),
            func.coalesce(func.sum(Post.share_count), 0),
        ).where(
            Post.author_user_id == user.id,
            Post.status == "PUBLISHED",
            Post.visibility == "PUBLIC",
            Post.deleted_at.is_(None),
            Post.shared_post_id.is_(None),
        )
    ).one()
    return CommunityDashboardResponse(
        published_posts=row[0], hearts_received=row[1], comments_received=row[2], shares_received=row[3]
    )


@router.patch("/me", response_model=CommunityProfileResponse)
def update(payload: CommunityProfileUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try:
        user = profiles.update_profile(db, user, settings=settings, **payload.model_dump(exclude_unset=True))
    except profiles.CommunityProfileError as exc: _error(exc)
    return _profile(user)


@router.post("/me/image", response_model=CommunityProfileResponse)
async def upload_profile_image(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    data = await file.read(settings.post_image_max_bytes + 1)
    try:
        _, ext, _, _, _ = _classify(data, file.content_type or "", settings)
    except post_service.PostError as exc:
        raise HTTPException(422, {"code": exc.code, "message": str(exc)})
    UUID(user.id)
    path = Path(settings.document_storage_root) / "users" / user.id / "profile" / f"avatar.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    user.profile_image_key = str(path.relative_to(Path(settings.document_storage_root))); user.updated_at = utc_now()
    db.commit(); db.refresh(user)
    return _profile(user)


@router.get("/users/search", response_model=list[CommunityUserSummary])
def search(q: str = Query(..., min_length=1, max_length=30), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    enforce(user.id, "community_search", settings.community_user_search_rate_per_minute)
    return [_summary(item) for item in profiles.search_users(db, user.id, q)]


@router.get("/users/{user_id}", response_model=CommunityProfileResponse)
def profile(user_id: str, db: Session = Depends(get_db)):
    user = profiles.public_user(db, user_id)
    if not user: raise HTTPException(404, {"code": "COMMUNITY_USER_NOT_FOUND", "message": "Community user not found."})
    return _profile(user)


@router.get("/users/{user_id}/image")
def profile_image(user_id: str, db: Session = Depends(get_db)):
    user = profiles.public_user(db, user_id)
    if not user or not user.profile_image_key: raise HTTPException(404, "Image not found.")
    root = Path(settings.document_storage_root).resolve(); path = (root / user.profile_image_key).resolve()
    try: path.relative_to(root)
    except ValueError: raise HTTPException(404, "Image not found.")
    if not path.is_file(): raise HTTPException(404, "Image not found.")
    return FileResponse(path)


@router.get("/users/{user_id}/posts", response_model=PostFeedResponse)
def user_posts(user_id: str, cursor: str | None = None, limit: int = 20,
               db: Session = Depends(get_db), current: User = Depends(get_current_user_model)):
    from app.routers.posts import _post_response
    if not profiles.public_user(db, user_id): raise HTTPException(404, {"code": "COMMUNITY_USER_NOT_FOUND", "message": "Community user not found."})
    try: items, next_cursor = post_service.list_posts(db, current.id, author_id=user_id, cursor=cursor, limit=limit, max_limit=settings.community_feed_max_page_size)
    except post_service.PostError as exc: raise HTTPException(422, {"code": exc.code, "message": str(exc)})
    return PostFeedResponse(items=[_post_response(item) for item in items], next_cursor=next_cursor)

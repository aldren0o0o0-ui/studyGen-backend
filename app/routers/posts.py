"""Phase 11A posts, feed, publishing, and protected media endpoints."""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.models.social import PostMedia
from app.dependencies import get_current_user, get_current_user_model, get_db
from app.schemas.social import *
from app.services import post_service
from app.services import post_media_service
from app.services.community_rate_limit import enforce
from config import settings

router = APIRouter(prefix="/posts", tags=["posts"], dependencies=[Depends(get_current_user)])


def _raise(exc: post_service.PostError):
    status = 404 if exc.code == "POST_NOT_FOUND" else (409 if exc.code in {"POST_ALREADY_DELETED", "POST_NOT_EDITABLE"} else 422)
    raise HTTPException(status, {"code": exc.code, "message": str(exc)})


def _user_summary(user):
    return CommunityUserSummary(user_id=user.id, username=user.username or "", display_name=user.display_name,
                                profile_image_url=f"/community/users/{user.id}/image" if user.profile_image_key else None)


def _media_response(post_id, media):
    return PostMediaResponse(media_id=media.id, media_type=media.media_type, filename=media.display_filename,
        mime_type=media.mime_type, file_size=media.file_size, position=media.position,
        processing_status=media.processing_status, width=media.width, height=media.height,
        duration_seconds=media.duration_seconds,
        url=f"/posts/{post_id}/media/{media.id}" if media.processing_status == "READY" else None)


def _post_response(post):
    shared = None
    if post.shared_post_id:
        available = getattr(post, "_shared_available", True) and post.shared_post is not None and post.shared_post.deleted_at is None and post.shared_post.status == "PUBLISHED"
        shared = SharedPostPreview(post_id=post.shared_post_id,
            author=_user_summary(post.shared_post.author) if available else _user_summary(post.author),
            caption=post.shared_post.caption if available else None,
            post_type=post.shared_post.post_type if available else "TEXT",
            media=[_media_response(post.shared_post.id, item) for item in post.shared_post.media if item.deleted_at is None] if available else [], available=available)
    return PostResponse(post_id=post.id, author=_user_summary(post.author), caption=post.caption,
        post_type=post.post_type, visibility=post.visibility, status=post.status,
        allow_comments=post.allow_comments, published_at=post.published_at,
        edited_at=post.edited_at, created_at=post.created_at,
        media=[_media_response(post.id, item) for item in post.media if item.deleted_at is None],
        reaction_count=post.reaction_count, comment_count=post.comment_count, share_count=post.share_count,
        save_count=post.save_count, current_user_reaction=getattr(post, "_current_user_reaction", None),
        is_saved=getattr(post, "_is_saved", False), shared_post=shared)


@router.post("", response_model=PostDetailResponse, status_code=201)
def create(payload: PostCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    enforce(user.id, "post_create", settings.community_post_create_rate_per_minute)
    try: post = post_service.create_post(db, user, payload, settings)
    except post_service.PostError as exc: _raise(exc)
    return PostDetailResponse(**_post_response(post).model_dump(), recipients=[])


@router.get("/feed", response_model=PostFeedResponse)
def feed(cursor: str | None = None, limit: int = Query(20, ge=1), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: items, next_cursor = post_service.list_posts(db, user.id, cursor=cursor, limit=limit, max_limit=settings.community_feed_max_page_size)
    except post_service.PostError as exc: _raise(exc)
    return PostFeedResponse(items=[_post_response(p) for p in items], next_cursor=next_cursor)


@router.get("/mine", response_model=PostFeedResponse)
def mine(cursor: str | None = None, limit: int = Query(20, ge=1), visibility: str | None = None,
         db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: items, next_cursor = post_service.list_posts(db, user.id, mine=True, visibility=visibility, cursor=cursor, limit=limit, max_limit=settings.community_feed_max_page_size)
    except post_service.PostError as exc: _raise(exc)
    return PostFeedResponse(items=[_post_response(p) for p in items], next_cursor=next_cursor)


@router.get("/{post_id}", response_model=PostDetailResponse)
def detail(post_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post = post_service.get_visible_post(db, post_id, user.id)
    except post_service.PostError as exc: _raise(exc)
    post_service.decorate_interaction_state(db, [post], user.id)
    recipients = []
    if post.author_user_id == user.id and post.visibility == "SPECIFIC_USERS":
        ids = [r.allowed_user_id for r in post.recipients]
        users = list(db.scalars(select(User).where(User.id.in_(ids))).all()) if ids else []
        recipients = [_user_summary(item) for item in users]
    return PostDetailResponse(**_post_response(post).model_dump(), recipients=recipients)


@router.patch("/{post_id}", response_model=PostDetailResponse)
def update(post_id: str, payload: PostUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post = post_service.update_post(db, post_id, user.id, payload, settings)
    except post_service.PostError as exc: _raise(exc)
    return PostDetailResponse(**_post_response(post).model_dump(), recipients=[])


@router.post("/{post_id}/publish", response_model=PostDetailResponse)
def publish(post_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post = post_service.publish_post(db, post_id, user.id)
    except post_service.PostError as exc: _raise(exc)
    return PostDetailResponse(**_post_response(post).model_dump(), recipients=[])


@router.delete("/{post_id}", response_model=PostDeleteResponse)
def delete(post_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post = post_service.delete_post(db, post_id, user.id)
    except post_service.PostError as exc: _raise(exc)
    return PostDeleteResponse(post_id=post.id, status="DELETED")


@router.post("/{post_id}/media", response_model=PostMediaResponse, status_code=201)
async def upload_media(post_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    enforce(user.id, "media_upload", settings.community_media_upload_rate_per_minute)
    data = await file.read(max(settings.post_video_max_bytes, settings.post_document_max_bytes, settings.post_image_max_bytes) + 1)
    try: media = post_media_service.add_media(db, post_id, user.id, file.filename or "", file.content_type or "", data, settings)
    except post_service.PostError as exc: _raise(exc)
    return _media_response(post_id, media)


@router.delete("/{post_id}/media/{media_id}", status_code=204)
def delete_media(post_id: str, media_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post_media_service.delete_draft_media(db, post_id, media_id, user.id)
    except post_service.PostError as exc: _raise(exc)


@router.get("/{post_id}/media/{media_id}")
def media(post_id: str, media_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post = post_service.get_visible_post(db, post_id, user.id)
    except post_service.PostError as exc: _raise(exc)
    item = next((m for m in post.media if m.id == media_id and m.deleted_at is None and m.processing_status == "READY"), None)
    if not item: raise HTTPException(404, {"code": "POST_NOT_FOUND", "message": "Media not found."})
    path = post_media_service.resolve_media_path(item, settings)
    if not path: raise HTTPException(404, {"code": "POST_NOT_FOUND", "message": "Media not found."})
    return FileResponse(path, media_type=item.mime_type, filename=item.display_filename)

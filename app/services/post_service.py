"""Post lifecycle, recipient transactions, and cursor-based feed queries."""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database.models.auth import User
from app.database.models.social import Post, PostAllowedUser, PostReaction, SavedPost
from app.database.types import utc_now
from app.services.post_privacy_service import can_delete_post, can_edit_post, visible_post_predicate


class PostError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def clean_caption(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > maximum:
        raise PostError("INVALID_POST_CAPTION", "Caption is too long.")
    if re.search(r"<\s*/?\s*(script|iframe|object|embed|style)\b", value, re.I):
        raise PostError("INVALID_POST_CAPTION", "HTML is not supported in captions.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise PostError("INVALID_POST_CAPTION", "Caption contains unsupported control characters.")
    return value or None


def _validate_recipients(db: Session, author_id: str, visibility: str, ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(value for value in ids if value != author_id))
    if visibility == "SPECIFIC_USERS" and not unique:
        raise PostError("SPECIFIC_RECIPIENT_REQUIRED", "Select at least one recipient.")
    if visibility != "SPECIFIC_USERS":
        return []
    active = set(db.scalars(select(User.id).where(User.id.in_(unique), User.status == "ACTIVE", User.deleted_at.is_(None))).all())
    if active != set(unique):
        raise PostError("INVALID_POST_RECIPIENT", "One or more recipients are unavailable.")
    return unique


def _replace_recipients(db: Session, post: Post, ids: list[str]) -> None:
    post.recipients.clear()
    db.flush()
    for user_id in ids:
        post.recipients.append(PostAllowedUser(id=str(uuid4()), post_id=post.id, allowed_user_id=user_id, created_at=utc_now()))


def create_post(db: Session, author: User, payload, settings) -> Post:
    from app.services.user_restriction_service import assert_can_create_post
    assert_can_create_post(db, author.id)
    if not author.normalized_username:
        # Phase 7 accounts created before Community profiles existed must still
        # be able to post. The immutable user-ID suffix is unique and avoids a
        # separate profile-completion race inside post creation.
        generated_username = f"user_{author.id.replace('-', '')[:12]}"
        author.username = generated_username
        author.normalized_username = generated_username
    recipients = _validate_recipients(db, author.id, payload.visibility, payload.recipient_user_ids)
    now = utc_now()
    post = Post(id=str(uuid4()), author_user_id=author.id,
                caption=clean_caption(payload.caption, settings.post_caption_max_characters),
                visibility=payload.visibility, status="DRAFT", post_type="TEXT",
                allow_comments=payload.allow_comments, created_at=now, updated_at=now)
    db.add(post); db.flush(); _replace_recipients(db, post, recipients); db.commit(); db.refresh(post)
    return post


def get_owned_post(db: Session, post_id: str, user_id: str, *, lock: bool = False) -> Post:
    stmt = select(Post).where(Post.id == post_id).options(joinedload(Post.author), selectinload(Post.media), selectinload(Post.recipients))
    if lock: stmt = stmt.with_for_update(of=Post)
    post = db.scalar(stmt)
    if not post or not can_edit_post(post, user_id):
        raise PostError("POST_NOT_FOUND", "Post not found.")
    return post


def get_visible_post(db: Session, post_id: str, user_id: str, *, lock: bool = False) -> Post:
    from app.services.user_restriction_service import assert_can_access_community
    assert_can_access_community(db, user_id)
    stmt = select(Post).where(Post.id == post_id, visible_post_predicate(user_id)).options(joinedload(Post.author), selectinload(Post.media), selectinload(Post.recipients))
    if lock: stmt = stmt.with_for_update(of=Post)
    post = db.scalar(stmt)
    if not post:
        raise PostError("POST_NOT_FOUND", "Post not found.")
    return post


def update_post(db: Session, post_id: str, user_id: str, payload, settings) -> Post:
    post = get_owned_post(db, post_id, user_id, lock=True)
    data = payload.model_dump(exclude_unset=True)
    visibility = data.get("visibility", post.visibility)
    recipient_input = data.get("recipient_user_ids")
    if visibility == "SPECIFIC_USERS":
        if recipient_input is None:
            recipient_input = [row.allowed_user_id for row in post.recipients]
    else:
        recipient_input = []
    recipients = _validate_recipients(db, user_id, visibility, recipient_input or [])
    if "caption" in data:
        post.caption = clean_caption(data["caption"], settings.post_caption_max_characters)
    post.visibility = visibility
    if "allow_comments" in data:
        post.allow_comments = data["allow_comments"]
    _replace_recipients(db, post, recipients)
    post.edited_at = post.updated_at = utc_now()
    db.commit(); db.refresh(post)
    return post


def publish_post(db: Session, post_id: str, user_id: str) -> Post:
    from app.services.user_restriction_service import assert_can_create_post
    post = get_owned_post(db, post_id, user_id, lock=True)
    assert_can_create_post(db, user_id)
    active_media = [item for item in post.media if item.deleted_at is None]
    if not post.caption and not active_media:
        raise PostError("EMPTY_POST", "Add a caption or attachment before publishing.")
    if any(item.processing_status != "READY" for item in active_media):
        post.status = "FAILED" if any(item.processing_status == "FAILED" for item in active_media) else "PROCESSING"
        db.commit()
        raise PostError("POST_MEDIA_PROCESSING_FAILED", "All attachments must be ready before publishing.")
    kinds = {item.media_type for item in active_media}
    post.post_type = "TEXT" if not kinds else (next(iter(kinds)) if len(kinds) == 1 else "MIXED")
    post.status = "PUBLISHED"; post.published_at = post.published_at or utc_now(); post.updated_at = utc_now()
    db.commit(); db.refresh(post)
    return post


def delete_post(db: Session, post_id: str, user_id: str) -> Post:
    post = db.get(Post, post_id)
    if not post:
        raise PostError("POST_NOT_FOUND", "Post not found.")
    if post.deleted_at is not None:
        raise PostError("POST_ALREADY_DELETED", "Post is already deleted.")
    if not can_delete_post(post, user_id):
        raise PostError("POST_NOT_FOUND", "Post not found.")
    post.status = "DELETED"; post.deleted_at = post.updated_at = utc_now()
    if post.shared_post_id:
        db.execute(update(Post).where(Post.id == post.shared_post_id, Post.share_count > 0).values(share_count=Post.share_count - 1))
    db.commit()
    return post


def _cursor(value: str | None):
    if not value: return None
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "===").decode())
        return datetime.fromisoformat(data["published_at"]), data["id"]
    except Exception as exc:
        raise PostError("INVALID_CURSOR", "Feed cursor is invalid.") from exc


def _encode_cursor(post: Post) -> str:
    return _encode_values(post.published_at, post.id)


def _encode_values(timestamp: datetime, row_id: str) -> str:
    raw = json.dumps({"published_at": timestamp.isoformat(), "id": row_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decorate_interaction_state(db: Session, posts: list[Post], user_id: str) -> None:
    ids = [post.id for post in posts]
    if not ids: return
    reactions = dict(db.execute(select(PostReaction.post_id, PostReaction.reaction_type).where(PostReaction.post_id.in_(ids), PostReaction.user_id == user_id)).all())
    saved = set(db.scalars(select(SavedPost.post_id).where(SavedPost.post_id.in_(ids), SavedPost.user_id == user_id)).all())
    shared_ids = {post.shared_post_id for post in posts if post.shared_post_id}
    visible_shared = set(db.scalars(select(Post.id).where(Post.id.in_(shared_ids), visible_post_predicate(user_id))).all()) if shared_ids else set()
    for post in posts:
        post._current_user_reaction = reactions.get(post.id)
        post._is_saved = post.id in saved
        if post.shared_post_id: post._shared_available = post.shared_post_id in visible_shared


def list_posts(db: Session, user_id: str, *, limit: int, max_limit: int, cursor=None, mine=False, author_id=None, visibility=None):
    from app.services.user_restriction_service import assert_can_access_community
    assert_can_access_community(db, user_id)
    limit = max(1, min(limit, max_limit))
    stmt = select(Post).options(joinedload(Post.author), selectinload(Post.media), joinedload(Post.shared_post).joinedload(Post.author), joinedload(Post.shared_post).selectinload(Post.media))
    if mine:
        stmt = stmt.where(Post.author_user_id == user_id, Post.deleted_at.is_(None))
    elif author_id:
        stmt = stmt.where(Post.author_user_id == author_id, visible_post_predicate(user_id))
    else:
        stmt = stmt.where(visible_post_predicate(user_id))
    if visibility:
        stmt = stmt.where(Post.visibility == visibility)
    decoded = _cursor(cursor)
    if decoded:
        published_at, post_id = decoded
        stmt = stmt.where(or_(Post.published_at < published_at, and_(Post.published_at == published_at, Post.id < post_id)))
    items = list(db.scalars(stmt.order_by(Post.published_at.desc().nullslast(), Post.id.desc()).limit(limit + 1)).unique().all())
    has_more = len(items) > limit; items = items[:limit]
    decorate_interaction_state(db, items, user_id)
    return items, (_encode_cursor(items[-1]) if has_more and items[-1].published_at else None)

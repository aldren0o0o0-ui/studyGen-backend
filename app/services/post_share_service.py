"""Canonical internal sharing with non-escalating privacy."""
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.models.social import Post, PostAllowedUser
from app.database.types import utc_now
from app.services.post_service import PostError, _replace_recipients, _validate_recipients, clean_caption, get_visible_post


def share(db: Session, requested_post_id: str, user: User, payload, settings):
    from app.services.user_restriction_service import assert_can_share
    requested = get_visible_post(db, requested_post_id, user.id, lock=True)
    assert_can_share(db, user.id)
    original = requested.shared_post or requested
    original = get_visible_post(db, original.id, user.id, lock=True)
    if original.visibility == "PRIVATE":
        raise PostError("SHARE_NOT_ALLOWED", "Private posts cannot be shared.")
    if original.visibility == "SPECIFIC_USERS" and payload.visibility == "PUBLIC":
        raise PostError("SHARE_VISIBILITY_ESCALATION", "This post cannot be shared publicly.")
    recipient_ids = list(dict.fromkeys(payload.recipient_user_ids))
    if payload.visibility == "SPECIFIC_USERS":
        recipient_ids = _validate_recipients(db, user.id, payload.visibility, recipient_ids)
        if original.visibility == "SPECIFIC_USERS":
            allowed = set(db.scalars(select(PostAllowedUser.allowed_user_id).where(PostAllowedUser.post_id == original.id)).all()) | {original.author_user_id, user.id}
            if not set(recipient_ids).issubset(allowed):
                raise PostError("SHARE_RECIPIENT_NOT_AUTHORIZED", "A selected recipient cannot access the original post.")
    else:
        recipient_ids = []
    now = utc_now()
    wrapper = Post(id=str(uuid4()), author_user_id=user.id,
                   caption=clean_caption(payload.caption, settings.post_caption_max_characters), post_type="TEXT",
                   visibility=payload.visibility, status="PUBLISHED", allow_comments=payload.allow_comments,
                   shared_post_id=original.id, published_at=now, created_at=now, updated_at=now)
    db.add(wrapper); db.flush(); _replace_recipients(db, wrapper, recipient_ids)
    db.execute(update(Post).where(Post.id == original.id).values(share_count=Post.share_count + 1)); db.commit(); db.refresh(wrapper)
    return wrapper


def eligible_recipients(db: Session, requested_post_id: str, user_id: str, query: str):
    requested = get_visible_post(db, requested_post_id, user_id); original = requested.shared_post or requested
    if original.visibility == "PRIVATE": return []
    pattern = f"%{query.strip().lower()}%"
    stmt = select(User).where(User.id != user_id, User.status == "ACTIVE", User.deleted_at.is_(None),
                              User.normalized_username.is_not(None), User.normalized_username.ilike(pattern)).limit(20)
    if original.visibility == "SPECIFIC_USERS":
        allowed = select(PostAllowedUser.allowed_user_id).where(PostAllowedUser.post_id == original.id)
        stmt = stmt.where(User.id.in_(allowed) | (User.id == original.author_user_id))
    return list(db.scalars(stmt.order_by(User.normalized_username)).all())

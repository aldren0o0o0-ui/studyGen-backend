"""Idempotent post reactions with atomic cached counters."""
from uuid import uuid4
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models.social import Post, PostReaction
from app.database.types import utc_now
from app.services.post_service import PostError, get_visible_post

REACTIONS = {"LIKE", "HELPFUL", "INSIGHTFUL"}


def set_reaction(db: Session, post_id: str, user_id: str, reaction_type: str):
    from app.services.user_restriction_service import assert_can_react
    if reaction_type not in REACTIONS:
        raise PostError("INVALID_REACTION", "Unsupported reaction type.")
    get_visible_post(db, post_id, user_id, lock=True)
    assert_can_react(db, user_id)
    row = db.scalar(select(PostReaction).where(PostReaction.post_id == post_id, PostReaction.user_id == user_id))
    now = utc_now()
    if row:
        row.reaction_type = reaction_type
        row.updated_at = now
        db.commit()
        return row, db.get(Post, post_id).reaction_count
    try:
        row = PostReaction(id=str(uuid4()), post_id=post_id, user_id=user_id, reaction_type=reaction_type, created_at=now, updated_at=now)
        db.add(row)
        db.execute(update(Post).where(Post.id == post_id).values(reaction_count=Post.reaction_count + 1))
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(select(PostReaction).where(PostReaction.post_id == post_id, PostReaction.user_id == user_id))
        if not row:
            raise
        if row.reaction_type != reaction_type:
            row.reaction_type = reaction_type; row.updated_at = now; db.commit()
    return row, db.get(Post, post_id).reaction_count


def remove_reaction(db: Session, post_id: str, user_id: str):
    get_visible_post(db, post_id, user_id, lock=True)
    row = db.scalar(select(PostReaction).where(PostReaction.post_id == post_id, PostReaction.user_id == user_id))
    if row:
        db.delete(row)
        db.execute(update(Post).where(Post.id == post_id, Post.reaction_count > 0).values(reaction_count=Post.reaction_count - 1))
        db.commit()
    return db.get(Post, post_id).reaction_count

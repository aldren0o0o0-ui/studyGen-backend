"""Authoritative interaction counts and repair support."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.database.models.social import Post, PostComment, PostReaction, SavedPost


def expected_counts(db: Session, post_id: str) -> dict[str, int]:
    return {
        "reaction_count": db.scalar(select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post_id)) or 0,
        "comment_count": db.scalar(select(func.count()).select_from(PostComment).where(PostComment.post_id == post_id, PostComment.status == "VISIBLE")) or 0,
        "share_count": db.scalar(select(func.count()).select_from(Post).where(Post.shared_post_id == post_id, Post.deleted_at.is_(None))) or 0,
        "save_count": db.scalar(select(func.count()).select_from(SavedPost).where(SavedPost.post_id == post_id)) or 0,
    }


def rebuild(db: Session, apply: bool = False):
    differences = []
    for post in db.scalars(select(Post)):
        expected = expected_counts(db, post.id)
        for name, value in expected.items():
            stored = getattr(post, name)
            if stored != value:
                differences.append((post.id, name, stored, value))
                if apply: setattr(post, name, value)
    if apply: db.commit()
    return differences

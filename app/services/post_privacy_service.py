"""Single source of truth for post visibility and mutation authorization."""
from sqlalchemy import and_, exists, or_, select

from app.database.models.social import Post, PostAllowedUser


def visible_post_predicate(user_id: str):
    recipient = exists(select(PostAllowedUser.id).where(
        PostAllowedUser.post_id == Post.id,
        PostAllowedUser.allowed_user_id == user_id,
    ))
    return and_(
        Post.deleted_at.is_(None), Post.status == "PUBLISHED",
        or_(Post.visibility == "PUBLIC", Post.author_user_id == user_id,
            and_(Post.visibility == "SPECIFIC_USERS", recipient)),
    )


def can_edit_post(post: Post, user_id: str) -> bool:
    return post.author_user_id == user_id and post.deleted_at is None and post.status not in {"HIDDEN", "REMOVED", "DELETED"}


can_delete_post = can_edit_post
can_manage_post_privacy = can_edit_post

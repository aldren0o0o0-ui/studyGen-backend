"""Private desired-state saves whose reads always revalidate post visibility."""
from uuid import uuid4
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database.models.social import Post, SavedPost
from app.database.types import utc_now
from app.services.post_privacy_service import visible_post_predicate
from app.services.post_service import PostError, get_visible_post


def save(db: Session, post_id: str, user_id: str):
    get_visible_post(db, post_id, user_id, lock=True)
    row = db.scalar(select(SavedPost).where(SavedPost.post_id == post_id, SavedPost.user_id == user_id))
    if not row:
        try:
            row = SavedPost(id=str(uuid4()), post_id=post_id, user_id=user_id, created_at=utc_now())
            db.add(row)
            db.execute(update(Post).where(Post.id == post_id).values(save_count=Post.save_count + 1))
            db.commit()
        except IntegrityError:
            db.rollback()
    return db.get(Post, post_id).save_count


def unsave(db: Session, post_id: str, user_id: str):
    get_visible_post(db, post_id, user_id, lock=True)
    row = db.scalar(select(SavedPost).where(SavedPost.post_id == post_id, SavedPost.user_id == user_id))
    if row:
        db.delete(row)
        db.execute(update(Post).where(Post.id == post_id, Post.save_count > 0).values(save_count=Post.save_count - 1))
        db.commit()
    return db.get(Post, post_id).save_count


def list_saved(db: Session, user_id: str, *, limit: int, max_limit: int, cursor: str | None = None):
    from app.services.user_restriction_service import assert_can_access_community
    assert_can_access_community(db, user_id)
    limit = max(1, min(limit, max_limit))
    stmt = (select(SavedPost, Post).join(Post, Post.id == SavedPost.post_id)
            .where(SavedPost.user_id == user_id, visible_post_predicate(user_id))
            .options(joinedload(Post.author), joinedload(Post.shared_post).joinedload(Post.author), selectinload(Post.media)))
    if cursor:
        import base64, json
        from datetime import datetime
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor + "===").decode()); created, row_id = datetime.fromisoformat(data["published_at"]), data["id"]
        except Exception as exc:
            raise PostError("INVALID_CURSOR", "Saved-post cursor is invalid.") from exc
        stmt = stmt.where(or_(SavedPost.created_at < created, and_(SavedPost.created_at == created, SavedPost.id < row_id)))
    rows = list(db.execute(stmt.order_by(SavedPost.created_at.desc(), SavedPost.id.desc()).limit(limit + 1)).unique().all())
    has_more = len(rows) > limit; rows = rows[:limit]
    next_cursor = None
    if has_more:
        from app.services.post_service import _encode_values
        next_cursor = _encode_values(rows[-1][0].created_at, rows[-1][0].id)
    posts = [post for _, post in rows]
    from app.services.post_service import decorate_interaction_state
    decorate_interaction_state(db, posts, user_id)
    return posts, next_cursor

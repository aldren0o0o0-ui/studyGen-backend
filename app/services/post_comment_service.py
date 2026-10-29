"""Plain-text comments and exactly one structural reply level."""
import base64, json, re
from datetime import datetime
from uuid import uuid4
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.database.models.social import Post, PostComment
from app.database.types import utc_now
from app.services.post_service import PostError, get_visible_post


def clean_content(value: str, maximum: int) -> str:
    value = value.strip()
    if not value or len(value) > maximum or re.search(r"<\s*/?\s*(script|iframe|object|embed|style)\b", value, re.I):
        raise PostError("INVALID_COMMENT", "Comment is empty, too long, or contains unsupported HTML.")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise PostError("INVALID_COMMENT", "Comment contains unsupported control characters.")
    return value


def create(db: Session, post_id: str, user_id: str, content: str, maximum: int, parent_id: str | None = None):
    from app.services.user_restriction_service import assert_can_comment
    post = get_visible_post(db, post_id, user_id, lock=True)
    assert_can_comment(db, user_id)
    if not post.allow_comments:
        raise PostError("COMMENTS_DISABLED", "Comments are turned off for this post.")
    parent = None
    if parent_id:
        parent = db.scalar(select(PostComment).where(PostComment.id == parent_id, PostComment.post_id == post.id))
        if not parent or parent.status in {"HIDDEN", "REMOVED"}:
            raise PostError("COMMENT_NOT_FOUND", "Comment not found.")
        if parent.parent_comment_id:
            parent = db.get(PostComment, parent.parent_comment_id)
    now = utc_now()
    row = PostComment(id=str(uuid4()), post_id=post.id, author_user_id=user_id,
                      parent_comment_id=parent.id if parent else None, content=clean_content(content, maximum),
                      status="VISIBLE", created_at=now, updated_at=now)
    db.add(row); db.execute(update(Post).where(Post.id == post.id).values(comment_count=Post.comment_count + 1)); db.commit(); db.refresh(row)
    return row


def edit(db: Session, comment_id: str, user_id: str, content: str, maximum: int):
    row = db.get(PostComment, comment_id)
    if not row or row.author_user_id != user_id or row.status != "VISIBLE":
        raise PostError("COMMENT_NOT_EDITABLE", "Comment cannot be edited.")
    get_visible_post(db, row.post_id, user_id, lock=True)
    row.content = clean_content(content, maximum); row.edited_at = row.updated_at = utc_now(); db.commit(); db.refresh(row)
    return row


def delete(db: Session, comment_id: str, user_id: str):
    row = db.get(PostComment, comment_id)
    if not row or row.author_user_id != user_id:
        raise PostError("COMMENT_NOT_FOUND", "Comment not found.")
    if row.status == "VISIBLE":
        db.execute(select(Post.id).where(Post.id == row.post_id).with_for_update())
        row.status = "DELETED"; row.content = ""; row.deleted_at = row.updated_at = utc_now()
        db.execute(update(Post).where(Post.id == row.post_id, Post.comment_count > 0).values(comment_count=Post.comment_count - 1)); db.commit()
    return row


def list_comments(db: Session, post_id: str, user_id: str, *, parent_id=None, cursor=None, limit=20, max_limit=50):
    get_visible_post(db, post_id, user_id); limit = max(1, min(limit, max_limit))
    stmt = select(PostComment).where(PostComment.post_id == post_id, PostComment.parent_comment_id == parent_id,
                                     PostComment.status.in_(["VISIBLE", "DELETED"])).options(joinedload(PostComment.author))
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor + "===").decode()); created = datetime.fromisoformat(data["created_at"]); cid = data["id"]
        except Exception as exc: raise PostError("INVALID_CURSOR", "Comment cursor is invalid.") from exc
        stmt = stmt.where(or_(PostComment.created_at > created, and_(PostComment.created_at == created, PostComment.id > cid)))
    rows = list(db.scalars(stmt.order_by(PostComment.created_at, PostComment.id).limit(limit + 1)).all())
    has_more = len(rows) > limit; rows = rows[:limit]; next_cursor = None
    if has_more:
        raw = json.dumps({"created_at": rows[-1].created_at.isoformat(), "id": rows[-1].id}, separators=(",", ":")); next_cursor = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return rows, next_cursor


def reply_counts(db: Session, ids: list[str]) -> dict[str, int]:
    if not ids: return {}
    return dict(db.execute(select(PostComment.parent_comment_id, func.count()).where(PostComment.parent_comment_id.in_(ids), PostComment.status.in_(["VISIBLE", "DELETED"])).group_by(PostComment.parent_comment_id)).all())

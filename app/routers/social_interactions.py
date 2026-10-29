"""Phase 11B reaction, comment, save, and internal-share APIs."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.models.social import PostComment
from app.dependencies import get_current_user, get_current_user_model, get_db
from app.schemas.social import (CommentCreate, CommentPageResponse, CommentResponse, CommentUpdate,
    CommunityUserSummary, PostDetailResponse, PostFeedResponse, ReactionRequest, ReactionResponse, SaveResponse, ShareRequest)
from app.services import post_comment_service, post_reaction_service, post_service, post_share_service, saved_post_service
from app.services.community_rate_limit import enforce
from config import settings

router = APIRouter(tags=["social-interactions"], dependencies=[Depends(get_current_user)])


def _error(exc: post_service.PostError):
    if exc.code in {"POST_NOT_FOUND", "COMMENT_NOT_FOUND"}: status = 404
    elif exc.code in {"COMMENT_NOT_EDITABLE", "COMMENTS_DISABLED", "SHARE_NOT_ALLOWED", "SHARE_VISIBILITY_ESCALATION", "SHARE_RECIPIENT_NOT_AUTHORIZED"}: status = 409
    else: status = 422
    raise HTTPException(status, {"code": exc.code, "message": str(exc)})


def _comment(row, counts=None):
    from app.routers.posts import _user_summary
    return CommentResponse(comment_id=row.id, post_id=row.post_id, author=_user_summary(row.author), parent_comment_id=row.parent_comment_id,
        content=row.content if row.status == "VISIBLE" else None, status=row.status, created_at=row.created_at,
        edited_at=row.edited_at, reply_count=(counts or {}).get(row.id, 0), replies=[])


@router.put("/posts/{post_id}/reaction", response_model=ReactionResponse)
def react(post_id: str, payload: ReactionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    enforce(user.id, "reaction", settings.community_reaction_rate_per_minute)
    try: row, count = post_reaction_service.set_reaction(db, post_id, user.id, payload.reaction_type)
    except post_service.PostError as exc: _error(exc)
    return ReactionResponse(post_id=post_id, current_user_reaction=row.reaction_type, reaction_count=count)


@router.delete("/posts/{post_id}/reaction", response_model=ReactionResponse)
def unreact(post_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: count = post_reaction_service.remove_reaction(db, post_id, user.id)
    except post_service.PostError as exc: _error(exc)
    return ReactionResponse(post_id=post_id, current_user_reaction=None, reaction_count=count)


@router.post("/posts/{post_id}/comments", response_model=CommentResponse, status_code=201)
def comment(post_id: str, payload: CommentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    enforce(user.id, "comment", settings.community_comment_rate_per_minute)
    try: row = post_comment_service.create(db, post_id, user.id, payload.content, settings.comment_max_characters)
    except post_service.PostError as exc: _error(exc)
    return _comment(row)


@router.get("/posts/{post_id}/comments", response_model=CommentPageResponse)
def comments(post_id: str, cursor: str | None = None, limit: int = Query(20, ge=1), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: rows, next_cursor = post_comment_service.list_comments(db, post_id, user.id, cursor=cursor, limit=limit, max_limit=settings.community_feed_max_page_size)
    except post_service.PostError as exc: _error(exc)
    counts = post_comment_service.reply_counts(db, [row.id for row in rows])
    return CommentPageResponse(items=[_comment(row, counts) for row in rows], next_cursor=next_cursor)


@router.post("/comments/{comment_id}/replies", response_model=CommentResponse, status_code=201)
def reply(comment_id: str, payload: CommentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    parent = db.get(PostComment, comment_id)
    if not parent: raise HTTPException(404, {"code": "COMMENT_NOT_FOUND", "message": "Comment not found."})
    enforce(user.id, "comment", settings.community_comment_rate_per_minute)
    try: row = post_comment_service.create(db, parent.post_id, user.id, payload.content, settings.comment_max_characters, parent.id)
    except post_service.PostError as exc: _error(exc)
    return _comment(row)


@router.get("/comments/{comment_id}/replies", response_model=CommentPageResponse)
def replies(comment_id: str, cursor: str | None = None, limit: int = Query(20, ge=1), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    parent = db.get(PostComment, comment_id)
    if not parent: raise HTTPException(404, {"code": "COMMENT_NOT_FOUND", "message": "Comment not found."})
    try: rows, next_cursor = post_comment_service.list_comments(db, parent.post_id, user.id, parent_id=parent.parent_comment_id or parent.id, cursor=cursor, limit=limit, max_limit=settings.community_feed_max_page_size)
    except post_service.PostError as exc: _error(exc)
    return CommentPageResponse(items=[_comment(row) for row in rows], next_cursor=next_cursor)


@router.patch("/comments/{comment_id}", response_model=CommentResponse)
def edit_comment(comment_id: str, payload: CommentUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: row = post_comment_service.edit(db, comment_id, user.id, payload.content, settings.comment_max_characters)
    except post_service.PostError as exc: _error(exc)
    return _comment(row)


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: post_comment_service.delete(db, comment_id, user.id)
    except post_service.PostError as exc: _error(exc)
    return Response(status_code=204)


@router.put("/posts/{post_id}/save", response_model=SaveResponse)
def save(post_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    enforce(user.id, "save", settings.community_save_rate_per_minute)
    try: count = saved_post_service.save(db, post_id, user.id)
    except post_service.PostError as exc: _error(exc)
    return SaveResponse(post_id=post_id, is_saved=True, save_count=count)


@router.delete("/posts/{post_id}/save", response_model=SaveResponse)
def unsave(post_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    try: count = saved_post_service.unsave(db, post_id, user.id)
    except post_service.PostError as exc: _error(exc)
    return SaveResponse(post_id=post_id, is_saved=False, save_count=count)


@router.get("/community/saved", response_model=PostFeedResponse)
def saved(cursor: str | None = None, limit: int = Query(20, ge=1), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    from app.routers.posts import _post_response
    try: items, next_cursor = saved_post_service.list_saved(db, user.id, cursor=cursor, limit=limit, max_limit=settings.community_feed_max_page_size)
    except post_service.PostError as exc: _error(exc)
    return PostFeedResponse(items=[_post_response(post) for post in items], next_cursor=next_cursor)


@router.post("/posts/{post_id}/share", response_model=PostDetailResponse, status_code=201)
def share(post_id: str, payload: ShareRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    from app.routers.posts import _post_response
    enforce(user.id, "share", settings.community_share_rate_per_minute)
    try: wrapper = post_share_service.share(db, post_id, user, payload, settings)
    except post_service.PostError as exc: _error(exc)
    post_service.decorate_interaction_state(db, [wrapper], user.id)
    return PostDetailResponse(**_post_response(wrapper).model_dump(), recipients=[])


@router.get("/posts/{post_id}/share-eligible-users", response_model=list[CommunityUserSummary])
def share_eligible_users(post_id: str, q: str = Query(..., min_length=1, max_length=30), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    from app.routers.posts import _user_summary
    try: users = post_share_service.eligible_recipients(db, post_id, user.id, q)
    except post_service.PostError as exc: _error(exc)
    return [_user_summary(item) for item in users]

"""User reporting and role-protected moderation APIs."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.models.moderation import ContentReport, ModerationAction, UserRestriction
from app.dependencies import get_current_user, get_current_user_model, get_db
from app.schemas.moderation import *
from app.services import moderation_service
from app.services.community_rate_limit import enforce
from app.services.moderation_permissions import require_permission
from app.services.post_service import PostError
from app.services.user_restriction_service import assert_can_access_community
from config import settings

router = APIRouter(tags=["moderation"], dependencies=[Depends(get_current_user)])


def _fail(exc: PostError):
    if exc.code in {"REPORT_NOT_FOUND", "POST_NOT_FOUND", "COMMENT_NOT_FOUND", "RESTRICTION_NOT_FOUND", "USER_NOT_FOUND"}: status = 404
    elif exc.code == "MODERATION_PERMISSION_REQUIRED": status = 403
    else: status = 409
    raise HTTPException(status, {"code": exc.code, "message": str(exc)})


def _report(row: ContentReport, privileged=False):
    data = {"report_id": row.id, "resource_type": row.resource_type, "resource_id": row.resource_id,
        "reported_user_id": row.reported_user_id, "reason": row.reason, "note": row.note,
        "status": row.status, "priority": row.priority, "assigned_moderator_user_id": row.assigned_moderator_user_id,
        "resolution_code": row.resolution_code, "resource_caption_excerpt": row.resource_caption_excerpt,
        "resource_status_at_report": row.resource_status_at_report, "created_at": row.created_at,
        "updated_at": row.updated_at, "resolved_at": row.resolved_at}
    if privileged: data["reporter_user_id"] = row.reporter_user_id
    return data


@router.post("/reports", response_model=ReportAck, status_code=201)
def report(payload: ReportCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    assert_can_access_community(db, user.id); enforce(user.id, "report", settings.community_report_rate_per_minute)
    try: row = moderation_service.create_report(db, user, payload)
    except PostError as exc: _fail(exc)
    return ReportAck(report_id=row.id, status=row.status, created_at=row.created_at)


@router.get("/moderation/reports")
def reports(status: str | None = None, resource_type: str | None = None, reason: str | None = None,
            assigned_to_me: bool = False, unassigned: bool = False, author_user_id: str | None = None,
            limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.view")
    rows = moderation_service.list_reports(db, status=status, resource_type=resource_type, reason=reason,
        assigned_to=user.id if assigned_to_me else None, unassigned=unassigned, author_user_id=author_user_id, limit=limit)
    return {"items": [_report(row) for row in rows], "next_cursor": None}


@router.get("/moderation/reports/{report_id}")
def report_detail(report_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.view"); row = db.get(ContentReport, report_id)
    if not row: raise HTTPException(404, {"code": "REPORT_NOT_FOUND", "message": "Report not found."})
    data = _report(row, privileged=True)
    data["related_reports_count"] = db.scalar(select(func.count(ContentReport.id)).where(
        ContentReport.resource_type == row.resource_type, ContentReport.resource_id == row.resource_id)) or 0
    return data


@router.post("/moderation/reports/{report_id}/assign")
def assign(report_id: str, payload: AssignRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.assign")
    try: return _report(moderation_service.assign_report(db, report_id, user, payload.moderator_user_id))
    except PostError as exc: _fail(exc)


@router.post("/moderation/reports/{report_id}/unassign")
def unassign(report_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.assign")
    try: return _report(moderation_service.assign_report(db, report_id, user, unassign=True))
    except PostError as exc: _fail(exc)


@router.post("/moderation/reports/{report_id}/dismiss")
def dismiss(report_id: str, payload: ResolutionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.resolve")
    try: return _report(moderation_service.close_report(db, report_id, user, dismiss=True, code="NO_VIOLATION", note=payload.resolution_note))
    except PostError as exc: _fail(exc)


@router.post("/moderation/reports/{report_id}/resolve")
def resolve(report_id: str, payload: ResolutionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.resolve")
    try: return _report(moderation_service.close_report(db, report_id, user, dismiss=False, code=payload.resolution_code, note=payload.resolution_note))
    except PostError as exc: _fail(exc)


def _content_action(kind, target_id, action, payload, db, user):
    permission = f"moderation.{kind}.{'restore' if action == 'restore' else ('hide' if action == 'hide' else 'remove')}"
    require_permission(user, permission)
    try:
        row = moderation_service.moderate_post(db, target_id, user, action, payload.note, payload.source_report_id) if kind == "post" else moderation_service.moderate_comment(db, target_id, user, action, payload.note, payload.source_report_id)
    except PostError as exc: _fail(exc)
    return {"id": row.id, "status": row.status}


@router.post("/moderation/posts/{target_id}/hide")
def hide_post(target_id: str, payload: ModerationNote, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)): return _content_action("post", target_id, "hide", payload, db, user)
@router.post("/moderation/posts/{target_id}/remove")
def remove_post(target_id: str, payload: ModerationNote, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)): return _content_action("post", target_id, "remove", payload, db, user)
@router.post("/moderation/posts/{target_id}/restore")
def restore_post(target_id: str, payload: ModerationNote, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)): return _content_action("post", target_id, "restore", payload, db, user)
@router.post("/moderation/comments/{target_id}/hide")
def hide_comment(target_id: str, payload: ModerationNote, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)): return _content_action("comment", target_id, "hide", payload, db, user)
@router.post("/moderation/comments/{target_id}/remove")
def remove_comment(target_id: str, payload: ModerationNote, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)): return _content_action("comment", target_id, "remove", payload, db, user)
@router.post("/moderation/comments/{target_id}/restore")
def restore_comment(target_id: str, payload: ModerationNote, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)): return _content_action("comment", target_id, "restore", payload, db, user)


@router.get("/moderation/users/{user_id}/restrictions")
def restrictions(user_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.report.view")
    return list(db.scalars(select(UserRestriction).where(UserRestriction.user_id == user_id).order_by(UserRestriction.created_at.desc())).all())


@router.post("/moderation/users/{user_id}/restrictions", status_code=201)
def restrict(user_id: str, payload: RestrictionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.user.restrict")
    try: return moderation_service.create_restriction(db, user_id, user, payload)
    except PostError as exc: _fail(exc)


@router.post("/moderation/restrictions/{restriction_id}/revoke")
def revoke(restriction_id: str, payload: RevokeRestriction, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.user.restriction_revoke")
    try: return moderation_service.revoke_restriction(db, restriction_id, user, payload.revocation_reason)
    except PostError as exc: _fail(exc)


@router.post("/moderation/users/{user_id}/deactivate")
def deactivate(user_id: str, payload: AccountAction, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.user.deactivate")
    try: row = moderation_service.set_user_active(db, user_id, user, False, payload.reason, payload.source_report_id); return {"user_id": row.id, "status": row.status}
    except PostError as exc: _fail(exc)


@router.post("/moderation/users/{user_id}/reactivate")
def reactivate(user_id: str, payload: AccountAction, db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.user.reactivate")
    try: row = moderation_service.set_user_active(db, user_id, user, True, payload.reason, payload.source_report_id); return {"user_id": row.id, "status": row.status}
    except PostError as exc: _fail(exc)


@router.get("/moderation/actions")
def actions(action_type: str | None = None, target_user_id: str | None = None, limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db), user: User = Depends(get_current_user_model)):
    require_permission(user, "moderation.audit.view"); stmt = select(ModerationAction)
    if action_type: stmt = stmt.where(ModerationAction.action_type == action_type)
    if target_user_id: stmt = stmt.where(ModerationAction.target_user_id == target_user_id)
    return {"items": list(db.scalars(stmt.order_by(ModerationAction.created_at.desc(), ModerationAction.id.desc()).limit(limit)).all()), "next_cursor": None}

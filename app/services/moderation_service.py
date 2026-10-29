"""Transactional Phase 11D report, moderation, restriction, and audit operations."""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models.auth import RefreshToken, User
from app.database.models.moderation import ContentReport, ModerationAction, UserRestriction
from app.database.models.social import Post, PostComment
from app.database.types import utc_now
from app.services.moderation_permissions import has_permission
from app.services.post_privacy_service import visible_post_predicate
from app.services.post_service import PostError
from app.services.admin_target_policy import assert_governance_target

ACTIVE_REPORT = ("OPEN", "UNDER_REVIEW")


def _clean(value: str | None, maximum=2000) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > maximum or any(ord(ch) < 32 and ch not in "\n\t" for ch in value) or re.search(r"<\s*/?\s*(script|iframe|object|embed|style)\b", value, re.I):
        raise PostError("INVALID_REPORT_NOTE", "Note contains unsupported content or is too long.")
    return value or None


def audit(db: Session, actor_id: str, action_type: str, target_type: str, target_id: str, *, target_user_id=None, source_report_id=None, reason_code=None, note=None, metadata=None):
    if metadata is not None:
        encoded = json.dumps(metadata, separators=(",", ":"), default=str)
        forbidden = {"password", "token", "authorization", "cookie", "secret"}
        if len(encoded.encode("utf-8")) > 4096 or any(key in encoded.casefold() for key in forbidden):
            raise PostError("INVALID_AUDIT_METADATA", "Audit metadata is unsafe or too large.")
    row = ModerationAction(id=str(uuid4()), actor_user_id=actor_id, action_type=action_type,
        target_type=target_type, target_id=target_id, target_user_id=target_user_id,
        source_report_id=source_report_id, reason_code=reason_code, note=_clean(note),
        metadata_json=metadata, created_at=utc_now())
    db.add(row)
    return row


def create_report(db: Session, reporter: User, payload) -> ContentReport:
    if payload.resource_type == "POST":
        resource = db.scalar(select(Post).where(Post.id == payload.resource_id, visible_post_predicate(reporter.id)))
        if not resource:
            raise PostError("REPORT_NOT_FOUND", "Reportable content was not found.")
        author_id, status, created, excerpt = resource.author_user_id, resource.status, resource.created_at, resource.caption
    else:
        resource = db.scalar(select(PostComment).join(Post, Post.id == PostComment.post_id).where(
            PostComment.id == payload.resource_id, PostComment.status == "VISIBLE", visible_post_predicate(reporter.id)))
        if not resource:
            raise PostError("REPORT_NOT_FOUND", "Reportable content was not found.")
        author_id, status, created, excerpt = resource.author_user_id, resource.status, resource.created_at, resource.content
    if author_id == reporter.id:
        raise PostError("REPORT_OWN_CONTENT", "You cannot report your own content.")
    existing = db.scalar(select(ContentReport).where(ContentReport.reporter_user_id == reporter.id,
        ContentReport.resource_type == payload.resource_type, ContentReport.resource_id == payload.resource_id,
        ContentReport.status.in_(ACTIVE_REPORT)))
    if existing:
        return existing
    now = utc_now()
    row = ContentReport(id=str(uuid4()), reporter_user_id=reporter.id, resource_type=payload.resource_type,
        resource_id=payload.resource_id, reported_user_id=author_id, reason=payload.reason,
        note=_clean(payload.note), status="OPEN",
        priority="CRITICAL" if payload.reason == "VIOLENCE_OR_THREATS" else ("HIGH" if payload.reason == "PRIVACY_VIOLATION" else "NORMAL"),
        resource_caption_excerpt=(excerpt or "")[:500] or None, resource_status_at_report=status,
        resource_created_at=created, created_at=now, updated_at=now)
    try:
        with db.begin_nested():
            db.add(row); db.flush()
            audit(db, reporter.id, "REPORT_CREATED", payload.resource_type, payload.resource_id,
                target_user_id=author_id, source_report_id=row.id, reason_code=payload.reason)
        db.commit(); db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ContentReport).where(ContentReport.reporter_user_id == reporter.id,
            ContentReport.resource_type == payload.resource_type, ContentReport.resource_id == payload.resource_id,
            ContentReport.status.in_(ACTIVE_REPORT)))
        if existing:
            return existing
        raise


def list_reports(db: Session, *, status=None, resource_type=None, reason=None, assigned_to=None, unassigned=False, author_user_id=None, limit=20):
    stmt = select(ContentReport)
    if status: stmt = stmt.where(ContentReport.status == status)
    if resource_type: stmt = stmt.where(ContentReport.resource_type == resource_type)
    if reason: stmt = stmt.where(ContentReport.reason == reason)
    if assigned_to: stmt = stmt.where(ContentReport.assigned_moderator_user_id == assigned_to)
    if unassigned: stmt = stmt.where(ContentReport.assigned_moderator_user_id.is_(None))
    if author_user_id: stmt = stmt.where(ContentReport.reported_user_id == author_user_id)
    rank = case((ContentReport.priority == "CRITICAL", 3), (ContentReport.priority == "HIGH", 2), else_=1)
    return list(db.scalars(stmt.order_by(rank.desc(), ContentReport.created_at, ContentReport.id).limit(min(max(limit, 1), 50))).all())


def assign_report(db: Session, report_id: str, actor: User, assignee_id: str | None = None, *, unassign=False):
    report = db.scalar(select(ContentReport).where(ContentReport.id == report_id).with_for_update(of=ContentReport))
    if not report: raise PostError("REPORT_NOT_FOUND", "Report not found.")
    if report.status not in ACTIVE_REPORT: raise PostError("REPORT_ALREADY_RESOLVED", "Report is already closed.")
    if unassign:
        report.assigned_moderator_user_id = None; action = "REPORT_UNASSIGNED"
    else:
        target = assignee_id or actor.id
        assignee = db.get(User, target)
        if not assignee or not has_permission(assignee, "moderation.report.view"):
            raise PostError("MODERATION_PERMISSION_REQUIRED", "Assignee is not a moderator.")
        if report.assigned_moderator_user_id and report.assigned_moderator_user_id != target:
            raise PostError("REPORT_ALREADY_ASSIGNED", "Report is already assigned.")
        if target == report.reported_user_id:
            raise PostError("MODERATION_CONFLICT", "A moderator cannot handle a report about their own content.")
        report.assigned_moderator_user_id = target; report.status = "UNDER_REVIEW"; action = "REPORT_ASSIGNED"
    report.updated_at = utc_now()
    audit(db, actor.id, action, "REPORT", report.id, target_user_id=report.reported_user_id, source_report_id=report.id)
    db.commit(); db.refresh(report); return report


def close_report(db: Session, report_id: str, actor: User, *, dismiss: bool, code: str, note=None):
    report = db.scalar(select(ContentReport).where(ContentReport.id == report_id).with_for_update(of=ContentReport))
    if not report: raise PostError("REPORT_NOT_FOUND", "Report not found.")
    if report.status not in ACTIVE_REPORT: raise PostError("REPORT_ALREADY_RESOLVED", "Report is already closed.")
    now = utc_now(); report.status = "DISMISSED" if dismiss else "RESOLVED"
    report.resolution_code = "NO_VIOLATION" if dismiss else code; report.resolution_note = _clean(note)
    report.resolved_by_user_id = actor.id; report.resolved_at = report.updated_at = now
    audit(db, actor.id, "REPORT_DISMISSED" if dismiss else "REPORT_RESOLVED", "REPORT", report.id,
        target_user_id=report.reported_user_id, source_report_id=report.id, reason_code=report.resolution_code, note=note)
    db.commit(); db.refresh(report); return report


def moderate_post(db: Session, post_id: str, actor: User, action: str, note=None, source_report_id=None):
    post = db.scalar(select(Post).where(Post.id == post_id).with_for_update(of=Post))
    if not post: raise PostError("POST_NOT_FOUND", "Post not found.")
    transitions = {"hide": ({"PUBLISHED"}, "HIDDEN", "POST_HIDDEN"), "remove": ({"PUBLISHED", "HIDDEN"}, "REMOVED", "POST_REMOVED")}
    if action == "restore":
        allowed = {"HIDDEN", "REMOVED"} if actor.role == "ADMIN" else {"HIDDEN"}; target, event = "PUBLISHED", "POST_RESTORED"
    else:
        allowed, target, event = transitions[action]
    if post.status not in allowed: raise PostError("INVALID_MODERATION_TRANSITION", "Post transition is not allowed.")
    if post.author_user_id == actor.id: raise PostError("MODERATION_CONFLICT", "You cannot moderate your own content.")
    post.status = target; post.updated_at = utc_now()
    audit(db, actor.id, event, "POST", post.id, target_user_id=post.author_user_id, source_report_id=source_report_id, note=note)
    db.commit(); db.refresh(post); return post


def moderate_comment(db: Session, comment_id: str, actor: User, action: str, note=None, source_report_id=None):
    candidate = db.get(PostComment, comment_id)
    if not candidate: raise PostError("COMMENT_NOT_FOUND", "Comment not found.")
    db.execute(select(Post.id).where(Post.id == candidate.post_id).with_for_update(of=Post))
    row = db.scalar(select(PostComment).where(PostComment.id == comment_id).with_for_update(of=PostComment))
    if action == "restore": allowed, target, event = {"HIDDEN"}, "VISIBLE", "COMMENT_RESTORED"
    elif action == "hide": allowed, target, event = {"VISIBLE"}, "HIDDEN", "COMMENT_HIDDEN"
    else: allowed, target, event = {"VISIBLE", "HIDDEN"}, "REMOVED", "COMMENT_REMOVED"
    if row.status not in allowed: raise PostError("INVALID_MODERATION_TRANSITION", "Comment transition is not allowed.")
    if row.author_user_id == actor.id: raise PostError("MODERATION_CONFLICT", "You cannot moderate your own content.")
    row.status = target; row.updated_at = utc_now()
    if target in {"HIDDEN", "REMOVED"}:
        db.execute(update(Post).where(Post.id == row.post_id, Post.comment_count > 0).values(comment_count=Post.comment_count - 1))
    elif target == "VISIBLE":
        db.execute(update(Post).where(Post.id == row.post_id).values(comment_count=Post.comment_count + 1))
    audit(db, actor.id, event, "COMMENT", row.id, target_user_id=row.author_user_id, source_report_id=source_report_id, note=note)
    db.commit(); db.refresh(row); return row


def create_restriction(db: Session, user_id: str, actor: User, payload):
    if payload.restriction_type == "ACCOUNT" and actor.role != "ADMIN": raise PostError("MODERATION_PERMISSION_REQUIRED", "Admin permission is required.")
    user = db.scalar(select(User).where(User.id == user_id).with_for_update(of=User))
    if not user: raise PostError("USER_NOT_FOUND", "User not found.")
    assert_governance_target(actor,user,"restrict")
    now = utc_now(); starts = payload.starts_at or now
    if payload.ends_at and payload.ends_at <= starts: raise PostError("INVALID_RESTRICTION_PERIOD", "Restriction end must follow its start.")
    db.execute(update(UserRestriction).where(UserRestriction.user_id==user_id,
        UserRestriction.restriction_type==payload.restriction_type,UserRestriction.status=="ACTIVE",
        UserRestriction.ends_at.is_not(None),UserRestriction.ends_at<=now).values(status="EXPIRED",updated_at=now))
    existing = db.scalar(select(UserRestriction).where(UserRestriction.user_id == user_id,
        UserRestriction.restriction_type == payload.restriction_type, UserRestriction.status == "ACTIVE").with_for_update(of=UserRestriction))
    if existing: raise PostError("USER_RESTRICTED", "An active restriction of this type already exists.")
    row = UserRestriction(id=str(uuid4()), user_id=user_id, restriction_type=payload.restriction_type,
        status="ACTIVE", reason=_clean(payload.reason) or "Policy enforcement", starts_at=starts,
        ends_at=payload.ends_at, created_by_user_id=actor.id, source_report_id=payload.source_report_id,
        created_at=now, updated_at=now)
    db.add(row); db.flush(); audit(db, actor.id, "USER_RESTRICTED", "USER", user_id, target_user_id=user_id,
        source_report_id=payload.source_report_id, reason_code=payload.restriction_type, note=payload.reason,
        metadata={"restriction_id": row.id, "ends_at": row.ends_at.isoformat() if row.ends_at else None})
    db.commit(); db.refresh(row); return row


def revoke_restriction(db: Session, restriction_id: str, actor: User, reason: str):
    row = db.scalar(select(UserRestriction).where(UserRestriction.id == restriction_id).with_for_update(of=UserRestriction))
    if not row: raise PostError("RESTRICTION_NOT_FOUND", "Restriction not found.")
    target=db.get(User,row.user_id);assert_governance_target(actor,target,"restriction_revoke")
    if row.status != "ACTIVE": raise PostError("RESTRICTION_ALREADY_REVOKED", "Restriction is not active.")
    now = utc_now(); row.status = "REVOKED"; row.revoked_at = row.updated_at = now; row.revoked_by_user_id = actor.id; row.revocation_reason = _clean(reason)
    audit(db, actor.id, "RESTRICTION_REVOKED", "RESTRICTION", row.id, target_user_id=row.user_id, note=reason)
    db.commit(); db.refresh(row); return row


def set_user_active(db: Session, user_id: str, actor: User, active: bool, reason: str, source_report_id=None):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update(of=User))
    if not user: raise PostError("USER_NOT_FOUND", "User not found.")
    assert_governance_target(actor,user,"reactivate" if active else "deactivate")
    expected, target = (("DEACTIVATED", "ACTIVE") if active else ("ACTIVE", "DEACTIVATED"))
    if user.status != expected: raise PostError("INVALID_MODERATION_TRANSITION", "Account transition is not allowed.")
    user.status = target; user.session_version += 1; user.updated_at = utc_now()
    if not active:
        db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=utc_now(), revoke_reason="USER_DEACTIVATED"))
    audit(db, actor.id, "USER_REACTIVATED" if active else "USER_DEACTIVATED", "USER", user.id,
        target_user_id=user.id, source_report_id=source_report_id, note=reason)
    db.commit(); db.refresh(user); return user

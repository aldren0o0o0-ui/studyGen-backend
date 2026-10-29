"""Dry-run Phase 11D moderation consistency audit."""
from sqlalchemy import select
from app.database.models.auth import User
from app.database.models.moderation import ContentReport, ModerationAction, UserRestriction
from app.database.models.social import Post, PostComment
from app.database.session import SessionLocal
from app.database.types import utc_now


def check(db):
    issues = []
    unresolved = set()
    for report in db.scalars(select(ContentReport)).all():
        resource = db.get(Post if report.resource_type == "POST" else PostComment, report.resource_id)
        if not resource: issues.append(f"report_resource_missing:{report.id}")
        elif resource.author_user_id != report.reported_user_id: issues.append(f"report_author_mismatch:{report.id}")
        if report.status in {"RESOLVED", "DISMISSED"} and (not report.resolved_by_user_id or not report.resolved_at): issues.append(f"resolved_report_incomplete:{report.id}")
        if report.assigned_moderator_user_id:
            assignee = db.get(User, report.assigned_moderator_user_id)
            if not assignee: issues.append(f"report_assignee_missing:{report.id}")
            elif assignee.role not in {"MODERATOR", "ADMIN"}: issues.append(f"report_assignee_not_moderator:{report.id}")
        if report.status in {"OPEN", "UNDER_REVIEW"}:
            key=(report.reporter_user_id,report.resource_type,report.resource_id)
            if key in unresolved: issues.append(f"duplicate_unresolved_report:{report.id}")
            unresolved.add(key)
    now = utc_now(); seen = set()
    for row in db.scalars(select(UserRestriction).where(UserRestriction.status == "ACTIVE")).all():
        if not db.get(User, row.user_id): issues.append(f"restriction_user_missing:{row.id}")
        if row.ends_at and row.ends_at <= row.starts_at: issues.append(f"restriction_period_invalid:{row.id}")
        if row.ends_at and row.ends_at <= now: issues.append(f"active_restriction_expired:{row.id}")
        key = (row.user_id, row.restriction_type)
        if key in seen: issues.append(f"duplicate_active_restriction:{row.user_id}:{row.restriction_type}")
        seen.add(key)
        if not db.scalar(select(ModerationAction.id).where(ModerationAction.action_type == "USER_RESTRICTED", ModerationAction.metadata_json["restriction_id"].as_string() == row.id)):
            issues.append(f"active_restriction_without_audit:{row.id}")
    for row in db.scalars(select(UserRestriction).where(UserRestriction.status == "REVOKED")).all():
        if not db.scalar(select(ModerationAction.id).where(ModerationAction.action_type == "RESTRICTION_REVOKED", ModerationAction.target_id == row.id)):
            issues.append(f"revoked_restriction_without_audit:{row.id}")
    audited = set(db.execute(select(ModerationAction.target_type, ModerationAction.target_id).where(ModerationAction.action_type.in_(["POST_HIDDEN", "POST_REMOVED", "COMMENT_HIDDEN", "COMMENT_REMOVED"]))).all())
    for post in db.scalars(select(Post).where(Post.status.in_(["HIDDEN", "REMOVED"]))).all():
        if ("POST", post.id) not in audited: issues.append(f"moderated_post_without_audit:{post.id}")
    for row in db.scalars(select(PostComment).where(PostComment.status.in_(["HIDDEN", "REMOVED"]))).all():
        if ("COMMENT", row.id) not in audited: issues.append(f"moderated_comment_without_audit:{row.id}")
    for user in db.scalars(select(User).where(User.status == "DEACTIVATED")).all():
        if not db.scalar(select(ModerationAction.id).where(ModerationAction.action_type == "USER_DEACTIVATED", ModerationAction.target_id == user.id)):
            issues.append(f"deactivated_user_without_audit:{user.id}")
    for action in db.scalars(select(ModerationAction)).all():
        if not db.get(User, action.actor_user_id): issues.append(f"audit_actor_missing:{action.id}")
        model={"POST":Post,"COMMENT":PostComment,"REPORT":ContentReport,"RESTRICTION":UserRestriction,"USER":User}.get(action.target_type)
        if model and not db.get(model,action.target_id): issues.append(f"audit_target_missing:{action.id}")
        if action.target_user_id and action.actor_user_id == action.target_user_id and action.action_type not in {"REPORT_CREATED"}:
            issues.append(f"self_moderation_residue:{action.id}")
    return issues


def main():
    with SessionLocal() as db: issues = check(db)
    for issue in issues: print(issue)
    print(f"Moderation consistency: {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__": raise SystemExit(main())

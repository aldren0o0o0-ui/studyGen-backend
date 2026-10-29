"""Read-only Phase 12A Admin consistency checker."""
from sqlalchemy import func,select,and_
from app.database.models.auth import RefreshToken,User
from app.database.models.moderation import UserRestriction,ModerationAction
from app.database.types import utc_now
from app.database.session import SessionLocal

VALID_ROLES = ("USER", "MODERATOR", "ADMIN")
# Keep this aligned with ck_users_status. DISABLED is used by the protected
# legacy-data owner and DELETED is the terminal state for removed accounts.
VALID_STATUSES = ("ACTIVE", "LOCKED", "DISABLED", "DEACTIVATED", "DELETED")

def check(db):
    issues=[];active_admins=db.scalar(select(func.count(User.id)).where(User.role=="ADMIN",User.status=="ACTIVE",User.deleted_at.is_(None))) or 0
    if active_admins==0:issues.append("CRITICAL: no active Admin account")
    invalid=list(db.execute(select(User.id,User.role,User.status).where((~User.role.in_(VALID_ROLES))|(~User.status.in_(VALID_STATUSES)))).all());issues.extend(f"invalid user state: {row[0]} role={row[1]} status={row[2]}" for row in invalid)
    versions=list(db.scalars(select(User.id).where(User.session_version<1)).all());issues.extend(f"invalid session version: {uid}" for uid in versions)
    tokens=list(db.scalars(select(RefreshToken.user_id).join(User,User.id==RefreshToken.user_id).where(User.status.in_(["DEACTIVATED","DISABLED","DELETED"]),RefreshToken.revoked_at.is_(None))).all());issues.extend(f"inactive user has active refresh session: {uid}" for uid in tokens)
    missing=list(db.scalars(select(UserRestriction.id).outerjoin(User,User.id==UserRestriction.user_id).where(User.id.is_(None))).all());issues.extend(f"restriction missing user: {rid}" for rid in missing)
    stale=list(db.scalars(select(UserRestriction.id).where(UserRestriction.status=="ACTIVE",UserRestriction.ends_at.is_not(None),UserRestriction.ends_at<=utc_now())).all());issues.extend(f"expired restriction still ACTIVE: {rid}" for rid in stale)
    duplicate=list(db.execute(select(UserRestriction.user_id,UserRestriction.restriction_type,func.count()).where(UserRestriction.status=="ACTIVE").group_by(UserRestriction.user_id,UserRestriction.restriction_type).having(func.count()>1)).all());issues.extend(f"duplicate ACTIVE restriction: user={u} scope={s}" for u,s,_ in duplicate)
    promoted=select(ModerationAction.id).where(ModerationAction.target_user_id==User.id,ModerationAction.action_type=="USER_PROMOTED_TO_MODERATOR").correlate(User).exists()
    unaudited_roles=list(db.scalars(select(User.id).where(User.role=="MODERATOR",User.updated_at>User.created_at,~promoted)).all());issues.extend(f"moderator role has no promotion audit: {uid}" for uid in unaudited_roles)
    deactivated=select(ModerationAction.id).where(ModerationAction.target_user_id==User.id,ModerationAction.action_type=="USER_DEACTIVATED").correlate(User).exists()
    unaudited_status=list(db.scalars(select(User.id).where(User.status=="DEACTIVATED",User.updated_at>User.created_at,~deactivated)).all());issues.extend(f"deactivation has no audit: {uid}" for uid in unaudited_status)
    return active_admins,issues
def main():
    db=SessionLocal()
    try:
        count,issues=check(db);print(f"Active Admins: {count}");print(f"Admin consistency: {len(issues)} issue(s)");[print(f"- {issue}") for issue in issues];return 1 if issues else 0
    finally:db.close()
if __name__=="__main__":raise SystemExit(main())

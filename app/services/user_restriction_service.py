"""Canonical request-time user restriction enforcement."""
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database.models.moderation import UserRestriction
from app.database.models.auth import User
from app.database.types import utc_now
from app.services.post_service import PostError

ERRORS = {
    "COMMUNITY_POSTING": "COMMUNITY_POSTING_RESTRICTED",
    "COMMENTING": "COMMENTING_RESTRICTED", "REACTING": "REACTION_RESTRICTED",
    "SHARING": "SHARING_RESTRICTED", "MEDIA_UPLOAD": "MEDIA_UPLOAD_RESTRICTED",
    "COMMUNITY_ACCESS": "COMMUNITY_ACCESS_RESTRICTED", "ACCOUNT": "ACCOUNT_RESTRICTED",
}

def effective_active_predicate(*,now=None,user_id=None,scope=None):
    now=now or utc_now()
    clauses=[UserRestriction.status=="ACTIVE",UserRestriction.starts_at<=now,
             or_(UserRestriction.ends_at.is_(None),UserRestriction.ends_at>now)]
    if user_id is not None:clauses.append(UserRestriction.user_id==user_id)
    if scope is not None:clauses.append(UserRestriction.restriction_type==scope)
    return tuple(clauses)


def get_active_restrictions(db: Session, user_id: str, *, now=None, lock=False):
    now = now or utc_now()
    stmt = select(UserRestriction).where(*effective_active_predicate(now=now,user_id=user_id))
    if lock:
        stmt = stmt.with_for_update(of=UserRestriction)
    return list(db.scalars(stmt).all())


def assert_allowed(db: Session, user_id: str, scope: str, *, lock_subject: bool = False) -> None:
    # The user row is the shared serialization boundary with restriction
    # creation. Locking it makes commit order define restriction-vs-action
    # outcomes even when no restriction row existed at check time.
    if lock_subject:
        db.execute(select(User.id).where(User.id == user_id).with_for_update(of=User))
    active = {row.restriction_type for row in get_active_restrictions(db, user_id)}
    blocked = "ACCOUNT" in active or "COMMUNITY_ACCESS" in active or scope in active
    if blocked:
        actual = "ACCOUNT" if "ACCOUNT" in active else ("COMMUNITY_ACCESS" if "COMMUNITY_ACCESS" in active else scope)
        raise PostError(ERRORS[actual], "This Community action is currently restricted.")


def assert_can_create_post(db, user_id): assert_allowed(db, user_id, "COMMUNITY_POSTING", lock_subject=True)
def assert_can_comment(db, user_id): assert_allowed(db, user_id, "COMMENTING", lock_subject=True)
def assert_can_react(db, user_id): assert_allowed(db, user_id, "REACTING", lock_subject=True)
def assert_can_share(db, user_id): assert_allowed(db, user_id, "SHARING", lock_subject=True)
def assert_can_upload_media(db, user_id): assert_allowed(db, user_id, "MEDIA_UPLOAD", lock_subject=True)
def assert_can_access_community(db, user_id): assert_allowed(db, user_id, "COMMUNITY_ACCESS")

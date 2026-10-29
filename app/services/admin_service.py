"""Transactional Admin account discovery and role management."""
from __future__ import annotations

import base64
import json
from datetime import datetime,timedelta

from sqlalchemy import and_, func, or_, select,text
from sqlalchemy.orm import Session

from app.database.models.auth import User
from app.database.models.job import ProcessingJob, WorkerHeartbeat
from app.database.models.moderation import ContentReport, ModerationAction, UserRestriction
from app.database.models.social import Post, PostComment
from app.database.types import utc_now
from app.services.moderation_service import audit
from app.services.post_service import PostError
from app.services.user_restriction_service import effective_active_predicate
from app.services.admin_target_policy import assert_governance_target
from config import settings

def _redis_health():
    try:
        import redis
        client=redis.Redis.from_url(settings.redis_url,socket_connect_timeout=min(settings.redis_connect_timeout_seconds,1),socket_timeout=1)
        return "HEALTHY" if client.ping() else "UNAVAILABLE"
    except Exception:
        return "UNAVAILABLE"


def _cursor_encode(created_at, user_id):
    raw=json.dumps([created_at.isoformat(),user_id],separators=(",",":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _cursor_decode(value):
    try:
        raw=base64.urlsafe_b64decode(value+"="*(-len(value)%4)); created,user_id=json.loads(raw)
        return datetime.fromisoformat(created),user_id
    except Exception as exc:
        raise PostError("INVALID_ADMIN_CURSOR","Admin cursor is invalid.") from exc


def dashboard(db: Session):
    db.execute(text("SELECT 1"))
    user_counts=dict(db.execute(select(User.status,func.count(User.id)).group_by(User.status)).all())
    role_counts=dict(db.execute(select(User.role,func.count(User.id)).group_by(User.role)).all())
    report_counts=dict(db.execute(select(ContentReport.status,func.count(ContentReport.id)).group_by(ContentReport.status)).all()) if settings.enable_community_features else {}
    active_restrictions=(db.scalar(select(func.count(UserRestriction.id)).where(*effective_active_predicate())) or 0) if settings.enable_community_features else 0
    published_posts=(db.scalar(select(func.count(Post.id)).where(Post.status=="PUBLISHED",Post.deleted_at.is_(None))) or 0) if settings.enable_community_features else 0
    job_counts=dict(db.execute(select(ProcessingJob.status,func.count(ProcessingJob.id)).group_by(ProcessingJob.status)).all())
    cutoff=utc_now()-timedelta(seconds=max(settings.worker_heartbeat_seconds*3,30))
    fresh_workers=db.scalar(select(func.count(WorkerHeartbeat.worker_id)).where(WorkerHeartbeat.status.in_(["READY","BUSY"]),WorkerHeartbeat.last_heartbeat_at>=cutoff)) or 0
    stale_workers=db.scalar(select(func.count(WorkerHeartbeat.worker_id)).where(WorkerHeartbeat.status.in_(["READY","BUSY"]),WorkerHeartbeat.last_heartbeat_at<cutoff)) or 0
    redis_health=_redis_health();worker_health="HEALTHY" if fresh_workers else ("DEGRADED" if stale_workers else "UNAVAILABLE")
    queue_health="HEALTHY" if redis_health=="HEALTHY" and fresh_workers else ("DEGRADED" if fresh_workers or stale_workers else "UNAVAILABLE")
    return {"users":{"total":sum(user_counts.values()),"active":user_counts.get("ACTIVE",0),"deactivated":user_counts.get("DEACTIVATED",0),"moderators":role_counts.get("MODERATOR",0),"admins":role_counts.get("ADMIN",0)},"community":{"published_posts":published_posts,"open_reports":report_counts.get("OPEN",0)},"moderation":{"under_review_reports":report_counts.get("UNDER_REVIEW",0),"active_restrictions":active_restrictions},"system":{"database":"HEALTHY","redis":redis_health,"worker":worker_health,"job_queue":queue_health,"active_workers":fresh_workers,"failed_jobs":job_counts.get("FAILED",0),"queued_jobs":job_counts.get("PENDING",0)+job_counts.get("QUEUED",0)}}


def list_users(db:Session,*,search=None,role=None,status=None,cursor=None,limit=20):
    active_count=select(func.count(UserRestriction.id)).where(*effective_active_predicate(user_id=User.id)).correlate(User).scalar_subquery()
    stmt=select(User,active_count.label("active_restriction_count"))
    if search:
        term=f"%{search.strip()}%"; stmt=stmt.where(or_(User.display_name.ilike(term),User.normalized_username.ilike(term.lower()),User.normalized_email.ilike(term.lower())))
    if role: stmt=stmt.where(User.role==role)
    if status: stmt=stmt.where(User.status==status)
    if cursor:
        created,user_id=_cursor_decode(cursor); stmt=stmt.where(or_(User.created_at<created,and_(User.created_at==created,User.id<user_id)))
    rows=db.execute(stmt.order_by(User.created_at.desc(),User.id.desc()).limit(min(max(limit,1),50)+1)).all()
    more=len(rows)>limit; rows=rows[:limit]
    return rows,(_cursor_encode(rows[-1][0].created_at,rows[-1][0].id) if more and rows else None)


def user_detail(db:Session,user_id:str):
    user=db.get(User,user_id)
    if not user: raise PostError("ADMIN_USER_NOT_FOUND","User not found.")
    restrictions=list(db.scalars(select(UserRestriction).where(UserRestriction.user_id==user_id).order_by(UserRestriction.created_at.desc()).limit(20)).all())
    actions=list(db.scalars(select(ModerationAction).where(ModerationAction.target_user_id==user_id).order_by(ModerationAction.created_at.desc()).limit(20)).all())
    if settings.enable_community_features:
        community=db.execute(select(func.count(Post.id),func.coalesce(func.sum(Post.comment_count),0),func.coalesce(func.sum(Post.share_count),0)).where(Post.author_user_id==user_id,Post.status=="PUBLISHED",Post.deleted_at.is_(None))).one()
        authored_comments=db.scalar(select(func.count(PostComment.id)).where(PostComment.author_user_id==user_id,PostComment.status=="VISIBLE")) or 0
        comm_data={"published_posts":community[0],"comments_received":community[1],"shares_received":community[2],"comments_authored":authored_comments}
    else:
        comm_data={"published_posts":0,"comments_received":0,"shares_received":0,"comments_authored":0}
    return user,restrictions,actions,comm_data


def list_restrictions(db:Session,*,status=None,restriction_type=None,cursor=None,limit=20):
    stmt=select(UserRestriction)
    if status=="ACTIVE":stmt=stmt.where(*effective_active_predicate())
    elif status: stmt=stmt.where(UserRestriction.status==status)
    if restriction_type: stmt=stmt.where(UserRestriction.restriction_type==restriction_type)
    if cursor:
        created,row_id=_cursor_decode(cursor); stmt=stmt.where(or_(UserRestriction.created_at<created,and_(UserRestriction.created_at==created,UserRestriction.id<row_id)))
    rows=list(db.scalars(stmt.order_by(UserRestriction.created_at.desc(),UserRestriction.id.desc()).limit(limit+1)).all())
    more=len(rows)>limit; rows=rows[:limit]
    return rows,(_cursor_encode(rows[-1].created_at,rows[-1].id) if more and rows else None)


def list_audit_actions(db:Session,*,action_type=None,target_user_id=None,cursor=None,limit=20):
    stmt=select(ModerationAction)
    if action_type: stmt=stmt.where(ModerationAction.action_type==action_type)
    if target_user_id: stmt=stmt.where(ModerationAction.target_user_id==target_user_id)
    if cursor:
        created,row_id=_cursor_decode(cursor); stmt=stmt.where(or_(ModerationAction.created_at<created,and_(ModerationAction.created_at==created,ModerationAction.id<row_id)))
    rows=list(db.scalars(stmt.order_by(ModerationAction.created_at.desc(),ModerationAction.id.desc()).limit(limit+1)).all())
    more=len(rows)>limit; rows=rows[:limit]
    return rows,(_cursor_encode(rows[-1].created_at,rows[-1].id) if more and rows else None)


def change_moderator_role(db:Session,target_id:str,actor:User,*,promote:bool,reason:str):
    target=db.scalar(select(User).where(User.id==target_id).with_for_update(of=User))
    if not target: raise PostError("ADMIN_USER_NOT_FOUND","User not found.")
    assert_governance_target(actor,target,"role_change")
    expected,new_role,event=("USER","MODERATOR","USER_PROMOTED_TO_MODERATOR") if promote else ("MODERATOR","USER","USER_DEMOTED_FROM_MODERATOR")
    if target.role!=expected: raise PostError("INVALID_ROLE_TRANSITION",f"User must currently be {expected.title()}.")
    if promote and target.status!="ACTIVE": raise PostError("USER_DEACTIVATED","A deactivated user cannot be promoted.")
    target.role=new_role; target.updated_at=utc_now()
    audit(db,actor.id,event,"USER",target.id,target_user_id=target.id,note=reason,metadata={"from_role":expected,"to_role":new_role})
    db.commit();db.refresh(target);return target

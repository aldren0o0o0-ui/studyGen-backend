"""Phase 12A database-authoritative Admin APIs."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.models.auth import User
from app.database.models.moderation import ModerationAction, UserRestriction
from app.dependencies import get_current_user, get_current_user_model, get_db
from app.schemas.admin import AdminActionRequest, AdminRoleResult, AdminUserItem, AdminUserPage,AdminUserDetail,AdminRestrictionPage,AdminActionPage
from app.database.types import utc_now
from app.database.models.moderation import ACTION_TYPES,RESTRICTION_TYPES
from typing import Literal
from app.services import admin_service
from app.services.moderation_permissions import require_permission
from app.services.post_service import PostError

router=APIRouter(prefix="/admin",tags=["admin"],dependencies=[Depends(get_current_user)])
def _require(user,permission):
    try:return require_permission(user,permission)
    except HTTPException:raise HTTPException(403,{"code":"ADMIN_PERMISSION_REQUIRED","message":"Administrator permission is required."})
def _fail(exc):raise HTTPException(404 if exc.code=="ADMIN_USER_NOT_FOUND" else 409,{"code":exc.code,"message":str(exc)})
def _user(row,count=0):return AdminUserItem(user_id=row.id,display_name=row.display_name,username=row.username,email=row.email,role=row.role,status=row.status,created_at=row.created_at,last_login_at=row.last_login_at,profile_image_url=f"/community/users/{row.id}/image" if row.profile_image_key else None,active_restriction_count=count)
def _restriction(row):return {"restriction_id":row.id,"user_id":row.user_id,"restriction_type":row.restriction_type,"status":row.status,"reason":row.reason,"starts_at":row.starts_at,"ends_at":row.ends_at,"created_by_user_id":row.created_by_user_id,"created_at":row.created_at,"revoked_at":row.revoked_at,"revoked_by_user_id":row.revoked_by_user_id,"revocation_reason":row.revocation_reason}
def _action(row):return {"action_id":row.id,"actor_user_id":row.actor_user_id,"action_type":row.action_type,"target_type":row.target_type,"target_id":row.target_id,"target_user_id":row.target_user_id,"reason_code":row.reason_code,"note":row.note,"metadata":row.metadata_json,"created_at":row.created_at}

@router.get("/dashboard")
def dashboard(db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):_require(user,"admin.dashboard.view");return admin_service.dashboard(db)

@router.get("/users",response_model=AdminUserPage)
def users(q:str|None=Query(None,max_length=100),search:str|None=Query(None,max_length=100),role:str|None=None,status:str|None=None,cursor:str|None=None,limit:int=Query(20,ge=1,le=50),db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):
    _require(user,"admin.users.view")
    if role and role not in {"USER","MODERATOR","ADMIN"}:raise HTTPException(422,{"code":"INVALID_ROLE_FILTER","message":"Role filter is invalid."})
    if status and status not in {"ACTIVE","LOCKED","DEACTIVATED"}:raise HTTPException(422,{"code":"INVALID_STATUS_FILTER","message":"Status filter is invalid."})
    try:rows,next_cursor=admin_service.list_users(db,search=q or search,role=role,status=status,cursor=cursor,limit=limit)
    except PostError as exc:_fail(exc)
    return AdminUserPage(items=[_user(row,count) for row,count in rows],next_cursor=next_cursor)

@router.get("/users/{user_id}",response_model=AdminUserDetail)
def user_detail(user_id:str,db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):
    _require(user,"admin.users.view")
    try:target,restrictions,actions,community=admin_service.user_detail(db,user_id)
    except PostError as exc:_fail(exc)
    now=utc_now()
    count=sum(row.status=="ACTIVE" and row.starts_at<=now and (row.ends_at is None or row.ends_at>now) for row in restrictions)
    return {"user":_user(target,count),"account":{"password_changed_at":target.password_changed_at,"failed_login_attempts":target.failed_login_attempts},"community":community,"restrictions":[_restriction(row) for row in restrictions],"actions":[_action(row) for row in actions],"self":target.id==user.id}

@router.post("/users/{user_id}/promote-moderator",response_model=AdminRoleResult)
def promote(user_id:str,payload:AdminActionRequest,db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):
    _require(user,"admin.roles.manage_moderator")
    try:target=admin_service.change_moderator_role(db,user_id,user,promote=True,reason=payload.reason)
    except PostError as exc:_fail(exc)
    return AdminRoleResult(user_id=target.id,role=target.role,status=target.status)

@router.post("/users/{user_id}/demote-moderator",response_model=AdminRoleResult)
def demote(user_id:str,payload:AdminActionRequest,db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):
    _require(user,"admin.roles.manage_moderator")
    try:target=admin_service.change_moderator_role(db,user_id,user,promote=False,reason=payload.reason)
    except PostError as exc:_fail(exc)
    return AdminRoleResult(user_id=target.id,role=target.role,status=target.status)

@router.get("/restrictions",response_model=AdminRestrictionPage)
def restrictions(status:Literal["ACTIVE","EXPIRED","REVOKED"]|None=None,restriction_type:Literal["COMMUNITY_POSTING","COMMENTING","REACTING","SHARING","MEDIA_UPLOAD","COMMUNITY_ACCESS","ACCOUNT"]|None=None,cursor:str|None=None,limit:int=Query(20,ge=1,le=50),db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):
    _require(user,"admin.restrictions.view")
    try:rows,next_cursor=admin_service.list_restrictions(db,status=status,restriction_type=restriction_type,cursor=cursor,limit=limit)
    except PostError as exc:_fail(exc)
    return {"items":[_restriction(row) for row in rows],"next_cursor":next_cursor}

@router.get("/audit",response_model=AdminActionPage)
def audit_actions(action_type:Literal["REPORT_CREATED","REPORT_ASSIGNED","REPORT_UNASSIGNED","REPORT_REVIEW_STARTED","REPORT_DISMISSED","REPORT_RESOLVED","POST_HIDDEN","POST_REMOVED","POST_RESTORED","COMMENT_HIDDEN","COMMENT_REMOVED","COMMENT_RESTORED","USER_RESTRICTED","RESTRICTION_REVOKED","USER_DEACTIVATED","USER_REACTIVATED","USER_PROMOTED_TO_MODERATOR","USER_DEMOTED_FROM_MODERATOR"]|None=None,target_user_id:str|None=None,cursor:str|None=None,limit:int=Query(20,ge=1,le=50),db:Session=Depends(get_db),user:User=Depends(get_current_user_model)):
    _require(user,"admin.audit.view")
    try:rows,next_cursor=admin_service.list_audit_actions(db,action_type=action_type,target_user_id=target_user_id,cursor=cursor,limit=limit)
    except PostError as exc:_fail(exc)
    return {"items":[_action(row) for row in rows],"next_cursor":next_cursor}

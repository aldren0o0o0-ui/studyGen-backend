from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminActionRequest(StrictAdminRequest):
    reason: str = Field(min_length=1, max_length=2000)


class AdminUserItem(BaseModel):
    user_id: str
    display_name: str
    username: str | None
    email: str
    role: Literal["USER", "MODERATOR", "ADMIN"]
    status: str
    created_at: datetime
    last_login_at: datetime | None
    profile_image_url: str | None
    active_restriction_count: int = 0


class AdminUserPage(BaseModel):
    items: list[AdminUserItem]
    next_cursor: str | None = None


class AdminRoleResult(BaseModel):
    user_id: str
    role: str
    status: str

class AdminAccountInfo(BaseModel):
    password_changed_at: datetime
    failed_login_attempts: int

class AdminCommunitySummary(BaseModel):
    published_posts:int;comments_received:int;shares_received:int;comments_authored:int

class AdminRestriction(BaseModel):
    restriction_id:str;user_id:str
    restriction_type:Literal["COMMUNITY_POSTING","COMMENTING","REACTING","SHARING","MEDIA_UPLOAD","COMMUNITY_ACCESS","ACCOUNT"]
    status:Literal["ACTIVE","EXPIRED","REVOKED"]
    reason:str;starts_at:datetime;ends_at:datetime|None;created_by_user_id:str;created_at:datetime
    revoked_at:datetime|None;revoked_by_user_id:str|None;revocation_reason:str|None

class AdminAction(BaseModel):
    action_id:str;actor_user_id:str;action_type:str;target_type:str;target_id:str
    target_user_id:str|None;reason_code:str|None;note:str|None;metadata:dict[str,Any]|None;created_at:datetime

class AdminUserDetail(BaseModel):
    user:AdminUserItem;account:AdminAccountInfo;community:AdminCommunitySummary
    restrictions:list[AdminRestriction];actions:list[AdminAction];self:bool

class AdminRestrictionPage(BaseModel):items:list[AdminRestriction];next_cursor:str|None=None
class AdminActionPage(BaseModel):items:list[AdminAction];next_cursor:str|None=None

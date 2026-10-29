from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReportType = Literal["POST", "COMMENT"]
ReportReason = Literal["SPAM", "HARASSMENT", "HATE_OR_ABUSE", "SEXUAL_OR_EXPLICIT_CONTENT", "VIOLENCE_OR_THREATS", "MISINFORMATION", "PRIVACY_VIOLATION", "COPYRIGHT_OR_UNAUTHORIZED_CONTENT", "IMPERSONATION", "OTHER"]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportCreate(StrictRequest):
    resource_type: ReportType
    resource_id: str = Field(min_length=1, max_length=36)
    reason: ReportReason
    note: str | None = Field(default=None, max_length=2000)


class ReportAck(BaseModel):
    report_id: str
    status: str
    created_at: datetime


class AssignRequest(StrictRequest):
    moderator_user_id: str | None = None


class ResolutionRequest(StrictRequest):
    resolution_code: Literal["NO_VIOLATION", "CONTENT_HIDDEN", "CONTENT_REMOVED", "COMMENT_REMOVED", "USER_RESTRICTED", "USER_DEACTIVATED", "OTHER_ACTION"] = "NO_VIOLATION"
    resolution_note: str | None = Field(default=None, max_length=2000)


class ModerationNote(StrictRequest):
    reason_code: str | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    source_report_id: str | None = None


class RestrictionCreate(StrictRequest):
    restriction_type: Literal["COMMUNITY_POSTING", "COMMENTING", "REACTING", "SHARING", "MEDIA_UPLOAD", "COMMUNITY_ACCESS", "ACCOUNT"]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    reason: str = Field(min_length=1, max_length=2000)
    source_report_id: str | None = None

    @model_validator(mode="after")
    def period(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class RevokeRestriction(StrictRequest):
    revocation_reason: str = Field(min_length=1, max_length=2000)


class AccountAction(StrictRequest):
    reason: str = Field(min_length=1, max_length=2000)
    source_report_id: str | None = None

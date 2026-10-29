"""Phase 11A public profile and post API contracts."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Visibility = Literal["PUBLIC", "SPECIFIC_USERS", "PRIVATE"]


class CommunityUserSummary(BaseModel):
    user_id: str
    username: str
    display_name: str
    profile_image_url: Optional[str] = None


class CommunityProfileResponse(CommunityUserSummary):
    bio: Optional[str] = None
    joined_at: datetime


class CommunityProfileUpdate(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    bio: Optional[str] = None


class PostCreate(BaseModel):
    caption: Optional[str] = None
    visibility: Visibility = "PUBLIC"
    recipient_user_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_comments: bool = True


class PostUpdate(BaseModel):
    caption: Optional[str] = None
    visibility: Optional[Visibility] = None
    recipient_user_ids: Optional[list[str]] = Field(None, max_length=100)
    allow_comments: Optional[bool] = None


class PostMediaResponse(BaseModel):
    media_id: str
    media_type: str
    filename: str
    mime_type: str
    file_size: int
    position: int
    processing_status: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[int] = None
    url: Optional[str] = None


class PostResponse(BaseModel):
    post_id: str
    author: CommunityUserSummary
    caption: Optional[str]
    post_type: str
    visibility: str
    status: str
    allow_comments: bool
    published_at: Optional[datetime]
    edited_at: Optional[datetime]
    created_at: datetime
    media: list[PostMediaResponse]
    reaction_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    save_count: int = 0
    current_user_reaction: Optional[str] = None
    is_saved: bool = False
    shared_post: Optional["SharedPostPreview"] = None


class PostDetailResponse(PostResponse):
    recipients: list[CommunityUserSummary] = Field(default_factory=list)


class PostFeedResponse(BaseModel):
    items: list[PostResponse]
    next_cursor: Optional[str] = None


class PostDeleteResponse(BaseModel):
    post_id: str
    status: str


class CommunityDashboardResponse(BaseModel):
    published_posts: int
    hearts_received: int
    comments_received: int
    shares_received: int


ReactionType = Literal["LIKE", "HELPFUL", "INSIGHTFUL"]


class SharedPostPreview(BaseModel):
    post_id: str
    author: CommunityUserSummary
    caption: Optional[str] = None
    post_type: str
    media: list[PostMediaResponse] = Field(default_factory=list)
    available: bool = True


class ReactionRequest(BaseModel):
    reaction_type: ReactionType


class ReactionResponse(BaseModel):
    post_id: str
    current_user_reaction: Optional[str]
    reaction_count: int


class CommentCreate(BaseModel):
    content: str


class CommentUpdate(BaseModel):
    content: str


class CommentResponse(BaseModel):
    comment_id: str
    post_id: str
    author: CommunityUserSummary
    parent_comment_id: Optional[str]
    content: Optional[str]
    status: str
    created_at: datetime
    edited_at: Optional[datetime]
    reply_count: int = 0
    replies: list["CommentResponse"] = Field(default_factory=list)


class CommentPageResponse(BaseModel):
    items: list[CommentResponse]
    next_cursor: Optional[str] = None


class SaveResponse(BaseModel):
    post_id: str
    is_saved: bool
    save_count: int


class ShareRequest(BaseModel):
    caption: Optional[str] = None
    visibility: Visibility = "PRIVATE"
    recipient_user_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_comments: bool = True


PostResponse.model_rebuild()
CommentResponse.model_rebuild()

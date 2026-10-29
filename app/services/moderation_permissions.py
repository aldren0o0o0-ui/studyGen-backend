"""Explicit Phase 11D RBAC; role names are not permission checks."""
from fastapi import HTTPException

MODERATOR_PERMISSIONS = {
    "moderation.report.view", "moderation.report.assign", "moderation.report.resolve",
    "moderation.post.hide", "moderation.post.remove", "moderation.post.restore",
    "moderation.comment.hide", "moderation.comment.remove", "moderation.comment.restore",
    "moderation.user.restrict", "moderation.user.restriction_revoke",
    "moderation.audit.view",
}
ADMIN_ONLY = {"moderation.user.deactivate", "moderation.user.reactivate"}
ADMIN_PERMISSIONS = {
    "admin.dashboard.view", "admin.users.view", "admin.users.search",
    "admin.users.manage_status", "admin.roles.view", "admin.roles.manage_moderator",
    "admin.restrictions.view", "admin.restrictions.manage", "admin.moderation.view",
    "admin.audit.view", "admin.system_health.view",
}
ROLE_PERMISSIONS = {
    "USER": {"community.report.create"},
    "MODERATOR": {"community.report.create", *MODERATOR_PERMISSIONS},
    "ADMIN": {"community.report.create", *MODERATOR_PERMISSIONS, *ADMIN_ONLY, *ADMIN_PERMISSIONS},
}


def has_permission(user, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(getattr(user, "role", "USER"), set())


def require_permission(user, permission: str):
    if not has_permission(user, permission):
        raise HTTPException(403, {"code": "MODERATION_PERMISSION_REQUIRED", "message": "This moderation permission is required."})
    return user

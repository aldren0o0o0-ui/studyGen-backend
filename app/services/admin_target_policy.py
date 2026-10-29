"""Central actor/target governance policy for account-impacting actions."""
from app.services.post_service import PostError

def assert_governance_target(actor,target,action:str):
    if actor.id == target.id:
        raise PostError("ADMIN_SELF_MUTATION_NOT_ALLOWED","You cannot perform this action on yourself.")
    if actor.role == "MODERATOR" and target.role != "USER":
        raise PostError("TARGET_ROLE_PROTECTED","Moderators may manage ordinary users only.")
    if actor.role == "ADMIN" and target.role == "ADMIN":
        raise PostError("PEER_ADMIN_PROTECTED","Peer Admin lifecycle actions are not allowed.")
    if actor.role not in {"MODERATOR","ADMIN"}:
        raise PostError("MODERATION_PERMISSION_REQUIRED","Administrative permission is required.")

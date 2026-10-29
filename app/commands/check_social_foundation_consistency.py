"""Dry-run Phase 11A consistency checker; --repair removes stale recipients only."""
import argparse
from pathlib import Path

from sqlalchemy import func, select

from app.database.models.auth import User
from app.database.models.social import Post, PostAllowedUser, PostMedia
from app.database.session import session_scope
from app.services.community_profile_service import normalize_username
from config import settings


def check(repair: bool = False) -> list[str]:
    issues: list[str] = []
    with session_scope() as db:
        posts = list(db.scalars(select(Post)).all())
        for post in posts:
            recipients = list(db.scalars(select(PostAllowedUser).where(PostAllowedUser.post_id == post.id)).all())
            if post.visibility == "SPECIFIC_USERS" and not recipients and post.deleted_at is None:
                issues.append(f"SPECIFIC_WITHOUT_RECIPIENT post={post.id}")
            if post.visibility != "SPECIFIC_USERS" and recipients:
                issues.append(f"STALE_RECIPIENTS post={post.id}")
                if repair:
                    for row in recipients: db.delete(row)
            if post.deleted_at and post.status != "DELETED": issues.append(f"DELETED_STILL_ACTIVE post={post.id}")
        for media in db.scalars(select(PostMedia).where(PostMedia.deleted_at.is_(None))):
            path = (Path(settings.document_storage_root) / media.storage_key).resolve()
            if not path.is_file(): issues.append(f"MISSING_MEDIA_FILE media={media.id}")
        for user in db.scalars(select(User).where(User.normalized_username.is_not(None))):
            try:
                if normalize_username(user.username or "") != user.normalized_username:
                    issues.append(f"INVALID_NORMALIZED_USERNAME user={user.id}")
            except ValueError:
                issues.append(f"INVALID_NORMALIZED_USERNAME user={user.id}")
    return issues


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--repair-stale-recipients", action="store_true")
    args = parser.parse_args(); issues = check(args.repair_stale_recipients)
    for issue in issues: print(issue)
    print(f"Social consistency issues: {len(issues)}")
    raise SystemExit(1 if issues else 0)


if __name__ == "__main__": main()

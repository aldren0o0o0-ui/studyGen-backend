"""Dry-run structural and counter audit for Phase 11B interactions."""
from sqlalchemy import func, select
from app.database.models.auth import User
from app.database.models.social import PostAllowedUser
from app.database.models.social import Post, PostComment, PostReaction, SavedPost
from app.database.session import session_scope
from app.services.social_counter_service import rebuild


def check():
    issues = []
    with session_scope() as db:
        posts = {row.id: row for row in db.scalars(select(Post))}
        users = set(db.scalars(select(User.id)))
        for post_id, user_id, count in db.execute(select(PostReaction.post_id, PostReaction.user_id, func.count()).group_by(PostReaction.post_id, PostReaction.user_id).having(func.count() > 1)):
            issues.append(f"DUPLICATE_REACTION post={post_id} user={user_id} count={count}")
        for user_id, post_id, count in db.execute(select(SavedPost.user_id, SavedPost.post_id, func.count()).group_by(SavedPost.user_id, SavedPost.post_id).having(func.count() > 1)):
            issues.append(f"DUPLICATE_SAVE post={post_id} user={user_id} count={count}")
        for row in db.scalars(select(PostReaction)):
            if row.post_id not in posts: issues.append(f"ORPHAN_REACTION reaction={row.id}")
            if row.user_id not in users: issues.append(f"MISSING_REACTION_USER reaction={row.id} user={row.user_id}")
        comments = {row.id: row for row in db.scalars(select(PostComment))}
        for row in comments.values():
            if row.post_id not in posts: issues.append(f"ORPHAN_COMMENT comment={row.id}")
            if row.author_user_id not in users: issues.append(f"MISSING_COMMENT_USER comment={row.id} user={row.author_user_id}")
            if row.parent_comment_id:
                parent = comments.get(row.parent_comment_id)
                if not parent or parent.post_id != row.post_id or parent.parent_comment_id:
                    issues.append(f"INVALID_REPLY_PARENT comment={row.id}")
        for row in db.scalars(select(SavedPost)):
            if row.post_id not in posts: issues.append(f"ORPHAN_SAVE save={row.id}")
            if row.user_id not in users: issues.append(f"MISSING_SAVE_USER save={row.id} user={row.user_id}")
        for post in posts.values():
            if post.shared_post_id and post.shared_post_id not in posts: issues.append(f"MISSING_SHARE_ORIGINAL post={post.id}")
            if post.shared_post_id == post.id: issues.append(f"SELF_SHARE post={post.id}")
            if post.shared_post_id and post.shared_post_id in posts:
                original = posts[post.shared_post_id]
                if original.shared_post_id: issues.append(f"SHARE_CHAIN post={post.id} original={original.id}")
                if original.visibility == "PRIVATE" or (original.visibility == "SPECIFIC_USERS" and post.visibility == "PUBLIC"):
                    issues.append(f"SHARE_VISIBILITY_ESCALATION post={post.id} original={original.id}")
                if original.visibility == "SPECIFIC_USERS" and post.visibility == "SPECIFIC_USERS":
                    original_allowed = set(db.scalars(select(PostAllowedUser.allowed_user_id).where(PostAllowedUser.post_id == original.id))) | {original.author_user_id, post.author_user_id}
                    for recipient in db.scalars(select(PostAllowedUser.allowed_user_id).where(PostAllowedUser.post_id == post.id)):
                        if recipient not in original_allowed: issues.append(f"SHARE_RECIPIENT_REVOKED post={post.id} user={recipient}")
            if post.author_user_id not in users: issues.append(f"MISSING_POST_AUTHOR post={post.id} user={post.author_user_id}")
            if min(post.reaction_count, post.comment_count, post.share_count, post.save_count) < 0: issues.append(f"NEGATIVE_COUNTER post={post.id}")
        for post_id, counter, stored, expected in rebuild(db):
            issues.append(f"COUNTER_MISMATCH post={post_id} counter={counter} stored={stored} expected={expected}")
    return issues


def main():
    issues = check()
    for issue in issues: print(issue)
    print(f"Social interaction consistency issues: {len(issues)}")
    raise SystemExit(1 if issues else 0)


if __name__ == "__main__": main()

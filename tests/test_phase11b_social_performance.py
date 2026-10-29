"""Explicit disposable-PostgreSQL representative social dataset and plan checks."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import create_engine,text

pytestmark=[pytest.mark.postgres_integration,pytest.mark.social_performance]


def test_representative_social_dataset_and_index_plans():
    if os.getenv("ENABLE_SOCIAL_PERF_TESTS")!="1":pytest.skip("Set ENABLE_SOCIAL_PERF_TESTS=1 explicitly")
    url=os.getenv("TEST_POSTGRES_DATABASE_URL")
    if not url or "test" not in url.lower():pytest.fail("Performance generator refuses a non-test database URL")
    engine=create_engine(url,pool_pre_ping=True);users_n=max(200,int(os.getenv("SOCIAL_PERF_USERS","200")));posts_n=int(os.getenv("SOCIAL_PERF_POSTS","2000"));reports_n=int(os.getenv("SOCIAL_PERF_REPORTS","2000"));actions_n=int(os.getenv("SOCIAL_PERF_ACTIONS","5000"));restrictions_n=int(os.getenv("SOCIAL_PERF_RESTRICTIONS","1000"));now="2026-08-14T00:00:00+00:00"
    users=[str(uuid4()) for _ in range(users_n)];posts=[str(uuid4()) for _ in range(posts_n)];shares=[str(uuid4()) for _ in range(min(1000,posts_n//2))]
    emails=[f"perf-{uid}@social-perf.test" for uid in users]
    print(f"Social performance target: {engine.url.database}; users={users_n}; posts={posts_n}; reports={reports_n}; actions={actions_n}; restrictions={restrictions_n}")
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users(id,email,normalized_email,password_hash,display_name,status,password_changed_at,created_at,updated_at) VALUES(:id,:email,:email,'x','Perf','ACTIVE',:now,:now,:now)"),[{"id":uid,"email":email,"now":now} for uid,email in zip(users,emails)])
            conn.execute(text("UPDATE users SET role='MODERATOR' WHERE id=ANY(:ids)"),{"ids":users[:5]})
            post_rows=[]
            for i,pid in enumerate(posts):
                visibility="PUBLIC" if i%10<6 else ("SPECIFIC_USERS" if i%10<9 else "PRIVATE")
                post_rows.append({"id":pid,"user":users[i%users_n],"visibility":visibility,"now":now})
            conn.execute(text("INSERT INTO posts(id,author_user_id,caption,post_type,visibility,status,allow_comments,published_at,created_at,updated_at) VALUES(:id,:user,'performance','TEXT',:visibility,'PUBLISHED',true,:now,:now,:now)"),post_rows)
            specific=[pid for i,pid in enumerate(posts) if i%10 in (6,7,8)]
            recipients=[]
            for i,pid in enumerate(specific):
                for offset in range(8):recipients.append({"id":str(uuid4()),"post":pid,"user":users[(i+offset+1)%users_n],"now":now})
            conn.execute(text("INSERT INTO post_allowed_users(id,post_id,allowed_user_id,created_at) VALUES(:id,:post,:user,:now)"),recipients)
            reaction_n=min(10000,posts_n*min(5,users_n));reaction_rows=[]
            for i in range(reaction_n):reaction_rows.append({"id":str(uuid4()),"post":posts[i%posts_n],"user":users[(i//posts_n)%users_n],"now":now})
            conn.execute(text("INSERT INTO post_reactions(id,post_id,user_id,reaction_type,created_at,updated_at) VALUES(:id,:post,:user,'HELPFUL',:now,:now)"),reaction_rows)
            save_n=min(5000,posts_n*min(3,users_n));save_rows=[]
            base_time=datetime(2026,8,14,tzinfo=timezone.utc)
            for i in range(save_n):save_rows.append({"id":str(uuid4()),"post":posts[i%posts_n],"user":users[(i//posts_n)%users_n],"created":base_time+timedelta(seconds=i)})
            conn.execute(text("INSERT INTO saved_posts(id,user_id,post_id,created_at) VALUES(:id,:user,:post,:created)"),save_rows)
            comment_n=min(10000,posts_n*5);comment_rows=[{"id":str(uuid4()),"post":posts[i%posts_n],"user":users[i%users_n],"now":now} for i in range(comment_n)]
            conn.execute(text("INSERT INTO post_comments(id,post_id,author_user_id,content,status,created_at,updated_at) VALUES(:id,:post,:user,'comment','VISIBLE',:now,:now)"),comment_rows)
            public=[pid for i,pid in enumerate(posts) if i%10<6]
            share_rows=[{"id":sid,"user":users[(i+1)%users_n],"original":public[i%len(public)],"now":now} for i,sid in enumerate(shares)]
            conn.execute(text("INSERT INTO posts(id,author_user_id,caption,post_type,visibility,status,allow_comments,published_at,created_at,updated_at,shared_post_id) VALUES(:id,:user,'shared','TEXT','PUBLIC','PUBLISHED',true,:now,:now,:now,:original)"),share_rows)
            report_ids=[str(uuid4()) for _ in range(reports_n)]
            report_rows=[{"id":rid,"reporter":users[(i% (users_n-1))+1],"post":posts[i%posts_n],"author":users[(i%posts_n)%users_n],"reason":"SPAM" if i%3 else "HARASSMENT","priority":"HIGH" if i%10==0 else "NORMAL","created":base_time+timedelta(seconds=i)} for i,rid in enumerate(report_ids)]
            conn.execute(text("INSERT INTO content_reports(id,reporter_user_id,resource_type,resource_id,reported_user_id,reason,status,priority,resource_status_at_report,resource_created_at,created_at,updated_at) VALUES(:id,:reporter,'POST',:post,:author,:reason,'OPEN',:priority,'PUBLISHED',:created,:created,:created)"),report_rows)
            action_rows=[{"id":str(uuid4()),"actor":users[i%5],"target":posts[i%posts_n],"user":users[(i%posts_n)%users_n],"report":report_ids[i%reports_n],"created":base_time+timedelta(seconds=i)} for i in range(actions_n)]
            conn.execute(text("INSERT INTO moderation_actions(id,actor_user_id,action_type,target_type,target_id,target_user_id,source_report_id,created_at) VALUES(:id,:actor,'REPORT_REVIEW_STARTED','POST',:target,:user,:report,:created)"),action_rows)
            scopes=['COMMENTING','REACTING','SHARING','COMMUNITY_POSTING','MEDIA_UPLOAD','COMMUNITY_ACCESS','ACCOUNT']
            restriction_rows=[{"id":str(uuid4()),"user":users[i%users_n],"scope":scopes[(i//users_n)%len(scopes)],"actor":users[i%5],"created":base_time+timedelta(seconds=i)} for i in range(restrictions_n)]
            conn.execute(text("INSERT INTO user_restrictions(id,user_id,restriction_type,status,reason,starts_at,created_by_user_id,created_at,updated_at) VALUES(:id,:user,:scope,'ACTIVE','performance',:created,:actor,:created,:created)"),restriction_rows)
        with engine.connect() as conn:
            plans={
                "feed":("SELECT id FROM posts WHERE status='PUBLISHED' ORDER BY published_at DESC,id DESC LIMIT 20","ix_posts_feed"),
                "saved":(f"SELECT post_id FROM saved_posts WHERE user_id='{users[0]}' ORDER BY created_at DESC LIMIT 20","ix_saved_posts_user_created"),
                "comments":(f"SELECT id FROM post_comments WHERE post_id='{posts[0]}' ORDER BY created_at,id LIMIT 20","ix_post_comments_post_created"),
                "recipient":(f"SELECT post_id FROM post_allowed_users WHERE allowed_user_id='{users[1]}'","ix_post_allowed_user_lookup"),
                "share":(f"SELECT id FROM posts WHERE shared_post_id='{public[0]}'","ix_posts_shared_post_id"),
                "moderation_queue":("SELECT id FROM content_reports WHERE status='OPEN' ORDER BY priority DESC,created_at,id LIMIT 20","ix_content_reports_queue"),
                "related_reports":(f"SELECT id FROM content_reports WHERE resource_type='POST' AND resource_id='{posts[0]}'","ix_content_reports_resource"),
                "active_restrictions":(f"SELECT id FROM user_restrictions WHERE user_id='{users[0]}' AND status='ACTIVE' AND starts_at<=NOW() AND (ends_at IS NULL OR ends_at>NOW())","ix_user_restrictions_active|uq_user_restrictions_active_scope"),
                "audit_target":(f"SELECT id FROM moderation_actions WHERE target_type='POST' AND target_id='{posts[0]}' ORDER BY created_at DESC","ix_moderation_actions_target"),
            }
            for name,(sql,index) in plans.items():
                plan="\n".join(row[0] for row in conn.execute(text("EXPLAIN (ANALYZE,COSTS OFF) "+sql)));print(f"[{name}]\n{plan}");assert any(candidate in plan for candidate in index.split("|"))
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM moderation_actions WHERE actor_user_id=ANY(:users)"),{"users":users});conn.execute(text("DELETE FROM user_restrictions WHERE user_id=ANY(:users)"),{"users":users});conn.execute(text("DELETE FROM content_reports WHERE reporter_user_id=ANY(:users)"),{"users":users})
            conn.execute(text("UPDATE posts SET shared_post_id=NULL WHERE author_user_id=ANY(:users)"),{"users":users});conn.execute(text("DELETE FROM posts WHERE author_user_id=ANY(:users)"),{"users":users});conn.execute(text("DELETE FROM users WHERE id=ANY(:users)"),{"users":users})
        engine.dispose()

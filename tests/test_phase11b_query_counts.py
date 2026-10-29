"""Bounded-query regression checks for Phase 11B feed, saved, and comments."""
import pytest
from sqlalchemy import event
from app.database.session import engine
from tests.test_phase11a_social import _configure, _draft, _publish

pytestmark=pytest.mark.social_interactions


def _select_count(call):
    statements=[]
    def before(_conn,_cursor,statement,_parameters,_context,_many):
        if statement.lstrip().upper().startswith("SELECT"):statements.append(statement)
    event.listen(engine,"before_cursor_execute",before)
    try:response=call()
    finally:event.remove(engine,"before_cursor_execute",before)
    return response,len(statements)


def test_feed_saved_and_comment_query_counts_are_bounded(client):
    _configure(client,"query_budget")
    posts=[_publish(client,_draft(client,caption=f"post {index}")["post_id"]) for index in range(5)]
    for post in posts:client.put(f"/posts/{post['post_id']}/save")
    for index in range(4):client.post(f"/posts/{posts[0]['post_id']}/comments",json={"content":f"comment {index}"})
    feed,feed_queries=_select_count(lambda:client.get("/posts/feed"));assert feed.status_code==200 and feed_queries<=10
    saved,saved_queries=_select_count(lambda:client.get("/community/saved"));assert saved.status_code==200 and saved_queries<=10
    comments,comment_queries=_select_count(lambda:client.get(f"/posts/{posts[0]['post_id']}/comments"));assert comments.status_code==200 and comment_queries<=10
    print(f"query-count feed={feed_queries} saved={saved_queries} comments={comment_queries}")

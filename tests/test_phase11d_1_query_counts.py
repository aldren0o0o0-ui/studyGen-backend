import pytest
from sqlalchemy import event
from app.database.session import SessionLocal,engine
from tests.test_phase11d_1_security import auth,post,user

pytestmark=pytest.mark.moderation

def count_selects(call):
    statements=[]
    def before(_conn,_cursor,statement,_parameters,_context,_many):
        if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
    event.listen(engine,'before_cursor_execute',before)
    try:return call(),len(statements)
    finally:event.remove(engine,'before_cursor_execute',before)

def test_queue_and_detail_select_counts_are_bounded(client):
    with SessionLocal() as db:
        author=user(db);moderator=user(db,'MODERATOR');target=post(db,author);reporters=[user(db) for _ in range(5)]
    ids=[]
    for reporter in reporters:
        response=client.post('/reports',headers=auth(reporter),json={'resource_type':'POST','resource_id':target.id,'reason':'SPAM'});assert response.status_code==201;ids.append(response.json()['report_id'])
    queue,queue_count=count_selects(lambda:client.get('/moderation/reports',headers=auth(moderator)));assert queue.status_code==200 and queue_count<=10
    detail,detail_count=count_selects(lambda:client.get('/moderation/reports/'+ids[0],headers=auth(moderator)));assert detail.status_code==200 and detail_count<=10
    print(f'moderation query-count queue={queue_count} detail={detail_count}')

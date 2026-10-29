import pytest
from sqlalchemy import event
from app.database.session import engine,SessionLocal
from app.services import admin_service
pytestmark=pytest.mark.admin
def selects(call):
    count=0
    def before(_conn,_cursor,statement,_parameters,_context,_many):
        nonlocal count
        if statement.lstrip().upper().startswith('SELECT'):count+=1
    event.listen(engine,'before_cursor_execute',before)
    try:call()
    finally:event.remove(engine,'before_cursor_execute',before)
    return count
def test_admin_query_budgets():
    with SessionLocal() as db:
        assert selects(lambda:admin_service.dashboard(db))<=10
        assert selects(lambda:admin_service.list_users(db,limit=20))<=8

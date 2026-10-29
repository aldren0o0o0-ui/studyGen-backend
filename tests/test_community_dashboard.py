"""Privacy-safe Community author dashboard metrics."""
import uuid

import pytest

pytestmark = pytest.mark.social_foundation


def register(client, name):
    response=client.post('/auth/register',json={'email':f'{name.lower().replace(" ","-")}-{uuid.uuid4()}@example.com','display_name':name,'password':'correct-horse-battery'})
    assert response.status_code==201
    return {'Authorization':f"Bearer {response.json()['access_token']}"}


def publish(client, visibility='PUBLIC', headers=None):
    draft=client.post('/posts',headers=headers or {},json={'caption':'Dashboard post','visibility':visibility,'recipient_user_ids':[]}).json()
    return client.post(f"/posts/{draft['post_id']}/publish",headers=headers or {}).json()


def test_dashboard_is_private_and_excludes_non_public_posts(client):
    client.patch('/community/me',json={'username':'dashboard_author'})
    other=register(client,'Dashboard visitor')
    public=publish(client)
    publish(client,'PRIVATE')
    assert client.put(f"/posts/{public['post_id']}/reaction",headers=other,json={'reaction_type':'HELPFUL'}).status_code==200
    assert client.post(f"/posts/{public['post_id']}/comments",headers=other,json={'content':'Useful'}).status_code==201
    assert client.post(f"/posts/{public['post_id']}/share",headers=other,json={'visibility':'PUBLIC'}).status_code==201
    result=client.get('/community/me/dashboard')
    assert result.status_code==200
    assert result.json()=={'published_posts':1,'hearts_received':1,'comments_received':1,'shares_received':1}
    assert set(result.json())=={'published_posts','hearts_received','comments_received','shares_received'}


def test_dashboard_requires_authentication(client):
    assert client.get('/community/me/dashboard',headers={'Authorization':'Bearer invalid'}).status_code==401

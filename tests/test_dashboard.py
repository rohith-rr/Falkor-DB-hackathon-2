import uuid
import pytest
from starlette.testclient import TestClient
from wayfinder.db import DatabaseManager
from wayfinder.company.fixture import ingest_fixture
from wayfinder.dashboard import create_app


def test_dashboard_smoke_without_paid_or_mutating_controls():
    db=DatabaseManager(graph_name='test_dashboard_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    try:
        ingest_fixture(db)
        with TestClient(create_app(db)) as client:
            page=client.get('/')
            assert page.status_code==200 and 'view-expertise' in page.text and 'view-proof' in page.text and 'view-capabilities' in page.text
            assert client.get('/assets/app.js').status_code==200
            state=client.get('/api/state').json()
            assert state['expertise']['items'][0]['login']=='faye' and not state['enable_live']
            assert client.post('/api/tools/who_knows',json={}).status_code==400
            token=client.get('/api/session').json()['token']; headers={'X-Wayfinder-Session':token}
            response=client.post('/api/tools/get_evidence',json={'evidence_ids':['company:northstar:claim:pc7']},headers=headers)
            assert response.status_code==200 and response.json()['items']
            assert client.post('/api/proofs',json={},headers=headers).status_code==400
            assert client.post('/api/tools/semantic_search',json={'query':'test'},headers=headers).status_code==400
            assert client.post('/api/tools/who_knows',json={},headers={**headers,'Origin':'https://untrusted.example'}).status_code==400
    finally: db.clear_graph()

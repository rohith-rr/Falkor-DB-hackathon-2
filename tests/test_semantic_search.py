import uuid
import pytest
from wayfinder.db import DatabaseManager
from wayfinder.company import capabilities


def test_native_semantic_search_distance_and_current_filter(monkeypatch):
    db=DatabaseManager(graph_name='test_semantic_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    monkeypatch.setattr(capabilities.settings,'embedding_dim',3)
    monkeypatch.setattr(capabilities.settings,'embedding_model','test-vectors')
    try:
        for ident,vector,retired in [('match',[1.,0.,0.],None),('far',[0.,1.,0.],None),('retired',[1.,0.,0.],'2026-01-01')]:
            db.upsert_node('Argument',{'id':ident,'summary':ident,'embedding':vector,'embedding_model':'test-vectors','embedding_dim':3,'valid_to':retired})
        capabilities.prepare_schema(db)
        calls=[]
        monkeypatch.setattr(capabilities,'query_embedding',lambda text:calls.append(text) or [1.,0.,0.])
        result=capabilities.semantic_search(db,'query',['Argument'],10)
        assert [r['entity']['id'] for r in result['items']]==['match','far']
        assert result['items'][0]['score']==pytest.approx(1.,abs=1e-6)
        assert calls==['query']
        with pytest.raises(ValueError): capabilities.semantic_search(db,'query',['Person'],10)
        assert calls==['query']
        capabilities.prepare_schema(db)  # Idempotent, dimension checked.
    finally: db.clear_graph()

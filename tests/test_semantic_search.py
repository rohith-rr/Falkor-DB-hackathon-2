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


def test_embedding_migration_rebuilds_index_and_vectors(monkeypatch):
    db=DatabaseManager(graph_name='test_migrate_'+uuid.uuid4().hex)
    if not db.ping(): pytest.skip('FalkorDB unavailable')
    monkeypatch.setattr(capabilities.settings,'embedding_dim',3)
    monkeypatch.setattr(capabilities.settings,'embedding_model','old-model')
    try:
        db.upsert_node('Argument',{'id':'a','summary':'reservation starvation','embedding':[1.,0.,0.],'embedding_model':'old-model','embedding_dim':3})
        capabilities.prepare_schema(db)
        monkeypatch.setattr(capabilities.settings,'embedding_dim',4)
        monkeypatch.setattr(capabilities.settings,'embedding_model','new-model')
        with pytest.raises(ValueError,match='enable-live'): capabilities.migrate_embeddings(db)
        texts=[]
        class Fake:
            def get_embedding(self,text):
                texts.append(text); return [0.,0.,0.,1.]
        # A provider failure must leave the old index and vectors untouched.
        class Failing:
            def get_embedding(self,text): raise RuntimeError('provider down')
        monkeypatch.setattr(capabilities,'embedding_client',lambda:Failing())
        with pytest.raises(RuntimeError): capabilities.migrate_embeddings(db,enable_live=True)
        assert db.graph.ro_query("MATCH (n {id:'a'}) RETURN n.embedding_model").result_set==[['old-model']]
        monkeypatch.setattr(capabilities,'embedding_client',lambda:Fake())
        report=capabilities.migrate_embeddings(db,enable_live=True)
        assert texts==['reservation starvation'] and report['embedding_requests']==1
        assert {'label':'Argument','dimension':3} in report['dropped_indexes']
        vector=next(i for i in capabilities.indexes(db) if i['label']=='Argument' and 'VECTOR' in i['types'])
        assert int(vector['options']['dimension'])==4
        found=capabilities.vector_search(db,[0.,0.,0.,1.],['Argument'],5)
        assert [r['entity']['id'] for r in found['items']]==['a']
        # Already migrated: no requests, nothing dropped.
        again=capabilities.migrate_embeddings(db)
        assert again['embedding_requests']==0 and again['dropped_indexes']==[]
    finally: db.clear_graph()

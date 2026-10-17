import json
from types import SimpleNamespace
import pytest
from wayfinder.company.live_seed import Seeder,records,messages,load_scope


def seeder(tmp_path):
    instance=Seeder.__new__(Seeder)
    instance.folder=tmp_path;instance.path=tmp_path/'seed-state.json'
    instance.state={'created':{},'pending':{}}
    return instance


def test_seed_reuses_completed_records_without_duplicate_post(tmp_path):
    instance=seeder(tmp_path);created=[]
    def create(): created.append(True);return {'id':'real-id'}
    assert instance.ensure('item:w1',lambda:None,create)=={'id':'real-id'}
    assert instance.ensure('item:w1',lambda:None,create)=={'id':'real-id'}
    assert len(created)==1


def test_uncertain_seed_request_requires_reconciliation(tmp_path):
    instance=seeder(tmp_path)
    def uncertain(): raise TimeoutError('outcome unknown')
    with pytest.raises(TimeoutError): instance.ensure('item:w1',lambda:None,uncertain)
    assert json.loads(instance.path.read_text())['pending']['item:w1']
    with pytest.raises(RuntimeError,match='Uncertain outcome'):
        instance.ensure('item:w1',lambda:None,lambda:pytest.fail('must not repeat POST'))
    recovered=instance.ensure('item:w1',lambda:{'id':'found-after-timeout'},lambda:pytest.fail('must reconcile first'))
    assert recovered['id']=='found-after-timeout' and not instance.state['pending']


def test_seed_uses_source_text_without_fixture_identities_or_claims():
    pages,items=records('Actual sandbox owner')
    assert len(pages)==6 and len(items)==12 and sum(len(i['comments']) for i in items)==20
    assert 'Actual sandbox owner' in pages[0]['description_html']
    assert all('created_by_id' not in i and 'created_at' not in i for i in items)
    assert all('actor_id' not in c for i in items for c in i['comments'])
    assert 'resolved' in items[0]['comments'][1]['comment_html']
    assert len(messages('Actual sandbox owner'))==6
    assert sum(len(m) for _,m in messages('Actual sandbox owner'))==30


def test_scope_requires_explicit_public_channels_and_fictional_designation(tmp_path):
    path=tmp_path/'scope.json'
    scope={'synthetic':True,'workspace':'demo','project_id':'project','slack_team_id':'TDEMO',
           'channels':{'runtime':'CRUNTIME','allocation':'CALLOCATION'}}
    path.write_text(json.dumps(scope));assert load_scope(path)==scope
    scope['synthetic']=False;path.write_text(json.dumps(scope))
    with pytest.raises(ValueError): load_scope(path)
    scope['synthetic']=True;scope['channels']['runtime']='DPRIVATE';path.write_text(json.dumps(scope))
    with pytest.raises(ValueError): load_scope(path)


def test_slack_reader_loads_local_env_token_without_export(monkeypatch):
    from wayfinder.company import slack
    monkeypatch.delenv('SLACK_USER_TOKEN',raising=False);monkeypatch.delenv('SLACK_BOT_TOKEN',raising=False)
    monkeypatch.setattr(slack,'dotenv_values',lambda path:{'SLACK_BOT_TOKEN':'xoxb-dummy-for-test'})
    with slack.SlackClient().session as session:
        assert session.headers['Authorization']=='Bearer xoxb-dummy-for-test'


def test_slack_sync_ignores_non_threadable_membership_events(monkeypatch):
    from wayfinder.company import slack
    calls=[];channels=[]
    class Client:
        def call(self,method,**params):
            return {'ok':True,'permalink':'https://demo.slack.com/archives/CDEMO/p2000000'}
        def messages(self,method,**params):
            if method=='conversations.history':
                return [{'ts':'1.0','subtype':'channel_join'},{'ts':'2.0','text':'Real discussion'}],False
            calls.append(params['ts'])
            assert params['ts']=='2.0'
            return [{'ts':'2.0','text':'Real discussion'}],False
        def close(self): pass
    monkeypatch.setattr(slack,'SlackClient',Client)
    monkeypatch.setattr(slack,'CompanyStore',lambda *args:SimpleNamespace(coverage=lambda *args,**kwargs:None))
    monkeypatch.setattr(slack,'ingest_channel',lambda store,channel:channels.append(channel))
    db=SimpleNamespace(graph=SimpleNamespace(ro_query=lambda *args,**kwargs:SimpleNamespace(result_set=[])))
    result=slack.sync_slack(db,{'slack':{'channel_ids':['CDEMO']}})
    assert calls==['2.0'] and result[0]['threads']==1 and channels


def test_polish_refuses_seed_records_edited_by_someone_else():
    from wayfinder.company.live_seed_polish import check_source_edit,plain_slack,assert_owned_plane
    with pytest.raises(ValueError,match='outside this migration'):
        check_source_edit('An important user addition','Original seed text','Replacement','slack:t0-0',plain_slack)
    check_source_edit('Replacement','Original seed text','Replacement','slack:t0-0',plain_slack)
    check_source_edit('<https://example.org/doc>','https://example.org/doc','new','slack:t0-0',plain_slack)
    with pytest.raises(ValueError,match='Not a verified seed'):
        assert_owned_plane({'external_id':'other-system:w1','external_source':'other-system'},'w1')


def test_polished_messages_are_conversations_without_visible_bookkeeping():
    from wayfinder.company.live_seed import message_text,content
    rows=messages('Owner')
    assert len(rows)==6 and sum(len(t) for _,t in rows)==30
    scope={'workspace':'demo','project_id':'project'}
    state={'created':{k:{'id':k.replace(':','-')} for thread in content()['threads']
        for keys in thread.get('links',{}).values() for k in keys}}
    for i,(_,thread) in enumerate(rows):
        for j in range(len(thread)):
            text=message_text('Owner',i,j,scope,state)
            assert 'Wayfinder seed' not in text and 'manifest' not in text and 'knowledge graph' not in text
            assert 'API authorship' not in text and text.count('https://')<=1


def test_labeled_slack_links_resolve_to_actual_source_records():
    from wayfinder.company.links import link_manifest
    url='https://app.plane.so/demo/projects/p/pages/adr'
    records=[['page','DocPage','Decision text',url,'plane'],
             ['comment','Comment','Please review <'+url+'|Reservation fairness ADR>.','https://slack.com/thread','slack']]
    def query(text,**kwargs):
        return SimpleNamespace(result_set=[] if 'SourceLink' in text or 't.source_system' in text or 'RETURN DISTINCT n.id' in text else records)
    edges=[]
    store=SimpleNamespace(org='demo',manifest={},db=SimpleNamespace(graph=SimpleNamespace(ro_query=query)),
        edge=lambda *args,**kwargs:edges.append(args))
    link_manifest(store)
    assert any(edge[:6]==('Comment','comment','DocPage','page','REFERENCES','slack') for edge in edges)


def test_removed_literal_links_retire_while_explicit_bindings_remain():
    from wayfinder.company.links import link_manifest
    url='https://example.org/adr';retained=[]
    def query(text,**kwargs):
        if 'RETURN labels(a)' in text: rows=[['Comment','DocPage','https://slack.com/thread']]
        elif 'SourceLink' in text or 't.source_system' in text: rows=[]
        elif 'RETURN DISTINCT n.id' in text: rows=[['comment']]
        else: rows=[['page','DocPage','Decision',url,'plane'],['comment','Comment','No document URL remains.','https://slack.com/thread','slack']]
        return SimpleNamespace(result_set=rows)
    store=SimpleNamespace(org='demo',manifest={'bindings':[{'source':'comment','target':'page','relationship':'REFERENCES'}]},
        db=SimpleNamespace(graph=SimpleNamespace(ro_query=query)),edge=lambda *args,**kwargs:None,
        reconcile_edges=lambda source,rels,ids:retained.append((source,ids)))
    link_manifest(store)
    assert retained==[('comment',['comment->REFERENCES->page'])]
    retained.clear();store.manifest={'bindings':[]};link_manifest(store)
    assert retained==[('comment',[])]


def test_github_urls_normalize_to_one_record_key():
    from wayfinder.company.links import normalize_url
    key='https://github.com/acme/wayfinder/issues/7'
    pr='https://github.com/acme/wayfinder/pull/7'
    for url in (pr,key,pr+'/',pr+'#discussion_r123',pr+'/files#diff-abc',pr+'/commits/0a1b2c',pr+'/checks',
                pr+'?w=1',pr+'.',pr+'**','http://github.com/acme/wayfinder/pull/7','https://www.github.com/Acme/Wayfinder/pull/7',
                key+'#issuecomment-9'):
        assert normalize_url(url)==key,url
    assert normalize_url('https://github.com/acme/wayfinder/pull/70')!=key
    item='https://app.plane.so/ws/projects/99999999-8888-4777-8666-555555555555/issues/11111111-2222-4333-8444-555555555555'
    assert normalize_url(item)==item.replace('/issues/','/work-items/')


def test_slack_link_prefers_the_message_over_its_thread():
    from wayfinder.company.links import link_manifest
    permalink='https://acme.slack.com/archives/C1/p1700000000000100'
    # The live Slack sync gives a thread the permalink of its root message.
    records=[['slack:t','Thread',None,permalink,'slack'],['slack:m','Comment','Root message',permalink,'slack'],
             ['plane:i','Issue','See '+permalink,'https://app.plane.so/ws/projects/p/work-items/i','plane']]
    def query(text,**kwargs):
        return SimpleNamespace(result_set=[] if 'SourceLink' in text or 't.source_system' in text or 'RETURN DISTINCT n.id' in text
            else records)
    for order in (records,list(reversed(records))):
        records[:]=order; edges=[]
        store=SimpleNamespace(org='demo',manifest={},db=SimpleNamespace(graph=SimpleNamespace(ro_query=query)),
            edge=lambda *args,**kwargs:edges.append((args,kwargs)))
        link_manifest(store)
        assert [(a[1],a[3],a[5],k) for a,k in edges]==[('plane:i','slack:m','plane',{'link_method':'EXACT_REFERENCE'})]


def test_wayfinder_backlog_is_connected_and_has_no_dependency_cycles():
    from wayfinder.company.live_seed import content
    data=content();items={r['key']:r for r in data['items']}
    all_keys={*('item:'+k for k in items),*('page:'+p['key'] for p in data['pages'])}
    visited=set();visiting=set()
    def visit(key):
        assert key in items
        assert key not in visiting,'Dependency cycle: '+key
        if key in visited:return
        visiting.add(key)
        for dependency in items[key]['depends_on']:visit(dependency)
        visiting.remove(key);visited.add(key)
    for key in items:visit(key)
    mentioned={key for t in data['threads'] for values in t.get('links',{}).values() for key in values}
    assert mentioned<=all_keys and all('item:'+key in mentioned for key in items)
    assert items['w8']['status']=='unstarted' and items['w9']['status']=='unstarted'
    assert items['w10']['status']=='unstarted' and items['w11']['status']=='unstarted'
    assert {i['status'] for i in items.values()}=={'completed','started','unstarted'}
    text=json.dumps(data).lower()
    assert all(phrase not in text for phrase in ('reservation starvation','five second shutdown','resize retry','node runtime'))
    assert set(data['channel_components'].values())=={c['id'] for c in data['components']}


def test_revision_guard_accepts_previous_reviewed_payload_but_not_new_user_edits():
    from wayfinder.company.live_seed_polish import check_source_edit
    check_source_edit('Previously reviewed revision',['Initial seed','Previously reviewed revision'],'Wayfinder revision','item:w1',str)
    with pytest.raises(ValueError,match='preserve and review'):
        check_source_edit('Unreviewed user changes',['Initial seed','Previously reviewed revision'],'Wayfinder revision','item:w1',str)


def test_plane_member_list_does_not_erase_a_known_display_name():
    from wayfinder.company.plane import ingest_project
    writes=[]
    store=SimpleNamespace(org='demo',manifest={'plane':{'workspace':'demo'}},
        person=lambda source,native:'company:demo:person:owner',
        node=lambda label,ident,**props:writes.append((label,props)),edge=lambda *args,**kwargs:None)
    ingest_project(store,{'id':'p','name':'Demo','members':[{'member_id':'native-owner'}],
        'work_items':[],'pages':[],'items_complete':False,'pages_complete':False})
    person=next(props for label,props in writes if label=='Person')
    assert 'display_name' not in person

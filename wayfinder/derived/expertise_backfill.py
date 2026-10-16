"""Explicit bounded GitHub backfill for already imported PR review threads."""
from ..models.common import now_iso
from ..github.activity import ActivityProcessor


def record_review_coverage(db,repo,number,connection,pr_id=None):
    complete=not connection.get('pageInfo',{}).get('hasNextPage',True) and all(
        not t.get('comments',{}).get('pageInfo',{}).get('hasNextPage',True) for t in connection.get('nodes',[]))
    db.upsert_node('ReviewThreadCoverage',{'id':f'review-coverage:{repo}:{number}',
        'pr_id':pr_id or f'k8s:pr:{repo}:{number}','repo':repo,'complete':complete,'observed_at':now_iso()})


def backfill(db,github,limit=10):
    if not 1<=limit<=100:
        raise ValueError('Use limit 1..100')
    result=db.graph.ro_query('MATCH (pr:PullRequest) WHERE pr.valid_to IS NULL AND coalesce(pr.is_deleted,false)=false '
        'OPTIONAL MATCH (c:ReviewThreadCoverage) WHERE c.pr_id=pr.id '
        'WITH pr,c WHERE c IS NULL OR c.complete=false RETURN pr.id,pr.repo,pr.number,pr.source_url ORDER BY pr.id LIMIT $limit',params={'limit':limit})
    processed=0
    for pid,repo,number,url in result.result_set:
        connection=github.fetch_review_threads(repo,number)
        raw={'__typename':'PullRequest','number':number,'url':url,'reviewThreads':connection}
        parsed=ActivityProcessor.process_github_node(raw,repo)
        # A review-only fetch must not overwrite PR attributes or recreate its main thread.
        for comments in parsed['nodes']['comments']:
            comments.pr_id=pid
            db.upsert_node('Comment',comments.to_cypher_props())
        for person in parsed['nodes']['people']:
            db.upsert_node('Person',person.to_cypher_props())
        for thread in parsed['nodes']['threads']:
            if thread.kind=='review_line':
                thread.root_ref=pid
                db.upsert_node('Thread',thread.to_cypher_props())
        for edge in parsed['edges']:
            if edge['source_id'].startswith('k8s:review-thread:') or edge['source_id'] in {c.id for c in parsed['nodes']['comments']} or edge['target_id'] in {c.id for c in parsed['nodes']['comments']}:
                if edge['target_label']=='PullRequest':
                    edge['target_id']=pid
                db.upsert_edge(edge['source_label'],edge['source_id'],edge['target_label'],edge['target_id'],edge['rel_type'],edge['props'])
        record_review_coverage(db,repo,number,connection,pr_id=pid)
        processed+=1
    return {'review_threads_backfilled_prs':processed,'cap':limit,'activity_coverage_changed':False}

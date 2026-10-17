"""Versioned, evidence-backed directory expertise; no network or LLM calls."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import uuid

from ..config import settings
from ..parsers.prow_parser import ProwParser
from ..github.activity import refresh_bot_flags

VERSION = 'v2'
STATE_ID = 'expertise:directory:published'


def date(value):
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def read(db, query, params=None):
    result = db.graph.ro_query(query, params=params or {}, timeout=5000)
    names = [h[1] for h in result.header]
    return [dict(zip(names, row)) for row in result.result_set]


def current(alias):
    return f"{alias}.valid_to IS NULL AND coalesce({alias}.is_deleted,false)=false"


class ExpertiseRefresh:
    def __init__(self, db, clock=None):
        self.db = db
        self.now = clock or datetime.now(timezone.utc)
        self.half_life = settings.expertise_half_life_days
        if self.half_life <= 0:
            raise ValueError('Expertise half-life must be positive')
        self.excluded = defaultdict(int)

    def decay(self, timestamp):
        at = date(timestamp)
        if at is None:
            self.excluded['invalid_timestamp'] += 1
            return None
        return 0.5 ** (max(0, (self.now-at).total_seconds()/86400) / self.half_life)

    def classify_saved_comments(self):
        comments = read(self.db, 'MATCH (c:Comment) RETURN c.id AS id,c.body_text AS text,c.kind AS kind')
        batch = []
        for c in comments:
            classification = ProwParser.classify(c['text'])
            batch.append({'id': c['id'], 'category': classification['category'],
                          'is_command': classification['category']=='command_only',
                          'commands': ProwParser.parse_commands(c['text']),
                          'attribution': 'file_specific' if c['kind']=='review_line' else 'pr_wide'})
        for i in range(0, len(batch), 500):
            self.db.query('UNWIND $batch AS x MATCH (c:Comment {id:x.id}) SET c.activity_category=x.category, '
                          'c.is_command=x.is_command,c.commands=x.commands,c.attribution=x.attribution', {'batch': batch[i:i+500]})
        return len(batch)

    def contributions(self):
        db = self.db
        # Explicit, current file membership; one allocation per directly touched directory.
        touched = read(db, f'MATCH (pr:PullRequest)-[t:TOUCHES]->(f:File)<-[c:CONTAINS]-(d:Directory) '
            f'WHERE {current("pr")} AND {current("t")} AND {current("f")} AND {current("c")} AND {current("d")} '
            'RETURN DISTINCT pr.id AS pr_id,d.id AS directory_id,d.path AS path,d.repo AS repo,f.path AS file_path')
        dirs, file_dirs = defaultdict(dict), {}
        for row in touched:
            if not row['pr_id'] or not row['directory_id']:
                self.excluded['missing_stable_id']+=1
                continue
            dirs[row['pr_id']][row['directory_id']] = row
            file_dirs[(row['pr_id'], row['file_path'])] = row
        authors = read(db, f'MATCH (p:Person)-[a:AUTHORED]->(pr:PullRequest) WHERE {current("p")} AND {current("a")} AND {current("pr")} '
            'RETURN DISTINCT p.id AS person_id,p.login AS login,p.is_bot AS bot,pr.id AS pr_id,pr.merged_at AS at,pr.state AS state,pr.source_url AS url')
        pr_authors = defaultdict(set)
        people = {}
        for row in authors:
            pr_authors[row['pr_id']].add(row['person_id'])
            people[row['person_id']] = row
        records = []

        def emit(person_id, pr_id, targets, channel, at, evidence_ids, urls, weight, category=None, quote=None):
            multiplier = self.decay(at)
            if multiplier is None:
                return
            for target in targets:
                records.append({'person_id': person_id, 'pr_id': pr_id, 'directory_id': target['directory_id'],
                    'path': target['path'], 'repo': target['repo'], 'channel': channel,
                    'at': date(at).isoformat(), 'score': weight*multiplier,
                    'base_weight': weight, 'evidence_ids': sorted(set(evidence_ids)),
                    'source_urls': sorted(set(u for u in urls if u)), 'category': category,
                    'quote': quote, 'attribution': 'file_specific' if channel=='peer_review' else
                    'changed_files' if channel=='implementation' else 'pr_wide'})

        for row in authors:
            targets = list(dirs[row['pr_id']].values())
            if row['bot'] or not targets:
                continue
            if not row['at']:
                if row['state']=='MERGED':
                    self.excluded['invalid_timestamp']+=1
                continue
            emit(row['person_id'], row['pr_id'], targets, 'implementation', row['at'],
                 [row['pr_id']], [row['url']], 2.0/len(targets))

        # Existing PR conversation comments have no file anchor and cannot become strong reviews.
        comments = read(db, f'MATCH (p:Person)-[a:AUTHORED]->(c:Comment) WHERE {current("p")} AND {current("a")} AND {current("c")} '
            'OPTIONAL MATCH (c)<-[h:HAS_COMMENT]-(:Thread)-[o:ON]->(pr:PullRequest) WHERE h.valid_to IS NULL AND o.valid_to IS NULL AND pr.valid_to IS NULL '
            'OPTIONAL MATCH (c)-[ir:IN_REVIEW]->(parent_review:Review) WHERE ir.valid_to IS NULL '
            'RETURN DISTINCT p.id AS person_id,p.login AS login,p.is_bot AS bot,c.id AS id,c.body_text AS text,c.kind AS kind,'
            'c.file_path AS file_path,coalesce(c.pr_id,pr.id) AS pr_id,c.created_at AS at,c.source_url AS url,'
            'parent_review.state AS review_state,parent_review.valid_to AS review_valid_to')
        comment_texts={row['id']:row['text'] for row in comments}
        groups = defaultdict(list)
        weak = {}
        for row in comments:
            if row['bot']:
                continue
            if row['review_state'] in ('PENDING','DISMISSED') or row['review_valid_to'] is not None:
                self.excluded['unpublished_or_retired_review']+=1
                continue
            classification = ProwParser.classify(row['text'])
            people[row['person_id']] = row
            if classification['category'] in ('command_only', 'administrative'):
                self.excluded[classification['category']] += 1
                continue
            target = file_dirs.get((row['pr_id'], row['file_path']))
            if row['kind']=='review_line' and target and classification['discussion_text']:
                if row['person_id'] in pr_authors[row['pr_id']] or not pr_authors[row['pr_id']]:
                    self.excluded['self_or_unknown_author_review'] += 1
                    continue
                if date(row['at']) is None:
                    self.excluded['invalid_timestamp'] += 1
                    continue
                groups[(row['person_id'],row['pr_id'],target['directory_id'])].append(row)
            elif dirs.get(row['pr_id']):
                weak[(row['person_id'],row['id'])] = {**row, 'category': 'discussion_or_coordination'}
        for (person, pr, directory), items in groups.items():
            latest = max(items, key=lambda x: date(x['at']))
            emit(person, pr, [dirs[pr][directory]], 'peer_review', latest['at'],
                 [i['id'] for i in items], [i['url'] for i in items], 2.0)

        reviews = read(db, f'MATCH (p:Person)-[s:SUBMITTED]->(r:Review)-[rv:REVIEWS]->(pr:PullRequest) '
            f'WHERE {current("p")} AND {current("s")} AND {current("r")} AND {current("rv")} AND {current("pr")} '
            'RETURN DISTINCT p.id AS person_id,p.login AS login,p.is_bot AS bot,r.id AS id,r.body_text AS text,r.submitted_at AS at,r.source_url AS url,pr.id AS pr_id,r.state AS state')
        for row in reviews:
            if row['bot'] or row['person_id'] in pr_authors[row['pr_id']]:
                continue
            if row['state']=='DISMISSED':
                continue
            people[row['person_id']] = row
            if row['state']=='APPROVED' or any(c in ('approve','lgtm') for c in ProwParser.parse_commands(row['text'])) or ProwParser.classify(row['text'])['category'] in ('discussion','mixed'):
                weak[(row['person_id'],row['id'])] = {**row, 'category': 'pr_wide_review'}
        approvals = read(db, f'MATCH (p:Person)-[r:APPROVED]->(pr:PullRequest) WHERE {current("p")} AND {current("r")} AND {current("pr")} '
            'RETURN DISTINCT p.id AS person_id,p.login AS login,p.is_bot AS bot,coalesce(r.via_comment,r.id) AS id,r.at AS at,r.source_url AS url,pr.id AS pr_id')
        for row in approvals:
            if row['bot']:
                continue
            people[row['person_id']] = row
            prior = weak.get((row['person_id'], row['id']), {})
            weak[(row['person_id'],row['id'])] = {**prior, **row, 'text':comment_texts.get(row['id'],prior.get('text')), 'category':'pr_wide_approval'}
        for row in weak.values():
            emit(row['person_id'], row['pr_id'], list(dirs[row['pr_id']].values()), 'involvement', row['at'],
                 [row['id']], [row['url']], 0, row['category'], row.get('text'))
        # Explicit event/directory key prevents replay or historical copies from multiplying credit.
        unique = {}
        for record in records:
            key = (record['person_id'],record['directory_id'],record['channel'],record['pr_id'],tuple(record['evidence_ids']))
            unique[key] = record
        return list(unique.values()), people

    def refresh(self):
        # Compare-and-publish prevents overlapping refreshes from replacing a newer generation.
        previous = read(self.db,'MATCH (s:ExpertiseState {id:$id}) RETURN s.generation AS generation', {'id':STATE_ID})
        expected = previous[0]['generation'] if previous else None
        generation = 'expertise:v2:' + uuid.uuid4().hex
        stamp = self.now.isoformat()
        self.db.upsert_node('ExpertiseState', {'id':STATE_ID})
        refresh_bot_flags(self.db)
        classified = self.classify_saved_comments()
        records, people = self.contributions()
        coverage=read(self.db,'MATCH (c:ActivityCoverage) RETURN c')
        review_coverage=read(self.db,'MATCH (c:ReviewThreadCoverage) RETURN count(c) AS records,sum(CASE WHEN c.complete THEN 1 ELSE 0 END) AS complete')
        metadata = {'id': generation, 'scoring_version':VERSION, 'calculated_at':stamp,
                    'half_life_days':self.half_life,'recent_window_days':settings.expertise_window_days,
                    'excluded_counts_json':json.dumps(dict(self.excluded)), 'status':'staging',
                    'coverage_json':json.dumps({'activity':[x['c'].properties for x in coverage],
                        'review_threads':review_coverage,'note':'Review coverage describes only explicitly fetched imported PRs, not complete activity.'})}
        self.db.upsert_node('ExpertiseSnapshot', metadata)
        nodes = []
        grouped = defaultdict(list)
        for index, rec in enumerate(records):
            ident = f'{generation}:contribution:{index}'
            nodes.append({'id':ident,'generation':generation,'person_id':rec['person_id'],
                'directory_id':rec['directory_id'],'path':rec['path'],'repo':rec['repo'], 'record_json':json.dumps(rec)})
            grouped[(rec['person_id'],rec['directory_id'])].append(rec)
        for i in range(0,len(nodes),500):
            self.db.batch_upsert_nodes('ExpertiseContribution', nodes[i:i+500])
        edges = []
        for (person,directory), items in grouped.items():
            strong = [x for x in items if x['channel']!='involvement']
            if not strong:
                continue
            logical = f'{person}->EXPERT_IN->{directory}'
            edges.append({'person':person,'directory':directory,'props':{
                'id':logical+'@'+generation,'logical_id':logical,'generation':generation,
                'scoring_version':VERSION,'score':sum(x['score'] for x in strong),
                'evidence_ids':sorted({i for x in strong for i in x['evidence_ids']}),
                'observed_at':stamp,'valid_from':stamp,'valid_to':stamp,
                'authority':'COMPUTED','link_method':'COMPUTED'}})
        for i in range(0,len(edges),500):
            self.db.query('UNWIND $batch AS x MATCH (p:Person {id:x.person}),(d:Directory {id:x.directory}) '
                'CREATE (p)-[r:EXPERT_IN]->(d) SET r=x.props', {'batch':edges[i:i+500]})
        # Stage unlisted relationships before publication, honoring inherited OWNERS.
        from .ownership import covering_directory_ids
        candidates = defaultdict(list)
        for edge in edges:
            candidates[edge['directory']].append(edge)
        unlisted = []
        for directory, members in candidates.items():
            covering = covering_directory_ids(self.db,directory)
            declared = {x['person'] for x in read(self.db, 'MATCH (p:Person)-[r:APPROVER_IN|REVIEWER_IN]->(:OwnersFile)-[c:COVERS]->(d:Directory) '
                'WHERE r.valid_to IS NULL AND c.valid_to IS NULL AND d.id IN $dirs RETURN DISTINCT p.id AS person',{'dirs':covering})}
            for edge in sorted(members,key=lambda x:(-x['props']['score'],x['person']))[:5]:
                if edge['person'] not in declared:
                    logical=f"{edge['person']}->UNLISTED_EXPERT->{directory}"
                    unlisted.append({**edge,'props':{**edge['props'],'id':logical+'@'+generation,'logical_id':logical}})
        for i in range(0,len(unlisted),500):
            self.db.query('UNWIND $batch AS x MATCH (p:Person {id:x.person}),(d:Directory {id:x.directory}) '
                'CREATE (p)-[r:UNLISTED_EXPERT]->(d) SET r=x.props', {'batch':unlisted[i:i+500]})
        # One database statement publishes the pointer and relationship intervals together.
        result = self.db.query('MATCH (s:ExpertiseState {id:$state}),(snapshot:ExpertiseSnapshot {id:$generation}) '
            'WHERE coalesce(s.generation,\'\')=coalesce($expected,\'\') '
            'SET s.generation=$generation,s.calculated_at=$stamp,s.scoring_version=\'v2\',snapshot.status=\'published\' '
            'WITH s OPTIONAL MATCH ()-[r:EXPERT_IN|UNLISTED_EXPERT]->(:Directory) '
            'WHERE r.valid_to IS NULL OR r.generation=$generation '
            'SET r.valid_to=CASE WHEN r.generation=$generation THEN null ELSE $stamp END '
            'RETURN s.generation', {'state':STATE_ID,'generation':generation,'expected':expected,'stamp':stamp})
        if not result.result_set:
            raise RuntimeError('Another refresh published first; staging generation was not published. Retry refresh.')
        return {'generation':generation,'scoring_version':VERSION,'calculated_at':stamp,
                'comments_reclassified':classified,'contributions':len(records),'expert_edges_created':len(edges),
                'scores':len(edges),'unlisted_experts':len(unlisted),'excluded_counts':dict(self.excluded)}


def rankings(db, term, as_of=None, clock=None):
    now = clock or datetime.now(timezone.utc)
    if as_of:
        snapshots = read(db,'MATCH (s:ExpertiseSnapshot) WHERE s.status=\'published\' AND s.calculated_at<=$at '
            'RETURN s ORDER BY s.calculated_at DESC LIMIT 1',{'at':date(as_of).isoformat() if date(as_of) else as_of})
    else:
        snapshots = read(db,'MATCH (state:ExpertiseState {id:$id}),(s:ExpertiseSnapshot) WHERE s.id=state.generation RETURN s',{'id':STATE_ID})
    if not snapshots:
        return None  # Caller retains explicitly labeled legacy historical compatibility.
    meta = snapshots[0]['s'].properties
    roots=read(db,'MATCH (c:Component {id:$term})-[r:IMPLEMENTED_IN]->(d:Directory) '
        'WHERE c.valid_to IS NULL AND r.valid_to IS NULL AND d.valid_to IS NULL '
        'AND coalesce(d.is_deleted,false)=false RETURN DISTINCT d.id AS id,d.path AS path,d.repo AS repo',{'term':term})
    exact_directory=read(db,'MATCH (d:Directory {id:$term}) RETURN d.path AS path,d.repo AS repo',{'term':term})
    repo=None
    if exact_directory:
        term=exact_directory[0]['path']
        repo=exact_directory[0]['repo']
    records = read(db,'MATCH (c:ExpertiseContribution {generation:$generation}) '
        'MATCH (p:Person {id:c.person_id}) WHERE coalesce(p.is_bot,false)=false AND p.valid_to IS NULL AND coalesce(p.is_deleted,false)=false '
        'AND ((size($roots)=0 AND (c.directory_id=$term OR c.path=$term OR c.path STARTS WITH ($term+\'/\')) '
        'AND ($repo IS NULL OR c.repo=$repo)) OR any(root IN $roots WHERE c.repo=root.repo AND '
        '(c.directory_id=root.id OR c.path=root.path OR root.path=\'\' OR c.path STARTS WITH (root.path+\'/\')))) '
        'RETURN DISTINCT c.record_json AS record,p.login AS login,p.display_name AS display_name',
        {'generation':meta['id'],'term':term,'repo':repo,'roots':roots})
    people = {}
    recent_cutoff = date(meta['calculated_at']).timestamp()-meta['recent_window_days']*86400
    for row in records:
        item = json.loads(row['record'])
        person = people.setdefault(item['person_id'], {'person_id':item['person_id'],'login':row['login'],
            'display_name':row['display_name'],'score':0.0,'score_breakdown':{'implementation':0.0,'peer_review':0.0},
            'matches':{},'evidence_ids':set(),'source_urls':set(),'prs':set(),'recent_prs':set(),'involvement':{}})
        if item['channel']=='involvement':
            key=(item['pr_id'],tuple(item['evidence_ids']))
            person['involvement'][key]=item
            continue
        person['score'] += item['score']
        person['score_breakdown'][item['channel']] += item['score']
        person['prs'].add(item['pr_id'])
        if date(item['at']).timestamp()>=recent_cutoff:
            person['recent_prs'].add(item['pr_id'])
        person['evidence_ids'].update(item['evidence_ids'])
        person['source_urls'].update(item['source_urls'])
        match = person['matches'].setdefault(item['directory_id'],{'target_id':item['directory_id'],'target_name':item['path'],'score':0.0,'evidence_ids':set()})
        match['score'] += item['score']
        match['evidence_ids'].update(item['evidence_ids'])
    def display_score(value):
        rounded=round(value,6)
        return value if value>0 and rounded==0 else rounded
    strong, weak = [], []
    for person in people.values():
        person['distinct_pr_count']=len(person.pop('prs'))
        person['recent_pr_count']=len(person.pop('recent_prs'))
        person['evidence_strength']='multiple_prs' if person['distinct_pr_count']>=3 else 'limited'
        involvement=list(person['involvement'].values())
        person['involvement_count']=len(involvement)
        # Full weak evidence remains in immutable contribution records; keep rankings bounded.
        person['involvement']=involvement[:5]
        person['involvement_truncated']=len(involvement)>5
        person['matches']=sorted(person['matches'].values(),key=lambda x:(x['target_name'],x['target_id']))
        for match in person['matches']:
            match['score']=display_score(match['score'])
            match['evidence_ids']=sorted(match['evidence_ids'])
        person['score']=display_score(person['score'])
        person['score_breakdown']={k:display_score(v) for k,v in person['score_breakdown'].items()}
        person['evidence_ids']=sorted(person['evidence_ids'])
        person['source_urls']=sorted(person['source_urls'])
        person['scoring_version']=VERSION
        person['calculated_at']=meta['calculated_at']
        (strong if person['distinct_pr_count'] else weak).append(person)
    age=max(0,(now-date(meta['calculated_at'])).total_seconds())
    warnings=['Activity import is not proven complete; rankings describe saved evidence, not lifetime competence.',
              'PR-wide involvement does not contribute to the expertise score.',
              'Scores aggregate allocated contributions across matched descendant directories.']
    if age>7200 and not as_of:
        warnings.append('STALE_SNAPSHOT: Run the expertise-only refresh; this snapshot is older than two hours.')
    return {'items':sorted(strong,key=lambda x:(-x['score'],x['login'])),
            'involvement_only':sorted(weak,key=lambda x:(-x['involvement_count'],x['login'])),
            'generation':meta['id'],'scoring_version':VERSION,'calculated_at':meta['calculated_at'],
            **({'component_id':term,'code_directories':roots} if roots else {}),
            'snapshot_age_seconds':age,'stale':age>7200 and not as_of,'warnings':warnings,
            'parameters':{'half_life_days':meta['half_life_days'],'recent_window_days':meta['recent_window_days'],'hard_cutoff':False},
            'excluded_counts':json.loads(meta.get('excluded_counts_json','{}')),
            'coverage':json.loads(meta.get('coverage_json','{}'))}


def get_rankings(db, term, as_of=None, clock=None):
    topic = read(db, 'MATCH (t:Topic) WHERE t.id=$term OR t.name=$term RETURN t.id LIMIT 1', {'term':term})
    report = None if topic else rankings(db,term,as_of,clock)
    if report is not None:
        return report
    from ..query import WayfinderBrainQuery
    legacy = WayfinderBrainQuery(db)._legacy_who_knows(term,as_of,limit=100000)
    people = {}
    for row in legacy:
        p = people.setdefault(row['login'], {'login':row['login'],'display_name':row['display_name'],
            'score':row['score'],'matches':[],'evidence_ids':set(),'scoring_version':'legacy-v1'})
        p['score']=max(p['score'],row['score'])
        p['matches'].append({'target_name':row['target_name'],'target_type':row['target_type'],
                              'score':row['score'],'evidence_ids':row['evidence_ids'] or []})
        p['evidence_ids'].update(row['evidence_ids'] or [])
    for p in people.values():
        p['evidence_ids']=sorted(p['evidence_ids'])
    return {'items':sorted(people.values(),key=lambda p:(-p['score'],p['login'])),
            'involvement_only':[],'scoring_version':'legacy-v1','generation':'legacy',
            'warnings':['Legacy scores use maximum matched target activity. Run expertise-only repair for v2 directory rankings.'
                        if not topic else 'Topic expertise uses the legacy topic model; directory v2 rules do not apply.']}

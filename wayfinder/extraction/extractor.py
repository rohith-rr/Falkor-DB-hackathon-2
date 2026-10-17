import hashlib
import json
from pydantic import ValidationError
from ..config import settings
from ..db import db_manager
from ..models.common import compute_content_hash, now_iso
from ..models.layer2 import UnitExtractionResult
from .cache import ExtractionCache
from .llm_client import OpenAPIClient
from .verifier import QuoteVerifier, VerificationStats

SYSTEM_EXTRACTION_PROMPT = """Extract technical meaning from text_to_analyze only, using the required response schema.
Every item requires an exact evidence_quote from this unit. All referenced IDs, including
addresses_target_id, supersedes_id and question_id, must belong to ALLOWED_TARGET_IDS.
Answers link this comment to an existing question in the same thread. SUPERSEDES requires
the earlier decision's target. Previous comments are context, never quote evidence.
An answer's question_id must identify an existing Question node (k8s:question:...),
never a Comment (k8s:comment:...), PullRequest, or the current text unit.
If ALLOWED_TARGET_IDS contains no existing Question ID, return answers: [].
Do not invent a question ID or classify every comment as an answer.
Technical explanations belong in arguments with kind "explanation". An actual
question asked in the current text belongs in questions, not answers.
Use kind "resolution" only for an explicit statement that a specific concern is
resolved, and set addresses_target_id to that concern. Approval or merging alone
is not resolution. Alternatives considered are not necessarily rejected.
For commands, acknowledgements, links without explanation, or text with no
technical meaning, return empty lists. Empty results are valid and expected.
Use this top-level output shape, adding items only when supported by the text:
{"arguments": [], "decisions": [], "rationales": [], "questions": [], "answers": [], "topics": []}
Return empty lists for text with no technical meaning.
"""


def extraction_schema(question_ids):
    """A strict response schema with answer targets restricted to real questions."""
    schema = UnitExtractionResult.model_json_schema()
    def strict(value):
        if isinstance(value, dict):
            value.pop('default', None)
            if value.get('type') == 'object':
                value['additionalProperties'] = False
                value['required'] = list(value.get('properties', {}))
            for child in value.values():
                strict(child)
        elif isinstance(value, list):
            for child in value:
                strict(child)
    strict(schema)
    if question_ids:
        schema['$defs']['ExtractedAnswer']['properties']['question_id']['enum'] = sorted(question_ids)
    else:
        schema['properties']['answers']['maxItems'] = 0
    return schema

class ExtractionPipeline:
    def __init__(self, db=None, llm_client=None, cache=None, extractor_version=None):
        self.db = db or db_manager
        self.llm = llm_client or OpenAPIClient()
        self.cache = cache or ExtractionCache()
        self.version = extractor_version or settings.extractor_version
        self.stats = VerificationStats()

    def process_text_unit(self, unit_type, unit_id, unit_text, author_id=None,
                          context_target_ids=None, thread_title=None, prev_comments_context=None,
                          source_url=None, source_ref=None, source_timestamp=None, parent_kep_id=None):
        text = unit_text or ''
        if len(text.strip()) < 20:
            return {'accepted': 0, 'rejected': 0, 'skipped': True}
        now = now_iso()
        content_hash = compute_content_hash(text)
        allowed = set(context_target_ids or []) | {unit_id}
        question_rows = self.db.query('MATCH (q:Question) WHERE q.id IN $ids AND q.valid_to IS NULL RETURN q.id', {'ids': sorted(allowed)})
        question_ids = sorted(r[0] for r in question_rows.result_set) if unit_type == 'Comment' else []
        schema = extraction_schema(question_ids)
        fingerprint = hashlib.sha256(json.dumps([text, self.version, getattr(self.llm, 'model', None),
            getattr(self.llm,'base_url',None),getattr(self.llm,'embedding_model',None),getattr(self.llm,'embedding_dim',None),
            SYSTEM_EXTRACTION_PROMPT, schema, sorted(allowed), thread_title, prev_comments_context,
            'publication-v2'], sort_keys=True).encode()).hexdigest()
        materialization = f'{self.version}:{fingerprint}'
        res = self.db.query('MATCH (u {id: $id}) RETURN u.extraction_version, u.source_url, u.source_ref, '
                            'coalesce(u.created_at, u.submitted_at, u.source_updated_at, u.valid_from), u.extraction_generation', {'id': unit_id})
        generation = 1
        if res.result_set:
            row = res.result_set[0]
            if row[0] == materialization:
                return {'accepted': 0, 'rejected': 0, 'skipped': True}
            generation = (row[4] or 0) + 1
            source_url = source_url or row[1]
            source_ref = source_ref or row[2]
            source_timestamp = source_timestamp or row[3]
        if isinstance(self.llm, OpenAPIClient) and not self.llm.is_live_llm_configured():
            return {'accepted': 0, 'rejected': 0, 'skipped': True, 'reason': 'offline'}
        if not source_url:
            return {'accepted': 0, 'rejected': 0, 'error': 'Missing source URL'}
        cache_hash = fingerprint
        self._before_extraction(unit_id)
        payload = self.cache.get(cache_hash, self.version)
        if payload is None:
            prompt = json.dumps({'thread_title': thread_title, 'allowed_target_ids': sorted(allowed),
                'allowed_question_ids': question_ids, 'previous_comments_context': prev_comments_context,
                'text_to_analyze': text})
            try:
                payload = self.llm.chat_completion_json(messages=[{'role': 'user', 'content': prompt}],
                    system_prompt=SYSTEM_EXTRACTION_PROMPT, response_schema=schema)
            except Exception as exc:
                return {'accepted': 0, 'rejected': 0, 'error': str(exc)}
            self.cache.put(cache_hash, self.version, payload)
        before = self.stats.rejected
        accepted = []
        if not isinstance(payload, dict) or set(payload) - set(UnitExtractionResult.model_fields):
            self.stats.total_proposed += 1
            self.stats.rejected += 1
            self.stats.schema_failures += 1
            return {'accepted': 0, 'rejected': 1}
        try:
            UnitExtractionResult.model_validate({'topics': payload.get('topics', [])})
        except ValidationError:
            self.stats.total_proposed += 1
            self.stats.rejected += 1
            self.stats.schema_failures += 1
        for group, label in [('arguments', 'Argument'), ('decisions', 'Decision'),
                             ('rationales', 'Rationale'), ('questions', 'Question'), ('answers', None)]:
            items = payload.get(group, [])
            if not isinstance(items, list):
                items = [items]
            for index, raw in enumerate(items):
                self.stats.total_proposed += 1
                try:
                    item = getattr(UnitExtractionResult.model_validate({group: [raw]}), group)[0].model_dump(mode='json')
                    if item.get('kind') == 'concern' and not item.get('concern_type'):
                        raise ValueError('A concern requires concern_type')
                    if item.get('kind') == 'resolution' and not item.get('addresses_target_id'):
                        raise ValueError('A resolution must identify the specific concern')
                except (ValidationError, ValueError):
                    self.stats.rejected += 1
                    self.stats.schema_failures += 1
                    self.stats.rejection_reasons.append(f'{group}: schema validation failed')
                    continue
                if not QuoteVerifier.verify_quote(item['evidence_quote'], text):
                    self.stats.rejected += 1
                    self.stats.quote_mismatches += 1
                    self.stats.rejection_reasons.append(f'{group}: evidence quote not present in source')
                    continue
                valid = all(QuoteVerifier.verify_target(item.get(k), allowed) for k in
                            ('target_id', 'addresses_target_id', 'supersedes_id', 'question_id'))
                if valid and item.get('target_id'):
                    targets = self.db.query('MATCH (n {id: $id}) WHERE n.valid_to IS NULL RETURN labels(n)', {'id': item['target_id']})
                    permitted = {'KEP', 'PullRequest', 'FeatureGate', 'Issue', 'Component', 'Project', 'DocPage'} if label == 'Decision' or item.get('kind') == 'concern' else {'KEP', 'PullRequest', 'Argument', 'Decision', 'Issue', 'Component', 'Project', 'DocPage'}
                    valid = bool(targets.result_set) and bool(set(targets.result_set[0][0]) & permitted)
                for key, target_label in [('addresses_target_id', 'Argument'), ('supersedes_id', 'Decision'), ('question_id', 'Question')]:
                    target = item.get(key)
                    if target and valid:
                        check = self.db.query(f'MATCH (n:{target_label} {{id: $id}}) WHERE n.valid_to IS NULL RETURN n.target_id', {'id': target})
                        valid = bool(check.result_set)
                        if key == 'supersedes_id' and valid:
                            valid = bool(item.get('target_id')) and check.result_set[0][0] == item['target_id']
                if label is None:
                    valid = valid and unit_type == 'Comment'
                    if valid:
                        same_thread = self.db.query('MATCH (t:Thread)-[:HAS_COMMENT]->(c:Comment {id: $unit}) '
                            'MATCH (t)-[:HAS_COMMENT]->(:Comment)-[r:ASSERTS]->(q:Question {id: $question}) '
                            'WHERE r.valid_to IS NULL RETURN q.id', {'unit': unit_id, 'question': item['question_id']})
                        valid = bool(same_thread.result_set)
                if not valid:
                    self.stats.rejected += 1
                    self.stats.invalid_targets += 1
                    self.stats.rejection_reasons.append(f'{group}: invalid target or question ID')
                    continue
                ident = hashlib.sha256(f'{unit_id}:{materialization}:{generation}:{group}:{index}'.encode()).hexdigest()[:24]
                namespace='k8s' if unit_id.startswith('k8s:') else unit_id.split(':',1)[0]
                accepted.append((label, f"{namespace}:{(label or 'answer').lower()}:{ident}", item))
                self.stats.accepted += 1
        nodes, edges = [], []
        revision_id = f'source-revision:{unit_id}:{hashlib.sha256(text.encode()).hexdigest()}'
        provenance = {'source_system': 'llm-extraction', 'source_url': source_url,
                      'source_ref': source_ref or unit_id, 'observed_at': now, 'valid_from': now,
                      'extractor_version': self.version, 'source_unit_id': unit_id,
                      'content_hash': content_hash, 'source_revision_id': revision_id, 'authority': 'EXTRACTED', 'link_method': 'LLM_EXTRACTED',
                      # Which model proposed the claim; several models can contribute to one graph.
                      'llm_model': getattr(self.llm, 'model', None), 'llm_base_url': getattr(self.llm, 'base_url', None)}
        def edge(sl, sid, tl, tid, rel, quote=None):
            props = dict(provenance, id=f'{sid}->{rel}->{tid}')
            if quote:
                props['evidence_quote'] = quote
            edges.append((sl, sid, tl, tid, rel, props))
        def label_for(tid):
            for part, label in [(':kep:', 'KEP'), (':pr:', 'PullRequest'), (':argument:', 'Argument'),
                                (':decision:', 'Decision'), (':feature_gate:', 'FeatureGate')]:
                if part in tid:
                    return label
            found = self.db.query('MATCH (n {id:$id}) RETURN labels(n)[0]', {'id':tid})
            return found.result_set[0][0] if found.result_set else 'Issue'
        for label, ident, item in accepted:
            quote = item['evidence_quote']
            if label is None:
                edge(unit_type, unit_id, 'Question', item['question_id'], 'ANSWERS', quote)
                continue
            props = dict(provenance, id=ident, **{k: v for k, v in item.items() if k != 'topics' and v is not None})
            props['source_timestamp'] = source_timestamp or now
            if label == 'Argument':
                props.update(embedding_model=getattr(self.llm,'embedding_model',None),embedding_dim=getattr(self.llm,'embedding_dim',None))
                props['embedding'] = self.llm.get_embedding(item['summary'])
            if label == 'Question':
                props['asked_at'] = source_timestamp or now
            nodes.append((label, props))
            edge(unit_type, unit_id, label, ident, 'ASSERTS', quote)
            if author_id and label in {'Argument', 'Question', 'Decision'}:
                edge('Person', author_id, label, ident, 'MADE', quote)
            target = item.get('target_id')
            if target:
                rel = 'ABOUT' if label == 'Decision' else ('RAISES_CONCERN_ON' if item.get('kind') == 'concern' else
                       {'supports': 'SUPPORTS', 'opposes': 'OPPOSES'}.get(item.get('stance')))
                if rel:
                    edge(label, ident, label_for(target), target, rel, quote)
            if item.get('addresses_target_id'):
                edge(label, ident, 'Argument', item['addresses_target_id'],
                     'RESOLVES' if item.get('kind') == 'resolution' else 'ADDRESSES', quote)
            if item.get('supersedes_id'):
                edge(label, ident, 'Decision', item['supersedes_id'], 'SUPERSEDES', quote)
            if label == 'Rationale' and unit_type == 'KEPSection':
                if not parent_kep_id:
                    parents = self.db.query('MATCH (k:KEP)-[:HAS_SECTION]->(s:KEPSection {id: $id}) RETURN k.id', {'id': unit_id})
                    parent_kep_id = parents.result_set[0][0] if parents.result_set else None
                if parent_kep_id:
                    edge('KEP', parent_kep_id, label, ident, 'HAS_RATIONALE', quote)
            for name in item.get('topics', []):
                if not name.strip():
                    continue
                tid = f"{namespace}:topic:{name.lower().replace(' ', '-')}"
                nodes.append(('Topic', dict(provenance, id=tid, name=name, embedding=self.llm.get_embedding(name),
                    embedding_model=getattr(self.llm,'embedding_model',None),embedding_dim=getattr(self.llm,'embedding_dim',None))))
                edge(label, ident, 'Topic', tid, 'ABOUT_TOPIC', quote)
                edge(unit_type, unit_id, 'Topic', tid, 'ABOUT_TOPIC', quote)
        if unit_type == 'Comment':
            nodes.append((unit_type, {'id': unit_id, 'embedding': self.llm.get_embedding(text),
                'embedding_model':getattr(self.llm,'embedding_model',None),'embedding_dim':getattr(self.llm,'embedding_dim',None)}))
        from .publication import publish
        publish(self.db, unit_id, unit_type, text, revision_id, nodes, edges,
                expected=res.result_set[0][0] if res.result_set else None,
                version=materialization, generation=generation, now=now, source_url=source_url)
        return {'accepted': len(accepted), 'rejected': self.stats.rejected - before, 'rejection_rate': self.stats.rejection_rate}

    def _before_extraction(self, unit_id):
        pass

    def _retire_interpretation(self, unit_id, now):
        # Source edits invalidate the previous interpretation even while offline.

        self.db.query("MATCH (u {id: $id})-[r:ASSERTS]->(n) WHERE r.valid_to IS NULL AND r.authority = 'EXTRACTED' "
                      'SET r.valid_to = $now, n.valid_to = $now WITH n MATCH (n)-[e]-() '
                      'WHERE e.valid_to IS NULL SET e.valid_to = $now', {'id': unit_id, 'now': now})
        self.db.query("MATCH (u {id: $id})-[r:ABOUT_TOPIC|ANSWERS]->() WHERE r.authority = 'EXTRACTED' "
                      'AND r.valid_to IS NULL SET r.valid_to = $now', {'id': unit_id, 'now': now})
        self.db.query('MATCH (u {id: $id}) SET u.extraction_version = null', {'id': unit_id})

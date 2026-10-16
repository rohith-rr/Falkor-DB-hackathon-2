"""Mechanical evidence checks; semantic accuracy requires independent review."""
import json
from .fixture import ROOT
from .brief import draft_brief,validate_brief


def evaluate(db,answers_path=None):
    labels=json.loads((ROOT/'fixtures/company/claim_labels.json').read_text(encoding='utf-8'))
    valid=0
    for label in labels:
        rows=db.graph.ro_query('MATCH (n {id:$id}) RETURN n.evidence_quote,n.source_revision_id',params={'id':label['claim_id']}).result_set
        if rows and rows[0][0]==label['quote']:
            source=db.graph.ro_query('MATCH (r:SourceRevision {id:$id}) RETURN r.text_content',params={'id':rows[0][1]}).result_set
            valid+=bool(source) and label['quote'] in source[0][0]
    evaluation=json.loads((ROOT/'fixtures/company/evaluation.json').read_text(encoding='utf-8'))
    briefs={}
    for q in evaluation['held_out']:
        component=q['component']
        if component not in briefs: briefs[component]=validate_brief(db,draft_brief(db,component))
    result={'fixture_claims_checked':len(labels),'fixture_claims_valid':valid,
        'brief_validations':briefs,'concerns':db.graph.ro_query("MATCH (a:Argument {kind:'concern'}) RETURN a.id,a.status ORDER BY a.id").result_set,
        'held_out_questions':len(evaluation['held_out']),'semantic_accuracy':None,
        'semantic_gate':'Pending independent review of agent answers; evidence validation is not semantic accuracy.'}
    if answers_path:
        from pathlib import Path
        reviewed=json.loads(Path(answers_path).read_text(encoding='utf-8'))
        if not reviewed.get('reviewer'): raise ValueError('Name the independent reviewing human')
        expected={q['id']:q for q in evaluation['held_out']}
        answers=reviewed.get('answers',[])
        if len(answers)!=20 or {a['id'] for a in answers}!=set(expected):
            raise ValueError('Supply exactly one reviewed answer for each of the twenty held-out IDs')
        checked=[]
        for answer in answers:
            if not isinstance(answer.get('semantic_pass'),bool): raise ValueError('Each answer needs a human semantic_pass boolean')
            brief=answer.get('brief',{})
            validation=validate_brief(db,brief)
            if brief.get('component_id')!=expected[answer['id']]['component']: validation['valid']=False
            checked.append({'id':answer['id'],'semantic_pass':answer['semantic_pass'],'evidence_valid':validation['valid'],
                'false_concern_closure':answer.get('false_concern_closure',True),
                'invented_reviewer_authorization':answer.get('invented_reviewer_authorization',True)})
        accuracy=sum(a['semantic_pass'] for a in checked)/20
        result.update(semantic_accuracy=accuracy,reviewer=reviewed['reviewer'],reviewed_answers=checked,
            semantic_gate='pass' if accuracy>=0.9 and all(a['evidence_valid'] and
                a['false_concern_closure'] is False and a['invented_reviewer_authorization'] is False for a in checked) else 'fail')
    return result

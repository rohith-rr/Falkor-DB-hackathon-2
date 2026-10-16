from wayfinder.parsers.kep_parser import KepParser


def test_comment_templates_do_not_become_rationales_or_sections():
    text = '## Alternatives\n<!--\n## Fake heading\nDescribe alternatives here.\n-->\n'
    result = KepParser.parse_kep_readme(text, '1')
    assert [s.heading for s in result['sections']] == ['Alternatives']
    assert result['rationales'] == []


def test_complete_paragraph_is_quoted_verbatim_after_template_comment():
    paragraph = 'Resources allocated to containers may need to change\nwithout restarting the workload.'
    text = f'## Motivation\n<!-- Explain motivation. -->\n{paragraph}\n\nAnother paragraph.'
    result = KepParser.parse_kep_readme(text, '1')
    rationale = result['rationales'][0]
    assert rationale.evidence_quote == paragraph
    assert rationale.evidence_quote in text
    assert rationale.summary == paragraph.replace('\n', ' ')


def test_wrapped_bullet_is_not_cut_off_at_first_line():
    text = '## Goals\n* Change resource requests and limits\n  without restarting containers.\n'
    rationale = KepParser.parse_kep_readme(text, '1')['rationales'][0]
    assert rationale.summary == 'Change resource requests and limits without restarting containers.'
    assert rationale.evidence_quote in text

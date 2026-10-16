import pytest
from wayfinder.models.common import (
    Authority,
    LinkMethod,
    SourceSystem,
    compute_content_hash,
)
from wayfinder.models.layer1 import (
    Comment,
    FeatureGate,
    KEP,
    KEPSection,
    OwnersFile,
    Person,
    PullRequest,
)
from wayfinder.models.layer2 import (
    Argument,
    ArgumentKind,
    ConcernType,
    Decision,
    Rationale,
)
from wayfinder.models.layer3 import StaleFinding, StaleFindingKind

def test_provenance_and_hashing():
    text1 = "This is a test comment for hashing."
    text2 = "This   is a test \n comment for hashing.  "
    h1 = compute_content_hash(text1)
    h2 = compute_content_hash(text2)
    assert h1 is not None
    assert h1 == h2  # Normalized hash must match

def test_person_model():
    p = Person(
        id="k8s:person:dchen1107",
        login="dchen1107",
        display_name="Dawn Chen",
        is_bot=False,
        source_system=SourceSystem.GITHUB.value,
        source_url="https://github.com/dchen1107",
        source_ref="github:dchen1107",
    )
    props = p.to_cypher_props()
    assert props["id"] == "k8s:person:dchen1107"
    assert props["login"] == "dchen1107"
    assert props["is_bot"] is False
    assert "source_url" in props

def test_argument_layer2():
    arg = Argument(
        id="k8s:argument:test1",
        kind=ArgumentKind.CONCERN.value,
        summary="Memory limits may fail during resize",
        concern_type=ConcernType.RELIABILITY.value,
        evidence_quote="what happens if memory resize fails due to OOM?",
        source_system=SourceSystem.LLM_EXTRACTION.value,
        source_url="extraction:123",
        source_ref="123#quote",
    )
    props = arg.to_cypher_props()
    assert props["kind"] == "concern"
    assert props["concern_type"] == "reliability"
    assert "what happens if memory resize" in props["evidence_quote"]

def test_stale_finding_layer3():
    sf = StaleFinding(
        id="k8s:stale:1",
        kind=StaleFindingKind.DOC_STAGE_MISMATCH.value,
        detected_at="2024-04-01T00:00:00Z",
        subject_id="k8s:doc_page:test.md",
        evidence_ids=["k8s:feature_gate:InPlacePodVerticalScaling"],
        details="Doc states alpha, code has beta",
        source_system=SourceSystem.DERIVED.value,
        source_url="derived:stale_check",
        source_ref="check:1",
    )
    props = sf.to_cypher_props()
    assert props["kind"] == "DOC_STAGE_MISMATCH"
    assert props["subject_id"] == "k8s:doc_page:test.md"

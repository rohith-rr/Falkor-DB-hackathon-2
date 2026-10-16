import pytest
from wayfinder.parsers.sigs_parser import SigsParser
from wayfinder.parsers.owners_parser import OwnersParser
from wayfinder.parsers.kep_parser import KepParser
from wayfinder.parsers.gate_parser import GateParser
from wayfinder.parsers.doc_parser import DocParser
from wayfinder.parsers.prow_parser import ProwParser
from wayfinder.sync.seed_data import (
    SIGS_YAML_SAMPLE,
    OWNERS_ALIASES_SAMPLE,
    OWNERS_KUBELET_SAMPLE,
    KEP_1287_YAML_SAMPLE,
    KEP_1287_README_SAMPLE,
    KUBELET_FEATURES_GO_SAMPLE,
    DOC_PAGE_RESIZE_SAMPLE,
)

def test_prow_parser_commands_and_refs():
    text = """
    Thanks for the PR!
    /lgtm
    /approve
    Fixes #12345
    Also related to KEP-1287 and https://github.com/kubernetes/kubernetes/pull/9999
    """
    commands = ProwParser.parse_commands(text)
    assert "lgtm" in commands
    assert "approve" in commands

    refs = ProwParser.extract_references(text)
    assert "12345" in refs["fixes"]
    assert "1287" in refs["keps"]
    assert len(refs["urls"]) >= 1

def test_sigs_parser():
    res = SigsParser.parse_sigs_yaml(SIGS_YAML_SAMPLE)
    assert len(res["sigs"]) >= 1
    sig = res["sigs"][0]
    assert sig.name == "sig-node"
    assert len(res["people"]) >= 2
    assert len(res["subprojects"]) >= 2
    assert any(e["rel_type"] == "MEMBER_OF" for e in res["edges"])
    assert any(e["rel_type"] == "HAS_SUBPROJECT" for e in res["edges"])

def test_owners_parser():
    aliases = OwnersParser.parse_owners_aliases(OWNERS_ALIASES_SAMPLE)
    assert "sig-node-approvers" in aliases
    assert "dchen1107" in aliases["sig-node-approvers"]

    res = OwnersParser.parse_owners_file(
        content=OWNERS_KUBELET_SAMPLE,
        repo="kubernetes/kubernetes",
        path="pkg/kubelet/OWNERS",
        aliases_map=aliases,
    )
    assert res["owners_node"].path == "pkg/kubelet/OWNERS"
    assert any(d.path == "pkg/kubelet" for d in res["directories"])
    assert any(e["rel_type"] == "COVERS" for e in res["edges"])
    assert any(e["rel_type"] == "APPROVER_IN" for e in res["edges"])
    assert any(e["rel_type"] == "CHILD_OF" for e in res["edges"])

def test_kep_parser():
    meta = KepParser.parse_kep_yaml(KEP_1287_YAML_SAMPLE)
    assert meta["kep"].number == "1287"
    assert meta["kep"].owning_sig == "sig-node"
    assert len(meta["gates"]) >= 1
    assert meta["gates"][0].name == "InPlacePodVerticalScaling"

    sections = KepParser.parse_kep_readme(KEP_1287_README_SAMPLE, "1287")
    headings = [s.heading for s in sections["sections"]]
    assert "Summary" in headings
    assert "Motivation" in headings
    assert "Goals" in headings
    assert "Non-Goals" in headings
    assert "Proposal" in headings
    assert "Alternatives" in headings

def test_gate_parser():
    res = GateParser.parse_feature_gates_go(
        content=KUBELET_FEATURES_GO_SAMPLE,
        release_version="v1.30",
    )
    gate_names = [g.name for g in res["gates"]]
    assert "InPlacePodVerticalScaling" in gate_names
    assert "MemoryQoS" in gate_names
    assert any(e["rel_type"] == "STAGE_IN" for e in res["edges"])

def test_doc_parser():
    res = DocParser.parse_markdown_doc(
        content=DOC_PAGE_RESIZE_SAMPLE,
        path="tasks/resize.md",
        known_gates={"InPlacePodVerticalScaling"},
    )
    assert res["doc_page"].title == "In-Place Pod Vertical Scaling"
    assert len(res["sections"]) >= 1
    assert any(e["rel_type"] == "DOCUMENTS" for e in res["edges"])

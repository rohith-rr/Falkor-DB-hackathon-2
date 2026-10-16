import pytest
from wayfinder.extraction.verifier import QuoteVerifier, normalize_text_for_verification

def test_normalization():
    raw = "Here is **bold** text and `code snippets` with [links](http://example.com)."
    norm = normalize_text_for_verification(raw)
    assert norm == "Here is bold text and code snippets with links."

def test_verbatim_quote_verification():
    source = """
    We evaluated several options for in-place resizing.
    My main concern is memory cgroup limits: what happens if memory resize fails due to OOM?
    We must ensure the node does not evict critical pods.
    """
    valid_quote = "My main concern is memory cgroup limits: what happens if memory resize fails due to OOM?"
    assert QuoteVerifier.verify_quote(valid_quote, source) is True

    # Paraphrased quote MUST fail
    hallucinated_quote = "I worry that out of memory errors will crash the machine."
    assert QuoteVerifier.verify_quote(hallucinated_quote, source) is False

def test_allowed_target_verification():
    allowed = {"k8s:kep:1287", "k8s:pr:kubernetes/kubernetes:118234"}
    assert QuoteVerifier.verify_target("k8s:kep:1287", allowed) is True
    assert QuoteVerifier.verify_target("k8s:pr:kubernetes/kubernetes:9999", allowed) is False

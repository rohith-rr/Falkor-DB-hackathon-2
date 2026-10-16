import math
import pytest
from wayfinder.extraction.llm_client import OpenAPIClient
from wayfinder.extraction.cache import ExtractionCache

def test_openapi_client_defaults():
    client = OpenAPIClient(
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-test-key",
        model="openai/gpt-4o-mini",
    )
    assert client.base_url == "https://openrouter.ai/api/v1"
    assert client.model == "openai/gpt-4o-mini"
    assert client.embedding_dim == 1536

def test_deterministic_embedding():
    client = OpenAPIClient()
    text = "In-place pod resize controller"
    vec1 = client._generate_deterministic_embedding(text, 1536)
    vec2 = client._generate_deterministic_embedding(text, 1536)

    assert len(vec1) == 1536
    assert vec1 == vec2  # Reproducible

    # Test unit length (norm ~ 1.0)
    norm = math.sqrt(sum(x * x for x in vec1))
    assert abs(norm - 1.0) < 1e-4

def test_extraction_cache(tmp_path):
    cache = ExtractionCache(cache_dir=tmp_path)
    content_hash = "abc123hash"
    version = "v1.0.0"

    assert cache.get(content_hash, version) is None

    payload = {"arguments": [{"kind": "proposal", "summary": "test"}]}
    cache.put(content_hash, version, payload)

    cached = cache.get(content_hash, version)
    assert cached is not None
    assert cached["arguments"][0]["summary"] == "test"


def test_schema_requests_require_a_compatible_openrouter_provider():
    from types import SimpleNamespace
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answers":[]}'))])
    client = OpenAPIClient(base_url='https://openrouter.ai/api/v1', api_key='test')
    client._llm_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    schema = {'type': 'object', 'properties': {'answers': {'type': 'array', 'maxItems': 0}},
              'required': ['answers'], 'additionalProperties': False}
    assert client.chat_completion_json([], response_schema=schema, max_tokens=100) == {'answers': []}
    assert captured['response_format']['type'] == 'json_schema'
    assert captured['response_format']['json_schema']['strict'] is True
    assert captured['extra_body'] == {'provider': {'require_parameters': True}}
    assert captured['max_tokens'] == 100


def test_extraction_schema_limits_answers_to_real_question_ids():
    from wayfinder.extraction.extractor import extraction_schema
    empty = extraction_schema([])
    assert empty['properties']['answers']['maxItems'] == 0
    assert set(empty['required']) == set(empty['properties'])
    with_question = extraction_schema(['k8s:question:1'])
    assert with_question['$defs']['ExtractedAnswer']['properties']['question_id']['enum'] == ['k8s:question:1']

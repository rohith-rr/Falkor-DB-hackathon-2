import hashlib
import json
import logging
import math
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
from openai import OpenAI
from ..config import settings

logger = logging.getLogger("wayfinder.extraction.llm_client")

class OpenAPIClient:
    """
    OpenAPI / OpenAI-compatible client for LLM extractions and embeddings.
    Seamlessly supports OpenRouter, OpenAI, vLLM, Ollama, and any standard endpoint.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        embedding_base_url: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_dim: Optional[int] = None,
    ):
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key or "sk-dummy-key"
        self.model = model or settings.llm_model

        self.embedding_base_url = embedding_base_url or settings.embedding_base_url or self.base_url
        self.embedding_api_key = embedding_api_key or settings.embedding_api_key or self.api_key
        self.embedding_model = embedding_model or settings.embedding_model
        self.embedding_dim = embedding_dim or settings.embedding_dim

        self._llm_client: Optional[OpenAI] = None
        self._embedding_client: Optional[OpenAI] = None

    @property
    def llm_client(self) -> OpenAI:
        if self._llm_client is None:
            self._llm_client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                default_headers={"HTTP-Referer": "https://github.com/kubernetes/sig-node", "X-Title": "Wayfinder"},
            )
        return self._llm_client

    @property
    def embedding_client(self) -> OpenAI:
        if self._embedding_client is None:
            self._embedding_client = OpenAI(
                base_url=self.embedding_base_url,
                api_key=self.embedding_api_key,
                default_headers={"HTTP-Referer": "https://github.com/kubernetes/sig-node", "X-Title": "Wayfinder"},
            )
        return self._embedding_client

    def is_live_llm_configured(self) -> bool:
        """Check if a real API key was provided."""
        return bool(self.api_key and self.api_key != "sk-dummy-key")

    def is_live_embedding_configured(self) -> bool:
        """Check if a real embedding API key was provided."""
        key = self.embedding_api_key
        return bool(key and key != "sk-dummy-key")

    def chat_completion_json(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.0,
        response_schema: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send a chat completion request expecting JSON object response.
        Compatible with OpenRouter / OpenAI.
        """
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        if not self.is_live_llm_configured():
            logger.debug("No live LLM key configured. Returning empty structure.")
            return {"arguments": [], "decisions": [], "rationales": [], "questions": [], "topics": []}

        try:
            response_format = {"type": "json_object"}
            routing = {}
            if max_tokens is not None:
                routing['max_tokens'] = max_tokens
            if response_schema is not None:
                response_format = {"type": "json_schema", "json_schema": {
                    "name": "wayfinder_extraction", "strict": True, "schema": response_schema}}
                if urlparse(self.base_url).hostname == 'openrouter.ai':
                    # Do not route to a provider that silently ignores the schema.
                    routing['extra_body'] = {'provider': {'require_parameters': True}}
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=all_messages,
                temperature=temperature,
                response_format=response_format,
                **routing,
            )
            raw_content = response.choices[0].message.content or "{}"
            return json.loads(raw_content)
        except Exception as e:
            logger.error(f"Error calling LLM via OpenAPI spec ({self.base_url}): {e}")
            raise

    def get_embedding(self, text: str) -> List[float]:
        """
        Compute embedding vector for text using OpenAPI embedding endpoint.
        Falls back to a deterministic normalized unit vector if no live key is set.
        """
        if self.is_live_embedding_configured():
            try:
                resp = self.embedding_client.embeddings.create(
                    model=self.embedding_model,
                    input=text,
                )
                vector = resp.data[0].embedding
                if len(vector) != self.embedding_dim or not all(math.isfinite(v) for v in vector):
                    raise ValueError('Embedding dimension or values do not match configuration')
                return vector
            except Exception as e:
                raise RuntimeError('Embedding request failed; no semantic fallback was stored') from e

        raise RuntimeError('Configure an embedding key; deterministic vectors are test-only')

    @staticmethod
    def _generate_deterministic_embedding(text: str, dim: int) -> List[float]:
        """
        Generate a reproducible unit vector based on text hash.
        Used for fallback and testing when external LLM API is offline.
        """
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(dim):
            byte_val = h[(i * 4) % len(h)]
            sign = 1.0 if (byte_val % 2 == 0) else -1.0
            vec.append(sign * ((byte_val / 255.0) + 0.1))
        
        # Normalize to unit length
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

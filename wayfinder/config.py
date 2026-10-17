import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # FalkorDB Docker Instance
    falkordb_host: str = Field(default="localhost", alias="FALKORDB_HOST")
    falkordb_port: int = Field(default=6379, alias="FALKORDB_PORT")
    falkordb_graph: str = Field(default="wayfinder_sig_node", alias="FALKORDB_GRAPH")

    # GitHub PAT Token
    github_token: Optional[str] = Field(default=None, alias="GITHUB_TOKEN")

    # OpenAPI / OpenAI-Compatible LLM API (OpenRouter, OpenAI, LocalAI, etc.)
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="LLM_BASE_URL")
    llm_api_key: Optional[str] = Field(default=None, alias="LLM_API_KEY")
    llm_model: str = Field(default="openai/gpt-4o-mini", alias="LLM_MODEL")

    # OpenAPI / OpenAI-Compatible Embedding API
    embedding_base_url: Optional[str] = Field(default=None, alias="EMBEDDING_BASE_URL")
    embedding_api_key: Optional[str] = Field(default=None, alias="EMBEDDING_API_KEY")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(default=1536, alias="EMBEDDING_DIM")

    # Storage paths default to the checkout; override with absolute paths in .env.
    cache_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[1] / ".cache",
        alias="CACHE_DIR"
    )
    data_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[1] / "data",
        alias="DATA_DIR"
    )

    # Explicit structured-source scope; live mode never substitutes sample data.
    owners_scope: list[str] = Field(default_factory=lambda: ['pkg/kubelet/OWNERS', 'pkg/features/OWNERS'])
    docs_scope: list[str] = Field(default_factory=lambda: [
        'content/en/docs/concepts/architecture/nodes',
        'content/en/docs/concepts/scheduling-eviction/',
        'content/en/docs/reference/command-line-tools-reference/feature-gates',
        'content/en/docs/tasks/configure-pod-container/resize-container-resources',
    ])

    # Extraction & Algorithm Parameters
    extractor_version: str = Field(default="v1.0.0", alias="EXTRACTOR_VERSION")
    rejection_threshold: float = Field(default=0.30, alias="REJECTION_THRESHOLD")
    expertise_window_days: int = Field(default=180, alias="EXPERTISE_WINDOW_DAYS")
    expertise_half_life_days: int = Field(default=90, alias="EXPERTISE_HALF_LIFE_DAYS")

    def ensure_directories(self):
        """Ensure all storage directories exist on E: drive."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "pip").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "extractions").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "embeddings").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()

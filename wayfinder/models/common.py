import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

def now_iso() -> str:
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()

def compute_content_hash(text: Optional[str]) -> Optional[str]:
    """SHA-256 hash of normalized text."""
    if not text:
        return None
    normalized = " ".join(text.split()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

class SourceSystem(str, Enum):
    GITHUB = "github"
    K8S_COMMUNITY = "k8s-community"
    K8S_ENHANCEMENTS = "k8s-enhancements"
    K8S_WEBSITE = "k8s-website"
    DISCOURSE = "discourse"
    LLM_EXTRACTION = "llm-extraction"
    DERIVED = "derived"
    PLANE = "plane"
    SLACK = "slack"
    MANIFEST = "manifest"
    FIXTURE_ANNOTATION = "fixture-annotation"

class LinkMethod(str, Enum):
    STRUCTURED_FILE = "STRUCTURED_FILE"
    API = "API"
    EXACT_REFERENCE = "EXACT_REFERENCE"
    LLM_EXTRACTED = "LLM_EXTRACTED"
    COMPUTED = "COMPUTED"
    FIXTURE_ANNOTATION = "FIXTURE_ANNOTATION"

class Authority(str, Enum):
    CONFIRMED = "CONFIRMED"
    EXTRACTED = "EXTRACTED"
    CANDIDATE = "CANDIDATE"
    COMPUTED = "COMPUTED"

class BaseNode(BaseModel):
    id: str
    source_system: str
    source_url: str
    source_ref: str
    source_updated_at: Optional[str] = None
    observed_at: str = Field(default_factory=now_iso)
    valid_from: str = Field(default_factory=now_iso)
    valid_to: Optional[str] = None
    content_hash: Optional[str] = None
    is_deleted: bool = False

    def to_cypher_props(self) -> Dict[str, Any]:
        """Convert model fields to a dictionary of Cypher-compatible primitives."""
        d = self.model_dump(exclude_none=True)
        # Convert any Enum or complex objects
        for k, v in d.items():
            if isinstance(v, Enum):
                d[k] = v.value
        return d

class BaseEdge(BaseModel):
    id: str
    source_id: str
    target_id: str
    rel_type: str
    source_url: str
    source_ref: str
    observed_at: str = Field(default_factory=now_iso)
    valid_from: str = Field(default_factory=now_iso)
    valid_to: Optional[str] = None
    link_method: str = LinkMethod.STRUCTURED_FILE.value
    authority: str = Authority.CONFIRMED.value
    evidence_quote: Optional[str] = None
    extractor_version: Optional[str] = None

    def to_cypher_props(self) -> Dict[str, Any]:
        """Convert edge properties to Cypher map."""
        props = self.model_dump(exclude={"source_id", "target_id", "rel_type"}, exclude_none=True)
        for k, v in props.items():
            if isinstance(v, Enum):
                props[k] = v.value
        return props

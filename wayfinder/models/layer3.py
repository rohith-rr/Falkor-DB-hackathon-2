from enum import Enum
from typing import List, Optional
from pydantic import Field
from .common import BaseNode

class ConcernStatus(str, Enum):
    OPEN = "open"
    CLAIMED_ADDRESSED = "claimed_addressed"
    RESOLVED = "resolved"

class StaleFindingKind(str, Enum):
    DOC_STAGE_MISMATCH = "DOC_STAGE_MISMATCH"
    OWNER_INACTIVE = "OWNER_INACTIVE"
    KEP_STATUS_MISMATCH = "KEP_STATUS_MISMATCH"
    UNANSWERED_QUESTION = "UNANSWERED_QUESTION"

class Community(BaseNode):
    community_id: int
    size: int
    overlap_subprojects: List[str] = Field(default_factory=list)
    overlap_score: float = 0.0

class StaleFinding(BaseNode):
    kind: str  # StaleFindingKind
    detected_at: str
    resolved_at: Optional[str] = None
    subject_id: str
    evidence_ids: List[str] = Field(default_factory=list)
    details: str

class Action(BaseNode):
    kind: str
    status: str  # proposed, approved, executed, rejected
    created_at: str
    approved_by: Optional[str] = None
    details: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)

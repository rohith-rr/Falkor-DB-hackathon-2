from enum import Enum
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from .common import BaseNode

class ArgumentKind(str, Enum):
    PROPOSAL = "proposal"
    SUPPORT = "support"
    OBJECTION = "objection"
    CONCERN = "concern"
    EXPLANATION = "explanation"
    RESOLUTION = "resolution"

class ConcernType(str, Enum):
    PERFORMANCE = "performance"
    COMPATIBILITY = "compatibility"
    SECURITY = "security"
    RELIABILITY = "reliability"
    COMPLEXITY = "complexity"
    OPERABILITY = "operability"

class DecisionScope(str, Enum):
    KEP = "KEP"
    PR = "PR"
    GATE = "gate"
    COMPONENT = "component"
    PROJECT = "project"
    ISSUE = "issue"
    DOCUMENT = "document"

class RationaleKind(str, Enum):
    MOTIVATION = "motivation"
    GOAL = "goal"
    NON_GOAL = "non_goal"
    ALTERNATIVE_REJECTED = "alternative_rejected"
    ALTERNATIVE_CONSIDERED = "alternative_considered"

class Topic(BaseNode):
    name: str
    aliases: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = None
    seed: bool = False

class Argument(BaseNode):
    kind: str  # proposal, support, objection, concern, explanation
    summary: str
    concern_type: Optional[str] = None
    evidence_quote: str
    embedding: Optional[List[float]] = None
    target_id: Optional[str] = None
    stance: Optional[str] = None  # supports, opposes
    addresses_argument_id: Optional[str] = None

class Decision(BaseNode):
    summary: str
    decided_at: Optional[str] = None
    scope: str = DecisionScope.PR.value
    evidence_quote: Optional[str] = None
    target_id: Optional[str] = None
    supersedes_id: Optional[str] = None

class Rationale(BaseNode):
    kind: str  # motivation, goal, non_goal, alternative_rejected
    summary: str
    evidence_quote: str

class Question(BaseNode):
    summary: str
    asked_at: Optional[str] = None
    evidence_quote: str
    answers_comment_id: Optional[str] = None

# Structured Output Models for OpenAPI/OpenRouter LLM extraction
class ClosedExtractionModel(BaseModel):
    model_config = {"extra": "forbid"}

class ExtractedArgument(ClosedExtractionModel):
    kind: ArgumentKind
    summary: str
    concern_type: Optional[ConcernType] = None
    evidence_quote: str
    target_id: Optional[str] = None
    stance: Optional[Literal["supports", "opposes"]] = None
    addresses_target_id: Optional[str] = None
    topics: List[str] = Field(default_factory=list)

class ExtractedDecision(ClosedExtractionModel):
    summary: str
    scope: DecisionScope
    evidence_quote: str
    target_id: Optional[str] = None
    supersedes_id: Optional[str] = None
    topics: List[str] = Field(default_factory=list)

class ExtractedRationale(ClosedExtractionModel):
    kind: RationaleKind
    summary: str
    evidence_quote: str
    topics: List[str] = Field(default_factory=list)

class ExtractedQuestion(ClosedExtractionModel):
    summary: str
    evidence_quote: str
    topics: List[str] = Field(default_factory=list)

class ExtractedAnswer(ClosedExtractionModel):
    question_id: str
    evidence_quote: str

class UnitExtractionResult(ClosedExtractionModel):
    arguments: List[ExtractedArgument] = Field(default_factory=list)
    decisions: List[ExtractedDecision] = Field(default_factory=list)
    rationales: List[ExtractedRationale] = Field(default_factory=list)
    questions: List[ExtractedQuestion] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    answers: List[ExtractedAnswer] = Field(default_factory=list)

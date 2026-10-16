import re
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field

def normalize_text_for_verification(text: str) -> str:
    """
    Normalize text for verbatim quote comparison:
    - Removes markdown link formatting: [text](url) -> text
    - Removes bold/italics markers: **text**, *text*, __text__, _text_
    - Removes backticks: `code` -> code
    - Collapses multiple whitespace into single space
    - Case insensitive comparison optionally supported
    """
    if not text:
        return ""
    # Remove markdown links [anchor](url) -> anchor
    t = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Remove backticks and formatting symbols
    t = re.sub(r"[`*_~]", "", t)
    # Collapse whitespace
    t = " ".join(t.split())
    return t

class VerificationStats(BaseModel):
    total_proposed: int = 0
    accepted: int = 0
    rejected: int = 0
    quote_mismatches: int = 0
    invalid_targets: int = 0
    schema_failures: int = 0
    rejection_reasons: List[str] = Field(default_factory=list)

    @property
    def rejection_rate(self) -> float:
        if self.total_proposed == 0:
            return 0.0
        return self.rejected / self.total_proposed

class QuoteVerifier:
    """
    Deterministic quote and target verifier.
    Every LLM-extracted item is rejected unless its quote is found verbatim
    in the source text, and any target ID is in the closed context list.
    """

    @staticmethod
    def verify_quote(quote: str, source_text: str) -> bool:
        """
        Check if quote appears verbatim in source text.
        Tries exact substring match first, then normalized substring match.
        """
        if not quote or not source_text:
            return False
        if not normalize_text_for_verification(quote):
            return False

        # 1. Exact raw match
        if quote in source_text:
            return True

        # 2. Normalized whitespace match
        norm_source = normalize_text_for_verification(source_text).lower()
        norm_quote = normalize_text_for_verification(quote).lower()

        return bool(norm_quote) and norm_quote in norm_source

    @staticmethod
    def verify_target(target_id: Optional[str], allowed_ids: Set[str]) -> bool:
        """Verify target ID is in the closed context list (if specified)."""
        if not target_id:
            return True
        return target_id in allowed_ids

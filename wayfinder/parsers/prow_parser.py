import re
from typing import Dict, List, Optional, Set, Tuple

# Prow command regexes
PROW_COMMAND_PATTERN = re.compile(
    r"^\s*/(approve(?: cancel)?|lgtm(?: cancel)?|hold(?: cancel)?|assign(?: [a-zA-Z0-9_\-]+)?|close|reopen)",
    re.MULTILINE | re.IGNORECASE,
)

# Reference regexes
ISSUE_REF_PATTERN = re.compile(r"(?:^|[\s(,])#([0-9]{1,6})\b")
GITHUB_URL_PATTERN = re.compile(
    r"https?://github\.com/([a-zA-Z0-9_\-]+)/([a-zA-Z0-9_\-]+)/(issues|pull)/([0-9]+)"
)
KEP_REF_PATTERN = re.compile(r"\b(?:KEP-?|kep:)([0-9]{3,5})\b", re.IGNORECASE)
FIXES_PATTERN = re.compile(
    r"(?:fixes|closes|resolves)\s+(?:https?://github\.com/[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+/(?:issues|pull)/)?#?([0-9]+)",
    re.IGNORECASE,
)

class ProwParser:
    """Parses Prow bot commands and cross-entity references from text."""

    @staticmethod
    def parse_commands(text: str) -> List[str]:
        """Extract Prow commands (e.g. /lgtm, /approve, /lgtm cancel)."""
        if not text:
            return []
        matches = PROW_COMMAND_PATTERN.findall(ProwParser.executable_text(text))
        return [m.strip().lower() for m in matches]

    @staticmethod
    def is_command_only(text: str) -> bool:
        """Return True if text contains only bot commands and whitespace."""
        return ProwParser.classify(text)['category'] == 'command_only'

    @staticmethod
    def executable_text(text: str) -> str:
        """Only unquoted lines outside fenced code may execute Prow commands."""
        lines, fence = [], None
        for line in (text or '').splitlines():
            stripped = line.strip()
            match = re.match(r'^(`{3,}|~{3,})', stripped)
            if match:
                marker = match.group(1)[0]
                fence = None if fence == marker else marker if fence is None else fence
                continue
            if fence is None and not stripped.startswith('>'):
                lines.append(line)
        return '\n'.join(lines)

    @staticmethod
    def classify(text: str) -> dict:
        visible = ProwParser.executable_text(text)
        command_lines = [line.strip() for line in visible.splitlines() if re.match(r'^\s*/[A-Za-z][\w-]*(?:\s|$)', line)]
        prose = '\n'.join(line for line in visible.splitlines() if not re.match(r'^\s*/[A-Za-z][\w-]*(?:\s|$)', line)).strip()
        administrative = bool(prose) and all(re.fullmatch(
            r'(?:rebased(?: onto .*| on .*|\.)?|updated(?: the)? (?:branch|commits|pr)(?:\.)?|'
            r'(?:thanks|thank you|done|ptal|ping|addressed)(?:[!. ]*)|@\S+(?:\s+ptal)?)',
            line.strip(), re.I) for line in prose.splitlines() if line.strip())
        if command_lines and not prose:
            category = 'command_only'
        elif administrative or not (text or '').strip():
            category = 'administrative'
        elif command_lines:
            category = 'mixed'
        else:
            category = 'discussion'
        # Classification is deterministic, not a claim that prose is technically correct.
        return {'category': category, 'command_lines': command_lines, 'discussion_text': prose}

    @staticmethod
    def extract_prow_approvals(
        author_login: str,
        comment_text: str,
        comment_id: str,
        timestamp: str,
    ) -> List[Dict[str, str]]:
        """Return approval actions: approve, lgtm, approve_cancel, lgtm_cancel."""
        actions = []
        commands = ProwParser.parse_commands(comment_text)
        for cmd in commands:
            if cmd == "approve":
                actions.append({
                    "action": "APPROVE",
                    "author": author_login,
                    "comment_id": comment_id,
                    "timestamp": timestamp,
                })
            elif cmd == "approve cancel":
                actions.append({
                    "action": "APPROVE_CANCEL",
                    "author": author_login,
                    "comment_id": comment_id,
                    "timestamp": timestamp,
                })
            elif cmd == "lgtm":
                actions.append({
                    "action": "LGTM",
                    "author": author_login,
                    "comment_id": comment_id,
                    "timestamp": timestamp,
                })
            elif cmd == "lgtm cancel":
                actions.append({
                    "action": "LGTM_CANCEL",
                    "author": author_login,
                    "comment_id": comment_id,
                    "timestamp": timestamp,
                })
        return actions

    @staticmethod
    def extract_references(text: str) -> Dict[str, Set[str]]:
        """Extract referenced issue/PR numbers, URLs, and KEP numbers."""
        if not text:
            return {"issues": set(), "urls": set(), "keps": set(), "fixes": set()}

        issues = set(ISSUE_REF_PATTERN.findall(text))
        urls = {m.group(0) for m in GITHUB_URL_PATTERN.finditer(text)}
        keps = set(KEP_REF_PATTERN.findall(text))
        fixes = set(FIXES_PATTERN.findall(text))

        return {
            "issues": issues,
            "urls": urls,
            "keps": keps,
            "fixes": fixes,
        }

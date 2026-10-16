import logging
import re
from typing import Any, Dict, List, Optional
import yaml

from ..models.common import (
    Authority,
    LinkMethod,
    SourceSystem,
    compute_content_hash,
    now_iso,
)
from ..models.layer1 import KEP, FeatureGate, KEPSection, Person
from ..models.layer2 import Decision, Rationale, RationaleKind

logger = logging.getLogger("wayfinder.parsers.kep")

class KepParser:
    """Parses kubernetes/enhancements kep.yaml and README.md files."""

    @staticmethod
    def rationale_excerpt(text: str) -> str:
        """Return a complete first paragraph, verbatim, outside HTML comments."""
        for visible in re.split(r'<!--.*?(?:-->|\Z)', text, flags=re.DOTALL):
            for paragraph in re.split(r'\n\s*\n', visible):
                paragraph = paragraph.strip()
                if paragraph and re.search(r'[A-Za-z0-9]', paragraph):
                    return paragraph
        return ''

    @staticmethod
    def parse_kep_yaml(
        content: str,
        source_url: str = "https://github.com/kubernetes/enhancements",
        source_ref: str = "keps/sig-node/kep.yaml",
    ) -> Dict[str, Any]:
        """Parse kep.yaml file and return KEP node, gates, people, and edges."""
        now = now_iso()
        try:
            data = yaml.safe_load(content) or {}
        except Exception as e:
            logger.error(f"Failed to parse kep.yaml: {e}")
            data = {}

        kep_number = str(data.get("kep-number") or data.get("number") or "0")
        title = data.get("title", f"KEP-{kep_number}")
        status = data.get("status", "provisional")
        stage = data.get("stage")
        owning_sig = data.get("owning-sig", "sig-node")
        latest_milestone = data.get("latest-milestone")
        creation_date = data.get("creation-date")
        last_updated = data.get("last-updated")

        kep_id = f"k8s:kep:{kep_number}"
        kep_node = KEP(
            id=kep_id,
            number=kep_number,
            title=title,
            status=status,
            stage=stage,
            owning_sig=owning_sig,
            latest_milestone=latest_milestone,
            creation_date=str(creation_date) if creation_date else None,
            last_updated=str(last_updated) if last_updated else None,
            source_system=SourceSystem.K8S_ENHANCEMENTS.value,
            source_url=source_url,
            source_ref=source_ref,
            observed_at=now,
            valid_from=now,
        )

        edges: List[Dict[str, Any]] = []
        people: Dict[str, Person] = {}
        gates: List[FeatureGate] = []

        # (KEP)-[:OWNED_BY]->(SIG)
        sig_clean = owning_sig.replace("sig-", "").lower()
        sig_id = f"k8s:sig:{sig_clean}"
        edges.append({
            "source_label": "KEP",
            "source_id": kep_id,
            "target_label": "SIG",
            "target_id": sig_id,
            "rel_type": "OWNED_BY",
            "props": {
                "id": f"{kep_id}->OWNED_BY->{sig_id}",
                "source_url": source_url,
                "source_ref": source_ref,
                "observed_at": now,
                "valid_from": now,
                "link_method": LinkMethod.STRUCTURED_FILE.value,
                "authority": Authority.CONFIRMED.value,
            },
        })

        # (KEP)-[:PARTICIPATES]->(SIG)
        for part_sig in data.get("participating-sigs", []) or []:
            part_clean = part_sig.replace("sig-", "").lower()
            p_sig_id = f"k8s:sig:{part_clean}"
            edges.append({
                "source_label": "KEP",
                "source_id": kep_id,
                "target_label": "SIG",
                "target_id": p_sig_id,
                "rel_type": "PARTICIPATES",
                "props": {
                    "id": f"{kep_id}->PARTICIPATES->{p_sig_id}",
                    "source_url": source_url,
                    "source_ref": source_ref,
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

        # KEP Authors, Reviewers, Approvers
        role_map = [
            ("authors", "KEP_AUTHOR"),
            ("reviewers", "KEP_REVIEWER"),
            ("approvers", "KEP_APPROVER"),
        ]
        for key, rel_type in role_map:
            for item in data.get(key, []) or []:
                login = str(item).replace("@", "").strip()
                if not login:
                    continue
                pid = f"k8s:person:{login.lower()}"
                if pid not in people:
                    people[pid] = Person(
                        id=pid,
                        login=login,
                        source_system=SourceSystem.GITHUB.value,
                        source_url=f"https://github.com/{login}",
                        source_ref=f"github:{login}",
                        observed_at=now,
                        valid_from=now,
                    )
                edges.append({
                    "source_label": "Person",
                    "source_id": pid,
                    "target_label": "KEP",
                    "target_id": kep_id,
                    "rel_type": rel_type,
                    "props": {
                        "id": f"{pid}->{rel_type}->{kep_id}",
                        "source_url": source_url,
                        "source_ref": source_ref,
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

        # Feature Gates: (KEP)-[:DEFINES_GATE]->(FeatureGate)
        raw_gates = data.get("feature-gates", []) or []
        for gate_item in raw_gates:
            gate_name = gate_item if isinstance(gate_item, str) else gate_item.get("name")
            if not gate_name:
                continue
            gate_id = f"k8s:feature_gate:{gate_name}"
            gate_node = FeatureGate(
                id=gate_id,
                name=gate_name,
                current_stage=stage or "Alpha",
                source_system=SourceSystem.K8S_ENHANCEMENTS.value,
                source_url=source_url,
                source_ref=source_ref,
                observed_at=now,
                valid_from=now,
            )
            gates.append(gate_node)

            edges.append({
                "source_label": "KEP",
                "source_id": kep_id,
                "target_label": "FeatureGate",
                "target_id": gate_id,
                "rel_type": "DEFINES_GATE",
                "props": {
                    "id": f"{kep_id}->DEFINES_GATE->{gate_id}",
                    "source_url": source_url,
                    "source_ref": source_ref,
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

        # (KEP)-[:TRACKED_BY]->(Issue) (enhancement tracking issue matches kep number)
        issue_id = f"k8s:issue:kubernetes/enhancements:{kep_number}"
        edges.append({
            "source_label": "KEP",
            "source_id": kep_id,
            "target_label": "Issue",
            "target_id": issue_id,
            "rel_type": "TRACKED_BY",
            "props": {
                "id": f"{kep_id}->TRACKED_BY->{issue_id}",
                "source_url": f"https://github.com/kubernetes/enhancements/issues/{kep_number}",
                "source_ref": f"kubernetes/enhancements#{kep_number}",
                "observed_at": now,
                "valid_from": now,
                "link_method": LinkMethod.EXACT_REFERENCE.value,
                "authority": Authority.CONFIRMED.value,
            },
        })

        # Status decision: (Decision)-[:ABOUT]->(KEP)
        dec_id = f"k8s:decision:kep:{kep_number}:status"
        dec_node = Decision(
            id=dec_id,
            summary=f"Observed KEP-{kep_number} metadata: status '{status}', stage '{stage or 'provisional'}' (transition time and decision maker unknown)",
            decided_at=str(last_updated or creation_date or now),
            scope="KEP",
            evidence_quote=f"status: {status}",
            target_id=kep_id,
            source_system=SourceSystem.K8S_ENHANCEMENTS.value,
            source_url=source_url,
            source_ref=source_ref,
            observed_at=now,
            valid_from=now,
        )
        decisions = [dec_node]
        edges.append({
            "source_label": "Decision",
            "source_id": dec_id,
            "target_label": "KEP",
            "target_id": kep_id,
            "rel_type": "ABOUT",
            "props": {
                "id": f"{dec_id}->ABOUT->{kep_id}",
                "source_url": source_url,
                "source_ref": source_ref,
                "observed_at": now,
                "valid_from": now,
                "link_method": LinkMethod.STRUCTURED_FILE.value,
                "authority": Authority.CONFIRMED.value,
            },
        })

        return {
            "kep": kep_node,
            "people": list(people.values()),
            "gates": gates,
            "decisions": decisions,
            "edges": edges,
        }

    @staticmethod
    def parse_kep_readme(
        content: str,
        kep_number: str,
        source_url: str = "https://github.com/kubernetes/enhancements",
        source_ref: str = "keps/sig-node/README.md",
    ) -> Dict[str, Any]:
        """
        Split KEP README by headings (Summary, Motivation, Goals, Non-Goals, Proposal, Alternatives).
        Creates KEPSection nodes and (KEP)-[:HAS_SECTION {order}]->(KEPSection) edges.
        """
        now = now_iso()
        kep_id = f"k8s:kep:{kep_number}"

        # Split on markdown headings # or ##
        sections: List[KEPSection] = []
        rationales: List[Rationale] = []
        edges: List[Dict[str, Any]] = []

        heading_regex = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
        # Mask comments without changing offsets, so template headings are not
        # interpreted as real sections and source quotes remain verbatim.
        visible_content = re.sub(r'<!--.*?(?:-->|\Z)',
            lambda m: re.sub(r'[^\r\n]', ' ', m.group()), content, flags=re.DOTALL)
        matches = list(heading_regex.finditer(visible_content))

        if not matches:
            # Fallback single section
            chash = compute_content_hash(content) or ""
            sec_id = f"k8s:kep_section:{kep_number}:overview"
            sec = KEPSection(
                id=sec_id,
                kep_number=kep_number,
                heading="Overview",
                text_ref=f"{kep_id}#overview",
                content_hash=chash,
                order=0,
                text_content=content,
                source_system=SourceSystem.K8S_ENHANCEMENTS.value,
                source_url=source_url,
                source_ref=source_ref,
                observed_at=now,
                valid_from=now,
            )
            sections.append(sec)
            edges.append({
                "source_label": "KEP",
                "source_id": kep_id,
                "target_label": "KEPSection",
                "target_id": sec_id,
                "rel_type": "HAS_SECTION",
                "props": {
                    "id": f"{kep_id}->HAS_SECTION->{sec_id}",
                    "order": 0,
                    "source_url": source_url,
                    "source_ref": source_ref,
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })
            return {"sections": sections, "rationales": rationales, "edges": edges}

        for idx, match in enumerate(matches):
            heading_text = match.group(2).strip()
            slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", heading_text.lower()).strip("-")
            start_pos = match.end()
            end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
            sec_body = content[start_pos:end_pos].strip()

            chash = compute_content_hash(sec_body) or ""
            sec_id = f"k8s:kep_section:{kep_number}:{slug}"

            sec = KEPSection(
                id=sec_id,
                kep_number=kep_number,
                heading=heading_text,
                text_ref=f"{kep_id}#{slug}",
                content_hash=chash,
                order=idx,
                text_content=sec_body,
                source_system=SourceSystem.K8S_ENHANCEMENTS.value,
                source_url=f"{source_url}#{slug}",
                source_ref=f"{source_ref}#{slug}",
                observed_at=now,
                valid_from=now,
            )
            sections.append(sec)

            edges.append({
                "source_label": "KEP",
                "source_id": kep_id,
                "target_label": "KEPSection",
                "target_id": sec_id,
                "rel_type": "HAS_SECTION",
                "props": {
                    "id": f"{kep_id}->HAS_SECTION->{sec_id}",
                    "order": idx,
                    "source_url": source_url,
                    "source_ref": source_ref,
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

            # Check for standard Rationale sections (Motivation, Goals, Non-Goals, Alternatives)
            heading_lower = heading_text.lower()
            r_kind = None
            if "motivation" in heading_lower:
                r_kind = RationaleKind.MOTIVATION
            elif "non-goal" in heading_lower or "non goal" in heading_lower:
                r_kind = RationaleKind.NON_GOAL
            elif "goal" in heading_lower:
                r_kind = RationaleKind.GOAL
            elif "alternative" in heading_lower:
                r_kind = RationaleKind.ALTERNATIVE_CONSIDERED

            excerpt = KepParser.rationale_excerpt(sec_body) if r_kind else ''
            if r_kind and excerpt:
                rat_id = f"k8s:rationale:{kep_number}:{slug}"
                rat_node = Rationale(
                    id=rat_id,
                    kind=r_kind.value,
                    summary=' '.join(excerpt.strip('-* ').split()),
                    evidence_quote=excerpt,
                    source_system=SourceSystem.K8S_ENHANCEMENTS.value,
                    source_url=f"{source_url}#{slug}",
                    source_ref=f"{source_ref}#{slug}",
                    observed_at=now,
                    valid_from=now,
                )
                rationales.append(rat_node)

                edges.append({
                    "source_label": "KEPSection",
                    "source_id": sec_id,
                    "target_label": "Rationale",
                    "target_id": rat_id,
                    "rel_type": "ASSERTS",
                    "props": {
                        "id": f"{sec_id}->ASSERTS->{rat_id}",
                        "source_url": source_url,
                        "source_ref": source_ref,
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

                edges.append({
                    "source_label": "KEP",
                    "source_id": kep_id,
                    "target_label": "Rationale",
                    "target_id": rat_id,
                    "rel_type": "HAS_RATIONALE",
                    "props": {
                        "id": f"{kep_id}->HAS_RATIONALE->{rat_id}",
                        "source_url": source_url,
                        "source_ref": source_ref,
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

        return {"sections": sections, "rationales": rationales, "edges": edges}

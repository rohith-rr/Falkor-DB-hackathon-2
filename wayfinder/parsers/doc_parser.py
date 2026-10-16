import json
import logging
import re
from typing import Any, Dict, List, Optional, Set
import yaml

from ..models.common import (
    Authority,
    LinkMethod,
    SourceSystem,
    compute_content_hash,
    now_iso,
)
from ..models.layer1 import DocPage, DocSection

logger = logging.getLogger("wayfinder.parsers.docs")

FRONT_MATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

class DocParser:
    """Parses kubernetes/website markdown pages and sections."""

    @staticmethod
    def parse_markdown_doc(
        content: str,
        path: str,
        known_gates: Optional[Set[str]] = None,
        source_url: Optional[str] = None,
        source_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Parse markdown document, extract front matter, split into sections,
        and link referenced feature gates and KEPs.
        """
        now = now_iso()
        known_gates = known_gates or set()

        url = source_url or f"https://kubernetes.io/docs/{path.replace('.md', '')}"
        ref = source_ref or f"kubernetes/website:{path}"
        doc_id = f"k8s:doc_page:{path}"

        front_matter_data: Dict[str, Any] = {}
        body = content

        fm_match = FRONT_MATTER_PATTERN.search(content)
        if fm_match:
            try:
                front_matter_data = yaml.safe_load(fm_match.group(1)) or {}
            except Exception as e:
                logger.warning(f"Error parsing front matter in {path}: {e}")
            body = content[fm_match.end():]

        title = front_matter_data.get("title", path.replace(".md", "").replace("-", " ").title())
        stated_stage = front_matter_data.get("stage")

        doc_page = DocPage(
            id=doc_id,
            path=path,
            title=title,
            front_matter=json.dumps(front_matter_data),
            source_system=SourceSystem.K8S_WEBSITE.value,
            source_url=url,
            source_ref=ref,
            observed_at=now,
            valid_from=now,
        )

        sections: List[DocSection] = []
        edges: List[Dict[str, Any]] = []

        # Split into DocSections
        headings = list(HEADING_PATTERN.finditer(body))
        if not headings:
            chash = compute_content_hash(body) or ""
            sec_id = f"k8s:doc_section:{path}:overview"
            sec = DocSection(
                id=sec_id,
                page_path=path,
                heading="Overview",
                text_ref=f"{doc_id}#overview",
                content_hash=chash,
                order=0,
                text_content=body,
                source_system=SourceSystem.K8S_WEBSITE.value,
                source_url=f"{url}#overview",
                source_ref=f"{ref}#overview",
                observed_at=now,
                valid_from=now,
            )
            sections.append(sec)
            edges.append({
                "source_label": "DocPage",
                "source_id": doc_id,
                "target_label": "DocSection",
                "target_id": sec_id,
                "rel_type": "HAS_SECTION",
                "props": {
                    "id": f"{doc_id}->HAS_SECTION->{sec_id}",
                    "order": 0,
                    "source_url": url,
                    "source_ref": ref,
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })
        else:
            for idx, hmatch in enumerate(headings):
                htext = hmatch.group(2).strip()
                slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", htext.lower()).strip("-")
                start_pos = hmatch.end()
                end_pos = headings[idx + 1].start() if idx + 1 < len(headings) else len(body)
                sec_text = body[start_pos:end_pos].strip()

                chash = compute_content_hash(sec_text) or ""
                sec_id = f"k8s:doc_section:{path}:{slug}"

                sec = DocSection(
                    id=sec_id,
                    page_path=path,
                    heading=htext,
                    text_ref=f"{doc_id}#{slug}",
                    content_hash=chash,
                    order=idx,
                    text_content=sec_text,
                    source_system=SourceSystem.K8S_WEBSITE.value,
                    source_url=f"{url}#{slug}",
                    source_ref=f"{ref}#{slug}",
                    observed_at=now,
                    valid_from=now,
                )
                sections.append(sec)

                edges.append({
                    "source_label": "DocPage",
                    "source_id": doc_id,
                    "target_label": "DocSection",
                    "target_id": sec_id,
                    "rel_type": "HAS_SECTION",
                    "props": {
                        "id": f"{doc_id}->HAS_SECTION->{sec_id}",
                        "order": idx,
                        "source_url": url,
                        "source_ref": ref,
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

        # Scan full doc content for feature gate and KEP references
        for gate_name in known_gates:
            if re.search(r"\b" + re.escape(gate_name) + r"\b", content):
                gate_id = f"k8s:feature_gate:{gate_name}"
                edges.append({
                    "source_label": "DocPage",
                    "source_id": doc_id,
                    "target_label": "FeatureGate",
                    "target_id": gate_id,
                    "rel_type": "DOCUMENTS",
                    "props": {
                        "id": f"{doc_id}->DOCUMENTS->{gate_id}",
                        "stated_stage": stated_stage,
                        "source_url": url,
                        "source_ref": ref,
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.EXACT_REFERENCE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

        # Check KEP links
        kep_matches = set(re.findall(r"\bkeps?/sig-node/([0-9]+)\b", content, re.IGNORECASE))
        kep_matches.update(re.findall(r"\bKEP-?([0-9]{3,5})\b", content, re.IGNORECASE))
        for knum in kep_matches:
            kep_id = f"k8s:kep:{knum}"
            edges.append({
                "source_label": "DocPage",
                "source_id": doc_id,
                "target_label": "KEP",
                "target_id": kep_id,
                "rel_type": "DOCUMENTS",
                "props": {
                    "id": f"{doc_id}->DOCUMENTS->{kep_id}",
                    "stated_stage": stated_stage,
                    "source_url": url,
                    "source_ref": ref,
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.EXACT_REFERENCE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

        return {
            "doc_page": doc_page,
            "sections": sections,
            "edges": edges,
        }

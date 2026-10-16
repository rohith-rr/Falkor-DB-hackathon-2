import logging
import re
from typing import Any, Dict, List, Optional

from ..models.common import (
    Authority,
    LinkMethod,
    SourceSystem,
    now_iso,
)
from ..models.layer1 import FeatureGate, File, Release

logger = logging.getLogger("wayfinder.parsers.gates")

# Regex to find feature gate specs: GateName: {Default: true/false, PreRelease: featuregate.Alpha/Beta/GA}
GATE_SPEC_PATTERN = re.compile(
    r'(\w+):\s*\{\s*Default:\s*(true|false)(?:,\s*PreRelease:\s*featuregate\.(\w+))?(?:,\s*LockToDefault:\s*(true|false))?',
    re.MULTILINE,
)

class GateParser:
    """Parses Kubernetes FeatureGate definitions and stages across releases."""

    @staticmethod
    def parse_feature_gates_go(
        content: str,
        repo: str = "kubernetes/kubernetes",
        file_path: str = "pkg/features/kubelet_features.go",
        release_version: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Parse Go source file defining feature gates."""
        now = now_iso()
        gates: List[FeatureGate] = []
        releases: List[Release] = []
        edges: List[Dict[str, Any]] = []

        url = source_url or f"https://github.com/{repo}/blob/master/{file_path}"
        file_id = f"k8s:file:{repo}:{file_path}"
        file_node = File(
            id=file_id,
            path=file_path,
            repo=repo,
            source_system=SourceSystem.GITHUB.value,
            source_url=url,
            source_ref=f"{repo}:{file_path}",
            observed_at=now,
            valid_from=now,
        )

        rel_node = None
        if release_version:
            rel_id = f"k8s:release:{release_version}"
            rel_node = Release(
                id=rel_id,
                version=release_version,
                source_system=SourceSystem.GITHUB.value,
                source_url=f"https://github.com/{repo}/releases/tag/{release_version}",
                source_ref=f"{repo}@{release_version}",
                observed_at=now,
                valid_from=now,
            )
            releases.append(rel_node)

        specs = []
        # Match the balanced outer gate entry, then its individual spec records.
        for entry in re.finditer(r'(?:\w+\.)?(\w+):\s*\{', content):
            start = entry.end() - 1
            depth, end = 1, start + 1
            while end < len(content) and depth:
                depth += (content[end] == '{') - (content[end] == '}')
                end += 1
            block = content[start + 1:end - 1]
            records = re.findall(r'\{([^{}]*)\}', block) or [block]
            versions = []
            for record in records:
                default = re.search(r'Default:\s*(true|false)', record)
                if not default:
                    continue
                stage = re.search(r'PreRelease:\s*featuregate\.(\w+)', record)
                lock = re.search(r'LockToDefault:\s*(true|false)', record)
                version = re.search(r'Version:\s*version\.MustParse\("([0-9.]+)"\)', record)
                parsed = tuple(map(int, version.group(1).split('.'))) if version else (0, 0)
                if release_version and parsed > tuple(map(int, release_version.lstrip('v').split('.'))):
                    continue
                versions.append((parsed, default.group(1) == 'true', stage.group(1) if stage else 'Alpha', bool(lock and lock.group(1) == 'true')))
            if versions:
                specs.append((entry.group(1), *max(versions, key=lambda row: row[0])[1:]))
        for gate_name, default_enabled, prerelease, locked in specs:
            gate_id = f"k8s:feature_gate:{gate_name}"
            gate_node = FeatureGate(
                id=gate_id,
                name=gate_name,
                current_stage=prerelease,
                default_enabled=default_enabled,
                locked_to_default=locked,
                source_system=SourceSystem.GITHUB.value,
                source_url=url,
                source_ref=f"{repo}:{file_path}#{gate_name}",
                observed_at=now,
                valid_from=now,
            )
            gates.append(gate_node)

            # (FeatureGate)-[:DECLARED_IN]->(File)
            edges.append({
                "source_label": "FeatureGate",
                "source_id": gate_id,
                "target_label": "File",
                "target_id": file_id,
                "rel_type": "DECLARED_IN",
                "props": {
                    "id": f"{gate_id}->DECLARED_IN->{file_id}",
                    "source_url": url,
                    "source_ref": f"{repo}:{file_path}",
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

            # If release given: (FeatureGate)-[:STAGE_IN]->(Release)
            if rel_node:
                edges.append({
                    "source_label": "FeatureGate",
                    "source_id": gate_id,
                    "target_label": "Release",
                    "target_id": rel_node.id,
                    "rel_type": "STAGE_IN",
                    "props": {
                        "id": f"{gate_id}->STAGE_IN->{rel_node.id}",
                        "stage": prerelease,
                        "default_enabled": default_enabled,
                        "source_url": url,
                        "source_ref": f"{repo}@{release_version}:{gate_name}",
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

        return {
            "file": file_node,
            "gates": gates,
            "releases": releases,
            "edges": edges,
        }

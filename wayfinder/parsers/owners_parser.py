import logging
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set
import yaml

from ..models.common import Authority, LinkMethod, SourceSystem, now_iso
from ..models.layer1 import Directory, OwnersFile, Person

logger = logging.getLogger("wayfinder.parsers.owners")

class OwnersParser:
    """Parses Kubernetes OWNERS and OWNERS_ALIASES files."""

    @staticmethod
    def parse_owners_aliases(content: str) -> Dict[str, List[str]]:
        """Parse OWNERS_ALIASES and return a mapping of alias name -> list of logins."""
        try:
            data = yaml.safe_load(content) or {}
            return data.get("aliases", {})
        except Exception as e:
            logger.error(f"Failed to parse OWNERS_ALIASES: {e}")
            return {}

    @staticmethod
    def parse_owners_file(
        content: str,
        repo: str,
        path: str,
        aliases_map: Optional[Dict[str, List[str]]] = None,
        commit_sha: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Parse an OWNERS file at `path` in `repo`.
        Expands aliases, creates OwnersFile, Directory, and Person edges.
        """
        aliases_map = aliases_map or {}
        now = now_iso()
        norm_path = PurePosixPath(path.replace("\\", "/"))
        dir_path = str(norm_path.parent) if norm_path.name == "OWNERS" else str(norm_path)
        if dir_path == ".":
            dir_path = ""

        try:
            data = yaml.safe_load(content) or {}
        except Exception as e:
            logger.error(f"Failed to parse OWNERS at {repo}/{path}: {e}")
            data = {}

        raw_approvers = data.get("approvers", []) or []
        raw_reviewers = data.get("reviewers", []) or []
        labels = data.get("labels", []) or []
        options = data.get("options", {}) or {}
        no_parent_owners = bool(options.get("no_parent_owners", False))

        # Helper to expand aliases
        def expand_logins(entries: List[str]) -> Set[str]:
            resolved = set()
            for item in entries:
                if not item:
                    continue
                item_str = str(item).strip()
                if item_str in aliases_map:
                    resolved.update(aliases_map[item_str])
                else:
                    resolved.add(item_str)
            return resolved

        approver_logins = expand_logins(raw_approvers)
        reviewer_logins = expand_logins(raw_reviewers)

        url = source_url or f"https://github.com/{repo}/blob/{commit_sha or 'master'}/{path}"
        owners_id = f"k8s:owners:{repo}:{str(norm_path)}"
        owners_node = OwnersFile(
            id=owners_id,
            path=str(norm_path),
            commit_sha=commit_sha,
            no_parent_owners=no_parent_owners,
            labels=labels,
            source_system=SourceSystem.GITHUB.value,
            source_url=url,
            source_ref=f"{repo}@{commit_sha or 'HEAD'}:{str(norm_path)}",
            observed_at=now,
            valid_from=now,
        )

        dir_id = f"k8s:dir:{repo}:{dir_path}"
        dir_node = Directory(
            id=dir_id,
            path=dir_path,
            repo=repo,
            source_system=SourceSystem.GITHUB.value,
            source_url=url,
            source_ref=f"{repo}@{commit_sha or 'HEAD'}:{dir_path}",
            observed_at=now,
            valid_from=now,
        )

        # Hierarchy edges
        edges: List[Dict[str, Any]] = []
        directories: Dict[str, Directory] = {dir_id: dir_node}

        # Owners covers Directory
        edges.append({
            "source_label": "OwnersFile",
            "source_id": owners_id,
            "target_label": "Directory",
            "target_id": dir_id,
            "rel_type": "COVERS",
            "props": {
                "id": f"{owners_id}->COVERS->{dir_id}",
                "source_url": url,
                "source_ref": f"{repo}:{path}",
                "observed_at": now,
                "valid_from": now,
                "link_method": LinkMethod.STRUCTURED_FILE.value,
                "authority": Authority.CONFIRMED.value,
            },
        })

        # Child directory hierarchy
        curr_p = PurePosixPath(dir_path)
        while str(curr_p) not in ("", "."):
            parent_p = curr_p.parent
            parent_str = str(parent_p) if str(parent_p) != "." else ""
            curr_id = f"k8s:dir:{repo}:{str(curr_p)}"
            parent_id = f"k8s:dir:{repo}:{parent_str}"

            if parent_id not in directories:
                directories[parent_id] = Directory(
                    id=parent_id,
                    path=parent_str,
                    repo=repo,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=url,
                    source_ref=f"{repo}:{parent_str}",
                    observed_at=now,
                    valid_from=now,
                )

            edges.append({
                "source_label": "Directory",
                "source_id": curr_id,
                "target_label": "Directory",
                "target_id": parent_id,
                "rel_type": "CHILD_OF",
                "props": {
                    "id": f"{curr_id}->CHILD_OF->{parent_id}",
                    "source_url": url,
                    "source_ref": f"{repo}:{dir_path}",
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

            if parent_str == "":
                break
            curr_p = parent_p

        # Person nodes and APPROVER_IN / REVIEWER_IN edges
        people: Dict[str, Person] = {}

        for login in approver_logins:
            pid = f"k8s:person:{login.lower()}"
            if pid not in people:
                people[pid] = Person(
                    id=pid,
                    login=login,
                    is_bot=False,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=f"https://github.com/{login}",
                    source_ref=f"github:{login}",
                    observed_at=now,
                    valid_from=now,
                )
            edges.append({
                "source_label": "Person",
                "source_id": pid,
                "target_label": "OwnersFile",
                "target_id": owners_id,
                "rel_type": "APPROVER_IN",
                "props": {
                    "id": f"{pid}->APPROVER_IN->{owners_id}",
                    "paper_owner": False,
                    "source_url": url,
                    "source_ref": f"{repo}:{path}",
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

        for login in reviewer_logins:
            pid = f"k8s:person:{login.lower()}"
            if pid not in people:
                people[pid] = Person(
                    id=pid,
                    login=login,
                    is_bot=False,
                    source_system=SourceSystem.GITHUB.value,
                    source_url=f"https://github.com/{login}",
                    source_ref=f"github:{login}",
                    observed_at=now,
                    valid_from=now,
                )
            edges.append({
                "source_label": "Person",
                "source_id": pid,
                "target_label": "OwnersFile",
                "target_id": owners_id,
                "rel_type": "REVIEWER_IN",
                "props": {
                    "id": f"{pid}->REVIEWER_IN->{owners_id}",
                    "paper_owner": False,
                    "source_url": url,
                    "source_ref": f"{repo}:{path}",
                    "observed_at": now,
                    "valid_from": now,
                    "link_method": LinkMethod.STRUCTURED_FILE.value,
                    "authority": Authority.CONFIRMED.value,
                },
            })

        return {
            "owners_node": owners_node,
            "directories": list(directories.values()),
            "people": list(people.values()),
            "edges": edges,
        }

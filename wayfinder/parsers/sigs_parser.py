import logging
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional
import yaml

from ..models.common import LinkMethod, Authority, SourceSystem, now_iso
from ..models.layer1 import SIG, Person, Subproject, OwnersFile

logger = logging.getLogger("wayfinder.parsers.sigs")

class SigsParser:
    """Parses sigs.yaml from kubernetes/community."""

    @staticmethod
    def parse_sigs_yaml(
        content: str,
        source_url: str = "https://github.com/kubernetes/community/blob/master/sigs.yaml",
        source_ref: str = "kubernetes/community:sigs.yaml",
        target_sig: Optional[str] = "node",
    ) -> Dict[str, Any]:
        """
        Parse sigs.yaml and extract SIGs, people, subprojects, and relationships.
        If target_sig is set (e.g. 'node'), prioritizes target SIG and related SIGs.
        """
        data = yaml.safe_load(content)
        if not data or "sigs" not in data:
            logger.warning("No 'sigs' key found in sigs.yaml")
            return {"sigs": [], "people": [], "subprojects": [], "edges": []}

        sig_nodes: List[SIG] = []
        people_nodes: Dict[str, Person] = {}
        subproject_nodes: List[Subproject] = []
        edges: List[Dict[str, Any]] = []

        now = now_iso()

        for sig_data in data["sigs"]:
            sig_name = sig_data.get("dir", "").replace("sig-", "") or sig_data.get("name", "").lower()
            if target_sig and sig_name != target_sig and target_sig not in sig_name:
                continue

            sig_id = f"k8s:sig:{sig_name}"
            sig_node = SIG(
                id=sig_id,
                name=f"sig-{sig_name}",
                dir=sig_data.get("dir"),
                charter_url=sig_data.get("charter_link"),
                mission=sig_data.get("mission"),
                source_system=SourceSystem.K8S_COMMUNITY.value,
                source_url=source_url,
                source_ref=source_ref,
                observed_at=now,
                valid_from=now,
            )
            sig_nodes.append(sig_node)

            # Parse leadership
            leadership = sig_data.get("leadership", {})
            for role_name in ["chairs", "tech_leads"]:
                role_label = "chair" if role_name == "chairs" else "tech_lead"
                for person_entry in leadership.get(role_name, []):
                    github_login = person_entry.get("github")
                    if not github_login:
                        continue
                    
                    person_id = f"k8s:person:{github_login.lower()}"
                    if person_id not in people_nodes:
                        people_nodes[person_id] = Person(
                            id=person_id,
                            login=github_login,
                            display_name=person_entry.get("name"),
                            is_bot=False,
                            source_system=SourceSystem.K8S_COMMUNITY.value,
                            source_url=source_url,
                            source_ref=source_ref,
                            observed_at=now,
                            valid_from=now,
                        )

                    # Edge: (Person)-[:MEMBER_OF {role}]->(SIG)
                    edges.append({
                        "source_label": "Person",
                        "source_id": person_id,
                        "target_label": "SIG",
                        "target_id": sig_id,
                        "rel_type": "MEMBER_OF",
                        "props": {
                            "id": f"{person_id}->MEMBER_OF->{sig_id}",
                            "role": role_label,
                            "source_url": source_url,
                            "source_ref": source_ref,
                            "observed_at": now,
                            "valid_from": now,
                            "link_method": LinkMethod.STRUCTURED_FILE.value,
                            "authority": Authority.CONFIRMED.value,
                        },
                    })

            # Parse subprojects
            for sp_data in sig_data.get("subprojects", []):
                sp_name = sp_data.get("name", "")
                if not sp_name:
                    continue
                
                sp_id = f"k8s:subproject:{sig_name}:{sp_name.lower().replace(' ', '-')}"
                sp_node = Subproject(
                    id=sp_id,
                    name=sp_name,
                    description=sp_data.get("description"),
                    source_system=SourceSystem.K8S_COMMUNITY.value,
                    source_url=source_url,
                    source_ref=source_ref,
                    observed_at=now,
                    valid_from=now,
                )
                subproject_nodes.append(sp_node)

                # Edge: (SIG)-[:HAS_SUBPROJECT]->(Subproject)
                edges.append({
                    "source_label": "SIG",
                    "source_id": sig_id,
                    "target_label": "Subproject",
                    "target_id": sp_id,
                    "rel_type": "HAS_SUBPROJECT",
                    "props": {
                        "id": f"{sig_id}->HAS_SUBPROJECT->{sp_id}",
                        "source_url": source_url,
                        "source_ref": source_ref,
                        "observed_at": now,
                        "valid_from": now,
                        "link_method": LinkMethod.STRUCTURED_FILE.value,
                        "authority": Authority.CONFIRMED.value,
                    },
                })

                # Owners files
                for owners_url in sp_data.get("owners", []):
                    parsed_url = urlparse(owners_url)
                    parts = parsed_url.path.strip('/').split('/')
                    if parsed_url.netloc == 'raw.githubusercontent.com' and len(parts) >= 4:
                        owner_path = '/'.join(parts[3:])
                    elif len(parts) >= 5 and parts[2] in ('blob', 'tree'):
                        owner_path = '/'.join(parts[4:])
                    else:
                        continue
                    owners_id = f"k8s:owners:{parts[0]}/{parts[1]}:{owner_path}"
                    edges.append({
                        "source_label": "Subproject",
                        "source_id": sp_id,
                        "target_label": "OwnersFile",
                        "target_id": owners_id,
                        "rel_type": "GOVERNED_BY",
                        "props": {
                            "id": f"{sp_id}->GOVERNED_BY->{owners_id}",
                            "source_url": source_url,
                            "source_ref": source_ref,
                            "observed_at": now,
                            "valid_from": now,
                            "link_method": LinkMethod.STRUCTURED_FILE.value,
                            "authority": Authority.CONFIRMED.value,
                        },
                    })

        return {
            "sigs": sig_nodes,
            "people": list(people_nodes.values()),
            "subprojects": subproject_nodes,
            "edges": edges,
        }

import logging
from typing import Any, Dict, List, Optional
from .db import DatabaseManager, db_manager
from .derived.ownership import covering_directory_ids

logger = logging.getLogger("wayfinder.query")

class WayfinderBrainQuery:
    """Answers high-level company brain questions over FalkorDB with provenance and 'as of' time-travel."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or db_manager

    def _time_filter_clause(self, rel_alias: str = "r", as_of: Optional[str] = None) -> str:
        """Cypher clause to restrict relationships to those valid at `as_of` timestamp."""
        if not as_of:
            return f"({rel_alias}.valid_to IS NULL)"
        return f"({rel_alias}.valid_from <= $as_of AND ({rel_alias}.valid_to IS NULL OR {rel_alias}.valid_to > $as_of))"

    def query_who_knows(self, topic_or_dir: str, as_of: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        from .derived.expertise_v2 import get_rankings
        report = get_rankings(self.db, topic_or_dir, as_of)
        self.last_expertise_metadata = {k:v for k,v in report.items() if k not in ('items','involvement_only')}
        return [{**item, 'target_type':'Directory' if report['scoring_version']=='v2' else 'Legacy target',
                 'target_name':item['matches'][0]['target_name'] if report['scoring_version']!='v2' and len(item['matches'])==1 else topic_or_dir} for item in report['items'][:limit]]

    def _legacy_who_knows(self, topic_or_dir: str, as_of: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Answers: 'Who knows about X?'
        Looks up EXPERT_IN relationships for Topic or Directory.
        """
        cypher = f"""
        MATCH (p:Person)-[r:EXPERT_IN]->(target)
        WHERE (target.name = $term OR target.path = $term OR target.id = $term OR target.id ENDS WITH $term
          OR ('Directory' IN labels(target) AND target.path STARTS WITH ($term + '/')))
          AND {self._time_filter_clause('r', as_of)}
          AND coalesce(p.is_bot, false) = false
        RETURN p.login AS login,
               p.display_name AS display_name,
               labels(target)[0] AS target_type,
               COALESCE(target.name, target.path, target.id) AS target_name,
               r.score AS score,
               r.evidence_ids AS evidence_ids
        ORDER BY r.score DESC
        LIMIT $limit
        """
        params = {"term": topic_or_dir, "limit": limit}
        if as_of:
            params["as_of"] = as_of

        results = []
        try:
            res = self.db.query(cypher, params)
            for row in res.result_set:
                results.append({
                    "login": row[0],
                    "display_name": row[1],
                    "target_type": row[2],
                    "target_name": row[3],
                    "score": row[4],
                    "evidence_ids": row[5],
                })
        except Exception as e:
            logger.error(f"Error querying who knows '{topic_or_dir}': {e}")
        return results

    def query_ownership(self, directory_path: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        """
        Answers: 'Who owns directory X?'
        Returns declared OWNERS approvers/reviewers, paper owners, and unlisted experts.
        """
        cypher_declared = f"""
        MATCH (p:Person)-[r:APPROVER_IN|REVIEWER_IN]->(o:OwnersFile)-[:COVERS]->(d:Directory)
        WHERE d.id IN $covering_dirs
          AND {self._time_filter_clause('r', as_of)}
        RETURN p.login AS login,
               type(r) AS role,
               r.paper_owner AS paper_owner,
               o.path AS owners_file
        """
        cypher_unlisted = f"""
        MATCH (p:Person)-[r:UNLISTED_EXPERT]->(d:Directory)
        WHERE (d.path = $dir OR d.id ENDS WITH $dir)
          AND {self._time_filter_clause('r', as_of)}
          AND coalesce(p.is_bot, false) = false
        RETURN p.login AS login, r.score AS score
        ORDER BY r.score DESC
        LIMIT 5
        """
        directories = self.db.query('MATCH (d:Directory) WHERE d.path = $dir OR d.id = $dir RETURN d.id', {'dir': directory_path})
        covering = []
        for row in directories.result_set:
            covering.extend(covering_directory_ids(self.db, row[0], as_of))
        params = {"dir": directory_path, "covering_dirs": covering}
        if as_of:
            params["as_of"] = as_of

        declared = []
        try:
            res = self.db.query(cypher_declared, params)
            for row in res.result_set:
                declared.append({
                    "login": row[0],
                    "role": row[1],
                    "paper_owner": row[2],
                    "owners_file": row[3],
                })
        except Exception as e:
            logger.warning(f"Error querying declared owners: {e}")

        unlisted = []
        try:
            res = self.db.query(cypher_unlisted, params)
            for row in res.result_set:
                unlisted.append({"login": row[0], "score": row[1]})
        except Exception as e:
            logger.warning(f"Error querying unlisted experts: {e}")

        return {
            "directory": directory_path,
            "declared_owners": declared,
            "unlisted_experts": unlisted,
        }

    def query_decisions_and_rationale(self, target_id: str) -> Dict[str, Any]:
        """
        Answers: 'Why was X designed this way? What decisions were made?'
        Returns KEP rationales, decisions, and supporting/opposing arguments with verbatim quotes.
        """
        cypher_decisions = """
        MATCH (d:Decision)-[about:ABOUT]->(target)
        WHERE (target.id = $tid OR target.id ENDS WITH $tid)
        AND about.valid_to IS NULL AND d.valid_to IS NULL
        OPTIONAL MATCH (p:Person)-[:MADE]->(d)
        RETURN d.id AS dec_id, d.summary AS summary, d.evidence_quote AS quote, p.login AS author
        """
        cypher_rationales = """
        MATCH (k:KEP)-[has:HAS_RATIONALE]->(r:Rationale)
        WHERE (k.id = $tid OR k.id ENDS WITH $tid OR k.number = $tid) AND has.valid_to IS NULL AND r.valid_to IS NULL
        RETURN r.kind AS kind, r.summary AS summary, r.evidence_quote AS quote
        """
        cypher_args = """
        MATCH (a:Argument)-[rel:SUPPORTS|OPPOSES|RAISES_CONCERN_ON]->(target)
        WHERE (target.id = $tid OR target.id ENDS WITH $tid) AND a.valid_to IS NULL AND rel.valid_to IS NULL
        OPTIONAL MATCH (p:Person)-[:MADE]->(a)
        RETURN a.kind AS kind, type(rel) AS stance, a.summary AS summary,
               a.evidence_quote AS quote, p.login AS author, a.concern_type AS concern_type
        """
        decisions = []
        try:
            res = self.db.query(cypher_decisions, {"tid": target_id})
            for row in res.result_set:
                decisions.append({"id": row[0], "summary": row[1], "quote": row[2], "author": row[3]})
        except Exception:
            pass

        rationales = []
        try:
            res = self.db.query(cypher_rationales, {"tid": target_id})
            for row in res.result_set:
                rationales.append({"kind": row[0], "summary": row[1], "quote": row[2]})
        except Exception:
            pass

        arguments = []
        try:
            res = self.db.query(cypher_args, {"tid": target_id})
            for row in res.result_set:
                arguments.append({
                    "kind": row[0],
                    "stance": row[1],
                    "summary": row[2],
                    "quote": row[3],
                    "author": row[4],
                    "concern_type": row[5],
                })
        except Exception:
            pass

        return {
            "target_id": target_id,
            "decisions": decisions,
            "rationales": rationales,
            "arguments": arguments,
        }

    def query_concerns(self, target_id: str) -> List[Dict[str, Any]]:
        """
        Answers: 'What concerns were raised on X and are they resolved?'
        """
        cypher = """
        MATCH (a:Argument {kind: 'concern'})-[rel:RAISES_CONCERN_ON]->(target)
        WHERE (target.id = $tid OR target.id ENDS WITH $tid) AND a.valid_to IS NULL AND rel.valid_to IS NULL
        OPTIONAL MATCH (p:Person)-[:MADE]->(a)
        RETURN a.id AS arg_id,
               a.concern_type AS concern_type,
               a.summary AS summary,
               a.status AS status,
               a.evidence_quote AS quote,
               p.login AS author
        """
        concerns = []
        try:
            res = self.db.query(cypher, {"tid": target_id})
            for row in res.result_set:
                concerns.append({
                    "id": row[0],
                    "concern_type": row[1],
                    "summary": row[2],
                    "status": row[3] or "open",
                    "quote": row[4],
                    "author": row[5],
                })
        except Exception as e:
            logger.warning(f"Error querying concerns: {e}")
        return concerns

    def query_stale_findings(self) -> List[Dict[str, Any]]:
        """Retrieve all detected stale findings."""
        cypher = """
        MATCH (sf:StaleFinding)-[:CONCERNS]->(subject)
        WHERE sf.resolved_at IS NULL
        RETURN sf.id AS finding_id,
               sf.kind AS kind,
               sf.details AS details,
               labels(subject)[0] AS subject_type,
               subject.id AS subject_id,
               sf.detected_at AS detected_at
        """
        findings = []
        try:
            res = self.db.query(cypher)
            for row in res.result_set:
                findings.append({
                    "finding_id": row[0],
                    "kind": row[1],
                    "details": row[2],
                    "subject_type": row[3],
                    "subject_id": row[4],
                    "detected_at": row[5],
                })
        except Exception as e:
            logger.warning(f"Error querying stale findings: {e}")
        return findings

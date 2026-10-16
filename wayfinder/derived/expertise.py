from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional, Tuple
from ..config import settings
from ..db import DatabaseManager, db_manager
from ..models.common import Authority, LinkMethod, now_iso
from .ownership import covering_directory_ids
from .activity_coverage import has_activity_coverage
from ..github.activity import refresh_bot_flags

logger = logging.getLogger("wayfinder.derived.expertise")

# Configurable weights from design document
WEIGHT_APPROVAL = 3.0
WEIGHT_REVIEW_WITH_COMMENTS = 2.0
WEIGHT_MERGED_PR_AUTHORED = 2.0
WEIGHT_ARGUMENT_EXPLANATION = 2.0
WEIGHT_ANSWERS_QUESTION = 2.0
WEIGHT_OTHER_COMMENT = 1.0

class ExpertiseCalculator:
    """Computes Layer 3 Expertise, Paper Owners, and Unlisted Experts."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or db_manager
        self.window_days = settings.expertise_window_days
        self.half_life_days = settings.expertise_half_life_days

    def compute_decay(self, event_time_iso: Optional[str]) -> float:
        """Compute half-life decay multiplier based on event age in days."""
        if not event_time_iso:
            return 1.0
        try:
            # Parse ISO date string
            t = datetime.fromisoformat(event_time_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta_days = max(0.0, (now - t).total_seconds() / 86400.0)
            if delta_days > self.window_days:
                return 0.0
            return 0.5 ** (delta_days / self.half_life_days)
        except Exception:
            return 1.0

    def calculate_directory_expertise(self) -> Dict[str, Any]:
        from .expertise_v2 import ExpertiseRefresh
        return ExpertiseRefresh(self.db).refresh()

    def _calculate_legacy_directory_expertise(self) -> Dict[str, Any]:
        """
        Calculates (Person)-[:EXPERT_IN]->(Directory) using activity on files in directories.
        """
        now = now_iso()
        logger.info("Computing Person -> Directory expertise scores...")
        refresh_bot_flags(self.db)

        # 1. Fetch activities touching directories within 180 days
        # Approvals: (Person)-[:APPROVED]->(PullRequest)-[:TOUCHES]->(File)<-[:CONTAINS]-(Directory)
        approval_query = """
        MATCH (p:Person)-[a:APPROVED]->(pr:PullRequest)-[:TOUCHES]->(f:File)<-[:CONTAINS]-(d:Directory)
        WHERE a.valid_to IS NULL AND coalesce(p.is_bot, false) = false
        RETURN DISTINCT p.id AS person_id, d.id AS dir_id, a.at AS event_time, a.id AS ev_id
        """
        # Authored PRs: (Person)-[:AUTHORED]->(PullRequest {state:'MERGED'})-[:TOUCHES]->(File)<-[:CONTAINS]-(Directory)
        authored_pr_query = """
        MATCH (p:Person)-[:AUTHORED]->(pr:PullRequest)-[:TOUCHES]->(f:File)<-[:CONTAINS]-(d:Directory)
        WHERE (pr.state = 'MERGED' OR pr.merged_at IS NOT NULL) AND coalesce(p.is_bot, false) = false
        RETURN DISTINCT p.id AS person_id, d.id AS dir_id, pr.merged_at AS event_time, pr.id AS ev_id
        """
        # Reviews: (Person)-[:SUBMITTED]->(r:Review)-[:REVIEWS]->(pr:PullRequest)-[:TOUCHES]->(File)<-[:CONTAINS]-(Directory)
        review_query = """
        MATCH (p:Person)-[:SUBMITTED]->(r:Review)-[:REVIEWS]->(pr:PullRequest)-[:TOUCHES]->(f:File)<-[:CONTAINS]-(d:Directory)
        WHERE size(coalesce(r.body_text, '')) > 0 AND coalesce(p.is_bot, false) = false
        RETURN DISTINCT p.id AS person_id, d.id AS dir_id, r.submitted_at AS event_time, r.id AS ev_id
        """
        # Comments on PR: (Person)-[:AUTHORED]->(c:Comment)<-[:HAS_COMMENT]-(:Thread)-[:ON]->(pr:PullRequest)-[:TOUCHES]->(File)<-[:CONTAINS]-(Directory)
        comment_query = """
        MATCH (p:Person)-[:AUTHORED]->(c:Comment)<-[:HAS_COMMENT]-(:Thread)-[:ON]->(pr:PullRequest)-[:TOUCHES]->(f:File)<-[:CONTAINS]-(d:Directory)
        WHERE coalesce(c.is_command, false) = false AND coalesce(p.is_bot, false) = false
        RETURN DISTINCT p.id AS person_id, d.id AS dir_id, c.created_at AS event_time, c.id AS ev_id
        """

        scores: Dict[Tuple[str, str], float] = {}
        evidence_map: Dict[Tuple[str, str], List[str]] = {}

        def record(p_id: str, d_id: str, base_weight: float, ev_time: Optional[str], ev_id: str):
            decay = self.compute_decay(ev_time)
            if decay <= 0:
                return
            key = (p_id, d_id)
            if ev_id in evidence_map.get(key, []):
                return
            score = base_weight * decay
            scores[key] = scores.get(key, 0.0) + score
            if key not in evidence_map:
                evidence_map[key] = []
            if ev_id not in evidence_map[key]:
                evidence_map[key].append(ev_id)

        for q, w in [
            (approval_query, WEIGHT_APPROVAL),
            (authored_pr_query, WEIGHT_MERGED_PR_AUTHORED),
            (review_query, WEIGHT_REVIEW_WITH_COMMENTS),
            (comment_query, WEIGHT_OTHER_COMMENT),
        ]:
            try:
                res = self.db.query(q)
                for row in res.result_set:
                    record(row[0], row[1], w, row[2], str(row[3]))
            except Exception as e:
                raise RuntimeError("Failed to query directory evidence") from e

        # Upsert (Person)-[:EXPERT_IN]->(Directory)
        created_count = 0
        for (p_id, d_id), score in scores.items():
            if score < 0.1:
                continue
            ev_ids = evidence_map.get((p_id, d_id), [])[:20]
            edge_id = f"{p_id}->EXPERT_IN->{d_id}"
            self.db.upsert_edge(
                source_label="Person",
                source_id=p_id,
                target_label="Directory",
                target_id=d_id,
                rel_type="EXPERT_IN",
                props={
                    "id": edge_id,
                    "score": round(score, 3),
                    "evidence_ids": ev_ids,
                    "window": self.window_days,
                    "link_method": LinkMethod.COMPUTED.value,
                    "authority": Authority.COMPUTED.value,
                    "observed_at": now,
                    "valid_from": now,
                },
            )
            created_count += 1

        self._retire_missing('Directory', scores, now)
        return {"expert_edges_created": created_count, "scores": len(scores)}

    def calculate_topic_expertise(self) -> Dict[str, Any]:
        """
        Calculates (Person)-[:EXPERT_IN]->(Topic) using explanations, questions, and PRs.
        """
        now = now_iso()
        logger.info("Computing Person -> Topic expertise scores...")
        refresh_bot_flags(self.db)

        # Explanation Arguments: (Person)-[:MADE]->(a:Argument {kind:'explanation'})-[:ABOUT_TOPIC]->(t:Topic)
        explanation_query = """
        MATCH (p:Person)-[m:MADE]->(a:Argument {kind:'explanation'})-[tag:ABOUT_TOPIC]->(t:Topic)
        WHERE m.valid_to IS NULL AND tag.valid_to IS NULL AND a.valid_to IS NULL AND coalesce(p.is_bot, false) = false
        RETURN p.id AS person_id, t.id AS topic_id, coalesce(a.source_timestamp, a.observed_at) AS event_time, a.id AS ev_id
        """
        # Question Answers: (Person)-[:AUTHORED]->(c:Comment)-[:ANSWERS]->(q:Question)-[:ABOUT_TOPIC]->(t:Topic)
        answers_query = """
        MATCH (p:Person)-[:AUTHORED]->(c:Comment)-[ans:ANSWERS]->(q:Question)-[tag:ABOUT_TOPIC]->(t:Topic)
        WHERE ans.valid_to IS NULL AND tag.valid_to IS NULL AND q.valid_to IS NULL AND coalesce(p.is_bot, false) = false
        RETURN p.id AS person_id, t.id AS topic_id, c.created_at AS event_time, c.id AS ev_id
        """

        scores: Dict[Tuple[str, str], float] = {}
        evidence_map: Dict[Tuple[str, str], List[str]] = {}

        def record(p_id: str, t_id: str, base_weight: float, ev_time: Optional[str], ev_id: str):
            decay = self.compute_decay(ev_time)
            if decay <= 0:
                return
            key = (p_id, t_id)
            if ev_id in evidence_map.get(key, []):
                return
            score = base_weight * decay
            scores[key] = scores.get(key, 0.0) + score
            if key not in evidence_map:
                evidence_map[key] = []
            if ev_id not in evidence_map[key]:
                evidence_map[key].append(ev_id)

        for q, w in [
            (explanation_query, WEIGHT_ARGUMENT_EXPLANATION),
            (answers_query, WEIGHT_ANSWERS_QUESTION),
        ]:
            try:
                res = self.db.query(q)
                for row in res.result_set:
                    record(row[0], row[1], w, row[2], str(row[3]))
            except Exception as e:
                raise RuntimeError("Failed to query topic evidence") from e

        # Upsert (Person)-[:EXPERT_IN]->(Topic)
        created_count = 0
        for (p_id, t_id), score in scores.items():
            if score < 0.1:
                continue
            ev_ids = evidence_map.get((p_id, t_id), [])[:20]
            edge_id = f"{p_id}->EXPERT_IN->{t_id}"
            self.db.upsert_edge(
                source_label="Person",
                source_id=p_id,
                target_label="Topic",
                target_id=t_id,
                rel_type="EXPERT_IN",
                props={
                    "id": edge_id,
                    "score": round(score, 3),
                    "evidence_ids": ev_ids,
                    "window": self.window_days,
                    "link_method": LinkMethod.COMPUTED.value,
                    "authority": Authority.COMPUTED.value,
                    "observed_at": now,
                    "valid_from": now,
                },
            )
            created_count += 1

        self._retire_missing('Topic', scores, now)
        return {"topic_expert_edges_created": created_count}

    def detect_paper_owners(self) -> List[Dict[str, Any]]:
        """
        Flag paper_owner = true on APPROVER_IN / REVIEWER_IN edges if the person
        is listed in OWNERS but had no review, approval, or comment in that directory
        in the last 180 days.
        """
        logger.info("Detecting paper owners...")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.window_days)).isoformat()
        covered = has_activity_coverage(self.db, cutoff)
        cypher = """
        MATCH (p:Person)-[r:APPROVER_IN|REVIEWER_IN]->(o:OwnersFile)-[:COVERS]->(d:Directory)
        WHERE r.valid_to IS NULL
        OPTIONAL MATCH (p)-[a:APPROVED]->(:PullRequest)-[:TOUCHES]->(:File)<-[:CONTAINS]-(child:Directory)
        WHERE a.valid_to IS NULL AND a.at >= $cutoff AND child.repo = d.repo
          AND (child.path = d.path OR child.path STARTS WITH (d.path + '/') OR d.path = '')
        OPTIONAL MATCH (p)-[:SUBMITTED]->(review:Review)-[:REVIEWS]->(:PullRequest)-[:TOUCHES]->(:File)<-[:CONTAINS]-(rd:Directory)
        WHERE review.submitted_at >= $cutoff AND rd.repo = d.repo
          AND (rd.path = d.path OR rd.path STARTS WITH (d.path + '/') OR d.path = '')
        OPTIONAL MATCH (p)-[:AUTHORED]->(c:Comment)<-[:HAS_COMMENT]-(:Thread)-[:ON]->(:PullRequest)-[:TOUCHES]->(:File)<-[:CONTAINS]-(cd:Directory)
        WHERE c.created_at >= $cutoff AND cd.repo = d.repo
          AND (cd.path = d.path OR cd.path STARTS WITH (d.path + '/') OR d.path = '')
        WITH p, r, o, d, count(a) + count(review) + count(c) AS activity
        SET r.paper_owner = CASE WHEN activity > 0 THEN false
                                WHEN $covered AND d.repo = 'kubernetes/kubernetes' THEN true ELSE null END
        WITH p, r, o WHERE r.paper_owner = true
        RETURN p.login AS login, o.path AS owners_path, type(r) AS role
        """
        paper_owners = []
        try:
            res = self.db.query(cypher, {"cutoff": cutoff, "covered": covered})
            for row in res.result_set:
                paper_owners.append({"login": row[0], "owners_path": row[1], "role": row[2]})
        except Exception as e:
            raise RuntimeError("Failed to recompute paper owners") from e

        logger.info(f"Identified {len(paper_owners)} paper owner entries.")
        return paper_owners

    def detect_unlisted_experts(self) -> List[Dict[str, Any]]:
        """
        Detect people in the top-5 EXPERT_IN score for a directory who are NOT in OWNERS.
        Creates (Person)-[:UNLISTED_EXPERT]->(Directory).
        """
        now = now_iso()
        logger.info("Detecting unlisted experts...")
        published = self.db.query("MATCH (s:ExpertiseState {id:'expertise:directory:published'}) RETURN s.generation")
        if published.result_set:
            result = self.db.query('MATCH (p:Person)-[r:UNLISTED_EXPERT]->(d:Directory) WHERE r.generation=$generation AND r.valid_to IS NULL '
                                   'RETURN p.id,d.id,r.score', {'generation':published.result_set[0][0]})
            return [{'person_id':p,'dir_id':d,'score':score} for p,d,score in result.result_set]
        cypher = """
        MATCH (p:Person)-[exp:EXPERT_IN]->(d:Directory)
        WHERE exp.valid_to IS NULL AND coalesce(p.is_bot, false) = false
        WITH d, p, exp
        ORDER BY exp.score DESC
        WITH d, collect({person: p, score: exp.score, ev_ids: exp.evidence_ids})[0..5] AS top_experts
        UNWIND top_experts AS item
        RETURN item.person.id AS person_id, d.id AS dir_id, item.score AS score, item.ev_ids AS ev_ids
        """
        unlisted = []
        try:
            res = self.db.query(cypher)
            for row in res.result_set:
                p_id, d_id, score, ev_ids = row[0], row[1], row[2], row[3]
                ancestors = covering_directory_ids(self.db, d_id)
                declared = self.db.query('MATCH (p:Person {id: $pid})-[r:APPROVER_IN|REVIEWER_IN]->(:OwnersFile)-[:COVERS]->(d:Directory) '
                                         'WHERE r.valid_to IS NULL AND d.id IN $dirs RETURN p.id',
                                         {'pid': p_id, 'dirs': ancestors})
                if declared.result_set:
                    continue
                edge_id = f"{p_id}->UNLISTED_EXPERT->{d_id}"
                self.db.upsert_edge(
                    source_label="Person",
                    source_id=p_id,
                    target_label="Directory",
                    target_id=d_id,
                    rel_type="UNLISTED_EXPERT",
                    props={
                        "id": edge_id,
                        "score": score,
                        "evidence_ids": ev_ids,
                        "link_method": LinkMethod.COMPUTED.value,
                        "authority": Authority.COMPUTED.value,
                        "observed_at": now,
                        "valid_from": now,
                    },
                )
                unlisted.append({"person_id": p_id, "dir_id": d_id, "score": score})
        except Exception as e:
            raise RuntimeError("Failed to recompute unlisted experts") from e

        active = [f"{i['person_id']}->UNLISTED_EXPERT->{i['dir_id']}" for i in unlisted]
        self.db.query('MATCH ()-[r:UNLISTED_EXPERT]->() WHERE r.valid_to IS NULL '
                      'AND NOT r.logical_id IN $ids SET r.valid_to = $now', {'ids': active, 'now': now})
        return unlisted

    def _retire_missing(self, label, scores, now):
        active = [f'{p}->EXPERT_IN->{d}' for (p, d), score in scores.items() if score >= 0.1]
        self.db.query(f'MATCH ()-[r:EXPERT_IN]->(:{label}) WHERE r.valid_to IS NULL '
                      'AND NOT coalesce(r.logical_id, r.id) IN $ids SET r.valid_to = $now',
                      {'ids': active, 'now': now})

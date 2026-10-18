"""Local stdio graph tools. semantic_search explicitly calls the embedding provider."""
import functools
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Support both -m and absolute script launches from any client working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from wayfinder.db import DatabaseManager
from wayfinder.brain_graph import BrainGraph, INSTRUCTIONS
from wayfinder.agent_trace import Tracer
from falkordb import FalkorDB


def create_server(db=None, enable_actions=False, manifest_path=None, trace=True, trace_folder=None):
    if db is None:
        db = DatabaseManager()
        db._client = FalkorDB(host=db.host, port=db.port, socket_connect_timeout=3, socket_timeout=8)
    brain = BrainGraph(db)
    # Records which entities each call touched, for the dashboard's live agent-path view.
    tracer = Tracer(db.graph_name, folder=trace_folder, enabled=trace)
    server = FastMCP("wayfinder-company-brain", instructions=INSTRUCTIONS)
    names = ["who_knows", "ownership", "decisions", "concerns", "stale_findings", "graph_stats",
             "graph_schema", "search_entities", "get_entity", "get_neighbors", "find_paths", "get_evidence",
             "get_thread", "person_context", "component_context", "list_questions", "recent_activity",
             "knowledge_coverage", "read_cypher", "company_context", "validate_review_brief", "semantic_search",
             "graph_algorithm", "verify_answer", "changes_since", "stale_documents", "knowledge_risk", "action_status"]
    descriptions = {
        "verify_answer": ("Check a drafted answer before giving it. Pass statements, each {text, evidence_ids}. Each statement comes "
            "back VERIFIED (cited records are current and any quoted text is in them), RECITE (source changed, same claim under a "
            "new ID), OUT_OF_DATE (the cited claim was retired; replaced_by holds the current one), REMOVED, DISPUTED (another "
            "source's current decision is about the same target) or UNSUPPORTED (no evidence, or a quote not in the source)."),
        "changes_since": ("Catch up on what changed since an ISO time, optionally around a component_id: records created and "
            "edited (from retained source revisions), records retired, and links made and broken (from relationship intervals)."),
        "stale_documents": ("List pages that may be out of date: merged pull requests changed the described component's code, or "
            "a linked task changed, after the page was last edited. A review signal, not proof the page is wrong."),
        "knowledge_risk": ("For a Component ID: who holds its knowledge (ownership, assigned tasks, authored and reviewed PRs, "
            "code expertise, discussion) and its bus factor. For a Person ID: what would lose its only owner, assignee or expert "
            "if they left. Measures recorded evidence, not actual knowledge; bots excluded."),
        "action_status": ("Check whether an approved or executed action still holds: its payload hash is intact, its brief "
            "validates now, and none of its cited evidence was retired or edited after approval."),
        "graph_algorithm": ("Run a FalkorDB native graph algorithm over the current graph (retired history excluded): "
            "'pagerank' (important entities by incoming links), 'betweenness' (brokers between areas), 'components' (connected "
            "groups and isolated islands), or 'shortest_path' (fewest relationships between source_id and target_id, returned with "
            "its edges). Optionally restrict node_labels / relationship_types, or around_id with hops (1-3) for a neighbourhood. "
            "Runs on a temporary projection graph that is deleted afterwards; the knowledge graph is only read. Scores rank "
            "structure, not correctness or authority; retrieve evidence before relying on a result."),
        "semantic_search": "Meaning-based discovery over native Topic/Argument/Comment vector indexes. Each explicit call makes one configured embedding API request (possibly paid), no retries, and no database writes. Retrieve source evidence after similarity matches.",
        "company_context": "Inspect a company component's declared owners, explicitly linked Plane work items/pages, discussions, decisions and concerns. Ownership is not code reviewer authorization.",
        "validate_review_brief": "Check structured brief sections, evidence IDs, URLs, source revisions and quote presence. Does not prove semantic correctness.",
        "who_knows": "Rank distinct humans by strong code expertise for a directory, topic, or stable company Component ID (via IMPLEMENTED_IN). Includes implementation/review breakdown, PR counts, coverage and freshness. Scores are not reviewer authorization.",
        "ownership": "Read inherited declared OWNERS with activity status and top unlisted human experts; missing evidence remains unknown.",
        "decisions": "Read decisions, rationale and arguments for a stable ID or exact KEP number, with provenance.",
        "concerns": "Read concerns and their stored resolution status for an exact target ID.",
        "stale_findings": "Read unresolved computed findings, which require source verification.",
        "graph_stats": "Count retained nodes and active versus retained relationship intervals.",
        "graph_schema": "Discover labels, properties, relationship endpoints and ontology meanings before querying.",
        "search_entities": "Resolve ambiguous names using exact-first lexical search, optional labels and repository. Follow next_cursor.",
        "get_entity": "Inspect a stable entity ID, provenance and relation counts. Read long source text using text_field/text_offset/text_length.",
        "get_neighbors": "Navigate one hop from a stable ID, filtered by direction (both/out/in), types and labels. Follow next_cursor.",
        "find_paths": "Find up to ten connections between stable entity IDs, with evidence and optional relationship filters. Max five hops.",
        "get_evidence": "Resolve node IDs and relationship interval/logical IDs into original sources and quotes. Follow ASSERTS/AUTHORED neighbors for claim context.",
        "get_thread": "Read imported comments and reviews chronologically for a Thread, PR or Issue ID. Follow claim/answer links with get_neighbors.",
        "person_context": "Explore a person's ownership, expertise, contributions, collaborators and communities by exact login or ID.",
        "component_context": "Explore directory descendants, contributors and explicitly linked knowledge and changed PRs; do not infer dependencies.",
        "list_questions": "Find extracted questions, optionally by target, author or answered status. Retrieve answer evidence through get_neighbors.",
        "recent_activity": "Read source contributions within ISO since/until, optionally linked to an entity or repository. Import coverage may be incomplete.",
        "knowledge_coverage": "Explain activity import coverage and limitations; scoped requests do not imply complete coverage.",
        "read_cypher": "Execute parameterized custom Cypher through database-enforced read-only execution with a five-second timeout. No writes. Add LIMIT; current/history predicates are your responsibility.",
    }
    for name in names:
        method = getattr(brain, name)
        def wrap(tool_name, bound):
            @functools.wraps(bound)
            def tool(*args, **kwargs) -> dict[str, Any]:
                arguments = inspect.signature(bound).bind(*args, **kwargs)
                try:
                    result = brain.invoke(tool_name, **arguments.arguments)
                except Exception as exc:
                    tracer.record(tool_name, arguments.arguments, error=f"{type(exc).__name__}: {exc}")
                    raise
                tracer.record(tool_name, arguments.arguments, result)
                return result
            tool.__annotations__ = {**getattr(bound, "__annotations__", {}), "return": dict[str, Any]}
            tool.__signature__ = inspect.signature(bound).replace(return_annotation=dict[str, Any])
            return tool
        server.add_tool(wrap(name, method), name=name, description=descriptions[name],
                        structured_output=True, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))

    @server.resource("wayfinder://schema", mime_type="application/json")
    def schema_resource() -> str:
        return json.dumps(brain.invoke("graph_schema"))

    @server.resource("wayfinder://coverage", mime_type="application/json")
    def coverage_resource() -> str:
        return json.dumps(brain.invoke("knowledge_coverage"))

    @server.prompt()
    def investigate_component(component: str) -> str:
        return f"Investigate {component}: check schema and coverage, resolve its ID, inspect ownership and human expertise, follow explicitly related PRs/KEPs/decisions/concerns, retrieve evidence and cite source URLs. State import gaps. {INSTRUCTIONS}"

    @server.prompt()
    def explain_decision(target: str) -> str:
        return f"Explain decisions for {target}: resolve its ID, follow rationale, supporting/opposing arguments and source discussions. Distinguish considered alternatives from verified rejection reasons. Cite quotes and URLs. {INSTRUCTIONS}"

    @server.prompt()
    def review_component(component: str) -> str:
        return (f'Investigate {component}. Call graph_schema and knowledge_coverage; resolve its ID with search_entities. '
            'Use company_context for a Component or component_context for a Directory. Call who_knows with the stable Component ID '
            'for code expertise via IMPLEMENTED_IN. Use semantic_search for relevant meaning, then get_neighbors, get_thread and get_evidence '
            'to navigate owners, tasks, documents, decisions and unresolved concerns. Inspect source revisions. '
            'Produce a structured brief with component_id, ownership, contributors, decisions, concerns, missing_evidence, '
            'next_action and context. Each factual claim needs summary, evidence_ids, source_urls and authority; extracted '
            'claims also need source_revision_id and evidence_quote. Validate with validate_review_brief. '
            'State unknowns and scope. Never invent reviewer authorization. Prepare a Plane task only if requested; '
            'human CLI approval is required before execution. '+INSTRUCTIONS)

    if enable_actions:
        from wayfinder.company.actions import PlaneActions
        from wayfinder.company.core import load_manifest
        from wayfinder.company.fixture import MANIFEST
        actions=PlaneActions(db,load_manifest(manifest_path or MANIFEST))

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
        def prepare_plane_work_item(title: str, description: str, brief: dict[str, Any]) -> dict[str, Any]:
            """Persist an exact proposal in the graph. Does not approve or create anything in Plane."""
            from wayfinder.company.core import response_context
            result={**actions.prepare(title,description,brief),'context':response_context(db)}
            tracer.record('prepare_plane_work_item',{'title':title,'brief':brief},result)
            return result

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=True))
        def execute_plane_work_item(action_id: str) -> dict[str, Any]:
            """Create the exact Plane work item only after human CLI approval. Uncertain outcomes require reconciliation."""
            from wayfinder.company.core import response_context
            result={**actions.execute(action_id),'context':response_context(db)}
            tracer.record('execute_plane_work_item',{'action_id':action_id},result)
            return result

    return server


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-actions',action='store_true')
    parser.add_argument('--manifest',type=str)
    parser.add_argument('--no-trace',action='store_true',help='Do not record tool calls for the agent-path view')
    args=parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    trace=not args.no_trace and os.getenv('WAYFINDER_TRACE','1')!='0'
    create_server(enable_actions=args.enable_actions,manifest_path=args.manifest,trace=trace).run(transport="stdio")


if __name__ == "__main__":
    main()

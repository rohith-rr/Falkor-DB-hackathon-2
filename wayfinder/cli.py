import argparse
import json
import logging
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import settings
from .db import db_manager
from .query import WayfinderBrainQuery
from .sync.incremental import IncrementalSync
from .sync.pipeline import PopulationPipeline

console = Console()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def cmd_init():
    console.print(Panel.fit("[bold cyan]Initializing Wayfinder Schema on FalkorDB[/bold cyan]"))
    db_manager.init_schema()
    console.print("[bold green]Schema and indexes initialized successfully![/bold green]")

def cmd_populate(args):
    console.print(Panel.fit("[bold cyan]Running 7-Step Population Pipeline[/bold cyan]"))
    pipeline = PopulationPipeline(db=db_manager)
    results = pipeline.run_full_pipeline(
        use_live_github=args.live,
        max_search_items=args.limit,
    )
    console.print("[bold green]Population completed![/bold green]")
    cmd_stats()

def cmd_sync(args):
    console.print(Panel.fit("[bold cyan]Running Incremental Sync[/bold cyan]"))
    sync_engine = IncrementalSync(db=db_manager)
    res = sync_engine.sync_once()
    console.print(f"[bold green]Sync finished:[/bold green] {res}")

def cmd_stats():
    stats = db_manager.get_stats()
    table = Table(title=f"Wayfinder Brain Stats (Graph: '{stats['graph']}')")
    table.add_column("Category", style="cyan")
    table.add_column("Type / Label", style="magenta")
    table.add_column("Count", justify="right", style="green")

    for lbl, cnt in stats["node_counts"].items():
        table.add_row("Node", lbl, str(cnt))
    for rel, cnt in stats["edge_counts"].items():
        table.add_row("Edge", rel, str(cnt))

    table.add_section()
    table.add_row("[bold]TOTAL[/bold]", "[bold]Nodes[/bold]", f"[bold]{stats['total_nodes']}[/bold]")
    table.add_row("[bold]TOTAL[/bold]", "[bold]Edges[/bold]", f"[bold]{stats['total_edges']}[/bold]")

    console.print(table)

def cmd_who_knows(args):
    query_engine = WayfinderBrainQuery(db=db_manager)
    results = query_engine.query_who_knows(args.term, as_of=args.as_of)
    for warning in query_engine.last_expertise_metadata.get('warnings', []):
        console.print(f'[yellow]{warning}[/yellow]')
    calculated = query_engine.last_expertise_metadata.get('calculated_at')
    if calculated:
        console.print(f"Scoring version: v2; calculated at: {calculated}")
    if not results:
        console.print(f"[yellow]No experts found for '{args.term}'[/yellow]")
        return

    table = Table(title=f"Expertise for '{args.term}'")
    table.add_column("Login", style="cyan")
    table.add_column("Implementation / Peer review", style="magenta")
    table.add_column("PRs / Recent / Evidence", style="white")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Evidence IDs", style="dim")

    for r in results:
        ev_str = ", ".join(r["evidence_ids"][:3])
        breakdown=r.get('score_breakdown',{})
        table.add_row(r['login'],f"{breakdown.get('implementation','?')} / {breakdown.get('peer_review','?')}",
                      f"{r.get('distinct_pr_count','?')} / {r.get('recent_pr_count','?')} / {r.get('evidence_strength','legacy')}",str(r['score']),ev_str)

    console.print(table)

def cmd_ownership(args):
    query_engine = WayfinderBrainQuery(db=db_manager)
    res = query_engine.query_ownership(args.directory, as_of=args.as_of)

    table_declared = Table(title=f"Declared OWNERS for '{args.directory}'")
    table_declared.add_column("Login", style="cyan")
    table_declared.add_column("Role", style="magenta")
    table_declared.add_column("Activity in imported scope", style="white")
    table_declared.add_column("OWNERS File", style="dim")

    for d in res["declared_owners"]:
        status = d["paper_owner"]
        is_paper = ("[yellow]Unknown (incomplete data)[/yellow]" if status is None else
                    "[red]No recent activity[/red]" if status else "[green]Active[/green]")
        table_declared.add_row(d["login"], d["role"], is_paper, d["owners_file"])

    console.print(table_declared)

    if res["unlisted_experts"]:
        table_unlisted = Table(title=f"Unlisted Experts for '{args.directory}' (Top Activity, not in OWNERS)")
        table_unlisted.add_column("Login", style="cyan")
        table_unlisted.add_column("Expertise Score", justify="right", style="green")
        for u in res["unlisted_experts"]:
            table_unlisted.add_row(u["login"], str(u["score"]))
        console.print(table_unlisted)

def cmd_decisions(args):
    query_engine = WayfinderBrainQuery(db=db_manager)
    res = query_engine.query_decisions_and_rationale(args.target)

    console.print(Panel.fit(f"[bold cyan]Decisions & Rationale for '{args.target}'[/bold cyan]"))
    if res["rationales"]:
        console.print("[bold underline]KEP Rationale:[/bold underline]")
        for r in res["rationales"]:
            console.print(f"- [magenta]{r['kind']}[/magenta]: {r['summary']}")
            if r.get("quote"):
                console.print(f"  [dim]Quote: \"{r['quote']}\"[/dim]")

    if res["decisions"]:
        console.print("\n[bold underline]Decisions Made:[/bold underline]")
        for d in res["decisions"]:
            console.print(f"- {d['summary']} (By: {d.get('author') or 'Team'})")
            if d.get("quote"):
                console.print(f"  [dim]Quote: \"{d['quote']}\"[/dim]")

    if res["arguments"]:
        console.print("\n[bold underline]Key Arguments & Debates:[/bold underline]")
        for a in res["arguments"]:
            color = "green" if a["stance"] == "SUPPORTS" else ("red" if a["stance"] == "OPPOSES" else "yellow")
            console.print(f"- [{color}]{a['kind']} ({a['stance']})[/{color}]: {a['summary']} (By: {a.get('author')})")
            if a.get("quote"):
                console.print(f"  [dim]Quote: \"{a['quote']}\"[/dim]")

def cmd_concerns(args):
    query_engine = WayfinderBrainQuery(db=db_manager)
    concerns = query_engine.query_concerns(args.target)
    if not concerns:
        console.print(f"[green]No concerns recorded on '{args.target}'[/green]")
        return

    table = Table(title=f"Concerns on '{args.target}'")
    table.add_column("Status", style="bold")
    table.add_column("Type", style="magenta")
    table.add_column("Summary", style="white")
    table.add_column("Author", style="cyan")
    table.add_column("Quote", style="dim")

    for c in concerns:
        status_color = "red" if c["status"] == "open" else ("yellow" if c["status"] == "claimed_addressed" else "green")
        table.add_row(
            f"[{status_color}]{c['status'].upper()}[/{status_color}]",
            c.get("concern_type") or "general",
            c["summary"],
            c.get("author") or "N/A",
            c.get("quote") or "",
        )
    console.print(table)

def cmd_stale():
    query_engine = WayfinderBrainQuery(db=db_manager)
    findings = query_engine.query_stale_findings()
    if not findings:
        console.print("[green]No stale findings detected in the graph.[/green]")
        return

    table = Table(title="Detected Stale Findings")
    table.add_column("Kind", style="bold red")
    table.add_column("Details", style="white")
    table.add_column("Subject Type", style="cyan")
    table.add_column("Subject ID", style="dim")
    table.add_column("Detected At", style="dim")

    for f in findings:
        table.add_row(f["kind"], f["details"], f["subject_type"], f["subject_id"], f["detected_at"])

    console.print(table)

def main():
    parser = argparse.ArgumentParser(description="Wayfinder: Company Brain on FalkorDB for Kubernetes SIG Node")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("init", help="Initialize FalkorDB schema and indexes")
    pop_p = subparsers.add_parser("populate", help="Run 7-step initial population")
    pop_p.add_argument("--live", action="store_true", help="Fetch live data from GitHub via PAT")
    pop_p.add_argument("--limit", type=int, default=None, help="Max items to query from GitHub")

    subparsers.add_parser("sync", help="Run incremental sync cycle")
    subparsers.add_parser("stats", help="Display graph nodes and edges statistics")

    wk_p = subparsers.add_parser("who-knows", help="Query who knows about a topic or directory")
    wk_p.add_argument("term", type=str, help="Topic name or directory path")
    wk_p.add_argument("--as-of", type=str, default=None, help="Query graph as of ISO timestamp")

    own_p = subparsers.add_parser("ownership", help="Query declared owners, paper owners, and unlisted experts")
    own_p.add_argument("directory", type=str, help="Directory path (e.g. pkg/kubelet)")
    own_p.add_argument("--as-of", type=str, default=None, help="Query graph as of ISO timestamp")

    dec_p = subparsers.add_parser("decisions", help="Query decisions, rationales, and arguments for a KEP or PR")
    dec_p.add_argument("target", type=str, help="KEP number or PR ID")

    con_p = subparsers.add_parser("concerns", help="Query concerns and resolution status for a KEP or PR")
    con_p.add_argument("target", type=str, help="KEP number or PR ID")

    subparsers.add_parser("stale", help="List all detected stale findings")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "init":
        cmd_init()
    elif args.command == "populate":
        cmd_populate(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "stats":
        cmd_stats()
    elif args.command == "who-knows":
        cmd_who_knows(args)
    elif args.command == "ownership":
        cmd_ownership(args)
    elif args.command == "decisions":
        cmd_decisions(args)
    elif args.command == "concerns":
        cmd_concerns(args)
    elif args.command == "stale":
        cmd_stale()
    if args.command in ('who-knows','ownership','decisions','concerns','stale'):
        from .company.core import response_context
        context=response_context(db_manager)
        console.print('[dim]Evidence scope: '+str(context['imported_pr_count'])+' imported PRs. '+
            ' '.join(context['warnings']+context['missing_evidence'])+'[/dim]')

if __name__ == "__main__":
    main()

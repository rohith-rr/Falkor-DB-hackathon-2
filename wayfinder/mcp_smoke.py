"""Read-only MCP handshake and pilot-graph smoke check."""
import asyncio
from datetime import timedelta
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check():
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(command=sys.executable, args=[str(root / "wayfinder" / "mcp_server.py")])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            await session.initialize()
            print(f"Tools discovered: {len((await session.list_tools()).tools)}")
            print(f"Resources discovered: {len((await session.list_resources()).resources)}")
            print(f"Prompts discovered: {len((await session.list_prompts()).prompts)}")
            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                if result.isError:
                    raise RuntimeError(f"{name}: {result.content}")
                data = result.structuredContent
                print(f"{name}: OK" + (f" ({data['count']} results)" if 'count' in data else ''))
                return data
            await call("graph_schema")
            await call("knowledge_coverage")
            stats = await call("graph_stats")
            print(f"Graph: {stats['graph']}")
            directories = await call("search_entities", {"query": "pkg/kubelet", "labels": ["Directory"], "limit": 1})
            if directories['items']:
                ident = directories['items'][0]['n']['id']
                await call("get_entity", {"entity_id": ident})
                await call("get_neighbors", {"entity_id": ident, "limit": 2})
                await call("component_context", {"component": ident, "limit": 2})
            experts = await call("who_knows", {"topic_or_dir": "pkg/kubelet", "limit": 2})
            if experts['items']:
                ids = experts['items'][0]['matches'][0].get('evidence_ids') or []
                if ids:
                    await call("get_evidence", {"evidence_ids": ids[:2]})
            await call("decisions", {"target_id": "1287", "limit": 2})
            await call("read_cypher", {"query": "RETURN $value AS value", "parameters": {"value": 42}})
            print("MCP smoke check passed. No graph writes or paid API calls.")


if __name__ == "__main__":
    try:
        asyncio.run(check())
    except Exception as exc:
        print(f"MCP smoke check failed: {exc}", file=sys.stderr)
        sys.exit(1)

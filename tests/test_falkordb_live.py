import pytest
from wayfinder.db import DatabaseManager
from wayfinder.sync.pipeline import PopulationPipeline
from wayfinder.query import WayfinderBrainQuery

def test_live_falkordb_pipeline_and_queries():
    test_graph = "wayfinder_integration_test"
    db = DatabaseManager(graph_name=test_graph)
    
    # 1. Verify live FalkorDB connection
    assert db.ping() is True

    try:
        # 2. Run full 7-step pipeline on test graph
        pipeline = PopulationPipeline(db=db)
        results = pipeline.run_full_pipeline(use_live_github=False)

        assert results["step1"]["status"] == "success"
        assert results["step2"]["processed_items"] >= 2
        assert results["step6"]["paper_owners"] >= 0

        # 3. Check graph stats
        stats = db.get_stats()
        assert stats["total_nodes"] > 10
        assert stats["total_edges"] > 10

        # Verify key node types exist
        assert "SIG" in stats["node_counts"]
        assert "Person" in stats["node_counts"]
        assert "KEP" in stats["node_counts"]
        assert "FeatureGate" in stats["node_counts"]

        # 4. Test Queries
        q = WayfinderBrainQuery(db=db)

        # Who knows about kubelet?
        who = q.query_who_knows("pkg/kubelet")
        assert len(who) >= 1

        # Who owns pkg/kubelet?
        ownership = q.query_ownership("pkg/kubelet")
        assert len(ownership["declared_owners"]) >= 1

        # Decisions & rationale for KEP-1287
        decisions = q.query_decisions_and_rationale("1287")
        assert len(decisions["rationales"]) >= 1

        # Stale findings
        stale = q.query_stale_findings()
        assert isinstance(stale, list)

        # "as of" time travel query
        historical = q.query_who_knows("pkg/kubelet", as_of="2020-01-01T00:00:00Z")
        # In 2020, activity in 2024 wasn't valid
        assert len(historical) == 0

    finally:
        # Clean up test graph
        db.clear_graph()

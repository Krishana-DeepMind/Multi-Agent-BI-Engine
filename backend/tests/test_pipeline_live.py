import pytest
import time
import uuid
from backend.core.state import AgentSwarmState, QAReport
from backend.agents.router_node import router_node
import logging

logger = logging.getLogger("pipeline_test")

pytestmark = pytest.mark.live

@pytest.mark.asyncio
async def test_full_pipeline_latency():
    """
    Test 6/8: Realistic full pipeline latency test.
    Instead of measuring a single hop against a strict technical boundary,
    this measures the full user-facing time for the agent chain to complete.
    Currently includes Router Node. Will be expanded as more agents are built.
    """
    start_time = time.perf_counter()
    
    # 1. Mock the state (simulating a completed upload)
    state = AgentSwarmState(
        session_id=uuid.uuid4(),
        user_id="test-user",
        pipeline_status="initiated",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
        current_agent="system",
        raw_query="Show me the revenue growth for Q3 across all regions",
        intent_class="trend_analysis",
        business_domain="unknown",
        key_entities=[],
        time_dimension=None,
        raw_file_path="test/sales.csv",
        file_type="csv",
        raw_row_count=1000,
        raw_col_count=10,
        schema_fingerprint="",
        schema_embedding_id=None,
        column_metadata=[],
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path="",
        data_quality_score=1.0,
        rows_before=1000,
        rows_after=1000,
        columns_dropped=[],
        feature_definitions=[],
        enriched_parquet_path="",
        feature_rationale="",
        generated_queries=[],
        query_results=[],
        queries_failed=[],
        dashboard_config=[],
        dashboard_title="",
        dashboard_theme="dark",
        layout_rationale="",
        qa_report=QAReport(
            data_quality_score=0.0,
            completeness_score=0.0,
            query_validity={},
            chart_relevance={},
            anomalies=[],
            suggestions=[],
            overall_confidence=0.0,
            approval_status="needs_review",
            reviewer_notes=None,
        ),
        errors=[],
        retry_count=0,
        token_usage={},
    )
    
    # 2. Run the agent chain
    logger.info("Starting pipeline chain...")
    
    # Hop 1: Router
    t0 = time.perf_counter()
    state = await router_node(state)
    logger.info(f"Router hop took {time.perf_counter() - t0:.2f}s")
    
    # Hop 2: Future Agents (Schema, Cleaning, etc.) will go here
    # state = await schema_inference_node(state)
    
    total_latency = time.perf_counter() - start_time
    logger.info(f"Total pipeline latency: {total_latency:.2f}s")
    
    # 3. Assert on user-facing latency budget (e.g. 30 seconds for the full chain)
    assert total_latency < 30.0, f"Pipeline too slow! Took {total_latency:.2f}s, budget is 30s."
    
    # 4. Verify end state
    assert state.intent_class != "unknown"
    assert state.business_domain != "unknown"

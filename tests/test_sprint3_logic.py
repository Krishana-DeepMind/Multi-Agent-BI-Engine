import asyncio
import os
import uuid
import json
import polars as pl
from unittest.mock import AsyncMock, patch

from backend.core.state import AgentSwarmState
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType
from backend.agents.feature_node import feature_node
from backend.agents.analytics_node import analytics_node

async def run_logic_tests():
    print("--- Starting Sprint 3 Logic Integration Test ---")
    
    # 1. Setup Mock Environment
    db_engine = DuckDBEngine()
    llm_router = LLMRouter()
    
    session_id = uuid.uuid4()
    user_id = "test_user"
    
    # Create a mock cleaned dataset locally
    os.makedirs(f"tmp_storage/{user_id}/{session_id}", exist_ok=True)
    cleaned_path = f"tmp_storage/{user_id}/{session_id}/cleaned.parquet"
    
    df = pl.DataFrame({"id": [1, 2, 3], "sales": [100.0, 150.0, 200.0], "units": [10, 15, 20]})
    df.write_parquet(cleaned_path)
    
    # Setup initial state simulating post-cleaning
    state = AgentSwarmState(
        session_id=session_id,
        user_id=user_id,
        pipeline_status="cleaning",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        current_agent="cleaning",
        raw_query="What is the average price per unit?",
        intent_class="trend_analysis",
        business_domain="sales",
        key_entities=["sales", "units"],
        raw_file_path="mock.csv",
        file_type="csv",
        raw_row_count=3,
        raw_col_count=3,
        schema_fingerprint="mock",
        column_metadata=[
            {"name": "id", "original_name": "id", "dtype": "INT", "semantic_type": "identifier", "business_label": "ID", "null_pct": 0.0, "unique_pct": 1.0, "sample_values": [1,2,3], "is_primary_key": True, "is_candidate_kpi": False},
            {"name": "sales", "original_name": "sales", "dtype": "DOUBLE", "semantic_type": "metric", "business_label": "Sales", "null_pct": 0.0, "unique_pct": 1.0, "sample_values": [100.0, 150.0, 200.0], "is_primary_key": False, "is_candidate_kpi": True},
            {"name": "units", "original_name": "units", "dtype": "INT", "semantic_type": "metric", "business_label": "Units", "null_pct": 0.0, "unique_pct": 1.0, "sample_values": [10, 15, 20], "is_primary_key": False, "is_candidate_kpi": False}
        ],
        similar_schemas_found=False,
        cleaned_parquet_path=cleaned_path
    )
    
    # 2. Mock LLMRouter to return controlled outputs for Feature Node
    async def mock_route_feature(*args, **kwargs):
        task_type = kwargs.get("task_type")
        if task_type == TaskType.FEATURE_IDEATION:
            mock_json = [
                {
                    "name": "price_per_unit",
                    "polars_expr": "pl.col('sales') / pl.col('units')",
                    "sql_expr": "sales / NULLIF(units, 0)",
                    "rationale": "Helps understand unit economics",
                    "expected_insight": "Trend in pricing"
                }
            ]
            return {"content": json.dumps(mock_json)}
        return {"content": "[]"}
        
    llm_router.route = AsyncMock(side_effect=mock_route_feature)
    
    # 3. Test Feature Node Logic
    print("Testing Feature Node...")
    new_state = await feature_node(state, db_engine, llm_router)
    
    assert new_state.pipeline_status == "featuring", "Pipeline status should be featuring"
    assert len(new_state.feature_definitions) == 1, "Should have 1 feature definition"
    assert new_state.enriched_parquet_path != "", "Enriched parquet path should be set"
    assert os.path.exists(new_state.enriched_parquet_path), "Enriched parquet file should exist"
    
    # Check if the DuckDB table 'data' was loaded with the enriched data
    res = db_engine.conn.execute("SELECT * FROM data LIMIT 1").fetchdf()
    assert "price_per_unit" in res.columns, "Derived feature should exist in DuckDB table 'data'"
    print("OK Feature Node logic passed.")
    
    # update state object for next step
    state = new_state
    
    # 4. Mock LLMRouter to return controlled outputs for Analytics Node
    async def mock_route_analytics(*args, **kwargs):
        task_type = kwargs.get("task_type")
        if task_type == TaskType.QUERY_DESIGN:
            mock_json = [
                {
                    "id": "q1",
                    "title": "Avg Price",
                    "business_question": "What is avg price?",
                    "sql": "SELECT AVG(price_per_unit) as avg_price FROM data",
                    "chart_type": "data_table",
                    "insight_summary": "Shows average pricing",
                    "priority": 1
                },
                {
                    "id": "q2",
                    "title": "Failing Syntax",
                    "business_question": "Test repair",
                    "sql": "SELECT SUMX(sales) FROM data",
                    "chart_type": "data_table",
                    "insight_summary": "Intentional fail",
                    "priority": 2
                }
            ]
            return {"content": json.dumps(mock_json)}
        elif task_type == TaskType.QUERY_REPAIR:
            # return fixed query
            mock_json = [
                {
                    "id": "q2",
                    "title": "Failing Syntax (Fixed)",
                    "business_question": "Test repair",
                    "sql": "SELECT SUM(sales) FROM data",
                    "chart_type": "data_table",
                    "insight_summary": "Fixed fail",
                    "priority": 2
                }
            ]
            return {"content": json.dumps(mock_json)}
        return {"content": "[]"}
        
    llm_router.route = AsyncMock(side_effect=mock_route_analytics)
    
    # 5. Test Analytics Node Logic
    print("Testing Analytics Node (with self-correction logic)...")
    final_state = await analytics_node(state, db_engine, llm_router)
    
    assert final_state.pipeline_status == "querying", "Pipeline status should be querying"
    assert len(final_state.generated_queries) == 2, "Should have 2 valid queries (1 repaired)"
    assert len(final_state.query_results) == 2, "Should have 2 query results"
    assert len(final_state.queries_failed) == 0, "All queries should have passed after repair"
    
    q2_result = next(q for q in final_state.query_results if getattr(q, "query_id", q.get("query_id") if isinstance(q, dict) else None) == "q2")
    res_path = getattr(q2_result, "result_storage_path", q2_result.get("result_storage_path") if isinstance(q2_result, dict) else None)
    assert os.path.exists(res_path), "Result parquet file should exist"
    
    print("OK Analytics Node logic passed.")
    print("--- Sprint 3 Logic Integration Test Completed Successfully ---")

if __name__ == "__main__":
    asyncio.run(run_logic_tests())

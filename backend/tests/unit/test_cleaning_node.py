import pytest
import json
import uuid
import polars as pl
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.state import AgentSwarmState, ColumnMeta
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType
from backend.agents.cleaning_node import cleaning_node

@pytest.fixture
def mock_state():
    return AgentSwarmState(
        session_id=str(uuid.uuid4()),
        user_id="test_user",
        pipeline_status="ingesting",
        created_at="2026-08-27T10:00:00Z",
        updated_at="2026-08-27T10:00:00Z",
        current_agent="ingestion",
        raw_query="Show me sales",
        intent_class="trend_analysis",
        business_domain="sales",
        key_entities=["sales"],
        raw_file_path="dummy.csv",
        file_type="csv",
        raw_row_count=5,
        raw_col_count=2,
        schema_fingerprint="abc",
        similar_schemas_found=False,
        column_metadata=[
            ColumnMeta(
                name="revenue", original_name="Revenue", dtype="DOUBLE",
                semantic_type="currency", business_label="Revenue",
                null_pct=0.1, unique_pct=0.9, sample_values=[100.0, 200.0],
                is_primary_key=False, is_candidate_kpi=True
            ),
            ColumnMeta(
                name="notes", original_name="Notes", dtype="VARCHAR",
                semantic_type="text_description", business_label="Notes",
                null_pct=0.5, unique_pct=0.8, sample_values=["good", "bad"],
                is_primary_key=False, is_candidate_kpi=False
            )
        ]
    )

@pytest.fixture
def mock_db_engine():
    engine = MagicMock(spec=DuckDBEngine)
    engine.current_table = "raw_data"
    engine.get_statistical_summary.return_value = "| Some | Summary |"
    # Mock to_polars_lazyframe to return a simple LazyFrame
    df = pl.DataFrame({"revenue": [100.0, None, 300.0, 400.0, 500.0], "notes": ["a", "b", None, "d", "e"]})
    engine.to_polars_lazyframe.return_value = df.lazy()
    
    # Mock connection for write operations
    engine.conn = MagicMock()
    return engine

@pytest.fixture
def mock_llm_router():
    router = MagicMock(spec=LLMRouter)
    
    async def mock_route(task_type, messages, max_tokens, **kwargs):
        if task_type == TaskType.CLEANING_STRATEGY:
            # Return JSON array of CleaningOperation
            data = [
                {
                    "column": "revenue",
                    "operation": "fill_null",
                    "strategy": "median",
                    "rationale": "Numeric column with low null %"
                },
                {
                    "column": "notes",
                    "operation": "drop_column",
                    "strategy": "drop",
                    "rationale": "High null pct"
                }
            ]
            return {"content": json.dumps(data), "tokens_used": 100}
        
        elif task_type == TaskType.CODE_GENERATION:
            # Assuming the prompt is for the revenue column fill_null
            return {"content": "pl.col('revenue').fill_null(pl.col('revenue').median())", "tokens_used": 50}
            
        return {"content": "", "tokens_used": 0}
        
    router.route = AsyncMock(side_effect=mock_route)
    return router

@pytest.mark.asyncio
async def test_cleaning_node_success(mock_state, mock_db_engine, mock_llm_router):
    # Execute
    new_state = await cleaning_node(mock_state, mock_db_engine, mock_llm_router)
    
    # Verify State updates
    assert new_state.pipeline_status == "cleaning"
    assert new_state.current_agent == "cleaning"
    
    # Should have parsed 2 operations, 1 dropped column, 1 executed code
    assert len(new_state.cleaning_operations) == 2
    
    dropped_ops = [op for op in new_state.cleaning_operations if op.operation == "drop_column"]
    assert len(dropped_ops) == 1
    assert dropped_ops[0].column == "notes"
    
    fill_ops = [op for op in new_state.cleaning_operations if op.operation == "fill_null"]
    assert len(fill_ops) == 1
    assert fill_ops[0].polars_code == "pl.col('revenue').fill_null(pl.col('revenue').median())"
    
    # Verify data properties
    assert new_state.rows_before == 5
    assert new_state.rows_after == 5
    assert "notes" in new_state.columns_dropped
    assert new_state.cleaned_parquet_path != ""
    assert new_state.data_quality_score == 1.0  # (No nulls remain after filling and dropping)

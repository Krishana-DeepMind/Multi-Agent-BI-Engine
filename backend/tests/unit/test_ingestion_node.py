import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.ingestion_node import ingestion_node
from backend.core.state import ColumnMeta

@pytest.mark.asyncio
async def test_ingestion_node_cache_hit():
    """Test ingestion node when a similar schema is found in the cache."""
    # Setup initial state
    state = {
        "session_id": "test-session-123",
        "raw_query": "Show me revenue by department",
        "business_domain": "finance",
        "raw_file_path": "uploads/test.csv",
        "file_type": "csv"
    }

    # Mock the components
    with patch("backend.agents.ingestion_node.DuckDBEngine") as MockDuckDB, \
         patch("backend.agents.ingestion_node.SchemaProfiler") as MockProfiler, \
         patch("backend.agents.ingestion_node.EmbeddingEngine") as MockEmbedding:
        
        # Configure DuckDB Engine Mock
        mock_db = MockDuckDB.return_value
        mock_db.load_from_supabase.return_value = {"row_count": 100, "column_count": 2}
        
        # Configure Schema Profiler Mock
        mock_profiler = MockProfiler.return_value
        meta = ColumnMeta(
            name="dept_id", original_name="dept_id", dtype="VARCHAR", semantic_type="unknown",
            business_label="", null_pct=0.0, unique_pct=1.0, sample_values=["A", "B"], 
            is_primary_key=True, is_candidate_kpi=False
        )
        mock_profiler.profile_table.return_value = [meta]
        
        # Configure Embedding Engine Mock for cache hit
        mock_embed = MockEmbedding.return_value
        mock_embed.embed_schema = AsyncMock(return_value=[0.1, 0.2, 0.3])
        
        # Simulate found cached schema
        cached_meta = meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
        cached_meta["semantic_type"] = "identifier"
        cached_meta["business_label"] = "Department ID"
        
        mock_embed.find_similar_schema = AsyncMock(return_value={
            "id": "embed-123",
            "column_metadata": [cached_meta]
        })
        
        # We don't expect the LLMRouter to be called, but we pass it anyway
        mock_router = AsyncMock()

        # Execute
        new_state = await ingestion_node(state, mock_router)
        
        # Verify
        assert new_state["raw_row_count"] == 100
        assert new_state["raw_col_count"] == 2
        assert new_state["similar_schemas_found"] is True
        assert new_state["schema_embedding_id"] == "embed-123"
        assert new_state["pipeline_status"] == "ingesting"
        
        # Verify metadata was updated from cache
        col0 = new_state["column_metadata"][0]
        assert col0["semantic_type"] == "identifier"
        assert col0["business_label"] == "Department ID"
        
        # Ensure LLM was NOT called
        mock_router.route.assert_not_called()

@pytest.mark.asyncio
async def test_ingestion_node_llm_inference():
    """Test ingestion node when no cache is found and LLM is invoked."""
    state = {
        "session_id": "test-session-456",
        "raw_query": "Show me revenue by department",
        "business_domain": "finance",
        "raw_file_path": "uploads/test2.csv",
        "file_type": "csv"
    }

    with patch("backend.agents.ingestion_node.DuckDBEngine") as MockDuckDB, \
         patch("backend.agents.ingestion_node.SchemaProfiler") as MockProfiler, \
         patch("backend.agents.ingestion_node.EmbeddingEngine") as MockEmbedding:
        
        mock_db = MockDuckDB.return_value
        mock_db.load_from_supabase.return_value = {"row_count": 100, "column_count": 1}
        
        mock_profiler = MockProfiler.return_value
        meta = ColumnMeta(
            name="revenue", original_name="revenue", dtype="DOUBLE", semantic_type="unknown",
            business_label="", null_pct=0.0, unique_pct=0.8, sample_values=[100.5, 200.0], 
            is_primary_key=False, is_candidate_kpi=False
        )
        mock_profiler.profile_table.return_value = [meta]
        
        mock_embed = MockEmbedding.return_value
        mock_embed.embed_schema = AsyncMock(return_value=[0.1, 0.2, 0.3])
        # Simulate cache miss
        mock_embed.find_similar_schema = AsyncMock(return_value=None)
        
        # Configure LLMRouter Mock
        mock_router = AsyncMock()
        mock_router.route.return_value = {
            "content": json.dumps({
                "columns": [
                    {
                        "name": "revenue",
                        "semantic_type": "metric",
                        "business_label": "Monthly Revenue",
                        "is_candidate_kpi": True
                    }
                ]
            })
        }

        new_state = await ingestion_node(state, mock_router)
        
        assert new_state["similar_schemas_found"] is False
        assert new_state["pipeline_status"] == "ingesting"
        
        col0 = new_state["column_metadata"][0]
        assert col0["semantic_type"] == "metric"
        assert col0["business_label"] == "Monthly Revenue"
        assert col0["is_candidate_kpi"] is True
        
        # Ensure LLM was called
        mock_router.route.assert_called_once()
        args, kwargs = mock_router.route.call_args
        assert kwargs["task_type"].value == "schema_inference"

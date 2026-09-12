import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from backend.core.checkpoint_manager import checkpoint_manager
from backend.core.state import AgentSwarmState, QAReport
from backend.agents.error_recovery_node import error_recovery_node
from backend.core.llm_router import LLMRouter, TaskType


@pytest.mark.asyncio
async def test_checkpoint_save_and_load():
    session_id = str(uuid.uuid4())
    state_data = {
        "session_id": session_id,
        "user_id": "test_user",
        "pipeline_status": "cleaning",
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
        "current_agent": "cleaning",
        "raw_query": "Analyze dataset",
        "intent_class": "trend_analysis",
        "business_domain": "finance",
        "key_entities": [],
        "raw_file_path": "test.csv",
        "file_type": "csv",
        "raw_row_count": 100,
        "raw_col_count": 5,
        "schema_fingerprint": "abc",
        "column_metadata": [],
        "similar_schemas_found": False,
        "cleaning_operations": [],
        "cleaned_parquet_path": "",
        "data_quality_score": 0.95,
        "rows_before": 100,
        "rows_after": 100,
        "columns_dropped": [],
        "feature_definitions": [],
        "enriched_parquet_path": "",
        "feature_rationale": "",
        "generated_queries": [],
        "query_results": [],
        "queries_failed": [],
        "dashboard_config": [],
        "dashboard_title": "",
        "dashboard_theme": "dark",
        "layout_rationale": "",
        "errors": [],
        "retry_count": 0,
        "token_usage": {}
    }

    # Save checkpoint
    saved = await checkpoint_manager.save_checkpoint(session_id, "cleaning", state_data)
    assert saved["session_id"] == session_id
    assert saved["agent_name"] == "cleaning"

    # Load checkpoint
    loaded = await checkpoint_manager.load_latest_checkpoint(session_id)
    assert loaded is not None
    assert loaded["session_id"] == session_id
    assert loaded["pipeline_status"] == "cleaning"


@pytest.mark.asyncio
async def test_error_recovery_node():
    session_id = str(uuid.uuid4())
    initial_state = AgentSwarmState(
        session_id=uuid.UUID(session_id),
        user_id="test_user",
        pipeline_status="cleaning",
        created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-01T00:00:00Z",
        current_agent="cleaning",
        raw_query="Analyze dataset",
        intent_class="trend_analysis",
        business_domain="finance",
        key_entities=[],
        raw_file_path="test.csv",
        file_type="csv",
        raw_row_count=100,
        raw_col_count=5,
        schema_fingerprint="abc",
        column_metadata=[],
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path="",
        data_quality_score=0.95,
        rows_before=100,
        rows_after=100,
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

    error_info = {
        "agent": "cleaning",
        "error": "Rate limit exceeded 429",
        "attempted": "Cleaning Operation Strategy"
    }

    result_state = await error_recovery_node(initial_state, error_info)
    assert result_state.pipeline_status == "failed"
    assert len(result_state.errors) == 1
    assert result_state.errors[0]["agent"] == "cleaning"
    assert result_state.errors[0]["recoverable"] == "true"


@pytest.mark.asyncio
async def test_llm_router_exponential_backoff_retry():
    router = LLMRouter()
    
    # Mock a failing provider that fails 2 times with rate limit 429 then succeeds on 3rd attempt
    attempt_count = 0

    async def mock_failing_caller(model, messages, response_format, max_tokens):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 3:
            raise Exception("429 Too Many Requests rate limit exceeded")
        return {"content": '{"test": "ok"}', "tokens_used": 100}

    router._providers["gemini"] = mock_failing_caller
    router._providers["groq"] = mock_failing_caller

    with patch.object(router, "_check_cache", return_value=None):
        result = await router.route(
            task_type=TaskType.CLEANING_STRATEGY,
            messages=[{"role": "user", "content": "hello_test_retry_key_unique"}]
        )
        assert result["content"] == '{"test": "ok"}'
        assert attempt_count == 3

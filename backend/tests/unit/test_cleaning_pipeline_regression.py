import pytest
import polars as pl
from unittest.mock import MagicMock
from backend.core.state import AgentSwarmState, CleaningOperation, ColumnMeta
from backend.core.duckdb_engine import DuckDBEngine
from backend.agents.cleaning_node import (
    cleaning_node,
    apply_polars_cleaning_op,
    order_and_enrich_cleaning_ops,
    normalize_phone_number,
)


class TestCleaningPipelineRegression:
    """
    Regression test suite for cleaning pipeline crash fixes:
    1. Phone fill_null and normalization without invalid after_nulls > before_nulls.
    2. Null filling idempotency (running on already-filled data).
    3. Deduplication idempotency (running more than once).
    4. Type casting idempotency (running more than once).
    5. No contact detail fabrication.
    """

    @pytest.mark.asyncio
    async def test_phone_fill_null_crash_regression(self):
        """
        Regression for:
        AgentSwarmState cleaning_operations.34: after_nulls cannot be greater than before_nulls for fill_null operation, column='phone'
        Verifies that when invalid phone strings are cleaned, the operation satisfies Pydantic validation
        and does NOT crash the pipeline.
        """
        engine = DuckDBEngine()
        # Raw data with valid, invalid, and null phone numbers
        df = pl.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "phone": ["+1-555-0101", None, "123", "invalid_contact", "+91-98765-43210"],
            "amount": [100.0, 200.0, 300.0, 400.0, 500.0],
        })
        engine.load_from_bytes(df.write_csv().encode("utf-8"), "csv")

        col_meta = [
            ColumnMeta(
                name="id", original_name="id", dtype="BIGINT", semantic_type="identifier",
                business_label="ID", null_pct=0.0, unique_pct=1.0, sample_values=[1, 2, 3],
                is_primary_key=True, is_candidate_kpi=False,
            ),
            ColumnMeta(
                name="phone", original_name="phone", dtype="VARCHAR", semantic_type="dimension",
                business_label="Phone", null_pct=0.2, unique_pct=0.8, sample_values=["+1-555-0101"],
                is_primary_key=False, is_candidate_kpi=False,
            ),
            ColumnMeta(
                name="amount", original_name="amount", dtype="DOUBLE", semantic_type="metric",
                business_label="Amount", null_pct=0.0, unique_pct=1.0, sample_values=[100.0],
                is_primary_key=False, is_candidate_kpi=True,
            ),
        ]

        import uuid
        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="user-1",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Clean this dataset",
            intent_class="trend_analysis",
            business_domain="ecommerce",
            key_entities=[],
            schema_fingerprint="fp123",
            similar_schemas_found=False,
            raw_file_path="dummy.csv",
            file_type="csv",
            raw_row_count=5,
            raw_col_count=3,
            column_metadata=col_meta,
            current_table=engine.current_table,
        )

        mock_router = MagicMock()
        # Mock LLM proposing a fill_null operation with placeholder strategy on phone column
        async def mock_route(*args, **kwargs):
            return {
                "content": '{"operations": [{"column": "phone", "operation": "fill_null", "strategy": "placeholder_replacement", "rationale": "Fill phone nulls"}]}'
            }
        mock_router.route = mock_route

        # Must execute cleanly without ValueError on state validation
        final_state = await cleaning_node(state, engine, mock_router)
        assert final_state.pipeline_status == "cleaning"
        assert len(final_state.cleaning_operations) > 0

        # Verify none of the fill_null operations violated after_nulls <= before_nulls
        for op in final_state.cleaning_operations:
            if op.operation == "fill_null":
                assert op.after_nulls <= op.before_nulls, f"Op {op.column} violated after_nulls <= before_nulls"

    @pytest.mark.asyncio
    async def test_idempotent_cleaning_operations_run_twice(self):
        """
        Verify that running cleaning operations multiple times on the same data
        is strictly idempotent and does not crash or corrupt counts.
        """
        engine = DuckDBEngine()
        df = pl.DataFrame({
            "id": [1, 2, 2, 3],
            "salary": ["$50,000", "$60,000", "$60,000", None],
            "category": ["Electronics", "Books", "Books", "Electronics"],
        })
        engine.load_from_bytes(df.write_csv().encode("utf-8"), "csv")

        col_meta = [
            ColumnMeta(
                name="id", original_name="id", dtype="BIGINT", semantic_type="identifier",
                business_label="ID", null_pct=0.0, unique_pct=0.75, sample_values=[1, 2, 3],
                is_primary_key=True, is_candidate_kpi=False,
            ),
            ColumnMeta(
                name="salary", original_name="salary", dtype="VARCHAR", semantic_type="metric",
                business_label="Salary", null_pct=0.25, unique_pct=0.5, sample_values=["$50,000"],
                is_primary_key=False, is_candidate_kpi=True,
            ),
            ColumnMeta(
                name="category", original_name="category", dtype="VARCHAR", semantic_type="dimension",
                business_label="Category", null_pct=0.0, unique_pct=0.5, sample_values=["Electronics"],
                is_primary_key=False, is_candidate_kpi=False,
            ),
        ]

        import uuid
        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="user-1",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Clean orders",
            intent_class="trend_analysis",
            business_domain="ecommerce",
            key_entities=[],
            schema_fingerprint="fp123",
            similar_schemas_found=False,
            raw_file_path="dummy.csv",
            file_type="csv",
            raw_row_count=4,
            raw_col_count=3,
            column_metadata=col_meta,
            current_table=engine.current_table,
        )

        mock_router = MagicMock()
        async def mock_route(*args, **kwargs):
            raise RuntimeError("Fallback to heuristic")
        mock_router.route = mock_route

        # Pass 1: Initial cleaning
        state_pass1 = await cleaning_node(state, engine, mock_router)
        assert state_pass1.pipeline_status == "cleaning"

        # Check deduplication affected rows
        dedup_ops = [op for op in state_pass1.cleaning_operations if op.operation == "deduplicate"]
        assert len(dedup_ops) == 1
        assert dedup_ops[0].rows_affected == 1  # 1 duplicate row removed

        # Pass 2: Run cleaning again on already-cleaned dataset
        state_pass2_input = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="user-1",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Clean orders again",
            intent_class="trend_analysis",
            business_domain="ecommerce",
            key_entities=[],
            schema_fingerprint="fp123",
            similar_schemas_found=False,
            raw_file_path="dummy.csv",
            file_type="csv",
            raw_row_count=3,
            raw_col_count=3,
            column_metadata=col_meta,
            current_table=engine.current_table,
        )
        state_pass2 = await cleaning_node(state_pass2_input, engine, mock_router)
        assert state_pass2.pipeline_status == "cleaning"

        # On pass 2, deduplicate rows_affected must be 0 (no more duplicates)
        dedup_ops_pass2 = [op for op in state_pass2.cleaning_operations if op.operation == "deduplicate"]
        assert len(dedup_ops_pass2) == 1
        assert dedup_ops_pass2[0].rows_affected == 0

        # All fill_null operations on pass 2 must satisfy after_nulls <= before_nulls
        for op in state_pass2.cleaning_operations:
            if op.operation == "fill_null":
                assert op.after_nulls <= op.before_nulls

    def test_missing_phone_never_fabricates_data(self):
        """
        Verify that missing or unresolvable contact details are preserved as NULL
        and never populated with fabricated fake phone numbers.
        """
        raw_vals = [None, "", "   ", "None", "N/A"]
        for val in raw_vals:
            norm, note = normalize_phone_number(val)
            assert norm is None
            if note:
                assert "NULL" in note or "Missing" in note or "preserved" in note

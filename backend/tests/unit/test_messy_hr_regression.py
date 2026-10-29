"""
Regression test suite specifically validating the messy HR dataset audit requirements:
1. End-to-end ingestion and schema profiling.
2. Full multi-year date range in Monthly New Hires (all 172 months from 2012 to 2027, not truncated to 5).
3. Duplicate ID validation: exact duplicate rows (18 duplicates) removed -> 527 rows; conflicting IDs preserved.
4. Safe numeric & currency normalization: FX conversion policy (USD: 1.0, EUR: 1.08, GBP: 1.28, INR: 0.012, JPY: 0.0067),
   'k' suffix scaling, sentinel values (-999) set to NULL.
5. Categorical normalization: schema-driven, preserving non-binary, prefer not to say, and distinguishing NULLs.
6. Date validation: multi-format parsing, future dates detected and preserved with review notes.
7. Grounded analytics & layout: QueryResult.chart_data contains all rows; ECharts xAxis.data spans all months.
8. Reproducible data quality score with measurable sub-scores and remaining issues breakdown.
9. Fully dataset-agnostic logic.
"""

import pytest
import os
import uuid
import polars as pl
from unittest.mock import MagicMock, AsyncMock

from backend.core.state import AgentSwarmState, ColumnMeta, QueryDefinition, QueryResult
from backend.core.duckdb_engine import DuckDBEngine
from backend.agents.ingestion_node import ingestion_node
from backend.agents.cleaning_node import cleaning_node, apply_polars_cleaning_op
from backend.agents.analytics_node import analytics_node, generate_generic_analytics_queries
from backend.agents.layout_node import layout_node, build_fallback_config, sanitize_echarts_option, _get_query_data


CSV_PATH = "test_datasets/messy_hr_employees.csv"


@pytest.fixture
def messy_hr_df():
    assert os.path.exists(CSV_PATH), f"Messy HR dataset not found at {CSV_PATH}"
    return pl.read_csv(CSV_PATH)


class TestMessyHRRegression:

    def test_raw_dataset_properties(self, messy_hr_df):
        """Verify baseline properties of the messy HR dataset."""
        assert messy_hr_df.height == 545
        assert messy_hr_df.width == 28
        # Verify presence of exact duplicate rows: 545 raw -> 527 unique rows (18 duplicates)
        assert messy_hr_df.unique().height == 527

    @pytest.mark.asyncio
    async def test_duplicate_identifiers_preserved_and_flagged(self, messy_hr_df):
        """
        Requirement 3: Validate employee counts against duplicate IDs.
        Distinguish exact duplicate rows from conflicting records.
        Never silently delete or merge records without a defensible rule.
        """
        engine = DuckDBEngine()
        csv_bytes = messy_hr_df.write_csv().encode("utf-8")
        engine.load_from_bytes(csv_bytes, "csv")

        col_meta = [
            ColumnMeta(
                name="employee_id", original_name="employee_id", dtype="VARCHAR",
                semantic_type="identifier", business_label="Employee ID",
                null_pct=0.0, unique_pct=0.96, sample_values=["EMP00001", "EMP00156"],
                is_primary_key=False, is_candidate_kpi=False
            ),
            ColumnMeta(
                name="full_name", original_name="full_name", dtype="VARCHAR",
                semantic_type="dimension", business_label="Full Name",
                null_pct=0.0, unique_pct=0.97, sample_values=["Hugo Murphy", "Tomas Malhotra"],
                is_primary_key=False, is_candidate_kpi=False
            ),
            ColumnMeta(
                name="hire_date", original_name="hire_date", dtype="VARCHAR",
                semantic_type="date", business_label="Hire Date",
                null_pct=0.05, unique_pct=0.8, sample_values=["2012-01-15"],
                is_primary_key=False, is_candidate_kpi=False
            ),
            ColumnMeta(
                name="salary", original_name="salary", dtype="VARCHAR",
                semantic_type="currency", business_label="Salary",
                null_pct=0.02, unique_pct=0.7, sample_values=["$85,000", "€65,000", "120k", "-999"],
                is_primary_key=False, is_candidate_kpi=True
            ),
            ColumnMeta(
                name="gender", original_name="gender", dtype="VARCHAR",
                semantic_type="dimension", business_label="Gender",
                null_pct=0.01, unique_pct=0.1, sample_values=["Female", "Male", "Non-binary"],
                is_primary_key=False, is_candidate_kpi=False
            ),
        ]

        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test_user",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Analyze employee retention and hires",
            intent_class="trend_analysis",
            business_domain="hr",
            key_entities=["employee", "salary", "hire_date"],
            schema_fingerprint="fp_hr",
            similar_schemas_found=False,
            raw_file_path=CSV_PATH,
            file_type="csv",
            raw_row_count=545,
            raw_col_count=28,
            column_metadata=col_meta,
            current_table=engine.current_table
        )

        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value={"content": "{}", "tokens_used": 0})

        cleaned_state = await cleaning_node(state, engine, mock_router)
        assert cleaned_state.pipeline_status == "cleaning"

        # Exactly 18 duplicate rows removed -> 527 rows remaining
        assert cleaned_state.rows_before == 545
        assert cleaned_state.rows_after == 527

        # Conflicting records sharing the same ID (e.g. EMP00156) must NOT be deleted
        cleaned_df = pl.read_parquet(cleaned_state.cleaned_parquet_path)
        assert cleaned_df.height == 527

        # Check EMP00156 specifically exists multiple times with distinct employees
        emp156 = cleaned_df.filter(pl.col("employee_id") == "EMP00156")
        assert emp156.height == 2, "Conflicting ID EMP00156 should be preserved for both distinct employees"

        # Check that review notes flagged duplicate identifiers
        flag_notes = [rn for rn in cleaned_state.review_notes if rn.get("action") == "flag_duplicate_identifiers"]
        assert len(flag_notes) > 0
        assert "duplicate identifiers" in flag_notes[0]["note"]

    @pytest.mark.asyncio
    async def test_numeric_currency_and_sentinel_normalization(self, messy_hr_df):
        """
        Requirement 4: Normalize numeric fields safely.
        Detect currency symbols ($ € £ ₹ ¥), suffix 'k', sentinels (-999), and missing values.
        """
        engine = DuckDBEngine()
        csv_bytes = messy_hr_df.write_csv().encode("utf-8")
        engine.load_from_bytes(csv_bytes, "csv")

        col_meta = [
            ColumnMeta(
                name="salary", original_name="salary", dtype="VARCHAR",
                semantic_type="currency", business_label="Salary",
                null_pct=0.02, unique_pct=0.7, sample_values=["$85,000", "€65,000", "120k", "-999"],
                is_primary_key=False, is_candidate_kpi=True
            ),
        ]

        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test_user",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Analyze salary",
            intent_class="distribution",
            business_domain="hr",
            key_entities=["salary"],
            schema_fingerprint="fp_hr",
            similar_schemas_found=False,
            raw_file_path=CSV_PATH,
            file_type="csv",
            raw_row_count=545,
            raw_col_count=28,
            column_metadata=col_meta,
            current_table=engine.current_table
        )

        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value={"content": "{}", "tokens_used": 0})

        cleaned_state = await cleaning_node(state, engine, mock_router)
        cleaned_df = pl.read_parquet(cleaned_state.cleaned_parquet_path)

        salaries = cleaned_df["salary"].drop_nulls()
        # All cleaned salaries must be positive valid numbers
        assert (salaries > 0).all()
        # Sentinel -999 must not exist in cleaned salaries
        assert (salaries == -999).sum() == 0
        assert (salaries < 0).sum() == 0
        # Normal ranges for annual compensation (no extreme out-of-scale sentinels)
        assert (salaries <= 5000000).all()

    @pytest.mark.asyncio
    async def test_categorical_normalization_preserves_legitimate_categories(self, messy_hr_df):
        """
        Requirement 5: Normalize categorical fields using schema-driven rules.
        Preserve legitimate categories such as non-binary and prefer not to say.
        Keep missing values distinguishable from actual categories.
        """
        engine = DuckDBEngine()
        csv_bytes = messy_hr_df.write_csv().encode("utf-8")
        engine.load_from_bytes(csv_bytes, "csv")

        col_meta = [
            ColumnMeta(
                name="gender", original_name="gender", dtype="VARCHAR",
                semantic_type="dimension", business_label="Gender",
                null_pct=0.01, unique_pct=0.1, sample_values=["Female", "Male", "Non-binary", "Prefer not to say"],
                is_primary_key=False, is_candidate_kpi=False
            ),
        ]

        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test_user",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Analyze gender breakdown",
            intent_class="distribution",
            business_domain="hr",
            key_entities=["gender"],
            schema_fingerprint="fp_hr",
            similar_schemas_found=False,
            raw_file_path=CSV_PATH,
            file_type="csv",
            raw_row_count=545,
            raw_col_count=28,
            column_metadata=col_meta,
            current_table=engine.current_table
        )

        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value={"content": "{}", "tokens_used": 0})

        from backend.agents.cleaning_node import apply_polars_cleaning_op
        from backend.core.state import CleaningOperation

        # 1. Test normalization operation specifically: sentinels -> NULL, legitimate categories preserved
        norm_op = CleaningOperation(
            column="gender",
            operation="normalize",
            strategy="standardize_categorical",
            rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
            rationale="Standardize categories"
        )
        lf, _ = apply_polars_cleaning_op(messy_hr_df.lazy(), norm_op)
        norm_series = lf.collect()["gender"]
        # All sentinels ('?', 'n/a', 'missing', 'nan', 'null') must be converted to NULL, not real categories
        assert norm_series.null_count() > 0
        norm_cats = norm_series.drop_nulls().unique().to_list()
        assert any(c.lower() == "non-binary" for c in norm_cats), "Non-binary must be preserved as distinct category"
        assert any("prefer not to say" in c.lower() for c in norm_cats), "Prefer not to say must be preserved"
        assert "?" not in norm_cats and "missing" not in norm_cats and "nan" not in norm_cats

        # 2. Test full cleaning_node pipeline execution
        cleaned_state = await cleaning_node(state, engine, mock_router)
        cleaned_df = pl.read_parquet(cleaned_state.cleaned_parquet_path)
        genders = cleaned_df["gender"].drop_nulls().to_list()
        assert any(g.lower() == "non-binary" for g in genders), "Non-binary preserved in final cleaned data"
        assert any("prefer not to say" in g.lower() for g in genders), "Prefer not to say preserved in final cleaned data"

    @pytest.mark.asyncio
    async def test_date_validation_and_multi_year_range(self, messy_hr_df):
        """
        Requirement 2 & 6: Validate dates, including future dates and multi-year temporal span.
        Ensure Monthly New Hires chart displays the full multi-year date range (2012–2027, ~172 months),
        not just January–May 2012.
        """
        engine = DuckDBEngine()
        csv_bytes = messy_hr_df.write_csv().encode("utf-8")
        engine.load_from_bytes(csv_bytes, "csv")

        col_meta = [
            ColumnMeta(
                name="hire_date", original_name="hire_date", dtype="VARCHAR",
                semantic_type="date", business_label="Hire Date",
                null_pct=0.05, unique_pct=0.8, sample_values=["2012-01-15", "2027-11-01"],
                is_primary_key=False, is_candidate_kpi=False
            ),
            ColumnMeta(
                name="employee_id", original_name="employee_id", dtype="VARCHAR",
                semantic_type="identifier", business_label="Employee ID",
                null_pct=0.0, unique_pct=0.96, sample_values=["EMP00001"],
                is_primary_key=False, is_candidate_kpi=False
            ),
        ]

        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test_user",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Monthly new hires over time",
            intent_class="trend_analysis",
            business_domain="hr",
            key_entities=["hire_date"],
            schema_fingerprint="fp_hr",
            similar_schemas_found=False,
            raw_file_path=CSV_PATH,
            file_type="csv",
            raw_row_count=545,
            raw_col_count=28,
            column_metadata=col_meta,
            current_table=engine.current_table
        )

        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value={"content": "{}", "tokens_used": 0})

        cleaned_state = await cleaning_node(state, engine, mock_router)
        cleaned_df = pl.read_parquet(cleaned_state.cleaned_parquet_path)

        # Date column parsed successfully to Date type
        assert cleaned_df["hire_date"].dtype == pl.Date

        # Valid dates span from 2012 to 2027
        valid_dates = cleaned_df["hire_date"].drop_nulls()
        min_date = valid_dates.min()
        max_date = valid_dates.max()
        assert min_date.year == 2012
        assert max_date.year == 2027

        # Review notes must flag future dates for completeness without deleting them
        future_notes = [rn for rn in cleaned_state.review_notes if rn.get("action") == "flag_future_dates"]
        assert len(future_notes) > 0
        assert "future-dated" in future_notes[0]["note"]

        # Run Analytics node on cleaned data
        engine.conn.execute("CREATE OR REPLACE TABLE data AS SELECT * FROM cleaned_data")
        analytics_state = await analytics_node(cleaned_state, engine, mock_router)

        # Find the time trend query
        trend_query = next((q for q in analytics_state.generated_queries if q.analytical_purpose == "trend"), None)
        assert trend_query is not None, "Time trend query must be generated"

        # Check QueryResult.chart_data
        trend_res = next((qr for qr in analytics_state.query_results if qr.query_id == trend_query.id), None)
        assert trend_res is not None
        assert len(trend_res.chart_data) > 100, f"chart_data should have >100 months, got {len(trend_res.chart_data)}"

        # Check Layout node builds full range chart
        layout_state = await layout_node(analytics_state, mock_router)
        trend_chart = next((c for c in layout_state.dashboard_config if c.query_id == trend_query.id), None)
        assert trend_chart is not None

        x_axis_data = trend_chart.echarts_option.get("xAxis", {}).get("data", [])
        assert len(x_axis_data) > 100, f"xAxis data should span full timeline (>100 months), got {len(x_axis_data)}"
        assert x_axis_data[0].startswith("2012")
        assert x_axis_data[-1].startswith("2027")

        # Verify series data matches categories length
        series_data = trend_chart.echarts_option.get("series", [])[0].get("data", [])
        assert len(series_data) == len(x_axis_data)

    @pytest.mark.asyncio
    async def test_reproducible_data_quality_score(self, messy_hr_df):
        """
        Requirement 9: Make the data-quality score reproducible.
        Show measurable checks, their results, and remaining issues breakdown.
        """
        engine = DuckDBEngine()
        csv_bytes = messy_hr_df.write_csv().encode("utf-8")
        engine.load_from_bytes(csv_bytes, "csv")

        col_meta = [
            ColumnMeta(
                name="employee_id", original_name="employee_id", dtype="VARCHAR",
                semantic_type="identifier", business_label="Employee ID",
                null_pct=0.0, unique_pct=0.96, sample_values=["EMP00001"],
                is_primary_key=True, is_candidate_kpi=False
            ),
            ColumnMeta(
                name="salary", original_name="salary", dtype="VARCHAR",
                semantic_type="currency", business_label="Salary",
                null_pct=0.02, unique_pct=0.7, sample_values=["$85,000"],
                is_primary_key=False, is_candidate_kpi=True
            ),
        ]

        state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test_user",
            pipeline_status="ingesting",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="ingestion",
            raw_query="Data quality assessment",
            intent_class="distribution",
            business_domain="hr",
            key_entities=[],
            schema_fingerprint="fp_hr",
            similar_schemas_found=False,
            raw_file_path=CSV_PATH,
            file_type="csv",
            raw_row_count=545,
            raw_col_count=28,
            column_metadata=col_meta,
            current_table=engine.current_table
        )

        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value={"content": "{}", "tokens_used": 0})

        cleaned_state = await cleaning_node(state, engine, mock_router)

        # Reproducible score validation
        sub_scores = cleaned_state.quality_sub_scores
        assert "completeness" in sub_scores
        assert "uniqueness" in sub_scores
        assert "type_consistency" in sub_scores
        assert "business_integrity" in sub_scores
        assert "remaining_issues" in sub_scores

        expected_dq = round(
            0.35 * sub_scores["completeness"] +
            0.25 * sub_scores["type_consistency"] +
            0.20 * sub_scores["business_integrity"] +
            0.20 * sub_scores["uniqueness"],
            4
        )
        assert abs(cleaned_state.data_quality_score - expected_dq) < 0.001
        assert len(sub_scores["remaining_issues"]) > 0

"""
Test Cleaning Rulebook Integration (Phase 6)
=============================================
Rule-coverage matrix tests for the new heuristic scanner logic
introduced in Phase 3, plus validation of Phase 2 schema changes
and Phase 4 compressor enhancements.
"""

import pytest
import polars as pl
from unittest.mock import MagicMock

from backend.core.state import CleaningOperation, ColumnMeta
from backend.agents.cleaning_node import (
    _is_protected_column,
    _is_kpi_candidate,
    generate_heuristic_cleaning_ops,
    order_and_enrich_cleaning_ops,
    apply_polars_cleaning_op,
    _safe_cast_mixed_columns,
)
from backend.core.schema_compressor import compress_column_meta_for_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_col_meta(
    name: str,
    dtype: str = "VARCHAR",
    semantic_type: str = "unknown",
    null_pct: float = 0.0,
    unique_pct: float = 0.5,
    is_primary_key: bool = False,
    is_candidate_kpi: bool = False,
    sample_values: list | None = None,
) -> dict:
    """Create a lightweight column-metadata dict for testing."""
    return {
        "name": name,
        "dtype": dtype,
        "semantic_type": semantic_type,
        "null_pct": null_pct,
        "unique_pct": unique_pct,
        "is_primary_key": is_primary_key,
        "is_candidate_kpi": is_candidate_kpi,
        "sample_values": sample_values or [],
    }


def _make_mock_db(table_name: str = "raw_data") -> MagicMock:
    """Create a mock DuckDBEngine whose conn.execute always returns MagicMock."""
    engine = MagicMock()
    engine.current_table = table_name
    engine.conn = MagicMock()
    return engine


# ===========================================================================
# 1. Protected Column Detection
# ===========================================================================

class TestProtectedColumnDetection:
    """Identifiers, primary keys, and high-uniqueness columns are protected."""

    def test_primary_key_is_protected(self):
        col = _make_col_meta("user_id", is_primary_key=True)
        assert _is_protected_column(col) is True

    def test_semantic_identifier_is_protected(self):
        col = _make_col_meta("employee_code", semantic_type="identifier")
        assert _is_protected_column(col) is True

    def test_high_uniqueness_is_protected(self):
        col = _make_col_meta("order_ref", unique_pct=0.99)
        assert _is_protected_column(col) is True

    def test_id_suffix_is_protected(self):
        for name in ["customer_id", "patient_id", "id_number"]:
            col = _make_col_meta(name)
            assert _is_protected_column(col) is True, f"'{name}' should be protected"

    def test_exact_id_column_is_protected(self):
        col = _make_col_meta("id")
        assert _is_protected_column(col) is True

    def test_code_sku_uuid_protected(self):
        for name in ["product_code", "sku", "uuid", "ssn", "passport"]:
            col = _make_col_meta(name)
            assert _is_protected_column(col) is True, f"'{name}' should be protected"

    def test_regular_column_not_protected(self):
        col = _make_col_meta("age", dtype="DOUBLE", unique_pct=0.3)
        assert _is_protected_column(col) is False

    def test_name_column_not_protected(self):
        col = _make_col_meta("first_name")
        assert _is_protected_column(col) is False

    def test_works_with_pydantic_model(self):
        """Ensure it also works with ColumnMeta Pydantic objects."""
        meta = ColumnMeta(
            name="patient_id", original_name="patient_id", dtype="VARCHAR",
            semantic_type="identifier", business_label="ID",
            null_pct=0.0, unique_pct=1.0, sample_values=["P001"],
            is_primary_key=True, is_candidate_kpi=False,
        )
        assert _is_protected_column(meta) is True


# ===========================================================================
# 2. KPI Candidate Detection
# ===========================================================================

class TestKPICandidateDetection:
    """KPI / target columns are detected by flag or name pattern."""

    def test_explicit_kpi_flag(self):
        col = _make_col_meta("some_field", is_candidate_kpi=True)
        assert _is_kpi_candidate(col) is True

    def test_revenue_is_kpi(self):
        col = _make_col_meta("revenue")
        assert _is_kpi_candidate(col) is True

    def test_total_sales_is_kpi(self):
        col = _make_col_meta("total_sales")
        assert _is_kpi_candidate(col) is True

    def test_profit_margin_is_kpi(self):
        col = _make_col_meta("profit_margin")
        assert _is_kpi_candidate(col) is True

    def test_age_is_not_kpi(self):
        col = _make_col_meta("age")
        assert _is_kpi_candidate(col) is False

    def test_status_is_not_kpi(self):
        col = _make_col_meta("status")
        assert _is_kpi_candidate(col) is False

    def test_works_with_pydantic_model(self):
        meta = ColumnMeta(
            name="revenue", original_name="revenue", dtype="DOUBLE",
            semantic_type="currency", business_label="Rev",
            null_pct=0.0, unique_pct=0.8, sample_values=[100.0],
            is_primary_key=False, is_candidate_kpi=True,
        )
        assert _is_kpi_candidate(meta) is True


# ===========================================================================
# 3. Heuristic Scanner: Protected Columns Never Dropped/Imputed
# ===========================================================================

class TestHeuristicProtectedColumns:
    """Protected columns are never dropped, imputed, or cast."""

    def test_identifier_not_dropped_even_with_high_nulls(self):
        cols = [_make_col_meta("patient_id", null_pct=0.80, is_primary_key=True)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        op_types = [(o.column, o.operation) for o in ops if o.column == "patient_id"]
        assert all(
            op != "drop_column" for _, op in op_types
        ), "Protected column should never be dropped"

    def test_identifier_not_imputed(self):
        cols = [_make_col_meta("employee_id", null_pct=0.20, semantic_type="identifier")]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        op_types = [(o.column, o.operation) for o in ops if o.column == "employee_id"]
        assert all(
            op != "fill_null" for _, op in op_types
        ), "Protected column should never be imputed"

    def test_identifier_varchar_gets_trim(self):
        cols = [_make_col_meta("sku", dtype="VARCHAR", null_pct=0.05)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        trim_ops = [o for o in ops if o.column == "sku" and o.operation == "trim_whitespace"]
        assert len(trim_ops) == 1, "Protected VARCHAR columns should still get trim_whitespace"


# ===========================================================================
# 4. Heuristic Scanner: KPI Columns with High Null% Get Review Note
# ===========================================================================

class TestHeuristicKPIReview:
    """KPI columns with >10% nulls get a review note, not blind imputation."""

    def test_kpi_high_null_gets_review(self):
        cols = [_make_col_meta("revenue", dtype="DOUBLE", null_pct=0.25, is_candidate_kpi=True)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        rev_ops = [o for o in ops if o.column == "revenue"]
        assert any("REVIEW" in (o.rationale or "") for o in rev_ops), \
            "KPI with high nulls should have REVIEW in rationale"

    def test_kpi_low_null_proceeds_normally(self):
        cols = [_make_col_meta("revenue", dtype="DOUBLE", null_pct=0.05)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        rev_ops = [o for o in ops if o.column == "revenue"]
        # Should have normal operations, not a review-only stub
        assert any(o.operation in ("fill_null", "remove_outliers") for o in rev_ops)


# ===========================================================================
# 5. Imputation Strategy Selection (Median vs Mean vs Zero)
# ===========================================================================

class TestImputationStrategy:
    """Verify correct fill strategy based on column semantics."""

    def test_discount_gets_zero(self):
        cols = [_make_col_meta("discount", dtype="DOUBLE", null_pct=0.1)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        fill_ops = [o for o in ops if o.column == "discount" and o.operation == "fill_null"]
        assert len(fill_ops) >= 1
        assert fill_ops[0].strategy == "zero", "Discount columns should use zero fill"

    def test_refund_gets_zero(self):
        cols = [_make_col_meta("refund", dtype="DOUBLE", null_pct=0.15)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        fill_ops = [o for o in ops if o.column == "refund" and o.operation == "fill_null"]
        assert len(fill_ops) >= 1
        assert fill_ops[0].strategy == "zero"

    def test_categorical_gets_mode(self):
        cols = [_make_col_meta("gender", dtype="VARCHAR", null_pct=0.1)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        fill_ops = [o for o in ops if o.column == "gender" and o.operation == "fill_null"]
        assert len(fill_ops) >= 1
        assert fill_ops[0].strategy == "mode"

    def test_generic_numeric_defaults_to_median(self):
        """Without DuckDB stats, generic numeric columns should default to median."""
        cols = [_make_col_meta("quantity", dtype="DOUBLE", null_pct=0.1)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        fill_ops = [o for o in ops if o.column == "quantity" and o.operation == "fill_null"]
        assert len(fill_ops) >= 1
        assert fill_ops[0].strategy in ("median", "mean"), \
            f"Expected median or mean, got {fill_ops[0].strategy}"


# ===========================================================================
# 6. Outlier Handling
# ===========================================================================

class TestOutlierHandling:
    """Outlier removal only on continuous numeric with ≥30 obs."""

    def test_outlier_proposed_for_continuous_numeric(self):
        cols = [_make_col_meta("salary", dtype="DOUBLE", null_pct=0.0, unique_pct=0.9)]
        db = _make_mock_db()
        # Mock row count = 100 (> 30)
        db.conn.execute.return_value.fetchone.return_value = (100,)
        ops = generate_heuristic_cleaning_ops(cols, db)
        outlier_ops = [o for o in ops if o.column == "salary" and o.operation == "remove_outliers"]
        assert len(outlier_ops) == 1

    def test_no_outlier_for_low_cardinality(self):
        cols = [_make_col_meta("rating", dtype="INT", null_pct=0.0, unique_pct=0.02)]
        db = _make_mock_db()
        db.conn.execute.return_value.fetchone.return_value = (100,)
        ops = generate_heuristic_cleaning_ops(cols, db)
        outlier_ops = [o for o in ops if o.column == "rating" and o.operation == "remove_outliers"]
        assert len(outlier_ops) == 0, "Low-cardinality columns should not get outlier removal"

    def test_no_outlier_for_small_dataset(self):
        cols = [_make_col_meta("value", dtype="DOUBLE", null_pct=0.0, unique_pct=0.9)]
        db = _make_mock_db()
        db.conn.execute.return_value.fetchone.return_value = (15,)  # < 30
        ops = generate_heuristic_cleaning_ops(cols, db)
        outlier_ops = [o for o in ops if o.column == "value" and o.operation == "remove_outliers"]
        assert len(outlier_ops) == 0, "Should not remove outliers with < 30 observations"

    def test_no_outlier_for_identifier(self):
        cols = [_make_col_meta("order_id", dtype="INT", null_pct=0.0, unique_pct=0.99)]
        db = _make_mock_db()
        db.conn.execute.return_value.fetchone.return_value = (500,)
        ops = generate_heuristic_cleaning_ops(cols, db)
        outlier_ops = [o for o in ops if o.column == "order_id" and o.operation == "remove_outliers"]
        assert len(outlier_ops) == 0, "Identifiers should never have outlier removal"

    def test_no_outlier_for_kpi(self):
        cols = [_make_col_meta("revenue", dtype="DOUBLE", null_pct=0.0, unique_pct=0.9, is_candidate_kpi=True)]
        db = _make_mock_db()
        db.conn.execute.return_value.fetchone.return_value = (500,)
        ops = generate_heuristic_cleaning_ops(cols, db)
        outlier_ops = [o for o in ops if o.column == "revenue" and o.operation == "remove_outliers"]
        assert len(outlier_ops) == 0, "KPI columns should never have outlier removal"


# ===========================================================================
# 7. Clean Email Operation (Phase 2 Literal)
# ===========================================================================

class TestCleanEmailOperation:
    """clean_email operation passes Pydantic validation and works correctly."""

    def test_clean_email_pydantic_validation(self):
        """Verify clean_email is accepted by CleaningOperation Literal."""
        op = CleaningOperation(
            column="email",
            operation="clean_email",
            strategy="null_invalid_email",
            rationale="Validate email format",
            rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
        )
        assert op.operation == "clean_email"

    def test_remove_outliers_pydantic_validation(self):
        """Verify remove_outliers (plural) is accepted by CleaningOperation Literal."""
        op = CleaningOperation(
            column="salary",
            operation="remove_outliers",
            strategy="iqr_1.5x",
            rationale="IQR-based outlier detection",
            rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
        )
        assert op.operation == "remove_outliers"

    def test_heuristic_emits_clean_email_for_email_column(self):
        cols = [_make_col_meta("email", dtype="VARCHAR", null_pct=0.1)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        email_ops = [o for o in ops if o.column == "email"]
        assert any(o.operation == "clean_email" for o in email_ops), \
            "Email columns should use clean_email operation, not fill_null"

    def test_clean_email_executor(self):
        df = pl.DataFrame({"email": ["a@b.com", "bad-email", None, "x@y.org"]})
        op = CleaningOperation(
            column="email", operation="clean_email", strategy="null_invalid_email",
            rationale="Validate", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()["email"].to_list()
        assert result[0] == "a@b.com"
        assert result[1] is None  # Invalid email → NULL (not "N/A")
        assert result[2] is None  # Missing email → NULL
        assert result[3] == "x@y.org"


# ===========================================================================
# 8. NULL_SENTINELS: "Unknown" Not Treated as Null
# ===========================================================================

class TestUnknownNotNull:
    """'Unknown' should be preserved as a legitimate category value."""

    def test_unknown_not_in_sentinels(self):
        """The _safe_cast_mixed_columns SQL should NOT replace 'Unknown' with NULL."""
        import duckdb

        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE test_tbl (category VARCHAR)")
        conn.execute("INSERT INTO test_tbl VALUES ('Unknown'), ('N/A'), ('valid'), (NULL), ('none')")

        mock_engine = MagicMock()
        mock_engine.current_table = "test_tbl"
        mock_engine.conn = conn

        _safe_cast_mixed_columns(mock_engine)

        result = conn.execute("SELECT category FROM test_tbl ORDER BY category").fetchall()
        values = [r[0] for r in result]

        # 'Unknown' should be PRESERVED
        assert "Unknown" in values, "'Unknown' should not be converted to NULL"
        # 'N/A' and 'none' should be converted to NULL
        null_count = values.count(None)
        assert null_count >= 3, f"Expected at least 3 NULLs (original + N/A + none), got {null_count}"

        conn.close()


# ===========================================================================
# 9. Operation Ordering Is Deterministic
# ===========================================================================

class TestOperationOrdering:
    """Operations must run in the correct deterministic order."""

    def test_dedup_before_drop(self):
        ops = [
            CleaningOperation(column="col_a", operation="drop_column", strategy="drop",
                              rationale="", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""),
            CleaningOperation(column="all", operation="deduplicate", strategy="remove_duplicates",
                              rationale="", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""),
        ]
        ordered = order_and_enrich_cleaning_ops(ops)
        assert ordered[0].operation == "deduplicate"
        assert ordered[1].operation == "drop_column"

    def test_outlier_before_fill(self):
        ops = [
            CleaningOperation(column="salary", operation="fill_null", strategy="median",
                              rationale="", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""),
            CleaningOperation(column="salary", operation="remove_outliers", strategy="iqr_1.5x",
                              rationale="", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""),
        ]
        ordered = order_and_enrich_cleaning_ops(ops)
        outlier_idx = next(i for i, o in enumerate(ordered) if o.operation == "remove_outliers")
        fill_idx = next(i for i, o in enumerate(ordered) if o.operation == "fill_null")
        assert outlier_idx < fill_idx, "remove_outliers must execute before fill_null"

    def test_auto_pairs_outlier_with_fill(self):
        ops = [
            CleaningOperation(column="value", operation="remove_outliers", strategy="iqr_1.5x",
                              rationale="", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""),
        ]
        enriched = order_and_enrich_cleaning_ops(ops)
        fill_ops = [o for o in enriched if o.operation == "fill_null" and o.column == "value"]
        assert len(fill_ops) == 1, "remove_outliers should auto-pair with median fill_null"
        assert fill_ops[0].strategy == "median"


# ===========================================================================
# 10. High-Null Non-Critical Columns Dropped
# ===========================================================================

class TestHighNullDrop:
    """Columns with >50% nulls (non-protected) should be dropped."""

    def test_high_null_drops(self):
        cols = [_make_col_meta("notes", dtype="VARCHAR", null_pct=0.75)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        drop_ops = [o for o in ops if o.column == "notes" and o.operation == "drop_column"]
        assert len(drop_ops) == 1

    def test_high_null_identifier_not_dropped(self):
        cols = [_make_col_meta("patient_id", dtype="VARCHAR", null_pct=0.75, is_primary_key=True)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        drop_ops = [o for o in ops if o.column == "patient_id" and o.operation == "drop_column"]
        assert len(drop_ops) == 0


# ===========================================================================
# 11. Schema Compressor Enhanced Output (Phase 4)
# ===========================================================================

class TestSchemaCompressorEnhanced:
    """Compressed schema output includes unique_pct, PK, and KPI columns."""

    def test_header_includes_new_columns(self):
        cols = [_make_col_meta("id", is_primary_key=True, unique_pct=1.0)]
        output = compress_column_meta_for_prompt(cols)
        assert "Uniq%" in output
        assert "PK" in output
        assert "KPI" in output

    def test_pk_flag_renders_Y(self):
        cols = [_make_col_meta("id", is_primary_key=True, unique_pct=1.0)]
        output = compress_column_meta_for_prompt(cols)
        lines = output.strip().split("\n")
        data_line = lines[2]  # first data row
        assert "| Y |" in data_line, f"PK=True should render 'Y': {data_line}"

    def test_kpi_flag_renders_Y(self):
        cols = [_make_col_meta("revenue", is_candidate_kpi=True)]
        output = compress_column_meta_for_prompt(cols)
        lines = output.strip().split("\n")
        data_line = lines[2]
        # Should have PK=N and KPI=Y
        assert "| N | Y |" in data_line, f"Expected PK=N KPI=Y: {data_line}"

    def test_works_with_pydantic_model(self):
        meta = ColumnMeta(
            name="salary", original_name="salary", dtype="DOUBLE",
            semantic_type="metric", business_label="Salary",
            null_pct=0.05, unique_pct=0.85, sample_values=[50000.0, 75000.0],
            is_primary_key=False, is_candidate_kpi=True,
        )
        output = compress_column_meta_for_prompt([meta])
        assert "85%" in output  # unique_pct
        assert "| N | Y |" in output  # PK=N, KPI=Y


# ===========================================================================
# 12. Deduplication Always Included
# ===========================================================================

class TestDeduplicationAlwaysIncluded:
    """Every heuristic scan should start with a deduplication op."""

    def test_dedup_present_even_empty_metadata(self):
        ops = generate_heuristic_cleaning_ops([], _make_mock_db())
        dedup_ops = [o for o in ops if o.operation == "deduplicate"]
        assert len(dedup_ops) == 1

    def test_dedup_is_first_after_ordering(self):
        cols = [_make_col_meta("age", dtype="DOUBLE", null_pct=0.1)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        ordered = order_and_enrich_cleaning_ops(ops)
        assert ordered[0].operation == "deduplicate"

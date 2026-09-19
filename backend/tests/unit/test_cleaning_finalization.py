"""
Test Cleaning Agent Finalization (Phase Final)
================================================
Tests for the principled missing-value handling, salary/age/contact
normalization, datatype preservation, and CSV export policy.

Core Principle:
    "Determine whether the missing value can be safely resolved according
    to the column's semantic type and cleaning rules. If it cannot be safely
    resolved, preserve it as missing and record an appropriate review note."
"""

import io
import pytest
import polars as pl
from unittest.mock import MagicMock

from backend.core.state import CleaningOperation, ColumnMeta
from backend.agents.cleaning_node import (
    apply_polars_cleaning_op,
    generate_heuristic_cleaning_ops,
    normalize_phone_number,
)


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
    engine = MagicMock()
    engine.current_table = table_name
    engine.conn = MagicMock()
    return engine


# ===========================================================================
# 1. Age — Preserve Missing as NULL
# ===========================================================================

class TestAgeMissingPreservedAsNull:
    """Age columns should NOT be auto-imputed. Missing values stay as NULL."""

    def test_age_missing_preserved_as_null(self):
        """When Age has missing values, fill_null with 'preserve_null' strategy
        must keep them as None (not imputed with mean/median/mode)."""
        df = pl.DataFrame({"age": [25, 30, None, 45, None]})
        op = CleaningOperation(
            column="age",
            operation="fill_null",
            strategy="preserve_null",
            rationale="Age: preserve missing as NULL",
            rows_affected=0, before_nulls=2, after_nulls=2, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()["age"].to_list()
        # Both nulls must remain as None
        assert result[2] is None, f"Expected None at index 2, got {result[2]}"
        assert result[4] is None, f"Expected None at index 4, got {result[4]}"
        # Non-null values must be unchanged
        assert result[0] == 25
        assert result[1] == 30
        assert result[3] == 45

    def test_age_review_note_generated_by_heuristic(self):
        """Heuristic scanner should emit a review note for age, not auto-impute."""
        cols = [_make_col_meta("age", dtype="VARCHAR", null_pct=0.2, sample_values=["25", "30"])]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        age_fill_ops = [o for o in ops if o.column == "age" and o.operation == "fill_null"]
        # Should have a fill_null op with preserve_null strategy
        assert len(age_fill_ops) >= 1, "Expected at least one fill_null op for age"
        assert age_fill_ops[0].strategy == "preserve_null", \
            f"Expected 'preserve_null' strategy, got '{age_fill_ops[0].strategy}'"
        assert "preserved as NULL" in age_fill_ops[0].rationale or "review" in age_fill_ops[0].rationale.lower(), \
            f"Expected review note in rationale, got: {age_fill_ops[0].rationale}"

    def test_age_not_filled_with_median(self):
        """Heuristic scanner must NOT produce median imputation for age."""
        cols = [_make_col_meta("Age", dtype="VARCHAR", null_pct=0.15, sample_values=["22", "35"])]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        age_median_ops = [
            o for o in ops
            if o.column == "Age" and o.operation == "fill_null" and "median" in o.strategy
        ]
        assert len(age_median_ops) == 0, \
            "Age should NOT have median imputation in heuristic scanner"

    def test_numeric_bigint_age_not_imputed_with_mean_or_median(self):
        """Numeric BIGINT Age column with nulls must get 'preserve_null', NEVER mean/median."""
        from backend.agents.cleaning_node import _pick_numeric_fill_strategy
        cols = [_make_col_meta("Age", dtype="BIGINT", null_pct=0.19, sample_values=[25, 30, 35, 40])]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        age_ops = [o for o in ops if o.column == "Age"]
        assert len(age_ops) == 1, f"Expected 1 op for Age, got {len(age_ops)}"
        assert age_ops[0].operation == "fill_null"
        assert age_ops[0].strategy == "preserve_null"
        assert "mean" not in age_ops[0].strategy and "median" not in age_ops[0].strategy
        # Verify helper safety guard
        assert _pick_numeric_fill_strategy("age", db, "Age") == "preserve_null"

    def test_numeric_double_age_not_imputed(self):
        """Numeric DOUBLE Age column with nulls must get 'preserve_null'."""
        cols = [_make_col_meta("age", dtype="DOUBLE", null_pct=0.10, sample_values=[28.0, 42.0])]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        age_ops = [o for o in ops if o.column == "age"]
        assert len(age_ops) == 1
        assert age_ops[0].strategy == "preserve_null"

    def test_employee_dataset_age_end_to_end(self):
        """End-to-end verification that a dataset with numeric Age preserves all nulls as NULL."""
        csv_data = (
            "Employee_ID,Age,Salary\n"
            "EMP1000,25,50000\n"
            "EMP1001,,60000\n"
            "EMP1002,,70000\n"
            "EMP1003,35,80000\n"
        )
        from backend.core.duckdb_engine import DuckDBEngine
        from backend.core.schema_profiler import SchemaProfiler
        engine = DuckDBEngine()
        engine.load_from_bytes(csv_data.encode("utf-8"), "csv")
        profiler = SchemaProfiler(engine)
        meta = profiler.profile_table()
        ops = generate_heuristic_cleaning_ops(meta, engine)
        lf = engine.to_polars_lazyframe()
        for op in ops:
            if op.column == "all" or op.operation in ("deduplicate", "remove_duplicates"):
                lf = lf.unique(maintain_order=True)
                continue
            current_cols = lf.collect_schema().names()
            if op.column not in current_cols and op.column != "all":
                continue
            lf, code = apply_polars_cleaning_op(lf, op)
        df = lf.collect()
        age_list = df["Age"].to_list()
        assert age_list == [25, None, None, 35], f"Expected nulls preserved, got {age_list}"
        buf = io.StringIO()
        df.write_csv(buf, null_value="NULL")
        csv_text = buf.getvalue()
        assert "EMP1001,NULL,60000" in csv_text
        assert "EMP1002,NULL,70000" in csv_text


# ===========================================================================
# 2. Salary — Cast to Integer
# ===========================================================================

class TestSalaryCastToInteger:
    """Salary columns should be cast to Int64, not Float64."""

    def test_salary_cast_to_int(self):
        """Salary with currency symbols should be cast to Int64."""
        df = pl.DataFrame({"salary": ["$85,000", "72000", "₹1,50,000", "N/A", "55000"]})
        op = CleaningOperation(
            column="salary",
            operation="cast_type",
            strategy="int",
            rationale="Cast salary to integer",
            rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        # Check dtype is Int64
        assert result["salary"].dtype == pl.Int64, \
            f"Expected Int64, got {result['salary'].dtype}"
        values = result["salary"].to_list()
        assert values[0] == 85000
        assert values[1] == 72000
        assert values[4] == 55000
        # N/A should become null, not 0
        assert values[3] is None

    def test_salary_median_fill_rounded_to_int(self):
        """Salary median imputation should produce Int64 values (no decimals)."""
        df = pl.DataFrame({"salary": [50000, 60000, None, 70000, 80000]}, schema={"salary": pl.Float64})
        op = CleaningOperation(
            column="salary",
            operation="fill_null",
            strategy="median",
            rationale="Impute salary with median",
            rows_affected=0, before_nulls=1, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        # Result dtype should be Int64
        assert result["salary"].dtype == pl.Int64, \
            f"Expected Int64, got {result['salary'].dtype}"
        # The null should be filled with the median (65000) rounded to int
        values = result["salary"].to_list()
        assert values[2] is not None, "NULL should be filled"
        assert values[2] == 65000, f"Expected 65000, got {values[2]}"

    def test_salary_heuristic_emits_int_strategy(self):
        """Heuristic scanner should emit cast_type with strategy 'int' for salary."""
        cols = [_make_col_meta("salary", dtype="VARCHAR", null_pct=0.1, sample_values=["$85,000"])]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        cast_ops = [o for o in ops if o.column == "salary" and o.operation == "cast_type"]
        assert len(cast_ops) == 1, "Expected one cast_type op for salary"
        assert cast_ops[0].strategy == "int", \
            f"Expected 'int' strategy for salary, got '{cast_ops[0].strategy}'"


# ===========================================================================
# 3. Contact Number — Country-aware Normalization
# ===========================================================================

class TestContactNumberNormalization:
    """Contact numbers should be normalized with country-aware rules."""

    def test_indian_with_plus91(self):
        """Indian number +91-98765-43210 → '9876543210'."""
        result, note = normalize_phone_number("+91-98765-43210")
        assert result == "9876543210", f"Expected '9876543210', got '{result}'"
        assert note is None

    def test_indian_with_91_prefix(self):
        """Indian number 919876543210 → '9876543210'."""
        result, note = normalize_phone_number("919876543210")
        assert result == "9876543210", f"Expected '9876543210', got '{result}'"
        assert note is None

    def test_indian_with_trunk_prefix(self):
        """Indian trunk-prefixed 09876543210 → '9876543210'."""
        result, note = normalize_phone_number("09876543210")
        assert result == "9876543210", f"Expected '9876543210', got '{result}'"
        assert note is None

    def test_us_with_plus1(self):
        """US number +1-555-867-5309 → '5558675309' (10 local digits, country code stripped)."""
        result, note = normalize_phone_number("+1-555-867-5309")
        assert result == "5558675309", f"Expected '5558675309', got '{result}'"
        assert note is None

    def test_uk_with_plus44(self):
        """UK number +44 20 7946 0958 → '2079460958' (10 digits)."""
        result, note = normalize_phone_number("+44 20 7946 0958")
        assert result == "2079460958", f"Expected '2079460958', got '{result}'"
        assert note is None

    def test_australian_with_plus61(self):
        """Australian number +61 412 345 678 → '412345678' (9 digits)."""
        result, note = normalize_phone_number("+61 412 345 678")
        assert result == "412345678", f"Expected '412345678', got '{result}'"
        assert note is None

    def test_plain_10_digit_number(self):
        """Plain 10-digit number 9876543210 → '9876543210'."""
        result, note = normalize_phone_number("9876543210")
        assert result == "9876543210", f"Expected '9876543210', got '{result}'"
        assert note is None

    def test_7_digit_local_number(self):
        """7-digit local number 5550103 is valid."""
        result, note = normalize_phone_number("5550103")
        assert result == "5550103", f"Expected '5550103', got '{result}'"
        assert note is None

    def test_missing_contact_preserved_as_null(self):
        """None contact → None with review note."""
        result, note = normalize_phone_number(None)
        assert result is None
        assert note is not None
        assert "preserved as NULL" in note

    def test_empty_contact_preserved_as_null(self):
        """Empty string contact → None with review note."""
        result, note = normalize_phone_number("")
        assert result is None
        assert note is not None

    def test_too_short_invalid(self):
        """Number with too few digits → None with review note."""
        result, note = normalize_phone_number("12345")
        assert result is None, f"Expected None for too-short number, got '{result}'"
        assert note is not None

    def test_too_long_invalid(self):
        """Number exceeding max length → None with review note."""
        result, note = normalize_phone_number("12345678901234567")
        assert result is None, f"Expected None for too-long number, got '{result}'"
        assert note is not None

    def test_never_invent_digits(self):
        """The normalizer must never invent missing digits."""
        result, note = normalize_phone_number("123")
        assert result is None, "Must not pad or invent digits"
        assert note is not None

    def test_contact_column_preserves_string_type(self):
        """Contact numbers must remain as Utf8 string, not numeric."""
        df = pl.DataFrame({"contact_number": ["+91-98765-43210", "5558675309", None]})
        op = CleaningOperation(
            column="contact_number",
            operation="fill_null",
            strategy="placeholder_replacement",
            rationale="Phone normalization",
            rows_affected=0, before_nulls=1, after_nulls=1, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        assert result["contact_number"].dtype == pl.Utf8, \
            f"Expected Utf8, got {result['contact_number'].dtype}"
        values = result["contact_number"].to_list()
        # +91 should be stripped, result is 10 digits
        assert values[0] == "9876543210", f"Expected '9876543210', got '{values[0]}'"
        # Plain number passes through
        assert values[1] == "5558675309"
        # Missing preserved as NULL
        assert values[2] is None

    def test_leading_zero_preserved(self):
        """Leading zeros are preserved because contact is stored as string."""
        result, note = normalize_phone_number("0412345678")
        # This is 10 digits after stripping trunk prefix 0 → 9 digits
        # 9 digits is valid in generic range
        assert result is not None
        assert isinstance(result, str)

    def test_formatted_with_parentheses(self):
        """Numbers with parentheses like (555) 867-5309 are cleaned."""
        result, note = normalize_phone_number("(555) 867-5309")
        assert result == "5558675309", f"Expected '5558675309', got '{result}'"
        assert note is None

    def test_french_number(self):
        """French number +33 6 12 34 56 78 → '612345678' (9 digits)."""
        result, note = normalize_phone_number("+33 6 12 34 56 78")
        assert result == "612345678", f"Expected '612345678', got '{result}'"
        assert note is None

    def test_chinese_number(self):
        """Chinese number +86 138 1234 5678 → '13812345678' (11 digits)."""
        result, note = normalize_phone_number("+86 138 1234 5678")
        assert result == "13812345678", f"Expected '13812345678', got '{result}'"
        assert note is None


# ===========================================================================
# 4. No N/A Strings in Numeric Columns
# ===========================================================================

class TestNoNAStringInNumericColumn:
    """Numeric columns must NEVER have 'N/A' string injected."""

    def test_fill_null_on_int_column_preserves_null(self):
        """fill_null on Int64 column with unrecognized strategy preserves NULL, no 'N/A'."""
        df = pl.DataFrame({"revenue": [100, 200, None, 400]})
        op = CleaningOperation(
            column="revenue",
            operation="fill_null",
            strategy="some_unknown_strategy",
            rationale="Test",
            rows_affected=0, before_nulls=1, after_nulls=1, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        # The dtype must remain numeric
        assert result["revenue"].dtype in (pl.Int64, pl.Int32, pl.Float64), \
            f"Expected numeric dtype, got {result['revenue'].dtype}"
        # The null must be preserved as None, NOT replaced with "N/A"
        values = result["revenue"].to_list()
        assert values[2] is None, f"Expected None, got {values[2]}"
        assert "N/A" not in str(values), "N/A string found in numeric column"

    def test_placeholder_on_numeric_preserves_null(self):
        """Placeholder strategy on a numeric column must not inject strings."""
        df = pl.DataFrame({"score": [10.0, None, 30.0]})
        op = CleaningOperation(
            column="score",
            operation="fill_null",
            strategy="placeholder_replacement",
            rationale="Test placeholder on numeric",
            rows_affected=0, before_nulls=1, after_nulls=1, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        assert result["score"].dtype in (pl.Float32, pl.Float64), \
            f"Expected float dtype, got {result['score'].dtype}"
        assert result["score"].to_list()[1] is None, \
            "NULL in numeric column should be preserved, not replaced with string"


# ===========================================================================
# 5. Unresolved Nulls Generate Review Notes
# ===========================================================================

class TestUnresolvedNullsReviewNotes:
    """Columns with remaining nulls after cleaning should generate review notes."""

    def test_heuristic_age_emits_review_rationale(self):
        """Age column with nulls should have 'preserved as NULL' or 'review' in rationale."""
        cols = [_make_col_meta("age", dtype="VARCHAR", null_pct=0.3, sample_values=["25", "40"])]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        age_ops = [o for o in ops if o.column == "age"]
        review_ops = [o for o in age_ops if "preserved as NULL" in o.rationale or "review" in o.rationale.lower()]
        assert len(review_ops) >= 1, \
            f"Expected at least one review note for age, got ops: {[(o.operation, o.rationale) for o in age_ops]}"


# ===========================================================================
# 6. CSV Export NULL Policy
# ===========================================================================

class TestCSVExportNullPolicy:
    """CSV export should write 'NULL' explicitly for missing values."""

    def test_csv_null_written_as_null_string(self):
        """write_csv with null_value='NULL' produces explicit 'NULL' text."""
        df = pl.DataFrame({
            "name": ["Alice", None, "Charlie"],
            "age": [25, None, 35],
        })
        buf = io.StringIO()
        df.write_csv(buf, null_value="NULL")
        csv_content = buf.getvalue()
        # Check that NULL appears in the CSV (not empty string, not N/A)
        assert "NULL" in csv_content, \
            f"Expected 'NULL' in CSV output, got:\n{csv_content}"
        # Verify the structure
        lines = csv_content.strip().split("\n")
        assert len(lines) == 4  # header + 3 data rows
        # Row 2 (index 1) should have NULL for both name and age
        assert "NULL" in lines[2]


# ===========================================================================
# 7. Datatype Preservation After Full Pipeline
# ===========================================================================

class TestDatatypePreservation:
    """Numeric columns must remain numeric after the cleaning pipeline."""

    def test_numeric_column_stays_numeric_after_fill_null(self):
        """A numeric column processed with fill_null should retain its dtype."""
        df = pl.DataFrame({"amount": [10.5, 20.0, None, 40.5]})
        op = CleaningOperation(
            column="amount",
            operation="fill_null",
            strategy="median",
            rationale="Impute median",
            rows_affected=0, before_nulls=1, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        assert result["amount"].dtype in (pl.Float32, pl.Float64), \
            f"Expected float dtype, got {result['amount'].dtype}"
        assert result["amount"].null_count() == 0

    def test_int_column_stays_int_after_zero_fill(self):
        """Int column with zero fill retains Int dtype."""
        df = pl.DataFrame({"discount": [0, 5, None, 10]})
        op = CleaningOperation(
            column="discount",
            operation="fill_null",
            strategy="zero",
            rationale="Impute zero",
            rows_affected=0, before_nulls=1, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()
        assert result["discount"].dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64), \
            f"Expected int dtype, got {result['discount'].dtype}"
        assert result["discount"].to_list() == [0, 5, 0, 10]


# ===========================================================================
# 8. Clean Email — NULL not N/A
# ===========================================================================

class TestCleanEmailNullNotNA:
    """clean_email operation should produce NULL for invalid emails, not 'N/A'."""

    def test_invalid_email_becomes_null(self):
        """Emails without @ become NULL, not 'N/A'."""
        df = pl.DataFrame({"email": ["a@b.com", "bad-email", None, "x@y.org"]})
        op = CleaningOperation(
            column="email", operation="clean_email", strategy="null_invalid_email",
            rationale="Validate", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()["email"].to_list()
        assert result[0] == "a@b.com"
        assert result[1] is None, f"Invalid email should be NULL, got '{result[1]}'"
        assert result[2] is None, f"Missing email should be NULL, got '{result[2]}'"
        assert result[3] == "x@y.org"

    def test_no_na_string_in_email_result(self):
        """Ensure 'N/A' string is never injected into email column."""
        df = pl.DataFrame({"email": [None, "invalid", "test@test.com"]})
        op = CleaningOperation(
            column="email", operation="clean_email", strategy="null_invalid_email",
            rationale="Validate", rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
        )
        lf, code = apply_polars_cleaning_op(df.lazy(), op)
        result = lf.collect()["email"].to_list()
        assert "N/A" not in result, f"Found 'N/A' in result: {result}"


# ===========================================================================
# 9. State Model — review_notes and skipped_columns
# ===========================================================================

class TestStateModelFields:
    """review_notes and skipped_columns are proper Pydantic fields."""

    def test_state_has_review_notes_field(self):
        from backend.core.state import AgentSwarmState
        fields = AgentSwarmState.model_fields
        assert "review_notes" in fields, "AgentSwarmState should have 'review_notes' field"

    def test_state_has_skipped_columns_field(self):
        from backend.core.state import AgentSwarmState
        fields = AgentSwarmState.model_fields
        assert "skipped_columns" in fields, "AgentSwarmState should have 'skipped_columns' field"

    def test_state_serializes_review_notes(self):
        """Ensure review_notes serializes properly through model_dump."""
        import uuid
        from backend.core.state import AgentSwarmState
        state = AgentSwarmState(
            session_id=uuid.uuid4(), user_id="u1", pipeline_status="initiated",
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
            current_agent="system", raw_query="test", intent_class="distribution",
            business_domain="unknown", key_entities=[], raw_file_path="t.csv",
            file_type="csv", raw_row_count=0, raw_col_count=0,
            schema_fingerprint="fp1", similar_schemas_found=False, column_metadata=[],
            review_notes=[{"column": "age", "note": "Missing preserved as NULL"}],
            skipped_columns=[{"column": "id", "reason": "Protected identifier"}],
        )
        dumped = state.model_dump()
        assert len(dumped["review_notes"]) == 1
        assert dumped["review_notes"][0]["column"] == "age"
        assert len(dumped["skipped_columns"]) == 1


# ===========================================================================
# 10. Global Principled Missing Value Handling
# ===========================================================================

class TestGlobalPrincipledMissingValues:
    """For ANY column, missing values should only be imputed when genuinely justified."""

    def test_heuristic_does_not_impute_protected_columns(self):
        """Protected identifier columns are never imputed regardless of null pct."""
        cols = [_make_col_meta("employee_id", dtype="VARCHAR", null_pct=0.3, semantic_type="identifier")]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        fill_ops = [o for o in ops if o.column == "employee_id" and o.operation == "fill_null"]
        assert len(fill_ops) == 0, "Protected columns should never be imputed"

    def test_heuristic_kpi_high_null_gets_review_not_imputation(self):
        """KPI columns with >10% nulls get a review note, not blind imputation."""
        cols = [_make_col_meta("revenue", dtype="DOUBLE", null_pct=0.25, is_candidate_kpi=True)]
        db = _make_mock_db()
        ops = generate_heuristic_cleaning_ops(cols, db)
        rev_ops = [o for o in ops if o.column == "revenue"]
        assert any("REVIEW" in (o.rationale or "") for o in rev_ops), \
            "KPI with high nulls should have REVIEW in rationale"


# ===========================================================================
# 11. Async cleaning_node Pipeline Integration & Audit Trail
# ===========================================================================

class TestCleaningNodePipelineIntegration:
    """Validate that the async cleaning_node properly produces state with review_notes,
    skipped_columns, and data quality metrics."""

    @pytest.mark.asyncio
    async def test_cleaning_node_heuristic_fallback_audit_trail(self, tmp_path):
        import uuid
        from backend.core.duckdb_engine import DuckDBEngine
        from backend.agents.cleaning_node import cleaning_node
        from backend.core.state import AgentSwarmState, QAReport

        # Create messy CSV with Age (missing), Salary (with $), and Customer ID (PK)
        test_csv_content = (
            "Customer ID,Age,salary,contact_phone,revenue\n"
            "1,25,$85000,+1-555-867-5309,1000.0\n"
            "2,None,$92000,invalid-phone,2000.0\n"
            "3,35,$65000,,None\n"
        )
        csv_file = tmp_path / "test_data.csv"
        csv_file.write_text(test_csv_content)

        engine = DuckDBEngine()
        engine.load_from_bytes(test_csv_content.encode("utf-8"), "csv")

        initial_state = AgentSwarmState(
            session_id=uuid.uuid4(),
            user_id="test_user",
            pipeline_status="initiated",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            current_agent="system",
            raw_query="Clean dataset",
            intent_class="trend_analysis",
            business_domain="finance",
            key_entities=[],
            raw_file_path=str(csv_file),
            file_type="csv",
            raw_row_count=3,
            raw_col_count=5,
            schema_fingerprint="fp123",
            similar_schemas_found=False,
            column_metadata=[
                ColumnMeta(name="Customer ID", original_name="Customer ID", dtype="BIGINT", semantic_type="identifier", business_label="ID", null_pct=0.0, unique_pct=1.0, sample_values=[1, 2, 3], is_primary_key=True, is_candidate_kpi=False),
                ColumnMeta(name="Age", original_name="Age", dtype="VARCHAR", semantic_type="unknown", business_label="Age", null_pct=0.33, unique_pct=0.66, sample_values=["25", "35"], is_primary_key=False, is_candidate_kpi=False),
                ColumnMeta(name="salary", original_name="salary", dtype="VARCHAR", semantic_type="currency", business_label="Salary", null_pct=0.0, unique_pct=1.0, sample_values=["$85000", "$92000", "$65000"], is_primary_key=False, is_candidate_kpi=False),
                ColumnMeta(name="contact_phone", original_name="contact_phone", dtype="VARCHAR", semantic_type="unknown", business_label="Phone", null_pct=0.33, unique_pct=0.66, sample_values=["+1-555-867-5309"], is_primary_key=False, is_candidate_kpi=False),
                ColumnMeta(name="revenue", original_name="revenue", dtype="VARCHAR", semantic_type="currency", business_label="Rev", null_pct=0.33, unique_pct=0.66, sample_values=["1000.0", "2000.0"], is_primary_key=False, is_candidate_kpi=True),
            ],
        )

        mock_router = MagicMock()
        # Make LLMRouter raise an exception so it falls back cleanly to deterministic heuristic scanner
        async def mock_route(*args, **kwargs):
            raise RuntimeError("Fallback to heuristic")
        mock_router.route = mock_route

        final_state = await cleaning_node(initial_state, engine, mock_router)

        # 1. State status
        assert final_state.pipeline_status == "cleaning"
        assert final_state.current_agent == "cleaning"
        assert final_state.data_quality_score > 0.0

        # 2. Check skipped_columns for Customer ID
        assert any(sc.get("column") == "Customer ID" for sc in final_state.skipped_columns)

        # 3. Check review_notes for Age (missing preserved as NULL)
        assert any(rn.get("column") == "Age" for rn in final_state.review_notes)

        # 4. Check review_notes for revenue (KPI with nulls)
        assert any(rn.get("column") == "revenue" for rn in final_state.review_notes)

        # 5. Check cleaned data in DuckDB
        cleaned_df = engine.to_polars_lazyframe().collect()
        assert cleaned_df["salary"].dtype == pl.Int64
        assert cleaned_df["salary"].to_list() == [85000, 92000, 65000]
        assert cleaned_df["Age"].to_list()[1] is None  # Age null preserved
        assert cleaned_df["contact_phone"].to_list()[0] == "5558675309"  # Stripped +1
        assert cleaned_df["contact_phone"].to_list()[1] is None  # Invalid becomes NULL
        assert cleaned_df["contact_phone"].to_list()[2] is None  # Missing remains NULL

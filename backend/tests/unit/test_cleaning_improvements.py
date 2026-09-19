import pytest
import uuid
import polars as pl
from backend.core.state import CleaningOperation, AgentSwarmState, ColumnMeta
from backend.agents.cleaning_node import apply_polars_cleaning_op, generate_heuristic_cleaning_ops, cleaning_node
from unittest.mock import MagicMock, AsyncMock


def test_gender_abbreviation_expansion():
    """Test 1: Categorical abbreviation expansion for gender columns."""
    df = pl.DataFrame({
        "gender": ["M", "F", "m", "f", "Male", "Female", "other", "O"]
    })
    op = CleaningOperation(
        column="gender",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize gender"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["gender"].to_list()
    assert result == ["male", "female", "male", "female", "male", "female", "other", "other"]


def test_boolean_standardization():
    """Test 2: Boolean standardization for is_/active/enabled columns."""
    df = pl.DataFrame({
        "is_active": ["true", "Y", "1", "n", "0", "false", "active", "inactive", "yes", "no"]
    })
    op = CleaningOperation(
        column="is_active",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize booleans"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["is_active"].to_list()
    assert result == ["yes", "yes", "yes", "no", "no", "no", "yes", "no", "yes", "no"]


def test_currency_symbol_stripping():
    """Test 3: Currency symbol ($ € £ ₹ ¥) and comma stripping before numeric cast."""
    df = pl.DataFrame({
        "purchase_amount": ["$120.50", "£150.25", "€99.99", "₹1,500.00", "¥5000", "95.00", "N/A"]
    })
    op = CleaningOperation(
        column="purchase_amount",
        operation="cast_type",
        strategy="safe_numeric_cast (float currency)",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Strip currency symbols and cast"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["purchase_amount"].to_list()
    assert result[0] == 120.50
    assert result[1] == 150.25
    assert result[2] == 99.99
    assert result[3] == 1500.00
    assert result[4] == 5000.0
    assert result[5] == 95.00
    assert result[6] is None


def test_country_variant_consolidation():
    """Test 4: Variant consolidation for geographic dimensions."""
    df = pl.DataFrame({
        "country": ["USA", "US", "United States", "uk", "GB", "United Kingdom", "Canada"]
    })
    op = CleaningOperation(
        column="country",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Consolidate country variants"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["country"].to_list()
    assert result == [
        "United States", "United States", "United States",
        "United Kingdom", "United Kingdom", "United Kingdom", "Canada"
    ]


def test_iqr_outlier_detection():
    """Test 5: IQR-based outlier detection replacing extreme values with None."""
    # Q1=25, Q3=75 -> IQR=50 -> upper=150, lower=-50
    values = [20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 999.0]
    df = pl.DataFrame({"revenue": values})
    op = CleaningOperation(
        column="revenue",
        operation="remove_outlier",
        strategy="domain_bounds",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Remove outliers via IQR"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    res = lf.collect()["revenue"].to_list()
    # 999.0 is an extreme outlier and should be replaced with None
    assert res[-1] is None
    # Regular values should remain
    assert res[0] == 20.0


def test_phone_number_validation():
    """Test 8: Phone number cleaning — country-aware normalization."""
    df = pl.DataFrame({
        "phone_number": ["+1-555-0101", None, "+44-20-7946-0958", "123", "555-0103", "1234567890123456789"]
    })
    op = CleaningOperation(
        column="phone_number",
        operation="fill_null",
        strategy="placeholder_replacement",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Clean phone numbers"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    res = lf.collect()["phone_number"].to_list()
    # +1-555-0101 → 15550101 (8 digits). Country code '1' not stripped because
    # local part '5550101' is only 7 digits (US requires 10). Kept as generic 8-digit.
    assert res[0] == "15550101", f"Expected '15550101', got '{res[0]}'"
    # None → None (preserved as NULL, not "Unspecified")
    assert res[1] is None, f"Expected None, got '{res[1]}'"
    # +44-20-7946-0958 → strip +44, local '2079460958' is 10 digits (valid UK)
    assert res[2] == "2079460958", f"Expected '2079460958', got '{res[2]}'"
    # 123 → too short, NULL with review note
    assert res[3] is None, f"Expected None for too-short number, got '{res[3]}'"
    # 555-0103 → 7 digits, valid
    assert res[4] == "5550103", f"Expected '5550103', got '{res[4]}'"
    # 1234567890123456789 → too long, NULL with review note
    assert res[5] is None, f"Expected None for too-long number, got '{res[5]}'"


def test_heuristic_scanner_boolean_and_country():
    """Test 2 & 4: Heuristic scanner generates normalize for boolean and country columns."""
    engine = MagicMock()
    metadata = [
        {"name": "Is_Active", "dtype": "VARCHAR", "semantic_type": "boolean", "null_pct": 0.0, "sample_values": ["true", "false"]},
        {"name": "Country", "dtype": "VARCHAR", "semantic_type": "dimension", "null_pct": 0.0, "sample_values": ["USA", "uk"]},
        {"name": "gender", "dtype": "VARCHAR", "semantic_type": "dimension", "null_pct": 0.0, "sample_values": ["M", "F"]},
    ]
    ops = generate_heuristic_cleaning_ops(metadata, engine)
    cols_normalized = [op.column for op in ops if op.operation == "normalize"]
    assert "Is_Active" in cols_normalized
    assert "Country" in cols_normalized
    assert "gender" in cols_normalized


@pytest.mark.asyncio
async def test_cleaning_node_quality_sub_scores_and_null_tracking():
    """Test 6 & 7: Quality sub-scores and per-operation null tracking in cleaning_node."""
    state = AgentSwarmState(
        session_id=uuid.uuid4(),
        user_id="u1",
        pipeline_status="ingesting",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        current_agent="ingestion",
        raw_query="Clean my data",
        intent_class="distribution",
        business_domain="ecommerce",
        key_entities=[],
        raw_file_path="test.csv",
        file_type="csv",
        raw_row_count=5,
        raw_col_count=3,
        schema_fingerprint="fp1",
        similar_schemas_found=False,
        column_metadata=[
            ColumnMeta(
                name="age", original_name="age", dtype="INT64",
                semantic_type="metric", business_label="Age",
                null_pct=0.2, unique_pct=0.8, sample_values=[20, 30],
                is_primary_key=False, is_candidate_kpi=False
            ),
            ColumnMeta(
                name="status", original_name="status", dtype="VARCHAR",
                semantic_type="dimension", business_label="Status",
                null_pct=0.0, unique_pct=0.4, sample_values=["active"],
                is_primary_key=False, is_candidate_kpi=False
            )
        ]
    )

    df = pl.DataFrame({
        "age": [20, 30, None, 50, 60],
        "status": ["active", "inactive", "active", "active", "inactive"]
    })
    engine = MagicMock()
    engine.current_table = "raw_data"
    engine.to_polars_lazyframe.return_value = df.lazy()
    engine.get_statistical_summary.return_value = "Stats"
    engine.conn = MagicMock()

    router = MagicMock()
    # Force fallback to heuristic scanner
    router.route = AsyncMock(return_value={"content": "{}", "tokens_used": 0})

    new_state = await cleaning_node(state, engine, router)
    assert new_state.pipeline_status == "cleaning"
    assert "quality_sub_scores" in new_state.model_dump()
    sub_scores = new_state.quality_sub_scores
    assert "completeness" in sub_scores
    assert "uniqueness" in sub_scores
    assert "type_consistency" in sub_scores
    assert 0.0 <= sub_scores["completeness"] <= 1.0
    assert 0.0 <= sub_scores["uniqueness"] <= 1.0
    assert 0.0 <= sub_scores["type_consistency"] <= 1.0

    # Verify per-column null tracking (Age missing preserved as NULL)
    for op in new_state.cleaning_operations:
        if op.column == "age" and op.operation == "fill_null":
            assert op.before_nulls == 1
            assert op.after_nulls == 1


def test_blood_type_positive_negative_standardization():
    """Test 9: Blood type standardization - positive/negative to +/- with standard groups."""
    df = pl.DataFrame({
        "BloodType": [
            "a-", "o-", "b-", "b positive", None, "b+", "a-",
            "a positive", "o positive", "a positive", "ab-",
            "b negative", "b-", "o+", "ab+", "b+", "a positive",
            "a+", None, "o positive", "0+", "0 negative", "B+ve", "A -", "AB pos"
        ]
    })
    op = CleaningOperation(
        column="BloodType",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Normalize blood type casing and formats."
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["BloodType"].to_list()
    assert result == [
        "A-", "O-", "B-", "B+", None, "B+", "A-",
        "A+", "O+", "A+", "AB-",
        "B-", "B-", "O+", "AB+", "B+", "A+",
        "A+", None, "O+", "O+", "O-", "B+", "A-", "AB+"
    ]


def test_rh_factor_positive_negative_standardization():
    """Test 10: Rh factor column positive/negative to +/- standardization."""
    df = pl.DataFrame({
        "rh_factor": ["positive", "negative", "pos", "neg", "+ve", "-ve", "+", "-"]
    })
    op = CleaningOperation(
        column="rh_factor",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize Rh factor"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["rh_factor"].to_list()
    assert result == ["+", "-", "+", "-", "+", "-", "+", "-"]


def test_employment_type_standardization():
    """Test 11: Employment type normalization and title-casing."""
    df = pl.DataFrame({
        "EmploymentType": ["FT", "FULLTIME", "full-time", "part-time", "PT", "Part Time", "CONTRACT", "contractor", "intern", "INTERN"]
    })
    op = CleaningOperation(
        column="EmploymentType",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize employment types"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["EmploymentType"].to_list()
    assert result == [
        "Full-Time", "Full-Time", "Full-Time", "Part-Time", "Part-Time", "Part-Time",
        "Contract", "Contract", "Intern", "Intern"
    ]


def test_status_standardization():
    """Test 12: Status normalization and title-casing."""
    df = pl.DataFrame({
        "Status": ["active", "ACTIVE", "resigned", "terminated", "termnated", "leave", "on leave", "ON LEAVE"]
    })
    op = CleaningOperation(
        column="Status",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize status variants"
    )
    lf, code = apply_polars_cleaning_op(df.lazy(), op)
    result = lf.collect()["Status"].to_list()
    assert result == [
        "Active", "Active", "Resigned", "Terminated", "Terminated",
        "On Leave", "On Leave", "On Leave"
    ]


def test_department_and_general_categorical_casing():
    """Test 13: General categorical columns standardized to Title Case with whitespace collapsed."""
    df = pl.DataFrame({
        "Department": ["ENGINEERING", "engineering", "sales", "FINANCE"],
        "City": ["  Berlin  ", "new york", "TORONTO", "  san francisco "]
    })
    op_dept = CleaningOperation(
        column="Department",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize department casing"
    )
    op_city = CleaningOperation(
        column="City",
        operation="normalize",
        strategy="normalize_categorical",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Standardize city casing"
    )
    lf, _ = apply_polars_cleaning_op(df.lazy(), op_dept)
    lf, _ = apply_polars_cleaning_op(lf, op_city)
    res = lf.collect()
    assert res["Department"].to_list() == ["Engineering", "Engineering", "Sales", "Finance"]
    assert res["City"].to_list() == ["Berlin", "New York", "Toronto", "San Francisco"]


def test_multiformat_date_parsing():
    """Test 14: Multi-format date parser successfully parses DD-MM-YYYY, DD-Mon-YY, DD/MM/YYYY, etc."""
    df = pl.DataFrame({
        "event_date": [
            "28-03-1983", "22-Apr-73", "24-06-1999", "31-Aug-24",
            "May 18 2023", "07-Aug-23", "15/04/1990", "2023-01-15", None
        ]
    })
    op = CleaningOperation(
        column="event_date",
        operation="parse_date",
        strategy="standardize_date",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Parse dates"
    )
    lf, _ = apply_polars_cleaning_op(df.lazy(), op)
    res = lf.collect()["event_date"]
    assert res.dtype == pl.Date
    # Only the originally None value should be null; all 8 text dates must parse
    assert res.null_count() == 1
    assert str(res[0]) == "1983-03-28"
    assert str(res[1]) == "1973-04-22"
    assert str(res[3]) == "2024-08-31"


def test_mode_imputation_with_predominant_nulls():
    """Test 15: Mode imputation selects most frequent non-null value even when None is predominant."""
    # None occurs 4 times, 'A+' occurs 3 times, 'B-' occurs 2 times
    df = pl.DataFrame({
        "blood_group": [None, "A+", None, "A+", None, "B-", "A+", "B-", None]
    })
    op = CleaningOperation(
        column="blood_group",
        operation="fill_null",
        strategy="mode",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="",
        rationale="Impute mode"
    )
    lf, _ = apply_polars_cleaning_op(df.lazy(), op)
    res = lf.collect()["blood_group"].to_list()
    # All nulls must be imputed with 'A+' (the non-null mode), not left as None
    assert None not in res
    assert res.count("A+") == 7


def test_order_and_enrich_cleaning_ops_outliers_before_fill():
    """Test 16: order_and_enrich_cleaning_ops places outlier removal before fill_null and pairs missing imputation."""
    from backend.agents.cleaning_node import order_and_enrich_cleaning_ops
    ops = [
        CleaningOperation(column="height", operation="fill_null", strategy="median", rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale=""),
        CleaningOperation(column="height", operation="remove_outlier", strategy="domain_bounds", rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale=""),
        CleaningOperation(column="weight", operation="remove_outlier", strategy="domain_bounds", rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale=""),
    ]
    ordered = order_and_enrich_cleaning_ops(ops)
    
    # Check that remove_outlier precedes fill_null for height
    height_ops = [op.operation for op in ordered if op.column == "height"]
    assert height_ops == ["remove_outlier", "fill_null"]

    # Check that weight was auto-enriched with a fill_null median
    weight_ops = [op.operation for op in ordered if op.column == "weight"]
    assert weight_ops == ["remove_outlier", "fill_null"]


import pytest
import polars as pl
import math
from datetime import datetime
from typing import Dict, Any, List

from backend.agents.layout_node import (
    _parse_date_sort_key,
    _format_metric_value,
    extract_canonical_plotted_data,
    generate_data_grounded_insight,
    validate_and_reconcile_chart_insight,
    split_kpi_cards,
)
from backend.agents.cleaning_node import (
    SENTINEL_VALUES,
    apply_polars_cleaning_op,
    generate_heuristic_cleaning_ops,
)
from backend.core.state import CleaningOperation, ColumnMeta
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler


def test_chronological_sorting_multi_year_and_numeric_months():
    """Verify chronological sorting handles YYYY-MM, single digits, and bare month strings correctly."""
    # 1. Bare month numbers: '1', '10', '11', '12', '2', '3' must sort numerically
    bare_months = ["1", "10", "11", "12", "2", "3", "4", "5", "6", "7", "8", "9"]
    sorted_bare = sorted(bare_months, key=_parse_date_sort_key)
    assert sorted_bare == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"], f"Got: {sorted_bare}"

    # 2. Multi-year YYYY-MM format
    multi_year = ["2024-03", "2022-01", "2026-10", "2023-12", "2022-02"]
    sorted_my = sorted(multi_year, key=_parse_date_sort_key)
    assert sorted_my == ["2022-01", "2022-02", "2023-12", "2024-03", "2026-10"], f"Got: {sorted_my}"

    # 3. Quarters
    quarters = ["2024-Q3", "2023-Q1", "2024-Q1", "2023-Q4"]
    sorted_q = sorted(quarters, key=_parse_date_sort_key)
    assert sorted_q == ["2023-Q1", "2023-Q4", "2024-Q1", "2024-Q3"], f"Got: {sorted_q}"

    # 4. Month names
    month_names = ["Mar", "Jan", "Dec", "Feb", "Oct"]
    sorted_mn = sorted(month_names, key=_parse_date_sort_key)
    assert sorted_mn == ["Jan", "Feb", "Mar", "Oct", "Dec"], f"Got: {sorted_mn}"


def test_mixed_currency_detection_and_fx_conversion():
    """Verify mixed currencies (€, £, ₹, ¥) are safely converted using baseline FX without naive summation."""
    raw_df = pl.DataFrame({
        "order_id": ["O1", "O2", "O3", "O4", "O5"],
        "bill_amount": ["$1,000.00", "€1,000.00", "£1,000.00", "₹100,000", "¥100,000"],
    }).lazy()

    op = CleaningOperation(
        column="bill_amount",
        operation="cast_type",
        strategy="safe_numeric_cast (convert to usd equivalent)",
        rationale="Standardize to USD using explicit traceable baseline FX policy",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
    )

    cleaned_lf, code = apply_polars_cleaning_op(raw_df, op)
    cleaned_df = cleaned_lf.collect()
    bills = cleaned_df["bill_amount"].to_list()

    # Expected values based on rates: EUR=1.08, GBP=1.28, INR=0.012, JPY=0.0067
    assert bills[0] == 1000.0   # USD
    assert bills[1] == 1080.0   # EUR: 1000 * 1.08
    assert bills[2] == 1280.0   # GBP: 1000 * 1.28
    assert bills[3] == 1200.0   # INR: 100,000 * 0.012
    assert bills[4] == 670.0    # JPY: 100,000 * 0.0067


def test_ratio_vs_percentage_formatting():
    """Verify 0.12 formats as 12.0%, while 12.0 formats as 12.0% without double scaling (never 1200%)."""
    # Unscaled ratios: in [-1.5, 1.5]
    assert _format_metric_value("profit_margin", 0.125) == "12.5%"
    assert _format_metric_value("attrition_rate", 0.084) == "8.4%"
    assert _format_metric_value("discount_pct", -0.05) == "-5.0%"

    # Already-scaled percentages: > 1.5
    assert _format_metric_value("margin_pct", 18.5) == "18.5%"
    assert _format_metric_value("completion_rate", 94.2) == "94.2%"

    # Counts must NEVER be formatted as currency or percentage
    assert _format_metric_value("patient_count", 486) == "486"
    assert _format_metric_value("total_records", 506) == "506"
    assert _format_metric_value("unique_patients", 478) == "478"


def test_boolean_guard_never_hijacks_diagnosis_or_categorical():
    """Ensure column 'diagnosis_category' or 'analysis_type' is never matched as a boolean column."""
    raw_df = pl.DataFrame({
        "diagnosis_category": ["Pediatrics", "pediatrics", "orthopedics", "Cardiology", "?", "-", "#N/A"],
    }).lazy()

    op = CleaningOperation(
        column="diagnosis_category",
        operation="normalize",
        strategy="normalize_categorical",
        rationale="Standardize casing and sentinels",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
    )

    cleaned_lf, code = apply_polars_cleaning_op(raw_df, op)
    cleaned_df = cleaned_lf.collect()
    vals = cleaned_df["diagnosis_category"].to_list()

    # Valid categories must be TitleCase and sentinels mapped to None/Unknown
    assert "Pediatrics" in vals
    assert "Orthopedics" in vals
    assert "Cardiology" in vals
    assert None in vals or "Unknown" in vals
    # Raw lowercase or sentinels must NOT remain
    assert "orthopedics" not in vals
    assert "?" not in vals
    assert "-" not in vals
    assert "#N/A" not in vals


def test_entity_id_counts_vs_total_records_split():
    """Verify KPI card splitting properly distinguishes total records from distinct entity count."""
    q_def = {"id": "headline_kpi_summary", "priority": 1}
    q_res = {
        "column_names": ["total_records", "unique_patients", "total_bill"],
        "chart_data": [{"total_records": 486, "unique_patients": 478, "total_bill": 6509934.08}],
        "row_count": 1
    }

    cards = split_kpi_cards(q_def, q_res, max_kpis=4)
    assert len(cards) == 3

    # Check titles and values
    titles = [c["title"] for c in cards]
    values = [c["echarts_option"]["value"] for c in cards]

    assert titles[0] == "Total Records"
    assert values[0] == "486"

    assert titles[1] == "Unique Patients"
    assert values[1] == "478"

    assert titles[2] == "Total Bill"
    assert values[2] == "$6.5M"


def test_insight_grounding_matches_highest_plotted_bar():
    """Verify generated insight always reconciles with the highest plotted bar and actual values."""
    q_def = {"title": "Effective Bill by Diagnosis Category", "x_axis": "category", "y_axis": "total_bill"}
    q_res = {
        "column_names": ["category", "total_bill"],
        "chart_data": [
            {"category": "Pediatrics", "total_bill": 2521484.99},
            {"category": "Cardiology", "total_bill": 1046287.79},
            {"category": "Orthopedics", "total_bill": 148014.82},
        ],
        "row_count": 3
    }

    insight = generate_data_grounded_insight(q_def, q_res, "bar")
    assert "Pediatrics ranks highest with $2.5M" in insight
    assert "followed by Cardiology at $1.0M" in insight
    assert "3 observed categories" in insight


def test_reconciliation_fixes_stale_insight():
    """Verify validate_and_reconcile_chart_insight overrides a hallucinated insight with grounded data."""
    chart_cfg = {
        "query_id": "test_chart",
        "title": "Revenue Breakdown",
        "chart_type": "bar",
        "insight_summary": "Neurology ranks highest with $9.9M across categories.",  # Hallucinated
        "echarts_option": {
            "xAxis": {"type": "category", "data": ["Pediatrics", "Cardiology", "Orthopedics"]},
            "series": [{"name": "Total Bill", "data": [2521484.99, 1046287.79, 148014.82]}],
        }
    }
    q_res = {
        "column_names": ["category", "total_bill"],
        "chart_data": [
            {"category": "Pediatrics", "total_bill": 2521484.99},
            {"category": "Cardiology", "total_bill": 1046287.79},
            {"category": "Orthopedics", "total_bill": 148014.82},
        ]
    }

    reconciled = validate_and_reconcile_chart_insight(chart_cfg, q_res)
    assert "Pediatrics ranks highest with $2.5M" in reconciled["insight_summary"]
    assert "Neurology" not in reconciled["insight_summary"]

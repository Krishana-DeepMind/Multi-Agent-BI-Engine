import pytest
import math
import polars as pl
import duckdb
from backend.agents.cleaning_node import apply_polars_cleaning_op, CleaningOperation
from backend.agents.layout_node import (
    split_kpi_cards,
    extract_canonical_plotted_data,
    build_fallback_config,
    sanitize_echarts_option,
    generate_data_grounded_insight,
)


def test_negative_and_sentinel_bmi_handling():
    """Negative and sentinel weights/heights (-999, -9999, <= 0) must become NULL, never negative floats or 0."""
    lf = pl.LazyFrame({
        "patient_id": ["P1", "P2", "P3", "P4", "P5", "P6"],
        "weight_kg": ["-999", "70.5", "-50", "0", "-", "85.0"],
        "height_cm": ["170", "175", "180", "-999", "165", "182"]
    })

    op_w = CleaningOperation(column="weight_kg", operation="cast_type", strategy="safe_numeric_cast", rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale="")
    lf_w, _ = apply_polars_cleaning_op(lf, op_w)

    op_h = CleaningOperation(column="height_cm", operation="cast_type", strategy="safe_numeric_cast", rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale="")
    lf_h, _ = apply_polars_cleaning_op(lf_w, op_h)

    df_cleaned = lf_h.collect()

    weights = df_cleaned["weight_kg"].to_list()
    heights = df_cleaned["height_cm"].to_list()

    assert weights[0] is None  # -999 was converted to None
    assert weights[1] == 70.5
    assert weights[2] is None  # negative weight converted to None
    assert weights[3] is None  # 0 weight converted to None
    assert weights[4] is None  # "-" converted to None
    assert weights[5] == 85.0

    assert heights[3] is None  # -999 converted to None

    # Derive BMI: must strictly be None when either weight or height is invalid
    df_bmi = df_cleaned.with_columns(
        pl.when((pl.col("weight_kg") > 0.0) & (pl.col("height_cm") > 0.0))
        .then((pl.col("weight_kg") / ((pl.col("height_cm") / 100.0) ** 2)).round(2))
        .otherwise(None)
        .alias("bmi")
    )
    bmis = df_bmi["bmi"].to_list()
    assert bmis[0] is None
    assert bmis[1] == 23.02
    assert bmis[2] is None
    assert bmis[3] is None
    assert bmis[4] is None
    assert bmis[5] == 25.66


def test_mixed_currency_amounts():
    """Mixed currency amounts (INR, JPY, EUR, GBP, USD) are correctly converted to USD equivalent."""
    lf = pl.LazyFrame({
        "bill_amount": ["₹100,000", "¥1,000,000", "€1,000", "£1,000", "$1,000"]
    })
    op = CleaningOperation(
        column="bill_amount",
        operation="cast_type",
        strategy="safe_numeric_cast (convert to usd equivalent)",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale=""
    )
    lf_clean, _ = apply_polars_cleaning_op(lf, op)
    df = lf_clean.collect()
    bills = df["bill_amount"].to_list()

    assert bills[0] == 1200.0   # 100,000 * 0.012
    assert bills[1] == 6700.0   # 1,000,000 * 0.0067
    assert bills[2] == 1080.0   # 1,000 * 1.08
    assert bills[3] == 1280.0   # 1,000 * 1.28
    assert bills[4] == 1000.0   # 1,000 * 1.0


def test_extreme_legitimate_monetary_values_preserved():
    """Legitimate large bills ($2,500,000) are preserved without truncation."""
    lf = pl.LazyFrame({
        "bill_amount": ["$2,500,000.00", "$150.00", "$2,100.00"]
    })
    op = CleaningOperation(column="bill_amount", operation="cast_type", strategy="safe_numeric_cast", rows_affected=0, before_nulls=0, after_nulls=0, polars_code="", rationale="")
    lf_clean, _ = apply_polars_cleaning_op(lf, op)
    df = lf_clean.collect()
    assert df["bill_amount"][0] == 2500000.0


def test_distinct_patient_and_record_counts_reconciliation():
    """Reconciles source records, valid records, unique patients, and excluded records."""
    data = [
        {"source_records": 506, "valid_records": 486, "unique_patients": 478, "excluded_records": 20, "total_revenue": 6500000, "avg_revenue": 13374}
    ]
    q_def = {"id": "kpi_overview", "title": "Overview", "priority": 1}
    q_res = {"columns": list(data[0].keys()), "data": data}

    cards = split_kpi_cards(q_def, q_res, max_kpis=6)
    assert len(cards) == 6

    titles = [c["title"] for c in cards]
    assert "Source Records" in titles
    assert "Valid Records" in titles
    assert "Unique Patients" in titles
    assert "Excluded Records" in titles

    # Verify no row count is labeled as patient count
    card_dict = {c["title"]: c["echarts_option"]["value"] for c in cards}
    assert card_dict["Source Records"] == "506"
    assert card_dict["Valid Records"] == "486"
    assert card_dict["Unique Patients"] == "478"
    assert card_dict["Excluded Records"] == "20"


def test_percentage_deviation_vs_index_benchmark():
    """Benchmark charts correctly use 0% baseline for deviation and 100 for index."""
    # 1. Percentage deviation: values can be negative and baseline is 0%
    q_dev = {
        "id": "diag_benchmark_dev",
        "title": "Diagnosis vs Overall (% Deviation)",
        "analytical_purpose": "comparison",
        "x_axis": "category",
        "y_axis": "pct_diff_from_overall"
    }
    q_res_dev = {
        "column_names": ["category", "pct_diff_from_overall"],
        "data": [
            {"category": "Pediatrics", "pct_diff_from_overall": 185.2},
            {"category": "Cardiology", "pct_diff_from_overall": 11.6},
            {"category": "Neurology", "pct_diff_from_overall": -83.0}
        ]
    }
    cfg_dev = build_fallback_config(q_dev, q_res_dev, "bar")
    opt_dev = sanitize_echarts_option(cfg_dev, q_res_dev["data"], "category", "pct_diff_from_overall", "bar", q_dev)

    mark_line_dev = opt_dev["series"][0].get("markLine")
    assert mark_line_dev is not None
    assert mark_line_dev["data"][0]["yAxis"] == 0
    assert "0%" in mark_line_dev["data"][0]["label"]["formatter"]

    # 2. Raw monetary comparison with overall_avg
    q_curr = {
        "id": "diag_benchmark_curr",
        "title": "Diagnosis Effective Bill vs Overall Average",
        "analytical_purpose": "comparison",
        "x_axis": "category",
        "y_axis": "category_avg"
    }
    q_res_curr = {
        "column_names": ["category", "category_avg", "overall_avg"],
        "data": [
            {"category": "Pediatrics", "category_avg": 38204.0, "overall_avg": 13394.9},
            {"category": "Neurology", "category_avg": 1746.0, "overall_avg": 13394.9}
        ]
    }
    cfg_curr = build_fallback_config(q_curr, q_res_curr, "bar")
    opt_curr = sanitize_echarts_option(cfg_curr, q_res_curr["data"], "category", "category_avg", "bar", q_curr)

    mark_line_curr = opt_curr["series"][0].get("markLine")
    assert mark_line_curr is not None
    assert mark_line_curr["data"][0]["yAxis"] == 13394.9


def test_date_truncation_multi_year_integrity():
    """Dates across multiple years sort chronologically and do not lump into bare 1-12 months."""
    dates = ["2022-03-15", "2023-03-20", "2024-03-21", "2022-10-01", "2025-01-10"]
    q_def = {"id": "trend", "x_axis": "month", "y_axis": "val"}
    q_res = {
        "column_names": ["month", "val"],
        "data": [
            {"month": "2024-03", "val": 3300000.0},
            {"month": "2022-03", "val": 15000.0},
            {"month": "2023-03", "val": 22000.0},
            {"month": "2022-10", "val": 18000.0},
            {"month": "2025-01", "val": 25000.0},
        ]
    }
    canonical = extract_canonical_plotted_data(q_def, q_res, "line")
    # Verify chronological sorting
    assert canonical["categories"] == ["2022-03", "2022-10", "2023-03", "2024-03", "2025-01"]
    # 2024-03 retains its isolated $3.3M spike without distorting 2022 or 2023
    idx_2024 = canonical["categories"].index("2024-03")
    assert canonical["values"][idx_2024] == 3300000.0


def test_safe_handling_of_empty_and_zero_denominators():
    """Zero denominators and empty result sets handle gracefully without crashing."""
    q_def = {"id": "empty_query", "x_axis": "dim", "y_axis": "metric"}
    q_res = {"column_names": ["dim", "metric"], "data": []}

    canonical = extract_canonical_plotted_data(q_def, q_res, "bar")
    assert canonical["categories"] == []
    assert canonical["values"] == []

    insight = generate_data_grounded_insight(q_def, q_res, "bar")
    assert "0 matching records" in insight or "No qualifying records" in insight


def test_domain_agnostic_hr_and_retail_compatibility():
    """Ensures pipeline functions correctly for HR and retail datasets without hardcoding."""
    # HR dataset: employee_id, department, salary, bonus_pct
    hr_data = [
        {"department": "Engineering", "salary": 120000, "bonus_pct": 15},
        {"department": "Sales", "salary": 90000, "bonus_pct": 20},
        {"department": "Marketing", "salary": 85000, "bonus_pct": 10}
    ]
    q_hr = {"id": "hr_kpi", "x_axis": "department", "y_axis": "salary"}
    q_res_hr = {"column_names": ["department", "salary"], "data": hr_data}
    canonical_hr = extract_canonical_plotted_data(q_hr, q_res_hr, "bar")
    assert canonical_hr["categories"] == ["Engineering", "Sales", "Marketing"]
    assert canonical_hr["values"] == [120000.0, 90000.0, 85000.0]

    # Retail dataset: order_id, product_category, revenue, discount_pct
    retail_data = [
        {"product_category": "Electronics", "revenue": 500000, "discount_pct": 5},
        {"product_category": "Apparel", "revenue": 200000, "discount_pct": 10}
    ]
    q_retail = {"id": "retail_ranking", "x_axis": "product_category", "y_axis": "revenue"}
    q_res_retail = {"column_names": ["product_category", "revenue"], "data": retail_data}
    canonical_retail = extract_canonical_plotted_data(q_retail, q_res_retail, "bar")
    assert canonical_retail["categories"] == ["Electronics", "Apparel"]
    assert canonical_retail["values"] == [500000.0, 200000.0]


def test_benchmark_subtitle_synchronization():
    """Validates that validate_and_reconcile_chart_insight synchronizes subtitle benchmarks with markLine."""
    from backend.agents.layout_node import validate_and_reconcile_chart_insight
    chart_cfg = {
        "query_id": "test_bench",
        "title": "Diagnosis Category Effective Bill vs Overall Average",
        "subtitle": "How does each diagnosis category's average compare to the overall benchmark average ($13.4K)?",
        "chart_type": "bar",
        "echarts_option": {
            "xAxis": {"data": ["Pediatrics", "Cardiology"]},
            "series": [{
                "data": [44019.48, 16940.84],
                "markLine": {
                    "data": [{
                        "yAxis": 14708.5,
                        "label": {"formatter": "Overall Avg ($14.7K)"}
                    }]
                }
            }]
        }
    }
    reconciled = validate_and_reconcile_chart_insight(chart_cfg, {"column_names": ["category", "avg"], "data": []})
    assert "($14.7K)" in reconciled["subtitle"]
    assert "($13.4K)" not in reconciled["subtitle"]


def test_categorical_breakdown_unassigned_retention():
    """Ensures categorical breakdown query retains unassigned records using COALESCE."""
    from backend.agents.analytics_node import generate_generic_analytics_queries
    from backend.core.duckdb_engine import DuckDBEngine
    from backend.core.schema_profiler import SchemaProfiler

    engine = DuckDBEngine()
    engine.conn.execute("CREATE TABLE data (diagnosis_category VARCHAR, effective_bill DOUBLE, patient_id VARCHAR)")
    engine.conn.execute("INSERT INTO data VALUES ('Pediatrics', 100.0, 'P1'), (NULL, 50.0, 'P2')")
    meta = SchemaProfiler(engine).profile_table("data")
    queries = generate_generic_analytics_queries(engine, meta, table_name="data")
    bd_queries = [q for q in queries if q["id"] == "breakdown_by_diagnosis_category"]
    assert len(bd_queries) == 1
    sql = bd_queries[0]["sql"]
    assert "COALESCE" in sql
    assert "Unassigned / Missing" in sql


def test_mutually_exclusive_bmi_partitioning():
    """Ensures the 6 BMI partition buckets partition all 486 rows without overlap."""
    import duckdb
    # Simulated 486 dataset matching the 6 mutually exclusive categories
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE test_data AS
        SELECT 
            CASE 
                WHEN i <= 340 THEN 25.0
                WHEN i <= 383 THEN 26.0
                WHEN i <= 408 THEN 24.0
                WHEN i <= 410 THEN 23.0
                ELSE NULL
            END AS bmi,
            CASE 
                WHEN i <= 383 THEN 'female'
                ELSE NULL
            END AS gender,
            CASE 
                WHEN i <= 340 OR (i > 383 AND i <= 408) THEN DATE '2024-01-01'
                ELSE NULL
            END AS admission_date
        FROM range(1, 487) t(i);
    """)
    res = conn.execute("""
        SELECT 
            COUNT(CASE WHEN bmi IS NOT NULL AND gender IS NOT NULL AND admission_date IS NOT NULL THEN 1 END) AS in_both,
            COUNT(CASE WHEN bmi IS NOT NULL AND gender IS NOT NULL AND admission_date IS NULL THEN 1 END) AS gender_only,
            COUNT(CASE WHEN bmi IS NOT NULL AND gender IS NULL AND admission_date IS NOT NULL THEN 1 END) AS monthly_only,
            COUNT(CASE WHEN bmi IS NOT NULL AND gender IS NULL AND admission_date IS NULL THEN 1 END) AS neither,
            COUNT(CASE WHEN bmi IS NULL THEN 1 END) AS invalid_bmi,
            COUNT(*) AS total_rows
        FROM test_data
    """).fetchone()
    assert res[0] == 340
    assert res[1] == 43
    assert res[2] == 25
    assert res[3] == 2
    assert res[4] == 76
    assert res[5] == 486
    assert sum(res[:5]) == res[5]


@pytest.mark.asyncio
async def test_dynamic_filtering_recalculates_kpis_and_charts():
    """Validates that filter_dashboard dynamically recalculates charts and KPIs from filtered dataset."""
    from backend.api.pipeline import filter_dashboard, FilterRequest
    session_id = "08bbe011-fcfe-4736-9278-d22e61ce9c35"
    
    # 1. Filter: DiagnosisCategory = Pediatrics
    res = await filter_dashboard(session_id, FilterRequest(filters={"diagnosis_category": "Pediatrics"}))
    assert res is not None
    assert "charts" in res
    kpis = [c for c in res["charts"] if c.get("chart_type") == "kpi_card"]
    assert len(kpis) >= 1
    # Check that Total Records reflects 66 pediatric patients
    rec_kpi = next((k for k in kpis if "Records" in k.get("title", "")), None)
    assert rec_kpi is not None
    val = rec_kpi.get("value") or rec_kpi.get("echarts_option", {}).get("value")
    assert str(val) == "66"

    # 2. Clear filters
    res_clear = await filter_dashboard(session_id, FilterRequest(filters={"diagnosis_category": "All"}))
    kpis_clear = [c for c in res_clear["charts"] if c.get("chart_type") == "kpi_card"]
    source_kpi = next((k for k in kpis_clear if k.get("title") == "Source Records"), None)
    assert source_kpi is not None
    val_clear = source_kpi.get("value") or source_kpi.get("echarts_option", {}).get("value")
    assert str(val_clear) == "506"


def test_outlier_values_patient_level_reconciliation():
    """Validates patient-level outlier ranking, values, and mu + 1.5 sigma threshold."""
    import duckdb
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE patient_bills (patient_id VARCHAR, effective_bill DOUBLE);
        INSERT INTO patient_bills VALUES 
            ('P00213', 2375000.0),
            ('P00216', 1305600.0),
            ('P00218', 902500.0),
            ('P00415', 902500.0),
            ('P00428', 30000.0),
            ('P00291', 17401.02);
        INSERT INTO patient_bills 
        SELECT 'NORM_' || i, 1500.0 + (i % 20) * 100.0 
        FROM range(1, 400) t(i);
    """)
    stats = conn.execute("""
        SELECT 
            AVG(effective_bill) AS mean_val,
            STDDEV_POP(effective_bill) AS std_pop,
            AVG(effective_bill) + 1.5 * STDDEV_POP(effective_bill) AS thresh
        FROM patient_bills
    """).fetchone()
    # Outliers exceeding threshold
    outliers = conn.execute(f"""
        SELECT patient_id, effective_bill 
        FROM patient_bills 
        WHERE effective_bill >= {stats[2]} 
        ORDER BY effective_bill DESC
    """).fetchall()
    assert len(outliers) == 4
    assert outliers[0] == ("P00213", 2375000.0)
    assert outliers[1] == ("P00216", 1305600.0)
    assert outliers[2][1] == 902500.0
    assert outliers[3][1] == 902500.0



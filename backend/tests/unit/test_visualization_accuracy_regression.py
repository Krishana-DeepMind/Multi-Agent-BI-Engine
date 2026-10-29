import pytest
import re
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler, _infer_unit, _infer_semantic_role
from backend.agents.layout_node import (
    _format_metric_value,
    generate_data_grounded_insight,
    build_fallback_config,
    sanitize_echarts_option,
    correct_chart_type,
)


@pytest.fixture(scope="module")
def engine():
    eng = DuckDBEngine()
    yield eng
    eng.close()


# ── Scenario 1: Currency and Large Numeric Values ─────────────────────────
def test_scenario_1_currency_and_large_numbers():
    # 1. Unit inference
    unit = _infer_unit("annual_revenue", ["$1250000", "$980000"], "currency")
    assert unit == "$"

    # 2. Metric formatting
    formatted_b = _format_metric_value("total_revenue", 2_500_000_000)
    assert formatted_b == "$2.5B"

    formatted_m = _format_metric_value("company_payroll", 18_400_000)
    assert formatted_m == "$18.4M"

    formatted_k = _format_metric_value("base_salary", 85_000)
    assert formatted_k == "$85.0K"

    # 3. Not accidentally formatted as percentage
    assert "%" not in formatted_k
    assert "%" not in formatted_m

    # 4. Data-grounded ranking insight
    q_def = {"title": "Department Revenue", "x_axis": "dept", "y_axis": "revenue"}
    q_res = {
        "column_names": ["dept", "revenue"],
        "sample_rows": [
            {"dept": "Engineering", "revenue": 1250000},
            {"dept": "Sales", "revenue": 950000},
        ],
        "row_count": 2,
    }
    insight = generate_data_grounded_insight(q_def, q_res, "bar")
    assert "Engineering ranks highest with $1.3M" in insight or "Engineering ranks highest with $1.2M" in insight
    assert "%" not in insight


# ── Scenario 2: Percentages and Proportions ────────────────────────────────
def test_scenario_2_percentages_and_proportions():
    # 1. Genuine percentage gets '%'
    formatted_pct = _format_metric_value("retention_pct", 84.6)
    assert formatted_pct == "84.6%"

    formatted_pct_name = _format_metric_value("discount_percentage", 15.0)
    assert formatted_pct_name == "15.0%"

    # 2. Non-percentage rates (heart rate, error count rate) MUST NOT be percentage
    formatted_hr = _format_metric_value("heart_rate_bpm", 72.0)
    assert "%" not in formatted_hr
    assert "72 bpm" in formatted_hr

    formatted_conc = _format_metric_value("gas_concentration_ppm", 412.5)
    assert "%" not in formatted_conc
    assert "412.5 ppm" in formatted_conc

    # 3. Percentage change vs Percentage points in chronological insights
    q_pct_trend = {"title": "Quarterly Gross Margin Trend", "x_axis": "quarter", "y_axis": "margin_pct"}
    q_res_pct = {
        "column_names": ["quarter", "margin_pct"],
        "sample_rows": [
            {"quarter": "Q1", "margin_pct": 20.0},
            {"quarter": "Q2", "margin_pct": 25.0},
        ],
        "row_count": 2,
    }
    insight_pct = generate_data_grounded_insight(q_pct_trend, q_res_pct, "line")
    assert "percentage points" in insight_pct
    assert "5.0 percentage points" in insight_pct

    # 4. Standard metric percentage change
    q_num_trend = {"title": "Monthly Units Sold", "x_axis": "month", "y_axis": "units_sold"}
    q_res_num = {
        "column_names": ["month", "units_sold"],
        "sample_rows": [
            {"month": "Jan", "units_sold": 100},
            {"month": "Feb", "units_sold": 150},
        ],
        "row_count": 2,
    }
    insight_num = generate_data_grounded_insight(q_num_trend, q_res_num, "line")
    assert "50.0%" in insight_num
    assert "percentage points" not in insight_num


# ── Scenario 3: Physical Measurements with Units ───────────────────────────
def test_scenario_3_physical_measurements_with_units():
    units_to_test = [
        ("co2_concentration_ppm", 478.3, "ppm"),
        ("barometric_pressure_hpa", 1013.25, "hPa"),
        ("ambient_temp_c", 22.8, "°C"),
        ("payload_weight_kg", 540.0, "kg"),
        ("latency_ms", 45.2, "ms"),
        ("frequency_hz", 60.0, "Hz"),
        ("power_mw", 1200.0, "MW"),
        ("noise_level_db", 68.4, "dB"),
    ]

    for col_name, val, expected_unit in units_to_test:
        unit = _infer_unit(col_name, [str(val)], "metric")
        assert unit == expected_unit, f"Failed for {col_name}: got {unit}, expected {expected_unit}"

        formatted = _format_metric_value(col_name, val)
        assert expected_unit in formatted, f"Unit {expected_unit} missing in {formatted}"
        assert "%" not in formatted, f"False percentage in {formatted}"


# ── Scenario 4: Dates and Chronological Series ────────────────────────────
def test_scenario_4_dates_and_chronological_series():
    q_def = {"title": "Telemetry Over Time", "x_axis": "recorded_at", "y_axis": "co2_ppm"}
    q_res = {
        "column_names": ["recorded_at", "co2_ppm"],
        "sample_rows": [
            {"recorded_at": "2024-01-01", "co2_ppm": 410.0},
            {"recorded_at": "2024-02-01", "co2_ppm": 435.0},
            {"recorded_at": "2024-03-01", "co2_ppm": 420.0},
        ],
        "row_count": 3,
    }
    opt = build_fallback_config(q_def, q_res, "line")
    sanitized = sanitize_echarts_option(opt, q_res["sample_rows"], "recorded_at")

    # 1. Straight lines enforced (no spline distortion)
    for s in sanitized.get("series", []):
        assert s.get("smooth") is False

    # 2. Chronological categories preserved
    assert sanitized["xAxis"]["data"] == ["2024-01-01", "2024-02-01", "2024-03-01"]


# ── Scenario 5: Categorical Labels and Identifiers ─────────────────────────
def test_scenario_5_categorical_labels_preserved():
    codes = ["ST-EAST", "ST-NORTH", "ST-WEST", "ST-CENTRAL", "ST-SOUTH", "Dept-A&B", "P#101"]
    q_def = {"title": "Station Emissions", "x_axis": "station_code", "y_axis": "co2_ppm"}
    q_res = {
        "column_names": ["station_code", "co2_ppm"],
        "sample_rows": [{"station_code": c, "co2_ppm": 400 + i * 10} for i, c in enumerate(codes)],
        "row_count": len(codes),
    }

    opt = build_fallback_config(q_def, q_res, "bar")
    assert opt["xAxis"]["data"] == codes

    # Ensure none of the placeholder names appear
    for label in opt["xAxis"]["data"]:
        assert not re.match(r"^Item\s+\d+$", label)
        assert not re.match(r"^Category\s+[A-Z]$", label)

    # Grounded insight accurately preserves category names and punctuation
    insight = generate_data_grounded_insight(q_def, q_res, "bar")
    assert "P#101" in insight or "ST-EAST" in insight


# ── Scenario 6: Missing Values, Unknown Units, Empty Results ───────────────
def test_scenario_6_missing_values_and_empty_results():
    # 1. Empty query result returns factual message
    q_def = {"title": "Outlier Spikes", "business_question": "Which readings are anomalies?"}
    q_res_empty = {"column_names": ["sensor_id", "val"], "sample_rows": [], "row_count": 0}
    insight = generate_data_grounded_insight(q_def, q_res_empty, "bar")
    assert "0 matching records" in insight
    assert "No qualifying statistical anomalies detected" in insight

    # 2. Unknown units display raw number formatted cleanly without inventing units
    formatted_unknown = _format_metric_value("arbitrary_score", 78.46)
    assert formatted_unknown == "78.5"
    assert not any(sym in formatted_unknown for sym in ("$", "%", "ppm", "kg", "m"))


# ── Scenario 7: Mixed Data Types and Unseen Schemas ───────────────────────
def test_scenario_7_mixed_unseen_schemas(engine):
    sql = """
    CREATE OR REPLACE TABLE unseen_fleet AS
    SELECT 
        'TRUCK-00' || i AS vehicle_id,
        'Active' AS fleet_status,
        20240101 + i AS event_date,
        1500.5 + i * 100 AS fuel_liters,
        0.05 * i AS slip_rate_pct,
        95.0 + i AS tire_pressure_psi
    FROM range(1, 10) t(i)
    """
    engine.conn.execute(sql)
    profiler = SchemaProfiler(engine)
    meta = profiler.profile_table("unseen_fleet")

    col_map = {m.name: m for m in meta}

    assert col_map["vehicle_id"].semantic_type == "identifier"
    assert col_map["fleet_status"].semantic_type == "dimension"
    assert col_map["fuel_liters"].semantic_type == "metric"
    assert col_map["slip_rate_pct"].semantic_type == "percentage"
    assert col_map["slip_rate_pct"].unit == "%"
    assert col_map["tire_pressure_psi"].unit == "psi"

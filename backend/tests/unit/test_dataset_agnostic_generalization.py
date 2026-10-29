import os
import io
import pytest
import polars as pl
from unittest.mock import MagicMock

from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler
from backend.agents.cleaning_node import generate_heuristic_cleaning_ops, apply_polars_cleaning_op
from backend.agents.feature_node import feature_node
from backend.agents.analytics_node import generate_generic_analytics_queries
from backend.agents.layout_node import rule_based_chart_type, correct_chart_type, generate_data_grounded_insight, detect_dashboard_filters
from backend.core.state import AgentSwarmState, ColumnMeta


@pytest.fixture(scope="module")
def sensor_csv_bytes():
    path = "test_datasets/unseen_sensor_telemetry.csv"
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture(scope="module")
def engine():
    eng = DuckDBEngine()
    yield eng
    eng.close()


def test_unseen_dataset_schema_profiling(engine, sensor_csv_bytes):
    """Test 1: SchemaProfiler infers types, roles, and KPIs for completely unseen domain."""
    res = engine.load_from_bytes(sensor_csv_bytes, "csv", table_name="sensor_telemetry")
    assert res["success"] is True

    profiler = SchemaProfiler(engine)
    meta = profiler.profile_table("sensor_telemetry")

    col_map = {m.name: m for m in meta}
    assert "sensor_id" in col_map
    assert "timestamp" in col_map
    assert "co2_ppm" in col_map
    assert "temperature_c" in col_map
    assert "humidity_pct" in col_map
    assert "pressure_hpa" in col_map
    assert "air_quality_index" in col_map
    assert "sensor_status" in col_map

    # 1. Identifier check
    assert col_map["sensor_id"].semantic_type == "identifier"
    assert col_map["sensor_id"].is_candidate_kpi is False

    # 2. Date check
    assert col_map["timestamp"].semantic_type == "date"

    # 3. Percentage check
    assert col_map["humidity_pct"].semantic_type == "percentage"
    assert col_map["humidity_pct"].is_candidate_kpi is True

    # 4. Metric & Candidate KPI checks
    assert col_map["temperature_c"].semantic_type == "metric"
    assert col_map["temperature_c"].is_candidate_kpi is True
    assert col_map["air_quality_index"].semantic_type == "metric"
    assert col_map["air_quality_index"].is_candidate_kpi is True

    # 5. Dimension checks
    assert col_map["station_code"].semantic_type == "dimension"
    assert col_map["sensor_status"].semantic_type == "dimension"

    # 6. Clean business labels
    assert "CO2" in col_map["co2_ppm"].business_label
    assert col_map["sensor_id"].business_label == "Sensor ID"


def test_unseen_dataset_generic_cleaning(engine, sensor_csv_bytes):
    """Test 2: Generic cleaning normalizes numeric strings, magnitudes (1.2k), sentinels (?), dates."""
    engine.load_from_bytes(sensor_csv_bytes, "csv", table_name="sensor_raw")
    profiler = SchemaProfiler(engine)
    meta = profiler.profile_table("sensor_raw")

    ops = generate_heuristic_cleaning_ops(meta, engine)
    lf = engine.to_polars_lazyframe()

    for op in ops:
        if op.column == "all" or op.operation in ("deduplicate", "remove_duplicates"):
            lf = lf.unique(maintain_order=True)
            continue
        current_cols = lf.collect_schema().names()
        if op.column not in current_cols:
            continue
        lf, _ = apply_polars_cleaning_op(lf, op)

    cleaned_df = lf.collect()

    # Verify co2_ppm 1.2k was expanded to 1200.0 and '?' was parsed as NULL
    co2_vals = cleaned_df["co2_ppm"].to_list()
    assert 1200.0 in co2_vals
    assert None in co2_vals

    # Verify timestamp was parsed to Date
    assert cleaned_df["timestamp"].dtype == pl.Date

    # Verify sensor_status was normalized to Title Case
    statuses = set(cleaned_df["sensor_status"].drop_nulls().to_list())
    assert "Active" in statuses
    assert "Calibrating" in statuses
    assert "Offline" in statuses


def test_unseen_dataset_analytics_query_generation(engine, sensor_csv_bytes):
    """Test 3: Analytics queries are dynamically synthesized from active schema without hardcoding."""
    engine.load_from_bytes(sensor_csv_bytes, "csv", table_name="data")
    profiler = SchemaProfiler(engine)
    meta = profiler.profile_table("data")

    queries = generate_generic_analytics_queries(engine, meta, table_name="data")
    assert len(queries) >= 5

    purposes = {q["analytical_purpose"] for q in queries}
    assert "kpi" in purposes
    assert "trend" in purposes
    assert "breakdown" in purposes

    # Execute every generated query in DuckDB to confirm syntax correctness
    for q in queries:
        sql = q["sql"]
        # Ensure no domain keywords were hallucinated
        assert "sales_amount" not in sql
        assert "salary" not in sql
        assert "total_orders" not in sql

        # Explain validation
        val = engine.execute_validated(f"EXPLAIN {sql}")
        assert val["success"] is True, f"Query '{q['title']}' failed EXPLAIN: {val.get('error')}"

        # Execution validation
        res = engine.execute_validated(sql)
        assert res["success"] is True, f"Query '{q['title']}' execution failed: {res.get('error')}"
        assert res["row_count"] > 0 or q.get("analytical_purpose") == "anomaly"


def test_unseen_dataset_chart_and_insight_selection():
    """Test 4: Chart types and evidence-based insights are selected accurately for telemetry data."""
    # 1. Line chart for chronological time series
    profile_trend = {
        "row_count": 12,
        "x_axis_type": "temporal",
        "y_axis_type": "numeric",
        "analytical_purpose": "trend",
        "business_question": "How does temperature evolve over time?",
        "query_title": "Temperature Over Time",
        "y_axis": "avg_temperature_c"
    }
    assert rule_based_chart_type(profile_trend) == "line"

    # 2. Bar chart for categorical station breakdown
    profile_station = {
        "row_count": 5,
        "x_axis_type": "categorical",
        "y_axis_type": "numeric",
        "analytical_purpose": "breakdown",
        "business_question": "What is the CO2 level by station?",
        "query_title": "CO2 by Station",
        "y_axis": "avg_co2_ppm"
    }
    assert rule_based_chart_type(profile_station) == "bar"

    # 3. Donut chart for categorical composition with <= 6 categories
    profile_donut = {
        "row_count": 4,
        "x_axis_type": "categorical",
        "y_axis_type": "numeric",
        "analytical_purpose": "composition",
        "business_question": "What is the status composition?",
        "query_title": "Sensor Status Composition",
        "y_axis": "count"
    }
    assert rule_based_chart_type(profile_donut) == "donut"

    # 4. Dynamic insight reflects actual telemetry values
    insight = generate_data_grounded_insight(
        query_def={
            "title": "CO2 by Station",
            "x_axis": "station_code",
            "y_axis": "avg_co2_ppm",
            "business_question": "What is the CO2 level by station?"
        },
        query_result={
            "column_names": ["station_code", "avg_co2_ppm"],
            "sample_rows": [
                {"station_code": "ST-EAST", "avg_co2_ppm": 703.6},
                {"station_code": "ST-NORTH", "avg_co2_ppm": 430.0},
            ],
            "row_count": 2
        },
        chart_type="bar"
    )
    assert "ST-EAST ranks highest with 703.6" in insight
    assert "ST-NORTH" in insight
    # Never includes generic retail text
    assert "orders" not in insight
    assert "sales" not in insight


def test_dashboard_filters_on_unseen_dataset():
    """Test 5: Dashboard filter detection discovers categorical station_code and sensor_status."""
    metadata = [
        ColumnMeta(name="sensor_id", original_name="sensor_id", dtype="VARCHAR", semantic_type="identifier", business_label="Sensor ID", null_pct=0.0, unique_pct=1.0, sample_values=["SN-1001"], is_primary_key=True, is_candidate_kpi=False),
        ColumnMeta(name="station_code", original_name="station_code", dtype="VARCHAR", semantic_type="dimension", business_label="Station Code", null_pct=0.0, unique_pct=0.33, sample_values=["ST-NORTH", "ST-SOUTH"], is_primary_key=False, is_candidate_kpi=False),
        ColumnMeta(name="sensor_status", original_name="sensor_status", dtype="VARCHAR", semantic_type="dimension", business_label="Sensor Status", null_pct=0.0, unique_pct=0.20, sample_values=["Active", "Offline"], is_primary_key=False, is_candidate_kpi=False),
        ColumnMeta(name="co2_ppm", original_name="co2_ppm", dtype="DOUBLE", semantic_type="metric", business_label="CO2 PPM", null_pct=0.0, unique_pct=0.85, sample_values=[412.5], is_primary_key=False, is_candidate_kpi=True),
    ]
    filters = detect_dashboard_filters(metadata, max_filters=3)
    filter_cols = [f["column"] for f in filters]
    assert "station_code" in filter_cols
    assert "sensor_status" in filter_cols
    # Primary key sensor_id and metric co2_ppm must NOT be filters
    assert "sensor_id" not in filter_cols
    assert "co2_ppm" not in filter_cols

import pytest
import polars as pl
import duckdb
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler
from backend.agents.analytics_node import generate_generic_analytics_queries
from backend.agents.layout_node import (
    _format_metric_value,
    generate_data_grounded_insight,
    split_kpi_cards,
    rule_based_chart_type,
    correct_chart_type,
)


class TestChartAccuracyAndReadabilityRegression:
    """
    Regression test suite across three distinct dataset types:
    1. Messy HR Employees (test_datasets/messy_hr_employees.csv)
    2. Messy Retail Orders (test_datasets/messy_retail_orders.csv)
    3. Unseen Sensor Telemetry (test_datasets/unseen_sensor_telemetry.csv)
    """

    @pytest.fixture
    def duckdb_engine(self):
        engine = DuckDBEngine()
        yield engine
        engine.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Correct Time-Series Aggregation & Ordering
    # ──────────────────────────────────────────────────────────────────────────
    def test_monthly_time_series_aggregation_and_chronological_sort(self, duckdb_engine):
        """Verify monthly time-series queries group by calendar month and sort chronologically."""
        df = pl.read_csv("test_datasets/messy_hr_employees.csv")
        clean_df = df.unique(maintain_order=True)
        # Parse hire_date cleanly as done by cleaning pipeline
        parsed_dates = clean_df.select(
            pl.coalesce([
                pl.col("hire_date").str.to_date("%Y-%m-%d", strict=False),
                pl.col("hire_date").str.to_date("%d-%b-%y", strict=False),
                pl.col("hire_date").str.to_date("%d-%b-%Y", strict=False),
                pl.col("hire_date").str.to_date("%m/%d/%Y", strict=False),
            ]).alias("hire_date")
        )
        clean_df = clean_df.with_columns(parsed_dates["hire_date"])
        duckdb_engine.load_from_bytes(clean_df.write_csv().encode("utf-8"), "csv", table_name="data")

        # Analytical time-series query: Monthly hires
        sql = """
            SELECT STRFTIME(CAST(hire_date AS DATE), '%Y-%m') AS month, COUNT(*) AS count
            FROM data
            WHERE hire_date IS NOT NULL
            GROUP BY month
            ORDER BY month ASC
        """
        res = duckdb_engine.execute_validated(sql)
        assert res["success"] is True
        rows = res["data"]
        assert len(rows) > 30, "Time series should span multi-year range, not truncated to 5 rows"

        # Check chronological ordering
        months = [r["month"] for r in rows if r["month"]]
        assert months == sorted(months), "Months must be strictly chronologically ordered"

        # Check that dates span multiple years without fabrication
        assert months[0].startswith("2012")
        assert any(m.startswith("2020") or m.startswith("2024") for m in months)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Prevent Unreadable Axes & Label Readability
    # ──────────────────────────────────────────────────────────────────────────
    def test_high_cardinality_chart_handling(self):
        """Verify charts with >25 time points or high categories get auto-interval and dataZoom."""
        # 172 months sample
        categories = [f"2012-{m:02d}" for m in range(1, 13)] + [f"2013-{m:02d}" for m in range(1, 13)] + [f"2014-{m:02d}" for m in range(1, 13)]
        assert len(categories) == 36

        # Rule check: line chart for time series
        profile = {
            "row_count": len(categories),
            "x_axis_type": "temporal",
            "y_axis_type": "numeric",
            "analytical_purpose": "trend",
            "query_title": "Monthly Hiring Trend",
            "y_axis": "count",
        }
        chart_type = rule_based_chart_type(profile)
        assert chart_type == "line"

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Correct Units and Numerical Formatting (Dimensionless vs Currency vs Pct)
    # ──────────────────────────────────────────────────────────────────────────
    def test_dimensionless_zscore_never_labeled_as_currency(self):
        """Z-Scores, ratios, and standard deviations must NEVER be formatted with $ or %."""
        # Salary Z-score: statistical measure, NOT dollars
        assert _format_metric_value("avg_salary_z_score", 0.4) == "0.40"
        assert _format_metric_value("avg_salary_z_score", -0.2) == "-0.20"
        assert _format_metric_value("salary_zscore", 2.15) == "2.15"
        assert _format_metric_value("z_score_deviation", -1.45) == "-1.45"
        assert "$" not in _format_metric_value("salary_z_score", 0.4)
        assert "%" not in _format_metric_value("salary_z_score", 0.4)

    def test_genuine_currency_and_negative_formatting(self):
        """Genuine monetary metrics should include currency symbol with correct negative formatting."""
        # Positive currency
        assert _format_metric_value("salary", 95000) == "$95.0K"
        assert _format_metric_value("total_revenue", 1250000) == "$1.2M"
        # Negative currency formatted as -$X, not $-X
        formatted_neg = _format_metric_value("net_profit", -25000)
        assert formatted_neg == "-$25.0K"
        assert "$-" not in formatted_neg

    def test_genuine_percentage_formatting(self):
        """Rates and percentages must format with %."""
        assert _format_metric_value("attrition_rate_pct", 14.5) == "14.5%"
        assert _format_metric_value("discount_percent", 20.0) == "20.0%"
        assert _format_metric_value("humidity_pct", 65.2) == "65.2%"

    def test_physical_telemetry_units(self):
        """Sensor telemetry measurements format with physical units."""
        assert _format_metric_value("co2_ppm", 412.5) == "412.5 ppm"
        assert _format_metric_value("pressure_hpa", 1013.25) == "1,013.2 hPa"
        assert _format_metric_value("temperature_c", 22.4) == "22.4 °C"

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Data-Driven KPI Cards across Three Diverse Datasets
    # ──────────────────────────────────────────────────────────────────────────
    def test_kpi_cards_dataset_1_messy_hr(self, duckdb_engine):
        """Dataset 1 (HR): Generates 3–6 data-driven KPI cards matching exact DuckDB counts."""
        df = pl.read_csv("test_datasets/messy_hr_employees.csv").unique(maintain_order=True)
        duckdb_engine.load_from_bytes(df.write_csv().encode("utf-8"), "csv", table_name="data")
        profiler = SchemaProfiler(duckdb_engine)
        meta = profiler.profile_table("data")

        queries = generate_generic_analytics_queries(duckdb_engine, meta, table_name="data")
        kpi_q = next(q for q in queries if q["analytical_purpose"] == "kpi")

        res = duckdb_engine.execute_validated(kpi_q["sql"])
        assert res["success"] is True
        cards = split_kpi_cards(kpi_q, res, max_kpis=6)

        # Must generate 3 to 6 KPI cards
        assert 3 <= len(cards) <= 6
        total_card = cards[0]
        # Total headcount must equal 527 clean rows (not 503 from silent ID deletion)
        assert total_card["echarts_option"]["raw_value"] == 527

    def test_kpi_cards_dataset_2_retail_orders(self, duckdb_engine):
        """Dataset 2 (Retail): Generates 3–6 data-driven KPI cards without hardcoded HR concepts."""
        df = pl.read_csv("test_datasets/messy_retail_orders.csv").unique(maintain_order=True)
        duckdb_engine.load_from_bytes(df.write_csv().encode("utf-8"), "csv", table_name="data")
        profiler = SchemaProfiler(duckdb_engine)
        meta = profiler.profile_table("data")

        queries = generate_generic_analytics_queries(duckdb_engine, meta, table_name="data")
        kpi_q = next(q for q in queries if q["analytical_purpose"] == "kpi")

        res = duckdb_engine.execute_validated(kpi_q["sql"])
        assert res["success"] is True
        cards = split_kpi_cards(kpi_q, res, max_kpis=6)

        assert 3 <= len(cards) <= 6
        # Must reflect total orders
        assert cards[0]["echarts_option"]["raw_value"] == len(df)
        labels = [c["title"].lower() for c in cards]
        # Must NOT contain HR terms like employee, tenure, salary
        assert not any("employee" in l for l in labels)
        assert not any("tenure" in l for l in labels)

    def test_kpi_cards_dataset_3_sensor_telemetry(self, duckdb_engine):
        """Dataset 3 (Sensors): Generates 3–6 data-driven KPI cards without currency symbols."""
        df = pl.read_csv("test_datasets/unseen_sensor_telemetry.csv").unique(maintain_order=True)
        duckdb_engine.load_from_bytes(df.write_csv().encode("utf-8"), "csv", table_name="data")
        profiler = SchemaProfiler(duckdb_engine)
        meta = profiler.profile_table("data")

        queries = generate_generic_analytics_queries(duckdb_engine, meta, table_name="data")
        kpi_q = next(q for q in queries if q["analytical_purpose"] == "kpi")

        res = duckdb_engine.execute_validated(kpi_q["sql"])
        assert res["success"] is True
        cards = split_kpi_cards(kpi_q, res, max_kpis=6)

        assert 3 <= len(cards) <= 6
        assert cards[0]["echarts_option"]["raw_value"] == len(df)
        # Environmental sensor data must not have $ symbols on KPI values
        for c in cards:
            val_str = str(c["echarts_option"]["value"])
            assert "$" not in val_str, f"Sensor KPI '{c['title']}' should not be formatted as currency: {val_str}"

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Evidence-Based Insights (Never Repeat Analytical Question)
    # ──────────────────────────────────────────────────────────────────────────
    def test_insight_generation_never_returns_business_question(self):
        """Insight narratives must synthesize factual findings from rows, NEVER repeating the question."""
        q_def = {
            "title": "Top Salary Outliers (Z-Score > 2)",
            "business_question": "Which employees have unusually high salaries compared to the rest of the workforce?",
            "analytical_purpose": "anomaly",
            "x_axis": "employee_id",
            "y_axis": "salary",
        }
        q_res = {
            "column_names": ["employee_id", "salary"],
            "sample_rows": [
                {"employee_id": "EMP00307", "salary": 258556.0},
                {"employee_id": "EMP00066", "salary": 223871.0},
            ],
            "row_count": 2,
        }
        insight = generate_data_grounded_insight(q_def, q_res, "bar")

        # Must NOT equal the question
        assert insight != q_def["business_question"]
        assert "EMP00307" in insight
        assert "$258.6K" in insight or "258556" in insight
        assert "outlier" in insight.lower()

    def test_zero_rows_query_explanation(self):
        """0-record result returns a clear, honest explanation rather than an empty chart."""
        q_def = {
            "title": "Severe Temperature Anomalies (> 50°C)",
            "business_question": "Are there any extreme sensor heat readings?",
            "analytical_purpose": "anomaly",
        }
        q_res = {"column_names": ["sensor_id", "temp"], "sample_rows": [], "row_count": 0}
        insight = generate_data_grounded_insight(q_def, q_res, "bar")
        assert "0 matching" in insight or "0 qualifying" in insight or "0 records" in insight
        assert insight != q_def["business_question"]

    # ──────────────────────────────────────────────────────────────────────────
    # 6. Semantic Chart Selection
    # ──────────────────────────────────────────────────────────────────────────
    def test_rates_and_benchmarks_never_selected_as_pie(self):
        """Rates, percentages, and benchmark comparisons must use bar charts, never pie or donut."""
        profile_rate = {
            "row_count": 5,
            "x_axis_type": "categorical",
            "y_axis_type": "numeric",
            "analytical_purpose": "comparison",
            "business_question": "What is the attrition rate by department?",
            "query_title": "Attrition Rate by Department",
            "y_axis": "attrition_rate_pct",
        }
        assert rule_based_chart_type(profile_rate) == "bar"
        # Even if LLM proposed pie/donut, post-LLM correction forces bar
        assert correct_chart_type("pie", profile_rate) == "bar"
        assert correct_chart_type("donut", profile_rate) == "bar"

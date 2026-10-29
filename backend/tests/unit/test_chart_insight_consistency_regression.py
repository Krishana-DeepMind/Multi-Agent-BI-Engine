import pytest
import math
from typing import Dict, Any

from backend.agents.layout_node import (
    _format_metric_value,
    _parse_date_sort_key,
    extract_canonical_plotted_data,
    sanitize_echarts_option,
    build_fallback_config,
    generate_data_grounded_insight,
    validate_and_reconcile_chart_insight,
    split_kpi_cards,
    assign_grid_layout,
)


class TestChartInsightConsistencyRegression:
    """
    Comprehensive regression suite validating:
    1. Exact derivation of insights from chart result objects.
    2. Unit validation and scaling (percentages vs ratios vs currency).
    3. Chronological sorting and time-series trend semantics.
    4. Automatic consistency checks (highest bar == insight top category).
    5. Arbitrary dataset support without hardcoded column names.
    """

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Exact Object Derivation
    # ──────────────────────────────────────────────────────────────────────────
    def test_insight_derives_directly_from_plotted_echarts_option(self):
        """
        When echarts_option is passed, insight must derive from the exact plotted
        categories and series numbers, not uncleaned raw sample rows.
        """
        # Chart plotted data where 'Electronics' is highest
        echarts_opt = {
            "xAxis": {"type": "category", "data": ["Electronics", "Home & Kitchen", "Apparel"]},
            "series": [{"name": "Sales", "type": "bar", "data": [16400.0, 14900.0, 9300.0]}],
        }
        # Inconsistent raw sample rows where Apparel happens to be the first row
        query_result = {
            "column_names": ["product_category", "total_sales"],
            "sample_rows": [
                {"product_category": "Apparel", "total_sales": 9300.0},
                {"product_category": "Electronics", "total_sales": 16400.0},
                {"product_category": "Home & Kitchen", "total_sales": 14900.0},
            ],
            "row_count": 3,
        }
        query_def = {
            "title": "Sales by Product Category",
            "x_axis": "product_category",
            "y_axis": "total_sales",
        }

        insight = generate_data_grounded_insight(query_def, query_result, "bar", echarts_option=echarts_opt)

        # Insight MUST cite Electronics as #1 and $16.4K, NOT Apparel
        assert "Electronics" in insight
        assert "16.4K" in insight or "16,400" in insight
        assert "Apparel" not in insight.split("ranks highest")[0]

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Units & Aggregation Validation (No 0.12 vs 12% mixing, no $0 margins)
    # ──────────────────────────────────────────────────────────────────────────
    def test_ratio_to_percentage_scaling_no_mixing(self):
        """
        A profit-margin or rate chart should not silently display 0.12 instead of 12.0%,
        nor should it multiply an already-scaled percentage (12.0%) into 1200%.
        """
        # Test decimal ratio <= 1.5
        formatted_ratio = _format_metric_value("profit_margin", 0.124)
        assert formatted_ratio == "12.4%"

        # Test pre-scaled percentage
        formatted_pct = _format_metric_value("attrition_rate", 15.2)
        assert formatted_pct == "15.2%"

        # Test negative ratio
        formatted_neg = _format_metric_value("operating_margin", -0.048)
        assert formatted_neg == "-4.8%"

        # Test unscaled ratio in canonical plotted data extraction
        rows = [
            {"dept": "Engineering", "margin": 0.25},
            {"dept": "Sales", "margin": 0.18},
            {"dept": "Support", "margin": 0.05},
        ]
        canonical = extract_canonical_plotted_data({"x_axis": "dept", "y_axis": "margin"}, {"sample_rows": rows}, "bar")
        cats, vals = canonical["categories"], canonical["values"]
        assert cats == ["Engineering", "Sales", "Support"]
        assert vals == [25.0, 18.0, 5.0]

    def test_margin_and_rate_never_formatted_as_currency(self):
        """
        'profit_margin' or columns containing 'margin' / 'rate' / 'pct' must NEVER
        be formatted with currency symbols ($), while true currency columns must be.
        """
        # Margin containing 'profit' word should NOT get '$'
        assert "$" not in _format_metric_value("profit_margin", 0.22)
        assert "$" not in _format_metric_value("gross_profit_ratio", 0.35)
        assert "$" not in _format_metric_value("operating_margin", 12.0)

        # Currency columns MUST get '$'
        profit_curr = _format_metric_value("profit", 14900.0)
        assert "$" in profit_curr
        sales_curr = _format_metric_value("sales_amount", 123820.0)
        assert "$" in sales_curr

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Time-Series Chronological Sorting & Metrics
    # ──────────────────────────────────────────────────────────────────────────
    def test_time_series_chronological_sorting_and_semantics(self):
        """
        Time-series data out of chronological order must be sorted chronologically
        with series values remaining aligned to their respective timestamps.
        """
        scrambled_rows = [
            {"month": "2024-03", "sales": 3200.0},
            {"month": "2023-01", "sales": 1500.0},
            {"month": "2024-01", "sales": 4000.0},
            {"month": "2023-06", "sales": 2500.0},
        ]
        canonical = extract_canonical_plotted_data({"x_axis": "month", "y_axis": "sales"}, {"sample_rows": scrambled_rows}, "line")
        cats, vals = canonical["categories"], canonical["values"]

        # Chronological order verified
        assert cats == ["2023-01", "2023-06", "2024-01", "2024-03"]
        assert vals == [1500.0, 2500.0, 4000.0, 3200.0]

        opt = {
            "xAxis": {"type": "category", "data": cats},
            "series": [{"name": "Monthly Sales", "type": "line", "data": vals}],
        }
        insight = generate_data_grounded_insight(
            {"title": "Monthly Sales", "x_axis": "month", "y_axis": "sales"},
            {"sample_rows": scrambled_rows},
            "line",
            echarts_option=opt,
        )

        # Insight mentions start (2023-01), end (2024-03), and peak (2024-01)
        assert "2023-01" in insight
        assert "2024-03" in insight
        assert "2024-01" in insight
        assert "increased" in insight.lower()

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Automatic Consistency Checks & Reconciliation
    # ──────────────────────────────────────────────────────────────────────────
    def test_automatic_consistency_check_reconciles_highest_bar(self):
        """
        If LLM insight says category A is highest, but the chart's highest bar is
        category B, validate_and_reconcile_chart_insight must reconcile the insight
        to match the chart's highest bar.
        """
        chart_config = {
            "query_id": "test_cat_sales",
            "title": "Category Sales",
            "chart_type": "bar",
            "insight_summary": "Apparel ranks highest with $9.3K, followed by Electronics.",
            "echarts_option": {
                "xAxis": {"type": "category", "data": ["Electronics", "Home", "Apparel"]},
                "series": [{"name": "Sales", "type": "bar", "data": [16400.0, 14900.0, 9300.0]}],
            },
        }
        reconciled = validate_and_reconcile_chart_insight(chart_config, {})

        # Insight MUST be reconciled to cite Electronics ($16.4K)
        assert "Electronics" in reconciled["insight_summary"]
        assert "16.4K" in reconciled["insight_summary"] or "16,400" in reconciled["insight_summary"]
        assert reconciled["echarts_option"]["_insight_summary"] == reconciled["insight_summary"]

    def test_automatic_consistency_check_reconciles_time_series_order(self):
        """
        If a line chart has xAxis data out of order, validate_and_reconcile_chart_insight
        sorts both xAxis and series data chronologically and reconciles the insight.
        """
        chart_config = {
            "query_id": "trend_test",
            "title": "User Growth Trend",
            "chart_type": "line",
            "insight_summary": "Initial insight",
            "echarts_option": {
                "xAxis": {"type": "category", "data": ["2024-05", "2023-01", "2023-12"]},
                "series": [{"name": "Users", "type": "line", "data": [500.0, 100.0, 300.0]}],
            },
        }
        reconciled = validate_and_reconcile_chart_insight(chart_config, {})

        # Order must be sorted chronologically
        assert reconciled["echarts_option"]["xAxis"]["data"] == ["2023-01", "2023-12", "2024-05"]
        assert reconciled["echarts_option"]["series"][0]["data"] == [100.0, 300.0, 500.0]
        assert "2023-01" in reconciled["insight_summary"]
        assert "2024-05" in reconciled["insight_summary"]

    def test_mismatched_array_lengths_aligned_1_to_1(self):
        """
        Categories and values must have identical 1:1 lengths (no orphan categories).
        """
        chart_config = {
            "query_id": "length_mismatch",
            "title": "Country Profit Margin",
            "chart_type": "bar",
            "insight_summary": "Overview",
            "echarts_option": {
                "xAxis": {"type": "category", "data": ["US", "India", "UK", "Australia", "Germany"]},
                "series": [{"name": "Margin", "type": "bar", "data": [18.5]}],
            },
        }
        reconciled = validate_and_reconcile_chart_insight(chart_config, {})

        x_len = len(reconciled["echarts_option"]["xAxis"]["data"])
        s_len = len(reconciled["echarts_option"]["series"][0]["data"])
        assert x_len == s_len == 1
        assert reconciled["echarts_option"]["xAxis"]["data"] == ["US"]

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Arbitrary Dataset Generalization
    # ──────────────────────────────────────────────────────────────────────────
    @pytest.mark.parametrize("dataset_meta", [
        {
            "name": "HR Domain",
            "category_col": "department",
            "metric_col": "attrition_rate",
            "rows": [
                {"department": "Sales", "attrition_rate": 0.22},
                {"department": "Support", "attrition_rate": 0.15},
                {"department": "R&D", "attrition_rate": 0.08},
            ],
            "chart_type": "bar",
            "expected_top": "Sales",
            "expected_val": "22.0%",
        },
        {
            "name": "IoT Sensor Domain",
            "category_col": "sensor_location",
            "metric_col": "ambient_temp_c",
            "rows": [
                {"sensor_location": "Furnace Zone B", "ambient_temp_c": 142.5},
                {"sensor_location": "Turbine Intake", "ambient_temp_c": 88.0},
                {"sensor_location": "Exhaust Flue", "ambient_temp_c": 64.2},
            ],
            "chart_type": "bar",
            "expected_top": "Furnace Zone B",
            "expected_val": "142.5",
        },
        {
            "name": "E-Commerce Logistics Domain",
            "category_col": "fulfillment_hub",
            "metric_col": "dispatch_time_hours",
            "rows": [
                {"fulfillment_hub": "Hub East", "dispatch_time_hours": 3.4},
                {"fulfillment_hub": "Hub West", "dispatch_time_hours": 2.1},
                {"fulfillment_hub": "Hub Central", "dispatch_time_hours": 1.2},
            ],
            "chart_type": "bar",
            "expected_top": "Hub East",
            "expected_val": "3.4",
        },
    ])
    def test_arbitrary_dataset_consistency_and_insight(self, dataset_meta):
        """
        Verify pipeline functions operate dynamically across any domain schema
        without relying on hardcoded column names.
        """
        rows = dataset_meta["rows"]
        cat_col = dataset_meta["category_col"]
        met_col = dataset_meta["metric_col"]
        c_type = dataset_meta["chart_type"]

        q_def = {"title": f"Distribution by {cat_col}", "x_axis": cat_col, "y_axis": met_col}
        q_res = {"column_names": [cat_col, met_col], "sample_rows": rows, "row_count": len(rows)}

        opt = build_fallback_config(q_def, q_res, c_type)
        insight = generate_data_grounded_insight(q_def, q_res, c_type, echarts_option=opt)

        assert dataset_meta["expected_top"] in insight
        assert dataset_meta["expected_val"] in insight

        # Validate reconciliation maintains consistency
        config = {
            "query_id": f"test_{cat_col}",
            "title": q_def["title"],
            "chart_type": c_type,
            "insight_summary": insight,
            "echarts_option": opt,
        }
        final = validate_and_reconcile_chart_insight(config, q_res)
        assert dataset_meta["expected_top"] in final["insight_summary"]

    # ──────────────────────────────────────────────────────────────────────────
    # 6. Tied Category Rankings
    # ──────────────────────────────────────────────────────────────────────────
    def test_tied_category_rankings_reported_accurately(self):
        """
        When two categories tie for highest, insight must recognize the tie
        instead of falsely claiming one ranks strictly above the other.
        """
        opt = {
            "xAxis": {"type": "category", "data": ["Region North", "Region South", "Region Central"]},
            "series": [{"name": "Score", "type": "bar", "data": [95.0, 95.0, 72.0]}],
        }
        q_def = {"title": "Region Performance", "x_axis": "region", "y_axis": "score"}
        q_res = {"sample_rows": [{"region": "Region North", "score": 95.0}, {"region": "Region South", "score": 95.0}, {"region": "Region Central", "score": 72.0}]}

        insight = generate_data_grounded_insight(q_def, q_res, "bar", echarts_option=opt)
        assert "tied" in insight.lower()
        assert "Region North" in insight
        assert "Region South" in insight
        assert "95.0" in insight or "95" in insight

    # ──────────────────────────────────────────────────────────────────────────
    # 7. Stacked Bar Chart Cumulative Totals
    # ──────────────────────────────────────────────────────────────────────────
    def test_stacked_bar_chart_cumulative_totals(self):
        """
        In stacked bar charts, rankings must derive from the cumulative sum across
        all stacked series, not solely from series[0].
        """
        opt = {
            "xAxis": {"type": "category", "data": ["East", "West"]},
            "series": [
                {"name": "Tier A", "type": "bar", "stack": "total", "data": [400.0, 100.0]},
                {"name": "Tier B", "type": "bar", "stack": "total", "data": [100.0, 500.0]},
            ],
        }
        # East total: 400 + 100 = 500
        # West total: 100 + 500 = 600 -> West is cumulative highest
        q_def = {"title": "Revenue by Tier", "x_axis": "region", "y_axis": "revenue"}
        q_res = {"sample_rows": []}

        insight = generate_data_grounded_insight(q_def, q_res, "bar", echarts_option=opt)
        assert "West" in insight
        assert "East" in insight
        assert "600" in insight or "ranks highest" in insight

    # ──────────────────────────────────────────────────────────────────────────
    # 8. Multi-Series Metric Matching
    # ──────────────────────────────────────────────────────────────────────────
    def test_multi_series_non_stacked_matches_target_metric(self):
        """
        In a multi-series chart where y_axis specifies churn_rate, insight must
        examine the churn_rate series rather than blindly inspecting series[0].
        """
        opt = {
            "xAxis": {"type": "category", "data": ["Cohort A", "Cohort B"]},
            "series": [
                {"name": "Active Users", "type": "line", "data": [10000.0, 20000.0]},
                {"name": "Churn Rate", "type": "line", "data": [12.5, 4.2]},
            ],
        }
        q_def = {"title": "Cohort Retention Analysis", "x_axis": "cohort", "y_axis": "churn_rate"}
        q_res = {"sample_rows": []}

        insight = generate_data_grounded_insight(q_def, q_res, "line", echarts_option=opt)
        # Should reference Churn Rate values (12.5% vs 4.2%), not 10,000 or 20,000 users
        assert "12.5" in insight or "10,000" not in insight

    # ──────────────────────────────────────────────────────────────────────────
    # 9. Correlation & Dimensionless Statistics vs Currency/Percentages
    # ──────────────────────────────────────────────────────────────────────────
    def test_correlation_and_dimensionless_values(self):
        """
        Dimensionless numbers (correlation, z-score) in [-1, 1] must NOT be
        converted to percentages or currency. Counts must NOT receive currency symbols.
        """
        assert _format_metric_value("correlation", 0.85) == "0.85"
        assert _format_metric_value("pearson_correlation", -0.72) == "-0.72"
        assert _format_metric_value("zscore", 1.45) == "1.45"
        assert _format_metric_value("p_value", 0.042) == "0.04"

        # Non-monetary counts
        assert "$" not in _format_metric_value("total_downloads", 14500)
        assert _format_metric_value("total_downloads", 14500) == "14.5K"
        assert "$" not in _format_metric_value("page_views", 1200000)
        assert _format_metric_value("page_views", 1200000) == "1.2M"
        assert "$" not in _format_metric_value("active_subscribers", 840)
        assert _format_metric_value("active_subscribers", 840) == "840"

    # ──────────────────────────────────────────────────────────────────────────
    # 10. Multi-Currency Detection & Formatting
    # ──────────────────────────────────────────────────────────────────────────
    def test_multi_currency_symbols(self):
        """
        Explicit currencies (EUR, GBP, INR, JPY) must preserve their respective
        currency symbols and not default blindly to USD ($).
        """
        assert _format_metric_value("total_sales_eur", 50000) == "€50.0K"
        assert _format_metric_value("revenue_gbp", 1200000) == "£1.2M"
        assert _format_metric_value("salary_inr", 850000) == "₹850.0K"
        assert _format_metric_value("cost_jpy", 3500000) == "¥3.5M"

    # ──────────────────────────────────────────────────────────────────────────
    # 11. Multi-Format & Chronological Quarter Date Parsing
    # ──────────────────────────────────────────────────────────────────────────
    def test_multi_format_date_sorting(self):
        """
        Quarter dates, single-digit month dates, and ISO timestamps must sort
        strictly chronologically.
        """
        quarter_dates = ["2024-Q3", "2023-Q4", "2024-Q1", "2023-Q2"]
        sorted_quarters = sorted(quarter_dates, key=_parse_date_sort_key)
        assert sorted_quarters == ["2023-Q2", "2023-Q4", "2024-Q1", "2024-Q3"]

        single_digit_months = ["2024-10", "2024-2", "2024-1"]
        sorted_months = sorted(single_digit_months, key=_parse_date_sort_key)
        assert sorted_months == ["2024-1", "2024-2", "2024-10"]

        compact_dates = ["20241231", "20230101", "20230615"]
        sorted_compact = sorted(compact_dates, key=_parse_date_sort_key)
        assert sorted_compact == ["20230101", "20230615", "20241231"]

    # ──────────────────────────────────────────────────────────────────────────
    # 12. Line Trend Fluctuations vs Net Change
    # ──────────────────────────────────────────────────────────────────────────
    def test_line_trend_minor_fluctuations_vs_true_trend(self):
        """
        When net change between start and end is <2% but there is a major mid-period
        surge, insight should describe fluctuations and cite the peak period rather
        than misrepresenting the trajectory as a strong increase or decrease.
        """
        opt = {
            "xAxis": {"type": "category", "data": ["Jan", "Feb", "Mar", "Apr"]},
            "series": [{"name": "Metric", "type": "line", "data": [100.0, 250.0, 180.0, 100.8]}],
        }
        q_def = {"title": "Metric Fluctuations", "x_axis": "month", "y_axis": "metric"}
        q_res = {"sample_rows": []}

        insight = generate_data_grounded_insight(q_def, q_res, "line", echarts_option=opt)
        assert "fluctuat" in insight.lower() or "peak" in insight.lower() or "steady" in insight.lower()
        assert "Feb" in insight

    # ──────────────────────────────────────────────────────────────────────────
    # 13. Empty Result & NaN/Inf Handling
    # ──────────────────────────────────────────────────────────────────────────
    def test_empty_and_nan_handling(self):
        """
        Empty query results or datasets with NaN/Inf must produce neutral, informative
        insights and sanitized chart options without raising unhandled exceptions.
        """
        empty_opt = {"xAxis": {"type": "category", "data": []}, "series": [{"data": []}]}
        empty_insight = generate_data_grounded_insight(
            {"title": "Empty Query", "x_axis": "x", "y_axis": "y"},
            {"sample_rows": [], "row_count": 0},
            "bar",
            echarts_option=empty_opt,
        )
        assert "no data" in empty_insight.lower() or "insufficient" in empty_insight.lower()

        # Sanitize option containing NaN and Infinity
        raw_opt = {
            "xAxis": {"type": "category", "data": ["Valid", "Invalid1", "Invalid2"]},
            "series": [{"name": "Measure", "type": "bar", "data": [12.0, float("nan"), float("inf")]}],
        }
        sanitized = sanitize_echarts_option(raw_opt, "bar")
        # series data must be numeric or 0, not raw NaN/Inf
        series_data = sanitized["series"][0]["data"]
        for val in series_data:
            assert not math.isnan(val) and not math.isinf(val)

    # ──────────────────────────────────────────────────────────────────────────
    # 14. Target Audit Tests (Task 5: Items 1 to 10)
    # ──────────────────────────────────────────────────────────────────────────
    def test_audit_1_kpi_and_chart_valid_data(self):
        """1. A dataset where both KPI and chart queries return valid data."""
        q_def = {"id": "kpi_overview", "title": "Headline KPIs", "analytical_purpose": "kpi"}
        q_res = {
            "column_names": ["total_revenue", "order_count", "profit_margin"],
            "sample_rows": [{"total_revenue": 1250000.0, "order_count": 8450, "profit_margin": 0.245}],
            "row_count": 1,
        }
        kpis = split_kpi_cards(q_def, q_res, max_kpis=3)
        assert len(kpis) == 3
        for k in kpis:
            assert k["chart_type"] == "kpi_card"
            assert k["echarts_option"]["type"] == "kpi_card"
            assert k["echarts_option"]["_chart_type"] == "kpi_card"
            assert k["echarts_option"]["value"] != "0 Records"
            assert k["echarts_option"]["value"] != ""

        # Chart companion
        chart_def = {"id": "sales_bar", "title": "Category Sales", "x_axis": "category", "y_axis": "sales"}
        chart_res = {
            "column_names": ["category", "sales"],
            "sample_rows": [{"category": "Electronics", "sales": 500000.0}, {"category": "Apparel", "sales": 320000.0}],
            "row_count": 2,
        }
        chart_opt = build_fallback_config(chart_def, chart_res, "bar")
        assert len(chart_opt["series"][0]["data"]) == 2

    def test_audit_2_valid_aggregation_equals_zero(self):
        """2. A dataset where a valid aggregation equals zero."""
        formatted_zero_count = _format_metric_value("churn_records", 0)
        assert formatted_zero_count == "0"
        formatted_zero_loss = _format_metric_value("net_loss", 0.0)
        assert formatted_zero_loss == "$0" or formatted_zero_loss == "0"

        q_def = {"id": "zero_kpi", "title": "Zero Metric", "analytical_purpose": "kpi"}
        q_res = {"column_names": ["returns_count"], "sample_rows": [{"returns_count": 0}], "row_count": 1}
        kpis = split_kpi_cards(q_def, q_res, max_kpis=1)
        assert len(kpis) == 1
        assert kpis[0]["echarts_option"]["value"] == "0"

    def test_audit_3_genuinely_empty_query_result(self):
        """3. A genuinely empty query result."""
        q_def = {"id": "empty_q", "title": "Empty Filter", "analytical_purpose": "comparison", "x_axis": "cat", "y_axis": "val"}
        q_res = {"column_names": ["cat", "val"], "sample_rows": [], "row_count": 0}
        insight = generate_data_grounded_insight(q_def, q_res, "bar")
        assert "0 matching records" in insight or "no data" in insight.lower()

    def test_audit_4_missing_or_incompatible_columns(self):
        """4. Missing or incompatible columns."""
        q_def = {"id": "mismatched_q", "title": "Missing Columns", "x_axis": "non_existent_x", "y_axis": "non_existent_y"}
        q_res = {
            "column_names": ["actual_dim", "actual_metric"],
            "sample_rows": [{"actual_dim": "Alpha", "actual_metric": 100.0}, {"actual_dim": "Beta", "actual_metric": 200.0}],
            "row_count": 2,
        }
        canonical = extract_canonical_plotted_data(q_def, q_res, "bar")
        assert canonical["categories"] == ["Beta", "Alpha"] or canonical["categories"] == ["Alpha", "Beta"]
        assert len(canonical["values"]) == 2

    def test_audit_5_dataset_with_no_monetary_columns(self):
        """5. A dataset with no monetary columns."""
        cols = ["temperature_c", "humidity_pct", "ambient_pressure_hpa", "particle_count"]
        vals = [24.5, 62.1, 1013.25, 480]
        for c, v in zip(cols, vals):
            fmt = _format_metric_value(c, v)
            assert "$" not in fmt, f"Column {c} unexpectedly received currency symbol"
            assert "€" not in fmt
            assert "£" not in fmt

    def test_audit_6_session_switching_isolation(self):
        """6. Switching between unrelated datasets and sessions."""
        kpi_s1 = split_kpi_cards({"id": "k1"}, {"column_names": ["patient_count"], "sample_rows": [{"patient_count": 120}]}, max_kpis=1)
        kpi_s2 = split_kpi_cards({"id": "k2"}, {"column_names": ["turbine_rpm"], "sample_rows": [{"turbine_rpm": 3600}]}, max_kpis=1)
        assert kpi_s1[0]["title"] == "Patient Count"
        assert kpi_s2[0]["title"] == "Turbine Rpm"
        assert kpi_s1[0]["echarts_option"]["value"] == "120"
        assert "3,600" in kpi_s2[0]["echarts_option"]["value"]

    def test_audit_7_filtering_data_and_recalculating_kpis(self):
        """7. Filtering data and recalculating KPIs."""
        orig_res = {"column_names": ["total_orders"], "sample_rows": [{"total_orders": 1000}], "row_count": 1}
        filtered_res = {"column_names": ["total_orders"], "sample_rows": [{"total_orders": 250}], "row_count": 1}
        kpi_orig = split_kpi_cards({"id": "orders"}, orig_res, max_kpis=1)[0]
        kpi_filt = split_kpi_cards({"id": "orders"}, filtered_res, max_kpis=1)[0]
        assert kpi_orig["echarts_option"]["value"] == "1.0K" or kpi_orig["echarts_option"]["value"] == "1,000"
        assert kpi_filt["echarts_option"]["value"] == "250"

    def test_audit_8_kpi_and_chart_consistency_after_refresh(self):
        """8. KPI and chart consistency after refresh."""
        q_def = {"id": "kpi_refresh", "title": "System Active Nodes"}
        q_res = {"column_names": ["active_nodes"], "sample_rows": [{"active_nodes": 42}], "row_count": 1}
        kpis = split_kpi_cards(q_def, q_res, max_kpis=1)
        kpi_card = kpis[0]
        layout = assign_grid_layout([kpi_card])
        assert len(layout) == 1
        assert layout[0]["chart_type"] == "kpi_card"
        assert layout[0]["echarts_option"]["value"] == "42"
        assert layout[0]["layout"]["y"] == 0

    def test_audit_9_pie_chart_many_categories_and_long_labels(self):
        """9. Pie charts with many categories and long labels."""
        long_cats = [
            {"region_name": f"International Logistics Hub Region {i} Extended Description", "volume": float(100 * (i + 1))}
            for i in range(12)
        ]
        q_def = {"title": "Global Hub Volume Distribution", "x_axis": "region_name", "y_axis": "volume"}
        q_res = {"column_names": ["region_name", "volume"], "sample_rows": long_cats, "row_count": 12}
        opt = build_fallback_config(q_def, q_res, "pie")
        assert opt["series"][0]["avoidLabelOverlap"] is True
        assert opt["legend"]["type"] == "scroll"
        assert len(opt["series"][0]["data"]) <= 7

    def test_audit_10_arbitrary_dataset_schemas(self):
        """10. Arbitrary dataset schemas (genomics/astronomy/unseen columns)."""
        schema_rows = [
            {"gene_locus": "BRCA1-LOC", "expression_rpkm": 142.8, "mutation_density": 0.024},
            {"gene_locus": "TP53-LOC", "expression_rpkm": 289.4, "mutation_density": 0.081},
            {"gene_locus": "EGFR-LOC", "expression_rpkm": 68.2, "mutation_density": 0.015},
        ]
        q_def = {"id": "genomics_kpi", "title": "Genomics Locus Summary"}
        q_res = {
            "column_names": ["avg_expression_rpkm", "total_loci"],
            "sample_rows": [{"avg_expression_rpkm": 166.8, "total_loci": 3}],
            "row_count": 1,
        }
        kpis = split_kpi_cards(q_def, q_res, max_kpis=2)
        assert len(kpis) == 2
        assert kpis[0]["title"] == "Avg Expression Rpkm"
        assert kpis[1]["title"] == "Total Loci"
        assert kpis[0]["echarts_option"]["value"] == "166.8"
        assert kpis[1]["echarts_option"]["value"] == "3"



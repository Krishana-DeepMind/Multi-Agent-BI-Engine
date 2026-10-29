import pytest
from backend.agents.layout_node import (
    rule_based_chart_type,
    correct_chart_type,
    build_fallback_config,
    validate_echarts_option,
    generate_data_grounded_insight,
)


class TestIntelligentChartSelection:
    """Test chart selection based on query semantics, column types, and data shape."""

    def test_single_row_aggregate_selects_kpi_card(self):
        profile = {
            "query_title": "Total Revenue",
            "business_question": "What is the total revenue?",
            "column_names": ["total_revenue"],
            "row_count": 1,
            "group_by": None,
            "x_axis_type": "unknown",
            "y_axis_type": "numeric",
            "analytical_purpose": "kpi",
        }
        assert rule_based_chart_type(profile) == "kpi_card"

    def test_temporal_xaxis_selects_line_chart(self):
        profile = {
            "query_title": "Monthly Revenue Trend",
            "business_question": "How did revenue change by month?",
            "column_names": ["month", "revenue"],
            "row_count": 12,
            "group_by": None,
            "x_axis_type": "temporal",
            "y_axis_type": "numeric",
            "analytical_purpose": "trend",
        }
        assert rule_based_chart_type(profile) == "line"

    def test_continuous_distribution_selects_histogram(self):
        profile = {
            "query_title": "Patient Age Distribution",
            "business_question": "What is the distribution of patient ages across frequency bins?",
            "column_names": ["age_bin", "patient_count"],
            "row_count": 8,
            "group_by": None,
            "x_axis_type": "categorical",
            "y_axis_type": "numeric",
            "analytical_purpose": "distribution",
        }
        assert rule_based_chart_type(profile) == "histogram"

    def test_relationship_between_numeric_selects_scatter(self):
        profile = {
            "query_title": "Billing vs Length of Stay",
            "business_question": "What is the relationship between bill amount versus length of stay?",
            "column_names": ["stay_days", "bill_amount"],
            "row_count": 50,
            "group_by": None,
            "x_axis_type": "numeric",
            "y_axis_type": "numeric",
            "analytical_purpose": "relationship",
        }
        assert rule_based_chart_type(profile) == "scatter"

    def test_low_cardinality_composition_selects_donut(self):
        profile = {
            "query_title": "Orders by Payment Status",
            "business_question": "What is the proportion of orders by status?",
            "column_names": ["status", "order_count"],
            "row_count": 4,
            "group_count": 4,
            "group_by": "status",
            "x_axis_type": "categorical",
            "y_axis_type": "numeric",
            "analytical_purpose": "composition",
        }
        assert rule_based_chart_type(profile) == "donut"

    def test_rates_and_percentages_never_use_pie_or_donut(self):
        """Rates, percentages, or averages must be corrected to bar charts."""
        profile = {
            "query_title": "Readmission Rate by Hospital Department",
            "business_question": "What is the readmission rate across departments?",
            "column_names": ["department", "readmission_rate"],
            "row_count": 5,
            "group_count": 5,
            "y_axis": "readmission_rate",
            "x_axis_type": "categorical",
            "y_axis_type": "numeric",
            "analytical_purpose": "comparison",
        }
        # Even if LLM proposed donut or pie, correct_chart_type must enforce bar
        assert correct_chart_type("donut", profile) == "bar"
        assert correct_chart_type("pie", profile) == "bar"

    def test_high_cardinality_pie_corrected_to_bar(self):
        """Pie charts with > 6 categories must be corrected to bar."""
        profile = {
            "query_title": "Revenue by City",
            "business_question": "What is the revenue breakdown across cities?",
            "column_names": ["city", "revenue"],
            "row_count": 15,
            "group_count": 15,
            "y_axis": "revenue",
            "x_axis_type": "categorical",
            "y_axis_type": "numeric",
            "analytical_purpose": "ranking",
        }
        assert correct_chart_type("pie", profile) == "bar"
        assert correct_chart_type("donut", profile) == "bar"

    def test_fallback_config_structure_for_all_chart_types(self):
        """Verify fallback configs generate valid ECharts specs for all supported types."""
        q_def = {"title": "Test Chart", "x_axis": "category", "y_axis": "val"}
        q_res = {
            "column_names": ["category", "val"],
            "sample_rows": [{"category": "A", "val": 10}, {"category": "B", "val": 20}],
            "row_count": 2,
        }

        for chart_type in ["bar", "line", "pie", "donut", "histogram", "scatter", "data_table", "kpi_card"]:
            opt = build_fallback_config(q_def, q_res, chart_type)
            is_valid, err = validate_echarts_option(opt, chart_type)
            assert is_valid, f"Failed for {chart_type}: {err}"


class TestDashboardInsightsGrounded:
    """Verify data-grounded insights strictly cite real query results."""

    def test_ranking_insight_cites_actual_top_entities(self):
        q_def = {"title": "Top Cities by Revenue", "x_axis": "city", "y_axis": "revenue"}
        q_res = {
            "column_names": ["city", "revenue"],
            "sample_rows": [
                {"city": "Pune", "revenue": 2100000},
                {"city": "Delhi", "revenue": 1840000},
                {"city": "Mumbai", "revenue": 950000},
            ],
            "row_count": 3,
        }
        insight = generate_data_grounded_insight(q_def, q_res, "bar")
        # Must name Pune and Delhi (actual top values), NOT hallucinated cities like Berlin
        assert "Pune" in insight
        assert "Delhi" in insight
        assert "$2.1M" in insight
        assert "Berlin" not in insight

    def test_part_to_whole_insight_calculates_correct_percentage(self):
        q_def = {"title": "Payment Method Breakdown", "x_axis": "method", "y_axis": "orders"}
        q_res = {
            "column_names": ["method", "orders"],
            "sample_rows": [
                {"method": "Credit Card", "orders": 75},
                {"method": "PayPal", "orders": 25},
            ],
            "row_count": 2,
        }
        insight = generate_data_grounded_insight(q_def, q_res, "donut")
        # 75 out of 100 = 75.0%
        assert "Credit Card" in insight
        assert "75.0%" in insight
        assert "PayPal" in insight
        assert "25.0%" in insight

    def test_zero_rows_provides_meaningful_explanation(self):
        q_def = {"title": "Orders with Disputed Status", "x_axis": "status", "y_axis": "count"}
        q_res = {
            "column_names": ["status", "count"],
            "sample_rows": [],
            "row_count": 0,
        }
        insight = generate_data_grounded_insight(q_def, q_res, "bar")
        assert "0 matching records" in insight
        assert "Orders with Disputed Status" in insight

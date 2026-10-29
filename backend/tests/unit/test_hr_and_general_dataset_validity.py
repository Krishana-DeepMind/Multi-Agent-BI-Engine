import pytest
import polars as pl
from backend.agents.cleaning_node import normalize_categorical_series, apply_polars_cleaning_op, CleaningOperation
from backend.agents.layout_node import extract_canonical_plotted_data, rule_based_chart_type, correct_chart_type

class TestHRAndGeneralDatasetValidity:
    def test_attrition_count_never_scaled_as_percentage(self):
        """Verify that integer counts (e.g. 1 termination, 24 terminations) remain exact integer counts and are never scaled by 100."""
        query_def = {
            "id": "attrition_by_month_trend",
            "title": "Attrition Trend Analysis",
            "analytical_purpose": "trend",
            "x_axis": "period",
            "y_axis": "attrition_count"
        }
        # Simulate query result with 1 termination in early months and 24 in peak month
        query_result = {
            "columns": ["period", "attrition_count"],
            "sample_rows": [
                {"period": "2011-11", "attrition_count": 1},
                {"period": "2013-02", "attrition_count": 1},
                {"period": "2016-12", "attrition_count": 2},
                {"period": "2026-09", "attrition_count": 24},
            ]
        }
        canonical = extract_canonical_plotted_data(query_def, query_result, "line")
        assert canonical["is_rate_or_margin"] is False
        assert canonical["is_unscaled_ratio"] is False
        # Values must remain exact [1, 1, 2, 24] and NOT [100.0, 100.0, 2.0, 24.0]
        assert canonical["values"] == [1.0, 1.0, 2.0, 24.0]

    def test_department_normalization_collapses_to_canonical_units(self):
        """Verify that all case variations, abbreviations, and sentinels collapse into canonical departments."""
        raw_departments = [
            "Eng", "engineering", "ENGINEERING", "Engg", "Engineering",
            "Finance", "finance", "FINANCE", "Fin",
            "Human Resources", "human resources", "HR", "H.R.", "H.R", "hr",
            "Marketing", "marketing", "MARKETING", "Mktg",
            "Operations", "operations", "OPS", "Ops",
            "Sales", "sales", "SALES", "Sales Dept",
            "?", "-", "#N/A", "missing", None
        ]
        s = pl.Series("department", raw_departments)
        cleaned = normalize_categorical_series(s, "department")
        unique_non_null = set(cleaned.drop_nulls().to_list())
        
        expected_departments = {"Engineering", "Finance", "Human Resources", "Marketing", "Operations", "Sales"}
        assert unique_non_null == expected_departments
        
        # Sentinels must be converted to None
        assert cleaned.null_count() == 5

    def test_numeric_sentinel_and_negative_cleaning(self):
        """Verify that 999 sentinel and impossible negative overtime hours are nullified."""
        df = pl.DataFrame({
            "employee_id": ["EMP1", "EMP2", "EMP3", "EMP4", "EMP5"],
            "overtime_hours": ["999", "-10.0", "40.5", "0.0", "400.0"]
        }).lazy()
        
        op = CleaningOperation(
            column="overtime_hours",
            operation="cast_type",
            strategy="safe_numeric_cast",
            rationale="Cast overtime_hours safely.",
            rows_affected=0,
            before_nulls=0,
            after_nulls=0,
            polars_code=""
        )
        cleaned_lf, _ = apply_polars_cleaning_op(df, op)
        cleaned_df = cleaned_lf.collect()
        
        vals = cleaned_df["overtime_hours"].to_list()
        # 999 (sentinel) -> None, -10.0 (negative) -> None, 400.0 (>200) -> None
        # 40.5 and 0.0 must be preserved
        assert vals[0] is None
        assert vals[1] is None
        assert vals[2] == 40.5
        assert vals[3] == 0.0
        assert vals[4] is None

    def test_outlier_chart_type_is_always_bar_never_donut(self):
        """Verify that outlier queries are always selected as bar charts and never donut/pie charts."""
        profile = {
            "row_count": 2,
            "x_axis_type": "identifier",
            "y_axis_type": "numeric",
            "group_count": 2,
            "analytical_purpose": "outlier",
            "business_question": "Which employees are working significantly more overtime than the organizational average?",
            "query_title": "High Overtime Hours Outliers",
            "x_axis": "employee_id",
            "y_axis": "overtime_hours"
        }
        rule_chart = rule_based_chart_type(profile)
        assert rule_chart == "bar"
        
        # Even if an LLM incorrectly proposes donut, correct_chart_type must correct it to bar
        corrected = correct_chart_type("donut", profile)
        assert corrected == "bar"

    def test_unseen_dataset_general_categorical_casing_and_sentinels(self):
        """Verify that general unseen categories (e.g. retail product categories, cities) are normalized and cleaned."""
        raw_categories = ["electronics", "Electronics", "ELECTRONICS", "clothing", "CLOTHING", "?", "missing", "n/a"]
        s = pl.Series("product_category", raw_categories)
        cleaned = normalize_categorical_series(s, "product_category")
        unique_non_null = set(cleaned.drop_nulls().to_list())
        
        assert unique_non_null == {"Electronics", "Clothing"}
        assert cleaned.null_count() == 3

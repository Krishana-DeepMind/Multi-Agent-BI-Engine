import json
import logging
import re
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.core.state import AgentSwarmState, ChartConfig
from backend.core.context_slicer import slice_context
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response

logger = logging.getLogger(__name__)

VALID_CHART_TYPES = {"bar", "line", "scatter", "area", "pie", "donut", "histogram", "heatmap", "kpi_card", "data_table"}

TEMPORAL_KEYWORDS = {"date", "month", "quarter", "year", "week", "timestamp", "day",
                     "period", "time", "jan", "feb", "mar", "q1", "q2", "q3", "q4"}

# AutoBI centralized chart color palette — applied by frontend, NOT by LLM
AUTOBI_PALETTE = [
    "#4F6EF7", "#22C997", "#FF8C42", "#E05D6F", "#9B6DFF",
    "#20B2AA", "#F4C542", "#6EC6FF", "#FF6B9D", "#95DE64",
]


def profile_query_result(query_def: Dict, query_result: Dict) -> Dict[str, Any]:
    """Profile a query result for chart selection. Uses analytical_purpose from QueryDefinition directly."""
    col_names: List[str] = query_result.get("column_names", [])
    sample_rows: List[Dict] = query_result.get("sample_rows", [])
    row_count: int = query_result.get("row_count", 0)
    x_axis = query_def.get("x_axis") or (col_names[0] if col_names else None)
    y_axis = query_def.get("y_axis") or (col_names[1] if len(col_names) > 1 else None)
    group_by = query_def.get("group_by")
    business_question = query_def.get("business_question", "")

    # Detect x_axis type
    x_type = "unknown"
    if x_axis and sample_rows:
        if any(kw in (x_axis or "").lower() for kw in TEMPORAL_KEYWORDS):
            x_type = "temporal"
        else:
            sample_x = str(sample_rows[0].get(x_axis, "")).lower()
            if any(kw in sample_x for kw in ["jan", "feb", "mar", "q1", "q2", "2020", "2021", "2022", "2023", "2024", "2025", "2026"]):
                x_type = "temporal"
            else:
                try:
                    float(str(sample_rows[0].get(x_axis, "")))
                    x_type = "numeric"
                except (ValueError, TypeError):
                    x_type = "categorical"

    # Detect y_axis type
    y_type = "unknown"
    if y_axis and sample_rows:
        try:
            float(str(sample_rows[0].get(y_axis, "")))
            y_type = "numeric"
        except (ValueError, TypeError):
            y_type = "categorical"

    # Group count
    group_count = 0
    if group_by and sample_rows:
        group_count = len(set(str(r.get(group_by, "")) for r in sample_rows))
    elif row_count:
        group_count = min(row_count, len(sample_rows))

    # Use analytical_purpose from QueryDefinition directly (set by Analytics Agent)
    analytical_purpose = query_def.get("analytical_purpose", "detail")

    return {
        "query_id": query_def.get("id"),
        "query_title": query_def.get("title", ""),
        "business_question": business_question,
        "column_names": col_names,
        "num_columns": len(col_names),
        "row_count": row_count,
        "x_axis": x_axis,
        "x_axis_type": x_type,
        "y_axis": y_axis,
        "y_axis_type": y_type,
        "group_by": group_by,
        "group_count": group_count,
        "analytical_purpose": analytical_purpose,
        "insight_summary": query_def.get("insight_summary", ""),
        "sample_rows": sample_rows[:5],
        "priority": query_def.get("priority", 3),
    }


def rule_based_chart_type(profile: Dict) -> Optional[str]:
    """Deterministic chart type pre-selection based on query purpose, column types, and data shape."""
    row_count = profile.get("row_count", 0)
    x_type = profile.get("x_axis_type", "unknown")
    y_type = profile.get("y_axis_type", "unknown")
    num_cols = profile.get("num_columns", 0)
    group_count = profile.get("group_count", 0)
    analytical_purpose = (profile.get("analytical_purpose") or "").lower().strip()
    business_question = profile.get("business_question", "").lower()
    title = profile.get("query_title", "").lower()
    y_col = (profile.get("y_axis") or "").lower()

    # Rule A: KPI card — single-row aggregate (1 row, any number of columns)
    if row_count == 1 and not profile.get("group_by"):
        return "kpi_card"

    # Rule B: Temporal x-axis — chronological line trend
    if x_type == "temporal" or analytical_purpose == "trend":
        return "line"

    # Rule C: Histogram — distribution of continuous measure
    if analytical_purpose == "distribution" or any(w in business_question or w in title for w in ["distribution", "histogram", "spread", "bins", "frequency"]):
        if row_count >= 2:
            return "histogram"

    # Rule D: Scatter plot — relationship / correlation between two numeric variables
    if analytical_purpose in ("relationship", "correlation") or any(w in business_question or w in title for w in ["correlation", "versus", "vs", "relationship"]):
        if x_type == "numeric" and y_type == "numeric" and row_count >= 5:
            return "scatter"

    # Rule Outlier/Anomaly/Benchmark: Individual anomaly, benchmark, or outlier entities MUST be bar charts (NEVER pie/donut)
    is_anomaly_or_outlier = (
        analytical_purpose in ("outlier", "anomaly", "benchmark", "comparison", "ranking")
        or any(w in business_question or w in title for w in ["outlier", "outliers", "anomaly", "anomalies", "benchmark", "abnormal", "exceeding", "deviation", "overtime"])
        or (x_type == "identifier" or any(id_kw in str(profile.get("x_axis", "")).lower() for id_kw in ("_id", "id", "sku", "code", "uuid", "ssn")))
    )
    if is_anomaly_or_outlier and row_count >= 1:
        return "bar"

    # Rule E: Part-to-whole (Pie / Donut) — meaningful composition with 2 to 6 categories
    # NEVER use pie/donut for rates, ratios, percentages, averages, or entity-level metrics
    is_rate = any(kw in y_col or kw in business_question or kw in title for kw in ["rate", "pct", "percent", "ratio", "readmit", "insured", "avg", "mean", "discount"])
    is_part_to_whole = analytical_purpose in ("composition", "share", "part_to_whole") or any(w in business_question or w in title for w in ["share", "composition", "proportion", "breakdown by status", "breakdown by payment", "breakdown by gender", "mix"])
    if 2 <= row_count <= 6 and not is_rate and not is_anomaly_or_outlier and (is_part_to_whole or (group_count <= 6 and not any(w in business_question for w in ["top", "rank", "highest", "lowest", "rate"]))):
        # Use donut for clean modern aesthetics
        return "donut"

    # Rule F: Comparison / Ranking / Rates / Anomalies -> Bar chart
    if analytical_purpose in ("comparison", "ranking", "breakdown", "anomaly") or is_rate or any(w in business_question or w in title for w in ["top", "rank", "best", "worst", "highest", "lowest", "outlier", "anomaly"]):
        return "bar"

    # Rule G: data_table — only for truly large unrenderable results
    is_ranking = any(w in business_question for w in ["top", "rank", "best", "worst", "highest", "lowest", "outlier", "anomaly"])
    if not is_ranking and (row_count > 100 or group_count > 30):
        return "data_table"

    return None


def correct_chart_type(llm_chart: str, profile: Dict) -> str:
    """Post-LLM deterministic correction to prevent misleading or invalid chart choices."""
    row_count = profile.get("row_count", 0)
    x_type = profile.get("x_axis_type", "unknown")
    y_type = profile.get("y_axis_type", "unknown")
    group_count = profile.get("group_count", 0)
    y_col = (profile.get("y_axis") or "").lower()
    x_col = (profile.get("x_axis") or "").lower()
    bq = profile.get("business_question", "").lower()
    title = profile.get("query_title", "").lower()

    if llm_chart in ("pie", "donut"):
        # Rates, percentages, outliers, and entity rankings do not sum to 100% — MUST be bar charts
        is_rate = any(kw in y_col or kw in bq or kw in title for kw in ["rate", "pct", "percent", "ratio", "readmit", "insured", "avg", "mean"])
        is_entity_or_outlier = (
            profile.get("analytical_purpose") in ("comparison", "ranking", "outlier", "anomaly", "benchmark")
            or any(w in title or w in bq for w in ["outlier", "anomaly", "benchmark", "highest", "lowest", "top", "rank", "exceeding", "overtime"])
            or any(id_kw in x_col for id_kw in ("_id", "id", "sku", "code", "uuid", "ssn", "employee", "patient", "order"))
        )
        if is_rate or group_count > 6 or row_count > 6 or is_entity_or_outlier:
            logger.info(f"Correcting {llm_chart} -> bar (rate/breakdown metric, outlier entity, or category count > 6)")
            return "bar"

    if llm_chart == "heatmap":
        cols = [c.lower() for c in profile.get("column_names", [])]
        has_id = any(any(p in c for p in ("_id", "id_", "sku", "uuid", "key", "number")) for c in (x_col, y_col) if c)
        is_outlier = any(w in title or w in bq for w in ("outlier", "anomaly", "high_", "top_"))
        # Heatmap requires genuine 2D cross-tabulation without entity IDs and row_count >= 15
        if has_id or is_outlier or row_count < 15 or group_count > 30:
            logger.info(f"Correcting heatmap -> bar for ranked entity/outlier query (has_id={has_id}, rows={row_count})")
            return "bar"

    if llm_chart == "line" and x_type == "categorical":
        if not any(kw in (profile.get("x_axis") or "").lower() for kw in TEMPORAL_KEYWORDS):
            logger.info("Correcting line -> bar (x_axis_type=categorical)")
            return "bar"

    if llm_chart == "kpi_card" and row_count > 1:
        logger.info(f"Correcting kpi_card -> bar (row_count={row_count})")
        return "bar"

    if llm_chart == "scatter" and y_type != "numeric":
        logger.info("Correcting scatter -> bar (y_axis not numeric)")
        return "bar"

    if llm_chart == "histogram" and row_count < 2:
        return "bar"

    return llm_chart


def _format_metric_value(col: str, val: Any) -> str:
    """Format KPI and chart values with proper currency, percentage, physical unit, or magnitude symbols."""
    try:
        num = float(val)
        col_lower = col.lower().strip()

        # 0. Dimensionless statistics check FIRST (z-score, correlation, p-value, index, std dev, count)
        is_dimensionless = bool(re.search(r"(^|[_ \-\(\[])(z_?score|zscore|correlation|p_?value|index|multiplier|std_?dev|deviation)([_ \-\)\]]|$)", col_lower))
        if is_dimensionless:
            sign = "-" if num < 0 else ""
            abs_n = abs(num)
            formatted = f"{abs_n:.2f}" if abs_n < 10 else f"{abs_n:.1f}"
            return f"{sign}{formatted}"

        is_count = bool(re.search(
            r"(^|[_ \-\(\[])(count|num|headcount|records?|employees?|orders?|items?|frequency|volume|cases|tenure|age|quantity|units?|downloads?|clicks?|views?|visits?|subscribers?)([_ \-\)\]]|$)",
            col_lower
        )) and not any(sym in col for sym in ("$", "€", "£", "₹", "¥"))
        is_explicit_count = bool(re.search(r"(_count|_records?|_num|_headcount|_total)$", col_lower)) or col_lower.startswith(("count_", "num_"))

        # 1. Physical unit check from suffix or brackets FIRST
        has_physical_unit = False
        bracket_match = re.search(r"[\(\[]([A-Za-z0-9°µ$%€£₹¥/_-]+)[\)\]]", col)
        unit = bracket_match.group(1).strip() if bracket_match else None
        if not unit:
            suffix_match = re.search(r"_(ppm|ppb|hpa|kpa|pa|bar|psi|c|f|kelvin|kg|g|mg|lbs|oz|km|miles|ms|sec|seconds|hours|days|kwh|mwh|mw|hz|khz|mhz|bpm|rpm|db|aqi)$", col_lower)
            if suffix_match:
                suf = suffix_match.group(1)
                units_map = {
                    "ppm": "ppm", "ppb": "ppb", "hpa": "hPa", "kpa": "kPa", "pa": "Pa",
                    "bar": "bar", "psi": "psi", "c": "°C", "f": "°F", "kelvin": "K",
                    "kg": "kg", "g": "g", "mg": "mg", "lbs": "lbs", "oz": "oz",
                    "km": "km", "miles": "mi", "ms": "ms", "sec": "s", "seconds": "s",
                    "hours": "hrs", "days": "days", "kwh": "kWh", "mwh": "MWh", "mw": "MW",
                    "hz": "Hz", "khz": "kHz", "mhz": "MHz", "bpm": "bpm", "rpm": "rpm",
                    "db": "dB", "aqi": "AQI"
                }
                unit = units_map.get(suf)
                has_physical_unit = True

        # 2. Percentage / Rate / Margin / Ratio check (takes precedence over monetary, unless explicitly an absolute count)
        is_pct = (
            (
                bool(re.search(r"(^|[_ \-\(\[])(margin|rate|ratio|pct|percent|percentage|share|proportion|yield|discount|conversion)([_ \-\)\]]|$)", col_lower))
                or (re.search(r"(^|[_ \-\(\[])(attrition|churn)([_ \-\)\]]|$)", col_lower) and not is_explicit_count)
            )
            or col_lower.endswith("_pct")
            or "%" in col_lower
        ) and not has_physical_unit and not is_explicit_count

        if is_pct:
            # Handle unscaled ratio vs percentage:
            # If value is in [-1.5, 1.5] and non-zero, it represents an unscaled ratio (e.g. 0.12 -> 12.0%, -0.05 -> -5.0%)
            # If value > 1.5, it is already on the percentage scale (e.g. 14.5 -> 14.5%, 84.6 -> 84.6%)
            if 0.0001 < abs(num) <= 1.5:
                return f"{num * 100:.1f}%"
            return f"{num:.1f}%"

        # 3. Currency check (must not be a dimensionless statistic, count, or rate/margin)
        has_currency_symbol = any(sym in col for sym in ("$", "€", "£", "₹", "¥"))
        has_currency_token = bool(re.search(r"\b(usd|eur|gbp|inr|jpy|cny)\b", col_lower))
        is_known_monetary_word = bool(re.search(
            r"(^|[_ \-\(\[])(salary|salaries|revenue|revenues|costs?|prices?|compensation|payroll|wages?|sales|profits?|budgets?|spends?|bills?|billing|billed|fees?|charges?|bonuses?|paid)([_ \-\)\]]|$)",
            col_lower
        ))
        is_curr = (has_currency_symbol or has_currency_token or is_known_monetary_word) and not is_count and not is_pct

        curr_sym = "$"
        if "€" in col or "eur" in col_lower:
            curr_sym = "€"
        elif "£" in col or "gbp" in col_lower:
            curr_sym = "£"
        elif "₹" in col or "inr" in col_lower:
            curr_sym = "₹"
        elif "¥" in col or "jpy" in col_lower or "cny" in col_lower:
            curr_sym = "¥"

        # 4. Magnitude formatting (preserve natural values for physical measurements < 100K)
        sign = "-" if num < 0 else ""
        abs_num = abs(num)
        if abs_num >= 1_000_000_000:
            formatted = f"{abs_num/1_000_000_000:.1f}B"
        elif abs_num >= 1_000_000:
            formatted = f"{abs_num/1_000_000:.1f}M"
        elif abs_num >= 1_000 and not has_physical_unit:
            formatted = f"{abs_num/1_000:.1f}K"
        else:
            formatted = f"{abs_num:,.1f}" if abs_num != int(abs_num) else f"{int(abs_num):,}"

        if is_curr:
            return f"{sign}{curr_sym}{formatted}"

        if unit:
            return f"{sign}{formatted} {unit}"
        return f"{sign}{formatted}"
    except (ValueError, TypeError):
        return str(val)


def validate_echarts_option(option: Dict, chart_type: str) -> Tuple[bool, str]:
    """Validate ECharts option before rendering."""
    if not isinstance(option, dict):
        return False, "Option is not a dict"
    if option.get("type") in ("kpi_card", "data_table"):
        return True, ""
    if "series" not in option:
        return False, "Missing 'series'"
    if not isinstance(option.get("series"), list):
        return False, "'series' must be an array"
    if len(option.get("series", [])) == 0:
        return False, "'series' is empty"

    series_list = option.get("series", [])
    has_data = any(
        isinstance(s.get("data"), list) and len(s.get("data")) > 0
        for s in series_list
        if isinstance(s, dict)
    )
    if not has_data:
        return False, "All series have empty data arrays"

    series_0 = series_list[0] if series_list else {}
    if chart_type in ("pie", "donut"):
        data = series_0.get("data", [])
        if not data or not isinstance(data, list):
            return False, f"{chart_type} chart missing series data"
        if data and not isinstance(data[0], dict):
            return False, f"{chart_type} data must be [{{name, value}}] format"
    if chart_type in ("line", "bar", "area", "histogram"):
        if "xAxis" not in option:
            return False, f"Missing xAxis for {chart_type} chart"
    return True, ""


def _looks_like_js_function(value: str) -> bool:
    """Detect JS function strings that LLMs sometimes emit as tooltip formatters."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return (
        stripped.startswith("function") or
        "=>" in stripped or
        stripped.startswith("(params")
    )


def _get_query_data(query_result: Dict) -> List[Dict]:
    """Retrieve full query result rows if available, falling back to sample_rows or data."""
    if not isinstance(query_result, dict):
        return []
    chart_data = query_result.get("chart_data")
    if chart_data and isinstance(chart_data, list) and len(chart_data) > 0:
        return chart_data
    data = query_result.get("data")
    if data and isinstance(data, list) and len(data) > 0:
        return data
    return query_result.get("sample_rows", [])


def _parse_date_sort_key(val: Any) -> str:
    s = str(val or "").strip()
    if not s:
        return s

    # 1. Quarters: e.g. 2024-Q1, 2024 Q2, Q3 2024, Q4-2023
    q_match1 = re.match(r"^(\d{4})[-_\s]?Q([1-4])$", s, re.IGNORECASE)
    if q_match1:
        year = q_match1.group(1)
        q = int(q_match1.group(2))
        month = (q - 1) * 3 + 1
        return f"{year}-{month:02d}"

    q_match2 = re.match(r"^Q([1-4])[-_\s]?(\d{4})$", s, re.IGNORECASE)
    if q_match2:
        year = q_match2.group(2)
        q = int(q_match2.group(1))
        month = (q - 1) * 3 + 1
        return f"{year}-{month:02d}"

    # Pure numeric integer: zero-pad to 4 digits so '1' -> '0001', '10' -> '0010', sorting numerically
    if s.isdigit():
        return f"{int(s):04d}"

    # Month names (e.g. 'Jan', 'January', 'Feb', 'March') without year
    for m_fmt in ("%b", "%B"):
        try:
            m_num = datetime.strptime(s, m_fmt).month
            return f"0000-{m_num:02d}"
        except Exception:
            pass

    # 2. ISO timestamps: e.g. 2024-01-15T10:30:00Z -> 2024-01-15 10:30:00
    iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}(?::\d{2})?)", s)
    if iso_match:
        return f"{iso_match.group(1)} {iso_match.group(2)}"

    # 3. Standard YYYY-MM or YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}(?:-\d{2})?$", s):
        return s

    # 4. Single-digit month/day: e.g. 2024-1 -> 2024-01, 2024-1-5 -> 2024-01-05
    single_ym = re.match(r"^(\d{4})[-/](\d{1,2})$", s)
    if single_ym:
        return f"{int(single_ym.group(1)):04d}-{int(single_ym.group(2)):02d}"

    single_ymd = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if single_ymd:
        return f"{int(single_ymd.group(1)):04d}-{int(single_ymd.group(2)):02d}-{int(single_ymd.group(3)):02d}"

    # 5. Month-Year with slash: 01/2024 or 1/2024
    my_match = re.match(r"^(\d{1,2})[-/](\d{4})$", s)
    if my_match:
        return f"{int(my_match.group(2)):04d}-{int(my_match.group(1)):02d}"

    # 6. YYYYMMDD compact format
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"

    # 7. DateTime parsing formats
    for fmt in (
        "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y", "%d/%m/%Y", "%m/%d/%Y",
        "%b %Y", "%B %Y", "%b-%Y", "%b-%y", "%Y-%m", "%Y/%m", "%Y.%m.%d", "%d.%m.%Y"
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return s


def _is_date_like(val: Any) -> bool:
    s = str(val or "").strip()
    if not s or len(s) < 4:
        return False
    if re.match(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?", s):
        return True
    if re.match(r"^(\d{4})[-_\s]?Q[1-4]$", s, re.IGNORECASE) or re.match(r"^Q[1-4][-_\s]?\d{4}$", s, re.IGNORECASE):
        return True
    if re.match(r"^\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4}", s):
        return True
    if re.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", s):
        return True
    if re.match(r"^[A-Za-z]{3,9}\s+\d{4}$", s) or re.match(r"^[A-Za-z]{3,9}[-_]\d{2,4}$", s):
        return True
    return False


def extract_canonical_plotted_data(query_def: Dict, query_result: Dict, chart_type: str) -> Dict[str, Any]:
    """
    Extract a single, reconciled, canonical plotted data object from query results.
    Guarantees:
    - 1:1 match between categories and series values (no dropped NaNs causing length mismatch).
    - True metric units & scales: no silent mixing of ratios (0.12) with percentages (12%).
    - Chronological sorting for time series.
    - Top-down ranking for bar charts.
    - Filtering of sentinel/null entities and infinite/NaN numbers.
    """
    col_names = query_result.get("column_names", [])
    data_rows = _get_query_data(query_result)
    x_col = query_def.get("x_axis") or (col_names[0] if col_names else "dimension")
    y_col = query_def.get("y_axis") or (col_names[1] if len(col_names) > 1 else col_names[0] if col_names else "metric")

    SENTINEL_ENTITIES = {"", "none", "null", "nan", "?", "-", "--", "#n/a", "n/a", "na", "unknown", "unspecified", "missing"}

    if data_rows:
        first_r = data_rows[0]
        if x_col not in first_r:
            for k in first_r.keys():
                if any(kw in k.lower() for kw in ("date", "month", "year", "period", "category", "country", "dept", "name", "id", "code")):
                    x_col = k
                    break
            if x_col not in first_r and len(first_r) > 0:
                x_col = list(first_r.keys())[0]

        if y_col not in first_r or y_col == x_col:
            for k in first_r.keys():
                if k != x_col and not any(kw in k.lower() for kw in ("date", "month", "year", "period")):
                    try:
                        float(str(first_r.get(k, 0) or 0))
                        y_col = k
                        break
                    except (ValueError, TypeError):
                        pass
            if (y_col not in first_r or y_col == x_col) and len(first_r) > 1:
                y_col = list(first_r.keys())[1]

    y_lower = str(y_col).lower()
    
    # Check if this metric is an absolute entity COUNT (not a percentage/rate/margin)
    is_count_col = bool(re.search(
        r"(^|[_ \-\(\[])(count|cnt|volume|hires|records?|cases|orders?|admissions?|occurrences|events?|employees?|patients?|units?|items?|users?|customers?|rows)([_ \-\)\]]|$)",
        y_lower
    )) or y_lower.endswith(("_count", "_cnt", "_vol", "_volume", "_total", "_num", "_hires"))

    is_rate_or_margin = (
        bool(re.search(
            r"(^|[_ \-\(\[])(margin|profit_margin|gross_margin|operating_margin|rate|pct|percent|percentage|share|proportion|yield|discount|conversion)([_ \-\)\]]|$)",
            y_lower
        ))
        and not is_count_col
        and not bool(re.search(r"_(bpm|ppm|ppb|hpa|kpa|pa|bar|psi|c|f|kelvin|kg|g|mg|lbs|oz|km|miles|ms|hz|db|aqi)$", y_lower))
        and not bool(re.search(r"(^|[_ \-\(\[])(salary|revenue|cost|price|spend|budget|payroll|amount|hours|days|duration)([_ \-\)\]]|$)", y_lower))
    )

    raw_pairs = []
    for r in data_rows:
        x_raw = r.get(x_col)
        y_raw = r.get(y_col)
        if x_raw is None:
            continue
        x_str = str(x_raw).strip()
        if x_str.lower() in SENTINEL_ENTITIES:
            continue
        try:
            y_float = float(y_raw if y_raw is not None else 0.0)
            if math.isnan(y_float) or math.isinf(y_float):
                continue
        except (ValueError, TypeError):
            continue
        raw_pairs.append((x_str, y_float))

    non_zero_ys = [p[1] for p in raw_pairs if p[1] != 0]
    # True unscaled ratio: column is a rate/margin and EVERY non-zero value is <= 1.5 (e.g. 0.05, 0.12, 0.85)
    is_unscaled_ratio = is_rate_or_margin and bool(non_zero_ys) and all(abs(y) <= 1.5 for y in non_zero_ys)

    valid_pairs = []
    for x_s, y_f in raw_pairs:
        if is_unscaled_ratio:
            # Scale entire series uniformly from ratio to percentage
            valid_pairs.append((x_s, round(y_f * 100.0, 2)))
        else:
            # Preserve original value (counts, already-percentage rates, currency, physical units)
            valid_pairs.append((x_s, y_f))

    is_temporal = (
        chart_type in ("line", "area")
        or any(kw in str(x_col).lower() for kw in ("date", "month", "year", "period", "quarter", "week", "day", "time", "hour", "timestamp"))
        or (bool(raw_pairs) and all(_is_date_like(p[0]) for p in raw_pairs[:min(5, len(raw_pairs))]))
    )
    if is_temporal:
        valid_pairs.sort(key=lambda p: _parse_date_sort_key(p[0]))

    categories = [p[0] for p in valid_pairs]
    values = [p[1] for p in valid_pairs]

    top_cat = categories[0] if categories else ""
    top_val = values[0] if values else 0.0
    sec_cat = categories[1] if len(categories) > 1 else ""
    sec_val = values[1] if len(values) > 1 else 0.0

    peak_idx = max(range(len(values)), key=lambda i: values[i]) if values else 0
    peak_date = categories[peak_idx] if categories else ""
    peak_val = values[peak_idx] if values else 0.0

    first_date = categories[0] if categories else ""
    first_val = values[0] if values else 0.0
    last_date = categories[-1] if categories else ""
    last_val = values[-1] if values else 0.0

    return {
        "x_col": x_col,
        "y_col": y_col,
        "categories": categories,
        "values": values,
        "is_rate_or_margin": is_rate_or_margin,
        "is_unscaled_ratio": is_unscaled_ratio,
        "is_temporal": is_temporal,
        "top_category": top_cat,
        "top_value": top_val,
        "second_category": sec_cat,
        "second_value": sec_val,
        "peak_date": peak_date,
        "peak_val": peak_val,
        "first_date": first_date,
        "first_val": first_val,
        "last_date": last_date,
        "last_val": last_val,
        "total_val": sum(values),
        "avg_val": sum(values) / len(values) if values else 0.0,
        "row_count": len(valid_pairs),
        "slices": [{"name": p[0], "value": p[1]} for p in valid_pairs[:7]],
    }


def _infer_xaxis_time_type(option: Dict, sample_rows: List[Dict], x_col: Optional[str]) -> Dict:
    """Post-process: standardize time/date series for robust rendering and straight lines."""
    x_axis = option.get("xAxis")
    target_axis = None
    if isinstance(x_axis, dict):
        target_axis = x_axis
    elif isinstance(x_axis, list) and len(x_axis) > 0 and isinstance(x_axis[0], dict):
        target_axis = x_axis[0]

    if not target_axis or not sample_rows:
        return option

    first_row = sample_rows[0]
    effective_x = x_col if (x_col and x_col in first_row) else None
    if not effective_x:
        for k in first_row.keys():
            if any(kw in k.lower() for kw in ("period", "date", "month", "year", "quarter", "week", "time")):
                effective_x = k
                break
        if not effective_x and len(first_row) > 0:
            effective_x = list(first_row.keys())[0]

    categories = target_axis.get("data", [])
    if (not categories or (len(categories) <= 5 and len(sample_rows) > len(categories))) and effective_x:
        categories = [str(r.get(effective_x, "")) for r in sample_rows]

    if categories:
        target_axis["data"] = categories
        target_axis["type"] = "category"
        for series in option.get("series", []):
            if isinstance(series, dict):
                raw_data = series.get("data", [])
                if raw_data and isinstance(raw_data[0], (list, tuple)):
                    series["data"] = [item[1] if len(item) > 1 else item[0] for item in raw_data]

    if len(categories) > 30 and "dataZoom" not in option:
        option["dataZoom"] = [
            {"type": "slider", "show": True, "start": 0, "end": 100, "height": 18, "bottom": 6},
            {"type": "inside", "start": 0, "end": 100}
        ]
        grid = option.get("grid")
        if isinstance(grid, dict) and "bottom" not in grid:
            grid["bottom"] = "22%"

    return option


def sanitize_echarts_option(
    option: Dict,
    sample_rows: Optional[List[Dict]] = None,
    x_col: Optional[str] = None,
    y_col: Optional[str] = None,
    chart_type: Optional[str] = None,
    query_def: Optional[Dict] = None,
) -> Dict:
    """Sanitize ECharts option; ground data arrays directly in query results to guarantee 1:1 series/category alignment."""
    if not isinstance(option, dict):
        return option

    # If chart_type was passed positionally as second argument
    if isinstance(sample_rows, str):
        chart_type = sample_rows
        sample_rows = None

    # 1. Strip JS function-valued tooltip formatters
    tooltip = option.get("tooltip")
    if isinstance(tooltip, dict):
        formatter = tooltip.get("formatter")
        if _looks_like_js_function(formatter):
            logger.warning("Stripped JS function from tooltip.formatter")
            trigger = tooltip.get("trigger", "axis")
            tooltip["formatter"] = "{b}: {c}" if trigger == "item" else "{b0}: {c0}"

    # 2. Extract and synchronize canonical data if sample_rows available
    opt_s_type = option.get("series", [{}])[0].get("type") if isinstance(option.get("series"), list) and option.get("series") else None
    eff_chart_type = chart_type or option.get("_chart_type") or opt_s_type or "bar"
    if sample_rows is not None and len(sample_rows) > 0 and eff_chart_type not in ("kpi_card", "data_table"):
        q_def = query_def or {"x_axis": x_col, "y_axis": y_col}
        canonical = extract_canonical_plotted_data(q_def, {"sample_rows": sample_rows, "chart_data": sample_rows}, eff_chart_type)
        
        x_axis = option.get("xAxis")
        target_axis = None
        if isinstance(x_axis, dict):
            target_axis = x_axis
        elif isinstance(x_axis, list) and x_axis and isinstance(x_axis[0], dict):
            target_axis = x_axis[0]

        series_list = option.get("series", [])
        if target_axis is not None and canonical["categories"] and series_list and isinstance(series_list[0], dict):
            target_axis["data"] = canonical["categories"]
            target_axis["type"] = "category"
            series_list[0]["data"] = canonical["values"]
            series_list[0]["name"] = canonical["y_col"].replace("_", " ").title()

            y_axis = option.get("yAxis")
            if isinstance(y_axis, dict) and (canonical["is_rate_or_margin"] or canonical["is_unscaled_ratio"]):
                y_name = y_axis.get("name", "Value")
                if "(%)" not in y_name and "%" not in y_name:
                    y_axis["name"] = f"{y_name} (%)"

            # Benchmark / Reference line check
            is_comp = (
                (query_def and (query_def.get("analytical_purpose") == "comparison" or "benchmark" in str(query_def.get("id", "")).lower() or "vs overall" in str(query_def.get("title", "")).lower()))
                or any(kw in str(canonical["y_col"]).lower() for kw in ("deviation", "diff", "pct_diff"))
            )
            if is_comp and "markLine" not in series_list[0]:
                if sample_rows and "overall_avg" in sample_rows[0]:
                    try:
                        o_val = float(sample_rows[0]["overall_avg"])
                        f_val = _format_metric_value(canonical["y_col"], o_val)
                        series_list[0]["markLine"] = {
                            "symbol": "none",
                            "silent": True,
                            "data": [{"yAxis": o_val, "lineStyle": {"type": "dashed", "color": "#0D9488", "width": 1.5}, "label": {"position": "insideEndTop", "formatter": f"Overall Avg ({f_val})", "color": "#0D9488", "fontWeight": "bold"}}]
                        }
                    except (ValueError, TypeError):
                        pass
                elif any(v < 0 for v in canonical["values"]) or any(kw in str(canonical["y_col"]).lower() for kw in ("deviation", "diff", "pct_diff")):
                    series_list[0]["markLine"] = {
                        "symbol": "none",
                        "silent": True,
                        "data": [{"yAxis": 0, "lineStyle": {"type": "dashed", "color": "#0D9488", "width": 1.5}, "label": {"position": "insideEndTop", "formatter": "Overall Average (0%)", "color": "#0D9488", "fontWeight": "bold"}}]
                    }
                elif "index" in str(canonical["y_col"]).lower():
                    series_list[0]["markLine"] = {
                        "symbol": "none",
                        "silent": True,
                        "data": [{"yAxis": 100, "lineStyle": {"type": "dashed", "color": "#0D9488", "width": 1.5}, "label": {"position": "insideEndTop", "formatter": "Benchmark (100)", "color": "#0D9488", "fontWeight": "bold"}}]
                    }

            if len(canonical["categories"]) > 30 and "dataZoom" not in option:
                option["dataZoom"] = [
                    {"type": "slider", "show": True, "start": 0, "end": 100, "height": 18, "bottom": 6},
                    {"type": "inside", "start": 0, "end": 100}
                ]
                grid = option.get("grid")
                if isinstance(grid, dict) and "bottom" not in grid:
                    grid["bottom"] = "22%"

    # 3. Strip non-finite numbers from series data, ensuring length integrity
    for series in option.get("series", []):
        if not isinstance(series, dict):
            continue
        if "encoding" in series and "encode" not in series:
            series["encode"] = series.pop("encoding")
        if isinstance(series.get("data"), list):
            series["data"] = [
                0.0 if (d is None or (isinstance(d, float) and (d != d or math.isinf(d)))) else d
                for d in series["data"]
            ]

    # 4. Remove any hardcoded color properties (frontend handles palette)
    for series in option.get("series", []):
        if isinstance(series, dict):
            series.pop("color", None)
            if isinstance(series.get("itemStyle"), dict):
                series["itemStyle"].pop("color", None)
    option.pop("color", None)

    # 5. Disable smooth cubic spline interpolation on line series; fix area series type
    for series in option.get("series", []):
        if isinstance(series, dict) and series.get("type") in ("line", "area"):
            series["smooth"] = False
            # ECharts has no "area" type — it's always "line" + areaStyle
            if series.get("type") == "area" or chart_type == "area":
                series["type"] = "line"
                if "areaStyle" not in series:
                    series["areaStyle"] = {"opacity": 0.25}

    # 6. Ensure heatmaps have valid coordinates and mandatory visualMap
    has_heatmap = any(isinstance(s, dict) and s.get("type") == "heatmap" for s in option.get("series", []))
    if has_heatmap or chart_type == "heatmap":
        x_axis = option.get("xAxis")
        y_axis = option.get("yAxis")
        x_data = x_axis.get("data", []) if isinstance(x_axis, dict) else []
        y_data = y_axis.get("data", []) if isinstance(y_axis, dict) else []
        x_len = max(len(x_data), 1)
        y_len = max(len(y_data), 1)

        all_vals = []
        for s in option.get("series", []):
            if not isinstance(s, dict):
                continue
            s["type"] = "heatmap"
            data = s.get("data", [])
            if isinstance(data, list):
                formatted_data = []
                for idx, item in enumerate(data):
                    if isinstance(item, (list, tuple)) and len(item) >= 3:
                        formatted_data.append(item)
                        if isinstance(item[2], (int, float)):
                            all_vals.append(float(item[2]))
                    elif isinstance(item, (int, float)):
                        x_i = idx % x_len
                        y_i = (idx // x_len) % y_len
                        formatted_data.append([x_i, y_i, float(item)])
                        all_vals.append(float(item))
                    elif isinstance(item, dict) and "value" in item:
                        val = item["value"]
                        if isinstance(val, (list, tuple)) and len(val) >= 3:
                            formatted_data.append(val)
                            if isinstance(val[2], (int, float)):
                                all_vals.append(float(val[2]))
                        elif isinstance(val, (int, float)):
                            x_i = idx % x_len
                            y_i = (idx // x_len) % y_len
                            formatted_data.append([x_i, y_i, float(val)])
                            all_vals.append(float(val))
                s["data"] = formatted_data

        min_v = min(all_vals) if all_vals else 0.0
        max_v = max(all_vals) if all_vals else 100.0
        if min_v == max_v:
            max_v = min_v + 10.0

        if "visualMap" not in option:
            option["visualMap"] = {
                "min": min_v,
                "max": max_v,
                "calculable": True,
                "orient": "horizontal",
                "left": "center",
                "bottom": "0%",
                "inRange": {
                    "color": ["#F8FAFC", "#EF4444", "#991B1B"]
                },
                "textStyle": {"color": "#64748B", "fontSize": 10}
            }

    return option


def build_fallback_config(query_def: Dict, query_result: Dict, chart_type: str) -> Dict:
    """Build a deterministic fallback ECharts config using full query data rows."""
    col_names = query_result.get("column_names", [])
    data_rows = _get_query_data(query_result)
    canonical = extract_canonical_plotted_data(query_def, query_result, chart_type)
    x_col = canonical["x_col"]
    y_col = canonical["y_col"]

    if chart_type == "kpi_card" and data_rows:
        row = data_rows[0] if data_rows else {}
        metrics = []
        for col in col_names:
            val = row.get(col, 0)
            formatted = _format_metric_value(col, val)
            label = col.replace("_", " ").title()
            metrics.append({"label": label, "value": formatted, "raw_value": val})
        return {"type": "kpi_card", "metrics": metrics}

    if chart_type == "data_table":
        return {"type": "data_table", "columns": col_names, "rows": data_rows[:100]}

    if chart_type in ("pie", "donut"):
        pie_data = canonical["slices"]
        radius = ["45%", "70%"] if chart_type == "donut" else ["0%", "65%"]
        return {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"type": "scroll", "orient": "vertical", "right": "5%", "top": "center"},
            "series": [{
                "name": y_col.replace("_", " ").title() if y_col else "Value",
                "type": "pie",
                "radius": radius,
                "avoidLabelOverlap": True,
                "itemStyle": {"borderRadius": 4 if chart_type == "donut" else 0, "borderColor": "#ffffff", "borderWidth": 2},
                "center": ["40%", "50%"],
                "data": pie_data,
            }],
        }

    if chart_type == "scatter":
        scatter_data = []
        for i, r in enumerate(data_rows):
            try:
                xv = float(r.get(x_col, 0) or 0)
            except (ValueError, TypeError):
                xv = float(i)
            try:
                yv = float(r.get(y_col, 0) or 0)
            except (ValueError, TypeError):
                yv = 0.0
            scatter_data.append([xv, yv])
        return {
            "tooltip": {"trigger": "item", "formatter": f"{x_col.replace('_', ' ').title()}: {{c0}}<br/>{y_col.replace('_', ' ').title()}: {{c1}}"},
            "grid": {"left": "5%", "right": "5%", "bottom": "15%", "top": "12%", "containLabel": True},
            "xAxis": {"type": "value", "name": x_col.replace("_", " ").title()},
            "yAxis": {"type": "value", "name": y_col.replace("_", " ").title()},
            "series": [{"name": f"{x_col} vs {y_col}", "type": "scatter", "symbolSize": 8, "data": scatter_data}],
        }

    # Grouped series if group_by exists
    group_col = query_def.get("group_by")
    series_type = "line" if chart_type in ("line", "area") else "bar"
    area_style = {"opacity": 0.25} if chart_type == "area" else None

    if group_col and group_col in col_names:
        categories = list(dict.fromkeys(str(r.get(x_col, "")) for r in data_rows if r.get(x_col) is not None))
        distinct_groups = list(dict.fromkeys(str(r.get(group_col, "")) for r in data_rows if r.get(group_col) is not None))
        val_map = {}
        for r in data_rows:
            c_val = str(r.get(x_col, ""))
            g_val = str(r.get(group_col, ""))
            try:
                v = float(r.get(y_col, 0) or 0)
            except (ValueError, TypeError):
                v = 0.0
            val_map[(c_val, g_val)] = v

        series_list = []
        for g in distinct_groups[:6]:
            s_data = [val_map.get((c, g), 0.0) for c in categories]
            s_entry = {
                "name": g.replace("_", " ").title(),
                "type": series_type,
                "data": s_data,
            }
            if area_style:
                s_entry["areaStyle"] = area_style
            series_list.append(s_entry)
        return {
            "tooltip": {"trigger": "axis"},
            "legend": {"type": "scroll", "bottom": 4},
            "grid": {"left": "5%", "right": "5%", "bottom": "18%", "top": "12%", "containLabel": True},
            "xAxis": {"type": "category", "data": categories},
            "yAxis": {"type": "value", "name": y_col.replace("_", " ").title() if y_col else "Value"},
            "series": series_list,
        }

    categories = canonical["categories"]
    values = canonical["values"]
    y_axis_name = y_col.replace("_", " ").title() if y_col else "Value"
    if canonical["is_rate_or_margin"] or canonical["is_unscaled_ratio"]:
        if "(%)" not in y_axis_name and "%" not in y_axis_name:
            y_axis_name = f"{y_axis_name} (%)"

    single_series: Dict = {
        "name": y_axis_name,
        "type": series_type,
        "data": values,
        "smooth": False if series_type == "line" else None
    }
    if area_style:
        single_series["areaStyle"] = area_style

    config = {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "5%", "right": "5%", "bottom": "15%", "top": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value", "name": y_axis_name},
        "series": [single_series],
    }
    # Benchmark / Comparative Reference Line
    is_comparison = (
        query_def.get("analytical_purpose") == "comparison"
        or "benchmark" in str(query_def.get("id", "")).lower()
        or "vs overall" in str(query_def.get("title", "")).lower()
        or "comparison" in str(query_def.get("title", "")).lower()
    )
    if is_comparison and values:
        if data_rows and "overall_avg" in data_rows[0]:
            try:
                overall_val = float(data_rows[0]["overall_avg"])
                formatted_overall = _format_metric_value(y_col, overall_val)
                config["series"][0]["markLine"] = {
                    "symbol": "none",
                    "silent": True,
                    "data": [{
                        "yAxis": overall_val,
                        "lineStyle": {"type": "dashed", "color": "#0D9488", "width": 1.5},
                        "label": {"position": "insideEndTop", "formatter": f"Overall Avg ({formatted_overall})", "color": "#0D9488", "fontWeight": "bold"}
                    }]
                }
            except (ValueError, TypeError):
                pass
        elif any(v < 0 for v in values) or any(kw in str(y_col).lower() for kw in ("deviation", "diff", "pct_diff")):
            config["series"][0]["markLine"] = {
                "symbol": "none",
                "silent": True,
                "data": [{
                    "yAxis": 0,
                    "lineStyle": {"type": "dashed", "color": "#0D9488", "width": 1.5},
                    "label": {"position": "insideEndTop", "formatter": "Overall Average (0%)", "color": "#0D9488", "fontWeight": "bold"}
                }]
            }
        elif "index" in str(y_col).lower():
            config["series"][0]["markLine"] = {
                "symbol": "none",
                "silent": True,
                "data": [{
                    "yAxis": 100,
                    "lineStyle": {"type": "dashed", "color": "#0D9488", "width": 1.5},
                    "label": {"position": "insideEndTop", "formatter": "Benchmark (100)", "color": "#0D9488", "fontWeight": "bold"}
                }]
            }

    if len(categories) > 30 and "dataZoom" not in config:
        config["dataZoom"] = [
            {"type": "slider", "show": True, "start": 0, "end": 100, "height": 18, "bottom": 6},
            {"type": "inside", "start": 0, "end": 100}
        ]
        config["grid"]["bottom"] = "22%"
    return config


def generate_data_grounded_insight(query_def: Dict, query_result: Dict, chart_type: str, echarts_option: Optional[Dict] = None) -> str:
    """
    Generate rigorous, trustworthy insight text synthesized directly from the exact same result object as its chart.
    Guarantees factual accuracy: rankings, totals, percentages, and entity names match the plotted data.
    Provides clear explanation if query returned 0 rows.
    """
    col_names = query_result.get("column_names", [])
    sample_rows = _get_query_data(query_result)
    row_count = query_result.get("row_count", 0) or len(sample_rows)
    title = query_def.get("title", "Metric")
    x_col = query_def.get("x_axis") or (col_names[0] if col_names else "dimension")
    y_col = query_def.get("y_axis") or (col_names[1] if len(col_names) > 1 else col_names[0] if col_names else "metric")

    SENTINEL_ENTITIES = {"", "none", "null", "nan", "?", "-", "--", "#n/a", "n/a", "na", "unknown", "unspecified", "missing"}

    # 1. Direct extraction from echarts_option if available
    plotted_cats = []
    plotted_vals = []
    series_l = []
    is_stacked = False
    if isinstance(echarts_option, dict):
        x_ax = echarts_option.get("xAxis")
        if isinstance(x_ax, list) and x_ax:
            x_ax = x_ax[0]
        if isinstance(x_ax, dict) and isinstance(x_ax.get("data"), list):
            plotted_cats = [str(c) for c in x_ax["data"]]
        series_l = echarts_option.get("series", [])
        if not isinstance(series_l, list):
            series_l = []

        is_stacked = any(bool(s.get("stack")) for s in series_l if isinstance(s, dict))
        
        if is_stacked and plotted_cats and len(series_l) > 1:
            # For stacked charts, calculate true stacked totals per category across all series
            stacked_totals = [0.0] * len(plotted_cats)
            for s in series_l:
                if isinstance(s, dict):
                    s_vals = s.get("data", [])
                    for i in range(min(len(plotted_cats), len(s_vals))):
                        try:
                            v = float(s_vals[i] if s_vals[i] is not None else 0.0)
                            if not math.isnan(v) and not math.isinf(v):
                                stacked_totals[i] += v
                        except (ValueError, TypeError):
                            pass
            plotted_vals = stacked_totals
        elif series_l and isinstance(series_l[0], dict) and isinstance(series_l[0].get("data"), list):
            target_series = series_l[0]
            if len(series_l) > 1 and y_col:
                for s in series_l:
                    if isinstance(s, dict) and str(s.get("name", "")).lower().strip() == str(y_col).lower().strip():
                        target_series = s
                        break
            s_data = target_series.get("data", [])
            if s_data and isinstance(s_data[0], dict) and "name" in s_data[0]:
                plotted_cats = [str(s.get("name", "")) for s in s_data]
                plotted_vals = [float(s.get("value", 0) or 0) for s in s_data]
            else:
                plotted_vals = [float(v if v is not None else 0) for v in s_data]

    has_plotted_data = bool(plotted_cats and plotted_vals)

    # 2. Zero rows case (only when neither query_result rows nor plotted data exists)
    if not has_plotted_data and (row_count == 0 or not sample_rows):
        title_lower = title.lower()
        bq_lower = query_def.get("business_question", "").lower()
        if any(w in title_lower or w in bq_lower for w in ("anomaly", "anomalies", "outlier", "unusual", "discrepancy")):
            return f"No qualifying statistical anomalies detected (0 matching records). All observed records fall within normal statistical distribution bounds for '{title}'."
        return f"Query returned 0 matching records. No data points found for '{title}'."

    # 3. KPI / Single row aggregate (only when not derived from multi-point chart)
    if not has_plotted_data and row_count == 1:
        row = sample_rows[0]
        insights = []
        for c in col_names[:4]:
            val = row.get(c)
            fmt = _format_metric_value(c, val)
            lbl = c.replace("_", " ").title()
            insights.append(f"{lbl}: {fmt}")
        return " | ".join(insights)

    # Part-to-whole (pie, donut)
    if chart_type in ("pie", "donut"):
        if plotted_cats and plotted_vals:
            items = [(c, v) for c, v in zip(plotted_cats, plotted_vals) if v > 0 and c.lower() not in SENTINEL_ENTITIES]
        else:
            items = []
            for r in sample_rows:
                name = str(r.get(x_col, "")).strip()
                if name.lower() in SENTINEL_ENTITIES:
                    continue
                try:
                    v = float(r.get(y_col, 0) or 0)
                    if v > 0:
                        items.append((name, v))
                except (ValueError, TypeError):
                    pass
        items.sort(key=lambda x: x[1], reverse=True)
        total_val = sum(x[1] for x in items)
        if items and total_val > 0:
            top_name, top_val = items[0]
            top_pct = (top_val / total_val) * 100
            fmt_val = _format_metric_value(y_col, top_val)
            if len(items) > 1:
                second_name, second_val = items[1]
                sec_pct = (second_val / total_val) * 100
                fmt_sec = _format_metric_value(y_col, second_val)
                return f"{top_name} leads with {top_pct:.1f}% ({fmt_val}), followed by {second_name} at {sec_pct:.1f}% ({fmt_sec}) out of {len(items)} categories."
            return f"{top_name} represents 100% ({fmt_val}) of the total."

    # 4. Temporal trend (line, area)
    if chart_type in ("line", "area"):
        if plotted_cats and plotted_vals and len(plotted_cats) == len(plotted_vals):
            x_first = plotted_cats[0]
            y_first = plotted_vals[0]
            x_last = plotted_cats[-1]
            y_last = plotted_vals[-1]
            peak_idx = max(range(len(plotted_vals)), key=lambda i: plotted_vals[i])
            max_val = plotted_vals[peak_idx]
            max_date = plotted_cats[peak_idx]
            num_pts = len(plotted_vals)
        else:
            first_r = sample_rows[0]
            last_r = sample_rows[-1]
            x_first = str(first_r.get(x_col, ""))
            x_last = str(last_r.get(x_col, ""))
            y_first = float(first_r.get(y_col, 0) or 0)
            y_last = float(last_r.get(y_col, 0) or 0)
            peak_idx = max(range(len(sample_rows)), key=lambda i: float(sample_rows[i].get(y_col, 0) or 0))
            max_r = sample_rows[peak_idx]
            max_val = float(max_r.get(y_col, 0) or 0)
            max_date = str(max_r.get(x_col, ""))
            num_pts = len(sample_rows)

        try:
            fmt_first = _format_metric_value(y_col, y_first)
            fmt_last = _format_metric_value(y_col, y_last)
            fmt_max = _format_metric_value(y_col, max_val)

            diff = y_last - y_first
            rel_change = abs(diff) / max(abs(y_first), 1e-6)
            is_y_pct = bool(re.search(r"(^|[_ \-\(\[])(pct|percent|percentage)([_ \-\)\]]|$)", y_col.lower())) or y_col.lower().endswith("_pct") or "%" in y_col.lower()

            # Guard against sparse boundary months (e.g. data starts or ends mid-month with <5% of peak volume)
            is_first_sparse = (max_val > 0 and y_first < 0.08 * max_val)
            is_last_sparse = (max_val > 0 and y_last < 0.08 * max_val)

            if (is_first_sparse or is_last_sparse) and num_pts >= 4:
                return f"Chronological trend peaks at {fmt_max} in {max_date}. Trajectory spans {x_first} to {x_last} with sparse activity recorded at boundary periods."

            if is_y_pct:
                pct_change = f" ({abs(diff):.1f} percentage points)" if abs(diff) >= 0.05 else ""
            elif y_first > 0 and abs(diff) > 0:
                pct_change = f" ({abs(diff)/y_first * 100:.1f}%)" if rel_change >= 0.01 else ""
            else:
                pct_change = ""

            has_mid_surge = (peak_idx > 0 and peak_idx < num_pts - 1 and max_val > 1.15 * max(y_first, y_last))

            # Accurately describe fluctuations when start and end differ only slightly
            if rel_change < 0.02 and not is_y_pct:
                if has_mid_surge:
                    return f"Chronological trend remained largely steady overall from {fmt_first} ({x_first}) to {fmt_last} ({x_last}), after peaking at {fmt_max} in {max_date}."
                return f"Chronological trend remained steady from {fmt_first} ({x_first}) to {fmt_last} ({x_last}), fluctuating within a narrow range."

            direction = "increased" if diff > 0 else "decreased"
            return f"Chronological trend {direction}{pct_change} from {fmt_first} ({x_first}) to {fmt_last} ({x_last}), peaking at {fmt_max} in {max_date}."
        except Exception:
            pass

    def _lookup(row: Dict, col_candidate: str) -> Any:
        if col_candidate in row:
            return row[col_candidate]
        cl = col_candidate.lower().strip()
        for k, v in row.items():
            if k.lower().strip() == cl:
                return v
        return None

    # Anomaly / Outlier queries
    is_anomaly = "anomaly" in str(query_def.get("analytical_purpose", "")).lower() or any(w in title.lower() for w in ["outlier", "anomaly", "deviat"])
    if is_anomaly and sample_rows:
        top_r = sample_rows[0]
        top_entity = str(_lookup(top_r, x_col) or list(top_r.values())[0])
        top_val = _lookup(top_r, y_col)
        if top_val is None and len(top_r) > 1:
            top_val = list(top_r.values())[1]
        fmt_top = _format_metric_value(y_col, top_val)
        if len(sample_rows) > 1:
            sec_r = sample_rows[1]
            sec_entity = str(_lookup(sec_r, x_col) or list(sec_r.values())[0])
            sec_val = _lookup(sec_r, y_col)
            if sec_val is None and len(sec_r) > 1:
                sec_val = list(sec_r.values())[1]
            fmt_sec = _format_metric_value(y_col, sec_val)
            return f"Identified {len(sample_rows)} high-value outliers; top outlier is {top_entity} ({fmt_top}), followed by {sec_entity} ({fmt_sec})."
        return f"Identified 1 outlier: {top_entity} with {fmt_top}."

    # 5. Ranking / Comparison (bar, histogram, default)
    ranked = []
    if plotted_cats and plotted_vals and len(plotted_cats) == len(plotted_vals):
        for c, v in zip(plotted_cats, plotted_vals):
            if c.lower() not in SENTINEL_ENTITIES:
                ranked.append((c, v))
    else:
        for r in sample_rows:
            entity = str(_lookup(r, x_col) or "").strip()
            if entity.lower() in SENTINEL_ENTITIES:
                continue
            try:
                val = float(_lookup(r, y_col) or 0)
                ranked.append((entity, val))
            except (ValueError, TypeError):
                pass

    if not ranked:
        for r in sample_rows:
            entity = str(_lookup(r, x_col) or (list(r.values())[0] if r else "")).strip()
            try:
                val = float(_lookup(r, y_col) or (list(r.values())[1] if len(r) > 1 else 0))
                ranked.append((entity, val))
            except (ValueError, TypeError):
                pass

    if ranked:
        # Sort descending by value so top_name is guaranteed highest plotted bar
        ranked.sort(key=lambda x: x[1], reverse=True)
        top_name, top_val = ranked[0]
        fmt_top = _format_metric_value(y_col, top_val)
        if len(ranked) > 1:
            second_name, second_val = ranked[1]
            fmt_sec = _format_metric_value(y_col, second_val)

            # Check for ties among top values
            tied_names = [r[0] for r in ranked if abs(r[1] - top_val) < 1e-6]
            if len(tied_names) > 1:
                if len(tied_names) == 2:
                    return f"{tied_names[0]} and {tied_names[1]} are tied for highest with {fmt_top} across {len(ranked)} observed categories."
                return f"{len(tied_names)} categories are tied for highest with {fmt_top} ({', '.join(tied_names[:3])}) across {len(ranked)} observed categories."

            if is_stacked:
                return f"{top_name} leads with highest stacked total of {fmt_top}, followed by {second_name} at {fmt_sec} across {len(ranked)} observed categories."

            return f"{top_name} ranks highest with {fmt_top}, followed by {second_name} at {fmt_sec} across {len(ranked)} observed categories."
        return f"{top_name} recorded {fmt_top}."

    if sample_rows:
        first_r = sample_rows[0]
        items = [f"{k.replace('_', ' ').title()}: {_format_metric_value(k, v)}" for k, v in list(first_r.items())[:3] if v is not None and str(v).strip() != ""]
        if items:
            return f"Observed {row_count} records. Top record: {', '.join(items)}."
    return f"Validated {row_count} records for '{title}' across observed dimensions."


def validate_and_reconcile_chart_insight(chart_config: Dict, query_result: Dict) -> Dict:
    """
    Automatic consistency check & reconciliation engine.
    Guarantees:
    1. Top-ranked category in insight matches the highest plotted bar.
    2. Displayed totals and averages reconcile with query result.
    3. Chronological sorting and time-series extremes reconcile.
    4. Series and categories have identical lengths.
    """
    opt = chart_config.get("echarts_option", {})
    if not isinstance(opt, dict) or opt.get("type") in ("kpi_card", "data_table"):
        return chart_config

    insight = chart_config.get("insight_summary", "")
    chart_type = chart_config.get("chart_type", "bar")
    x_axis = opt.get("xAxis")
    if isinstance(x_axis, list) and x_axis:
        x_axis = x_axis[0]
    categories = x_axis.get("data", []) if isinstance(x_axis, dict) else []

    series = opt.get("series", [])
    if not series or not isinstance(series[0], dict):
        return chart_config
    values = series[0].get("data", [])

    # Check 1: Length alignment
    if categories and values and len(categories) != len(values):
        min_len = min(len(categories), len(values))
        categories = categories[:min_len]
        values = values[:min_len]
        if isinstance(x_axis, dict):
            x_axis["data"] = categories
        series[0]["data"] = values

    # Check 2: Top-ranked bar matches insight
    if chart_type in ("bar", "histogram") and categories and values:
        pairs = list(zip(categories, values))
        highest_pair = max(pairs, key=lambda p: p[1])
        highest_cat = highest_pair[0]
        highest_val = highest_pair[1]

        # Check if insight cites the highest category as top-ranked
        top_match = re.search(r"^([A-Za-z0-9_\-\s&]+?)\s+(ranks highest|leads|contributes the most)", insight, re.IGNORECASE)
        top_cited_cat = top_match.group(1).strip() if top_match else ""

        is_mismatched = False
        if top_cited_cat and top_cited_cat.lower() != highest_cat.lower():
            is_mismatched = True
        elif highest_cat.lower() not in insight.lower():
            is_mismatched = True

        if is_mismatched:
            logger.warning(
                f"[{chart_config.get('query_id')}] Consistency mismatch: highest bar is '{highest_cat}' ({highest_val}), "
                f"but insight says '{insight}'. Reconciling insight directly from plotted chart."
            )
            reconciled_insight = generate_data_grounded_insight(
                {"title": chart_config.get("title", ""), "x_axis": "category", "y_axis": series[0].get("name", "value")},
                query_result,
                chart_type,
                echarts_option=opt
            )
            chart_config["insight_summary"] = reconciled_insight
            opt["_insight_summary"] = reconciled_insight

    # Check 3: Time-series chronological alignment
    if chart_type in ("line", "area") and categories and values:
        sorted_keys = sorted(categories, key=_parse_date_sort_key)
        if categories != sorted_keys:
            logger.warning(f"[{chart_config.get('query_id')}] Time series out of chronological order. Re-sorting.")
            c_pairs = sorted(zip(categories, values), key=lambda p: _parse_date_sort_key(p[0]))
            categories = [p[0] for p in c_pairs]
            values = [p[1] for p in c_pairs]
            if isinstance(x_axis, dict):
                x_axis["data"] = categories
            series[0]["data"] = values

            reconciled_insight = generate_data_grounded_insight(
                {"title": chart_config.get("title", ""), "x_axis": "period", "y_axis": series[0].get("name", "value")},
                query_result,
                chart_type,
                echarts_option=opt
            )
            chart_config["insight_summary"] = reconciled_insight
            opt["_insight_summary"] = reconciled_insight

    # Check 4: Benchmark consistency across subtitle, markLine, and insight
    if series and isinstance(series[0], dict):
        mark_line = series[0].get("markLine")
        if isinstance(mark_line, dict) and mark_line.get("data"):
            m_data = mark_line["data"]
            if isinstance(m_data, list) and m_data and isinstance(m_data[0], dict):
                ml_label = m_data[0].get("label", {}).get("formatter", "")
                sub = chart_config.get("subtitle", "")
                if sub and ("benchmark" in sub.lower() or "overall average" in sub.lower() or "compare" in sub.lower()):
                    ml_paren = re.search(r"\(([^)]+)\)", ml_label)
                    if ml_paren:
                        target_bench = ml_paren.group(1).strip()
                        sub_reconciled = re.sub(r"\(\$?[0-9,.]+[kKmM%]?\)", f"({target_bench})", sub)
                        chart_config["subtitle"] = sub_reconciled

    # Check 5: Title count reconciliation (e.g. "Top 10" in title when only 4 outliers exist)
    title = chart_config.get("title", "")
    n_items = len(categories) if categories else (len(values) if values else (query_result.get("row_count", 0) if isinstance(query_result, dict) else 0))
    if "top 10" in title.lower() and n_items != 10:
        if n_items > 0:
            new_title = re.sub(r"(?i)\btop\s+10\b", f"Top {n_items}", title)
            chart_config["title"] = new_title
            if isinstance(opt.get("title"), dict):
                opt["title"]["text"] = new_title

    # Check 6: Label normalization for raw all-caps or all-lowercase text
    if categories and all(isinstance(c, str) for c in categories):
        normalized_cats = []
        for c in categories:
            if c.isupper() and len(c) > 3:
                normalized_cats.append(c.title())
            elif c.islower() and " " in c:
                normalized_cats.append(c.title())
            else:
                normalized_cats.append(c)
        if normalized_cats != categories:
            if isinstance(x_axis, dict):
                x_axis["data"] = normalized_cats

    return chart_config


def split_kpi_cards(query_def: Dict, query_result: Dict, max_kpis: int = 6) -> List[Dict]:
    """
    Split a single KPI query result with multiple columns into multiple individual KPI card configs.
    Returns up to max_kpis KPI card entries (approximately 3–6 data-driven KPI cards).
    """
    col_names = query_result.get("column_names") or query_result.get("columns") or []
    data_rows = _get_query_data(query_result)
    if not data_rows:
        return []
    if not col_names and data_rows:
        col_names = list(data_rows[0].keys())

    row = data_rows[0]
    kpi_cards = []
    selected_cols = col_names[:max_kpis]
    w_size = 2 if len(selected_cols) >= 5 else (12 // max(1, len(selected_cols)))
    for col in selected_cols:
        val = row.get(col, 0)
        formatted = _format_metric_value(col, val)
        col_l = col.lower()
        if col_l.startswith("unique_"):
            base = col[7:].replace("_id", "s").replace("_key", "s").replace("_code", "s").replace("_uuid", "s")
            label = f"Unique {base.replace('_', ' ').title()}"
        elif col_l in ("valid_records", "clean_records", "cleaned_records"):
            label = "Valid Records"
        elif col_l in ("source_records", "raw_records", "initial_records"):
            label = "Source Records"
        elif col_l in ("excluded_records", "dropped_records", "invalid_records"):
            label = "Excluded Records"
        elif col_l == "total_records":
            label = "Total Records"
        else:
            label = col.replace("_", " ").title()
        kpi_id = f"{query_def.get('id', 'kpi')}_{col}"
        kpi_cards.append({
            "query_id": kpi_id,
            "chart_type": "kpi_card",
            "priority": query_def.get("priority", 1),
            "title": label,
            "subtitle": "",  # KPI cards: no insight paragraph
            "echarts_option": {
                "type": "kpi_card",
                "_chart_type": "kpi_card",
                "chart_type": "kpi_card",
                "value": formatted,
                "label": label,
                "raw_value": val
            },
            "layout": {"x": len(kpi_cards) * w_size, "y": 0, "w": w_size, "h": 2},
            "responsive": True,
        })
    return kpi_cards


def detect_dashboard_filters(column_metadata: List[Dict], max_filters: int = 3) -> List[Dict]:
    """
    Detect up to max_filters dynamic filter candidates from column metadata.
    Good candidates: categorical/dimension columns with low-medium cardinality.
    """
    candidates = []
    for cm_item in (column_metadata or []):
        cm = cm_item.model_dump() if hasattr(cm_item, "model_dump") else dict(cm_item) if hasattr(cm_item, "__dict__") else cm_item
        if not isinstance(cm, dict):
            continue
        sem_type = cm.get("semantic_type", "unknown")
        name = cm.get("name", "")
        unique_pct = cm.get("unique_pct", 1.0)
        dtype = cm.get("dtype", "").lower()
        is_pk = cm.get("is_primary_key", False)
        is_kpi = cm.get("is_candidate_kpi", False)
        samples = cm.get("sample_values", [])

        # Skip identifiers, PKs, KPIs, high-cardinality, text descriptions
        if is_pk or is_kpi:
            continue
        if sem_type in ("identifier", "text_description"):
            continue
        if sem_type in ("metric", "currency", "percentage"):
            continue

        # Skip numeric columns that aren't true categorical dimensions
        if any(t in dtype for t in ("int", "float", "double", "decimal", "numeric", "hugeint", "smallint", "tinyint")) and sem_type != "boolean":
            continue

        # Skip derived metrics or delay/difference/ratio/rate columns
        name_lower = name.lower()
        if any(pat in name_lower for pat in (
            "_per_", "_delay_", "_days", "_pct", "_ratio", "_rate", "_score", "_diff", "_amount", "_margin", "_total", "_count"
        )):
            continue

        # Skip contact / PII / free text columns (never show fax, phone, email, SSN, comments in executive filters)
        if any(pat in name_lower for pat in (
            "fax", "phone", "contact", "mobile", "email", "ssn", "uuid", "address",
            "street", "postal", "zip", "url", "link", "comment", "notes", "description"
        )):
            continue

        # Date columns are good filter candidates
        if sem_type == "date" or "date" in dtype:
            lbl = (cm.get("business_label") or "").strip() or name.replace("_", " ").title()
            candidates.append({
                "column": name,
                "label": lbl,
                "type": "date",
                "score": 0.6,
                "values": [],
            })
            continue

        # Dimension/geographic/categorical columns with reasonable cardinality
        if sem_type in ("dimension", "geographic", "boolean") or ("varchar" in dtype and unique_pct <= 0.35):
            score = 1.0 - unique_pct
            if unique_pct > 0.8:
                continue
            SENTINEL_VALUES = {"na", "n/a", "#n/a", "null", "none", "nan", "missing", "-", "--", "?", "", "undefined", "tbd", "unknown", "unspecified"}
            raw_unique_vals = list(dict.fromkeys(str(v).strip() for v in samples if v is not None and str(v).strip())) if samples else []
            unique_vals = [v for v in raw_unique_vals if v.lower() not in SENTINEL_VALUES]
            if len(unique_vals) < 2:
                continue
            lbl = (cm.get("business_label") or "").strip() or name.replace("_", " ").title()
            candidates.append({
                "column": name,
                "label": lbl,
                "type": "select",
                "score": score,
                "values": unique_vals,
            })

    # Sort by score (higher = better filter candidate) and take top N
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:max_filters]


def assign_grid_layout(chart_configs: List[Dict]) -> List[Dict]:
    """Assign 12-column grid positions based on chart type and priority."""
    # Separate KPIs from other charts
    kpi_configs = [c for c in chart_configs if c.get("chart_type") == "kpi_card"]
    other_configs = [c for c in chart_configs if c.get("chart_type") != "kpi_card"]

    # Sort non-KPIs by priority
    other_configs.sort(key=lambda c: c.get("priority", 3))

    # Assign KPI positions (row 0, up to 6 cards)
    current_y = 0
    num_kpis = min(len(kpi_configs), 6)
    w_size = 2 if num_kpis >= 5 else (12 // max(1, num_kpis))
    for i, kpi in enumerate(kpi_configs[:num_kpis]):
        kpi["layout"] = {"x": (i * w_size), "y": 0, "w": w_size, "h": 2}
    if kpi_configs:
        current_y = 2

    # Assign other chart positions
    col_x = 0
    for config in other_configs:
        chart_type = config.get("chart_type", "bar")
        priority = config.get("priority", 3)

        if chart_type == "data_table":
            w, h = 12, 5
        elif priority == 1:
            w, h = 12, 4
        elif priority == 2:
            w, h = 6, 4
        else:
            w, h = 6, 4

        if w == 12:
            if col_x > 0:
                current_y += 4
                col_x = 0
            config["layout"] = {"x": 0, "y": current_y, "w": 12, "h": h}
            current_y += h
        else:
            if col_x + w > 12:
                current_y += 4
                col_x = 0
            config["layout"] = {"x": col_x, "y": current_y, "w": w, "h": h}
            col_x += w

    return kpi_configs[:num_kpis] + other_configs


async def layout_node(state: AgentSwarmState, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Layout Agent node.
    - Profiles each analytics result
    - Selects chart types (rule-based + LLM)
    - Generates ECharts configurations
    - Splits KPI queries into individual cards
    - Detects dynamic filters from column metadata
    - Assigns dashboard grid layout
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    session_id = state_dict.get("session_id")
    logger.info(f"Starting layout_node for session {session_id}")

    try:
        from backend.agents.prompts import LAYOUT_CHART_SELECTION_PROMPT, LAYOUT_ECHARTS_CONFIG_PROMPT
    except ImportError as ie:
        logger.error(f"Layout prompts not found: {ie}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [
            {"agent": "layout", "error": "Layout prompts not imported", "recoverable": False}
        ]
        return AgentSwarmState(**state_dict)

    ctx = slice_context(state, "layout")
    generated_queries: List[Dict] = ctx.get("generated_queries", [])
    query_results: List[Dict] = ctx.get("query_results", [])
    raw_query: str = ctx.get("raw_query", "")
    business_domain: str = ctx.get("business_domain", "unknown")
    column_metadata: List[Dict] = ctx.get("column_metadata", [])

    if not generated_queries or not query_results:
        logger.warning("Layout node: no queries/results - generating default summary dashboard")
        raw_rows = state_dict.get("raw_row_count") or state_dict.get("rows_after") or 0
        raw_cols = state_dict.get("raw_col_count") or len(column_metadata)
        dq_score = state_dict.get("data_quality_score", 1.0)

        metrics = [
            {"label": "Total Records", "value": f"{raw_rows:,}" if raw_rows else "—", "raw_value": raw_rows},
            {"label": "Analyzed Columns", "value": str(raw_cols), "raw_value": raw_cols},
            {"label": "Data Quality", "value": f"{int(dq_score * 100)}%", "raw_value": dq_score},
            {"label": "Pipeline Status", "value": "Validated", "raw_value": 1},
        ]

        fallback_kpi = {
            "query_id": "fallback_kpi_summary",
            "title": "Dataset Key Metrics",
            "subtitle": f"Overview across {raw_cols} columns",
            "chart_type": "kpi_card",
            "echarts_option": {
                "type": "kpi_card",
                "metrics": metrics
            },
            "insight_summary": f"Executive summary covering {raw_rows:,} records across {raw_cols} profiled dimensions.",
            "priority": 1,
            "responsive": True,
            "layout": {"x": 0, "y": 0, "w": 12, "h": 2}
        }

        state_dict["dashboard_config"] = [fallback_kpi]
        state_dict["dashboard_title"] = f"{business_domain.title() if business_domain != 'unknown' else 'Dataset'} Executive Overview"
        state_dict["layout_rationale"] = f"Automated executive dashboard summarizing {raw_rows} records."
        state_dict["dashboard_filters"] = detect_dashboard_filters(column_metadata)
        state_dict["pipeline_status"] = "layouting"
        state_dict["current_agent"] = "layout"
        return AgentSwarmState(**state_dict)

    # Detect dynamic filters from column metadata
    dashboard_filters = detect_dashboard_filters(column_metadata)
    try:
        import duckdb
        eng = duckdb.connect()
        tbl_ready = False
        try:
            eng.execute("SELECT 1 FROM data LIMIT 1")
            tbl_ready = True
        except Exception:
            pass
        if not tbl_ready:
            enr_path = state_dict.get("enriched_path") or state_dict.get("clean_path")
            if enr_path and os.path.exists(enr_path):
                norm_p = enr_path.replace("\\", "/")
                eng.execute(f"CREATE TEMP VIEW data AS SELECT * FROM read_parquet('{norm_p}')")
                tbl_ready = True
        if tbl_ready:
            SENTINEL_VALUES = {"na", "n/a", "#n/a", "null", "none", "nan", "missing", "-", "--", "?", "", "undefined", "tbd", "unknown", "unspecified"}
            for f in dashboard_filters:
                if f.get("type") == "select":
                    c_name = f["column"]
                    try:
                        res = eng.execute(f'SELECT DISTINCT "{c_name}" FROM data WHERE "{c_name}" IS NOT NULL AND TRIM(CAST("{c_name}" AS VARCHAR)) != \'\' LIMIT 25').fetchall()
                        clean_vals = [str(r[0]).strip() for r in res if r[0] is not None and str(r[0]).strip().lower() not in SENTINEL_VALUES]
                        if len(clean_vals) >= 2:
                            f["values"] = sorted(clean_vals)
                    except Exception as fe:
                        logger.debug(f"Filter value enrichment error for {c_name}: {fe}")
        eng.close()
    except Exception as e:
        logger.debug(f"Could not enrich filter values from DuckDB: {e}")
    logger.info(f"Detected {len(dashboard_filters)} dashboard filters: {[f['column'] for f in dashboard_filters]}")

    results_by_id: Dict[str, Dict] = {qr.get("query_id"): qr for qr in query_results}
    chart_configs_raw: List[Dict] = []

    for q_dict in generated_queries:
        q_id = q_dict.get("id", "")
        q_result = results_by_id.get(q_id)
        if not q_result or q_result.get("error"):
            logger.warning(f"Skipping layout for {q_id}: no result or error")
            continue

        profile = profile_query_result(q_dict, q_result)
        logger.info(f"[{q_id}] Profile: purpose={profile['analytical_purpose']}, x_type={profile['x_axis_type']}, rows={profile['row_count']}")

        # Step 1: Rule-based chart type pre-selection
        chart_type = rule_based_chart_type(profile)

        # Step 2: KPI splitting — if kpi_card with multiple columns, split into individual cards
        if chart_type == "kpi_card" and len(profile["column_names"]) > 1:
            kpi_cards = split_kpi_cards(q_dict, q_result, max_kpis=6)
            chart_configs_raw.extend(kpi_cards)
            logger.info(f"[{q_id}] Split into {len(kpi_cards)} KPI cards")
            continue

        # Step 3: LLM chart selection if rules didn't decide
        if chart_type is None:
            user_msg = (
                f"Query title: {profile['query_title']}\n"
                f"Business question: {profile['business_question']}\n"
                f"Analytical purpose: {profile['analytical_purpose']}\n"
                f"Columns: {profile['column_names']}\n"
                f"Row count: {profile['row_count']}\n"
                f"X axis: {profile['x_axis']} (type: {profile['x_axis_type']})\n"
                f"Y axis: {profile['y_axis']} (type: {profile['y_axis_type']})\n"
                f"Group by: {profile['group_by']} ({profile['group_count']} distinct values)\n"
                f"Sample data: {json.dumps(profile['sample_rows'][:2])}"
            )
            try:
                response = await llm_router.route(
                    task_type=TaskType.CHART_SELECTION,
                    messages=[
                        {"role": "system", "content": LAYOUT_CHART_SELECTION_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=1000,
                )
                parsed = parse_json_response(response["content"])
                llm_chart = parsed.get("chart_type", "bar").lower().strip()
                if llm_chart not in VALID_CHART_TYPES:
                    llm_chart = "bar"
                chart_type = llm_chart
                logger.info(f"[{q_id}] LLM chart type: {chart_type} -- {parsed.get('reasoning', '')}")
            except Exception as e:
                logger.error(f"[{q_id}] Chart selection LLM failed: {e} -- using bar fallback")
                chart_type = "bar"

        # Step 4: Post-LLM correction
        chart_type = correct_chart_type(chart_type, profile)
        logger.info(f"[{q_id}] Final chart type: {chart_type}")

        # Step 5: Generate ECharts configuration
        echarts_option: Optional[Dict] = None
        config_user_msg = (
            f"Query: {q_dict.get('title', '')}\n"
            f"Business question: {q_dict.get('business_question', '')}\n"
            f"Chart type: {chart_type}\n"
            f"X axis column: {q_dict.get('x_axis')} (type: {profile['x_axis_type']})\n"
            f"Y axis column: {q_dict.get('y_axis')} (type: {profile['y_axis_type']})\n"
            f"Group by: {q_dict.get('group_by')}\n"
            f"Column names: {q_result.get('column_names', [])}\n"
            f"Row count: {q_result.get('row_count', 0)}\n"
            f"Sample rows (up to 5): {json.dumps(q_result.get('sample_rows', []))}"
        )
        try:
            config_response = await llm_router.route(
                task_type=TaskType.ECHARTS_CONFIG,
                messages=[
                    {"role": "system", "content": LAYOUT_ECHARTS_CONFIG_PROMPT},
                    {"role": "user", "content": config_user_msg},
                ],
                response_format={"type": "json_object"},
                max_tokens=3000,
            )
            parsed_config = parse_json_response(config_response["content"])
            if isinstance(parsed_config, dict):
                echarts_option = parsed_config
        except Exception as e:
            logger.error(f"[{q_id}] ECharts config generation failed: {e}")

        # Step 6: Validate
        if echarts_option is not None:
            is_valid, err_msg = validate_echarts_option(echarts_option, chart_type)
            if not is_valid:
                logger.warning(f"[{q_id}] Invalid ECharts config ({err_msg}), using fallback")
                echarts_option = None

        # Step 7: Fallback or sanitize
        if echarts_option is None:
            echarts_option = build_fallback_config(q_dict, q_result, chart_type)
            logger.info(f"[{q_id}] Using fallback ECharts config")
        else:
            echarts_option = sanitize_echarts_option(
                echarts_option,
                sample_rows=_get_query_data(q_result),
                x_col=q_dict.get("x_axis"),
                y_col=q_dict.get("y_axis"),
                chart_type=chart_type,
            )

        if isinstance(echarts_option, dict):
            echarts_option["_chart_type"] = chart_type

        grounded_insight = generate_data_grounded_insight(q_dict, q_result, chart_type, echarts_option=echarts_option)
        chart_item = {
            "query_id": q_id,
            "chart_type": chart_type,
            "priority": q_dict.get("priority", 3),
            "title": q_dict.get("title", ""),
            "subtitle": q_dict.get("business_question", ""),
            "insight_summary": grounded_insight,
            "echarts_option": echarts_option,
            "responsive": True,
        }
        chart_item = validate_and_reconcile_chart_insight(chart_item, q_result)

        # Guard against empty / flat 0.0 visualizations on non-KPI charts
        if chart_type != "kpi_card":
            q_rows = _get_query_data(q_result)
            if not q_rows:
                logger.warning(f"Excluding empty chart '{chart_item['title']}': query returned 0 rows.")
                continue

            # Check if all plotted numeric values are 0 or empty
            opt_series = echarts_option.get("series", []) if isinstance(echarts_option, dict) else []
            if opt_series and isinstance(opt_series, list):
                s0_data = opt_series[0].get("data", [])
                if isinstance(s0_data, list) and s0_data:
                    num_vals = []
                    for item in s0_data:
                        v = item.get("value") if isinstance(item, dict) else item
                        if isinstance(v, (int, float)):
                            num_vals.append(v)
                    if num_vals and all(v == 0.0 or v == 0 for v in num_vals):
                        logger.warning(f"Excluding flat 0.0 chart '{chart_item['title']}': all {len(num_vals)} categories are 0.0.")
                        continue

        chart_configs_raw.append(chart_item)

    # Step 8: Assign grid layout
    if chart_configs_raw:
        chart_configs_raw = assign_grid_layout(chart_configs_raw)

    # Step 9: Build final ChartConfig objects
    final_configs: List[Dict] = []
    for cc in chart_configs_raw:
        try:
            # Store insight_summary inside echarts_option for frontend access
            if isinstance(cc.get("echarts_option"), dict):
                cc["echarts_option"]["_insight_summary"] = cc.get("insight_summary", "")
                cc["echarts_option"]["_analytical_purpose"] = cc.get("chart_type", "")
                cc["echarts_option"]["_chart_type"] = cc.get("chart_type", "")
                cc["echarts_option"]["chart_type"] = cc.get("chart_type", "")

            cfg = ChartConfig(
                query_id=cc["query_id"],
                chart_type=cc.get("chart_type"),
                echarts_option=cc["echarts_option"],
                layout=cc.get("layout", {"x": 0, "y": 0, "w": 12, "h": 4}),
                title=cc["title"],
                subtitle=cc.get("subtitle", ""),
                insight_summary=cc.get("insight_summary", ""),
                responsive=True,
            )
            final_configs.append(cfg.model_dump())
        except Exception as e:
            logger.error(f"Failed to build ChartConfig for {cc.get('query_id')}: {e}")

    # Dashboard title
    dashboard_title = f"BI Dashboard — {business_domain.replace('_', ' ').title()}"
    if raw_query:
        short_q = raw_query[:60] + ("..." if len(raw_query) > 60 else "")
        dashboard_title = f"Analysis: {short_q}"

    kpi_count = sum(1 for cc in chart_configs_raw if cc.get("chart_type") == "kpi_card")
    chart_types_used = set(cc.get("chart_type", "bar") for cc in chart_configs_raw)
    layout_rationale = (
        f"Generated {len(final_configs)} visualizations for {len(generated_queries)} queries. "
        f"{kpi_count} KPI cards, chart types: {', '.join(chart_types_used)}. "
        f"{len(dashboard_filters)} dynamic filters detected."
    )

    state_dict["dashboard_config"] = final_configs
    state_dict["dashboard_title"] = dashboard_title
    state_dict["dashboard_filters"] = dashboard_filters
    state_dict["layout_rationale"] = layout_rationale
    state_dict["pipeline_status"] = "layouting"
    state_dict["current_agent"] = "layout"

    logger.info(f"Layout node complete. {len(final_configs)} ChartConfigs, {len(dashboard_filters)} filters.")
    return AgentSwarmState(**state_dict)
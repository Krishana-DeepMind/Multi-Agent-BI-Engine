import logging
import os
import re
from typing import Dict, Any, List, Optional

from backend.core.state import AgentSwarmState, QueryDefinition, QueryResult
from backend.core.context_slicer import slice_context
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import ANALYTICS_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Valid chart_type values from the Pydantic model
VALID_CHART_TYPES = {"bar", "line", "scatter", "pie", "heatmap", "kpi_card", "data_table", "area", "funnel"}

# Valid analytical_purpose values from the Pydantic model
VALID_ANALYTICAL_PURPOSES = {
    "kpi", "trend", "comparison", "composition", "relationship",
    "distribution", "ranking", "breakdown", "anomaly", "detail"
}


def _normalize_analytical_purpose(
    raw: Optional[str],
    sql: str = "",
    title: str = "",
    chart_type: str = "",
    priority: int = 3
) -> str:
    """
    Normalize analytical_purpose to one of the 10 canonical values:
    kpi, trend, comparison, composition, relationship, distribution, ranking, breakdown, anomaly, detail.
    Infers from SQL structure, title, chart_type, and priority if not provided or unrecognized.
    """
    if raw and isinstance(raw, str):
        normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
        # Direct match
        if normalized in VALID_ANALYTICAL_PURPOSES:
            return normalized

        # Map common aliases/variations
        purpose_map = {
            "primary_kpi": "kpi",
            "kpi_card": "kpi",
            "kpi_metric": "kpi",
            "metric": "kpi",
            "summary": "kpi",
            "time_trend": "trend",
            "time_series": "trend",
            "temporal": "trend",
            "trend_analysis": "trend",
            "trend_segmentation": "trend",
            "benchmark": "comparison",
            "benchmark_comparison": "comparison",
            "comparative": "comparison",
            "compare": "comparison",
            "vs": "comparison",
            "share": "composition",
            "proportion": "composition",
            "part_to_whole": "composition",
            "correlation": "relationship",
            "association": "relationship",
            "spread": "distribution",
            "frequency": "distribution",
            "histogram": "distribution",
            "top_n": "ranking",
            "top": "ranking",
            "bottom": "ranking",
            "order": "ranking",
            "rank": "ranking",
            "segmentation": "breakdown",
            "breakdown_segmentation": "breakdown",
            "grouping": "breakdown",
            "categorical": "breakdown",
            "outlier": "anomaly",
            "anomaly_detection": "anomaly",
            "anomalies": "anomaly",
            "tabular": "detail",
            "raw_data": "detail",
            "table": "detail",
        }
        if normalized in purpose_map:
            return purpose_map[normalized]
        for key, val in purpose_map.items():
            if key in normalized:
                return val

    # Fallback inference from chart_type, title, SQL, and priority
    sql_upper = sql.upper() if sql else ""
    title_upper = title.upper() if title else ""

    if chart_type == "kpi_card":
        return "kpi"
    if chart_type == "scatter":
        return "relationship"
    if chart_type in ["pie", "funnel"]:
        return "composition"

    if any(kw in title_upper or kw in sql_upper for kw in ["ANOMALY", "OUTLIER", "UNUSUAL", "DISCREPANCY"]):
        return "anomaly"
    if any(kw in title_upper for kw in ["TOP", "BOTTOM", "HIGHEST", "LOWEST", "RANK"]) and "ORDER BY" in sql_upper:
        return "ranking"

    time_keywords = {"DATE_TRUNC", "YEAR", "MONTH", "QUARTER", "WEEK", "JOINING_DATE", "TREND"}
    if any(kw in sql_upper or kw in title_upper for kw in time_keywords):
        return "trend"

    if any(kw in title_upper or kw in sql_upper for kw in ["BENCHMARK", "COMPARE", "VERSUS", "AVERAGE"]):
        return "comparison"

    if any(kw in title_upper for kw in ["SHARE", "PROPORTION", "PERCENTAGE", "RATIO"]):
        return "composition"

    # Infer by priority aligned with prompt guidance
    if priority == 1:
        return "kpi"
    elif priority == 2:
        return "trend"
    elif priority == 3:
        return "breakdown"
    elif priority == 4:
        return "anomaly"
    elif priority == 5:
        return "comparison"

    if "GROUP BY" in sql_upper:
        return "breakdown"

    return "detail"


def _normalize_chart_type(raw: str) -> str:
    """Normalize chart_type to a valid literal. Falls back to 'data_table'."""
    if not raw:
        return "data_table"
    normalized = raw.strip().lower().replace(" ", "_").replace("-", "_")
    # Common aliases
    aliases = {
        "kpi": "kpi_card",
        "card": "kpi_card",
        "table": "data_table",
        "datatable": "data_table",
        "barchart": "bar",
        "linechart": "line",
        "areachart": "area",
        "piechart": "pie",
        "scatterplot": "scatter",
        "heat_map": "heatmap",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in VALID_CHART_TYPES else "data_table"


def _infer_axes_from_sql(sql: str, result_columns: List[str]) -> Dict[str, Optional[str]]:
    """
    Smart fallback: infer x_axis, y_axis, group_by from SQL structure and result columns.
    Returns dict with 'x_axis', 'y_axis', 'group_by', 'chart_type_hint'.
    """
    sql_upper = sql.upper()
    inferred = {"x_axis": None, "y_axis": None, "group_by": None, "chart_type_hint": None}

    # Detect if it's a scalar/KPI query (no GROUP BY, single row expected)
    has_group_by = "GROUP BY" in sql_upper
    has_agg = any(fn in sql_upper for fn in ["COUNT(", "SUM(", "AVG(", "MIN(", "MAX(", "MEDIAN(", "ROUND(AVG", "ROUND(SUM"])

    if has_agg and not has_group_by:
        # Scalar KPI query — no axes needed
        inferred["chart_type_hint"] = "kpi_card"
        return inferred

    if has_group_by:
        # Extract GROUP BY columns
        gb_match = re.search(r'GROUP\s+BY\s+(.+?)(?:\s+ORDER\s+BY|\s+HAVING|\s+LIMIT|\s*$)', sql_upper, re.DOTALL)
        if gb_match:
            gb_raw = gb_match.group(1).strip()
            # Split by comma, handling positional references like "1, 2"
            gb_parts = [p.strip() for p in gb_raw.split(",")]

            # Try to resolve positional references (GROUP BY 1, 2) from result columns
            resolved_dims = []
            for part in gb_parts:
                if part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < len(result_columns):
                        resolved_dims.append(result_columns[idx])
                else:
                    # Remove any alias-like stuff, take the column name
                    clean = part.strip().strip('"').strip("'")
                    # Check if it matches a result column
                    for rc in result_columns:
                        if rc.upper() == clean or clean.endswith(rc.upper()):
                            resolved_dims.append(rc)
                            break
                    else:
                        resolved_dims.append(clean)

            if resolved_dims:
                inferred["x_axis"] = resolved_dims[0]
                if len(resolved_dims) > 1:
                    inferred["group_by"] = resolved_dims[1]

            # y_axis = first aggregated column not in the GROUP BY dimensions
            dim_set = {d.upper() for d in resolved_dims}
            for col in result_columns:
                if col.upper() not in dim_set:
                    inferred["y_axis"] = col
                    break

    # Infer chart_type_hint from structure
    if has_group_by:
        # Check for time-based x_axis
        time_keywords = {"DATE_TRUNC", "YEAR", "MONTH", "QUARTER", "WEEK"}
        if any(kw in sql_upper for kw in time_keywords):
            inferred["chart_type_hint"] = "line"
        else:
            inferred["chart_type_hint"] = "bar"

    return inferred


def _normalize_query_dict(q_dict: dict, idx: int, result_columns: List[str] = None) -> dict:
    """
    Normalize a raw LLM query dict by extracting all semantic fields with
    robust key aliasing and SQL-based inference fallbacks.
    """
    # --- ID ---
    q_id = (q_dict.get("id") or q_dict.get("query_id") or f"q_{idx+1}").strip()

    # --- Title ---
    title = (
        q_dict.get("title")
        or q_dict.get("query_title")
        or q_dict.get("query_intent")
        or (q_dict.get("business_value", "")[:60] if q_dict.get("business_value") else None)
        or f"Query {q_id}"
    ).strip()

    # --- Business Question ---
    business_question = (
        q_dict.get("business_question")
        or q_dict.get("question")
        or q_dict.get("business_value")
        or q_dict.get("query_intent")
        or q_dict.get("description")
        or ""
    ).strip()

    # --- SQL ---
    sql = (q_dict.get("sql") or q_dict.get("query") or q_dict.get("sql_query") or "").strip().rstrip(";")

    # --- Axis & Grouping ---
    x_axis = q_dict.get("x_axis")
    y_axis = q_dict.get("y_axis")
    group_by = q_dict.get("group_by")
    chart_type_raw = q_dict.get("chart_type") or q_dict.get("visualization") or q_dict.get("viz_type") or ""

    # --- SQL-based inference fallback ---
    if sql and result_columns and (not x_axis and not y_axis):
        inferred = _infer_axes_from_sql(sql, result_columns)
        if not x_axis and inferred["x_axis"]:
            x_axis = inferred["x_axis"]
        if not y_axis and inferred["y_axis"]:
            y_axis = inferred["y_axis"]
        if not group_by and inferred["group_by"]:
            group_by = inferred["group_by"]
        if (not chart_type_raw or chart_type_raw == "data_table") and inferred["chart_type_hint"]:
            chart_type_raw = inferred["chart_type_hint"]

    # --- Chart Type normalization ---
    chart_type = _normalize_chart_type(chart_type_raw)

    # --- Insight Summary ---
    insight_summary = (
        q_dict.get("insight_summary")
        or q_dict.get("insight")
        or q_dict.get("summary")
        or q_dict.get("business_value")
        or q_dict.get("description")
        or ""
    ).strip()

    # --- Priority ---
    try:
        priority = int(q_dict.get("priority", 3))
        priority = max(1, min(5, priority))  # Clamp to valid range
    except (ValueError, TypeError):
        priority = 3

    # --- Analytical Purpose ---
    analytical_purpose_raw = (
        q_dict.get("analytical_purpose")
        or q_dict.get("purpose")
        or q_dict.get("analysis_type")
        or q_dict.get("intent")
    )
    analytical_purpose = _normalize_analytical_purpose(
        analytical_purpose_raw,
        sql=sql,
        title=title,
        chart_type=chart_type,
        priority=priority
    )

    return {
        "id": q_id,
        "title": title,
        "business_question": business_question,
        "analytical_purpose": analytical_purpose,
        "sql": sql,
        "x_axis": x_axis if x_axis else None,
        "y_axis": y_axis if y_axis else None,
        "group_by": group_by if group_by else None,
        "chart_type": chart_type,
        "insight_summary": insight_summary,
        "priority": priority,
    }


def generate_generic_analytics_queries(
    db_engine: DuckDBEngine,
    column_metadata: List[Any],
    table_name: str = "data",
    rows_before: Optional[int] = None,
    rows_after: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Generate 5 to 7 robust DuckDB analytical queries dynamically based on
    the actual active table schema, column types, and distributions.
    Works for ANY dataset domain without hardcoded assumptions.
    """
    tbl = table_name or db_engine.current_table or "data"
    
    try:
        desc_res = db_engine.conn.execute(f"DESCRIBE {tbl}").fetchall()
        col_types = {row[0]: row[1].upper() for row in desc_res}
    except Exception:
        col_types = {}

    available_cols = list(col_types.keys())
    if not available_cols:
        return []

    meta_by_name = {}
    for cm in column_metadata:
        c_name = cm.get("name") if isinstance(cm, dict) else getattr(cm, "name", "")
        if c_name:
            meta_by_name[c_name.lower()] = cm

    date_cols = []
    metric_cols = []
    dim_cols = []

    id_cols = []
    for c in available_cols:
        cl = c.lower()
        cm = meta_by_name.get(cl)
        sem_type = (cm.get("semantic_type") if isinstance(cm, dict) else getattr(cm, "semantic_type", "")).lower() if cm else ""
        is_pk = (cm.get("is_primary_key") if isinstance(cm, dict) else getattr(cm, "is_primary_key", False)) if cm else False
        dtype = col_types.get(c, "")

        if is_pk or cl == "id" or cl.endswith(("_id", "id_", "_key", "_code", "_uuid")) or sem_type == "identifier":
            id_cols.append(c)
            continue

        is_geographic = (
            sem_type == "geographic"
            or any(g in cl for g in ("latitude", "longitude", "coord", "postal", "zip"))
            or cl in ("lat", "lon", "lng")
        )
        if is_geographic:
            dim_cols.append(c)
            continue

        is_physically_numeric = any(t in dtype for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL"))
        is_semantic_metric = sem_type in ("metric", "currency", "percentage") or (cm and (cm.get("is_candidate_kpi") if isinstance(cm, dict) else getattr(cm, "is_candidate_kpi", False)))

        if sem_type == "date" or any(dt in dtype for dt in ("DATE", "TIME", "TIMESTAMP")) or any(k in cl for k in ("date", "time", "timestamp")):
            date_cols.append(c)
        elif is_physically_numeric or is_semantic_metric:
            metric_cols.append(c)
        elif sem_type in ("dimension", "geographic", "boolean") or "VARCHAR" in dtype:
            dim_cols.append(c)

    # Prioritize business metrics (sales, revenue, profit, amount, bill, salary, cost, quantity)
    def _metric_priority(col_name: str) -> int:
        cn = col_name.lower()
        if any(k in cn for k in ("revenue", "sales", "net_revenue", "effective_bill", "total_amount", "amount", "profit")):
            return 1
        if any(k in cn for k in ("salary", "compensation", "wage", "bill", "cost", "spend", "budget")):
            return 2
        if any(k in cn for k in ("quantity", "orders", "volume", "units", "count", "headcount", "patients")):
            return 3
        if any(k in cn for k in ("price", "margin", "discount", "bonus", "rating", "score")):
            return 4
        return 5

    metric_cols.sort(key=_metric_priority)

    queries = []

    # 1. Primary Headline KPI Card (select 2 to 5 diverse metrics to yield approximately 3-6 KPI cards)
    unique_entity_measures = []
    if id_cols:
        primary_id = id_cols[0]
        id_clean = primary_id.lower().replace("_id", "s").replace("id_", "").replace("_key", "s").replace("_code", "s").strip()
        if not id_clean.endswith("s"):
            id_clean += "s"
        unique_entity_measures.append(f'COUNT(DISTINCT "{primary_id}") AS "unique_{id_clean}"')

    kpi_measures = []
    for m in metric_cols:
        m_lower = m.lower()
        if any(bad in m_lower for bad in ("id", "code", "zip", "key", "year", "month", "day", "ssn", "phone", "license", "lat", "latitude", "lon", "long", "longitude", "coord")):
            continue
        m_dtype = col_types.get(m, "")
        is_phys_num = any(t in m_dtype for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL"))
        m_expr = f'"{m}"' if is_phys_num else f'TRY_CAST("{m}" AS DOUBLE)'

        # Monetary/volume totals
        if any(k in m_lower for k in ("revenue", "sales", "spend", "cost", "budget", "payroll", "volume", "amount")):
            kpi_measures.append(f'ROUND(SUM(COALESCE({m_expr}, 0)), 0) AS "total_{m}"')
        elif any(k in m_lower for k in ("margin", "profit_margin")):
            rev_candidate = next((c for c in metric_cols if any(k in c.lower() for k in ("total_order_value", "revenue", "sales"))), None)
            profit_candidate = next((c for c in metric_cols if "profit" in c.lower() and not any(k in c.lower() for k in ("margin", "pct", "rate"))), None)
            if rev_candidate and profit_candidate:
                kpi_measures.append(f'ROUND(SUM("{profit_candidate}") / NULLIF(SUM("{rev_candidate}"), 0) * 100, 2) AS "profit_margin_pct"')
            else:
                kpi_measures.append(f'ROUND(AVG({m_expr}), 1) AS "avg_{m}"')
        elif any(k in m_lower for k in ("pct", "percent", "rate", "ratio", "score")):
            kpi_measures.append(f'ROUND(AVG({m_expr}), 1) AS "avg_{m}"')
        else:
            kpi_measures.append(f'ROUND(AVG({m_expr}), 1) AS "avg_{m}"')
        if len(kpi_measures) >= 5:
            break

    # If fewer than 2 measures were found, try any remaining numeric columns
    if len(kpi_measures) < 2:
        for m in metric_cols:
            if not any(f'"{m}"' in km for km in kpi_measures):
                m_dtype = col_types.get(m, "")
                is_phys_num = any(t in m_dtype for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL"))
                m_expr = f'"{m}"' if is_phys_num else f'TRY_CAST("{m}" AS DOUBLE)'
                kpi_measures.append(f'ROUND(AVG({m_expr}), 1) AS "avg_{m}"')
            if len(kpi_measures) >= 4:
                break

    if rows_before and rows_after and rows_before > rows_after:
        base_count = f"{rows_before} AS source_records, COUNT(*) AS valid_records, ({rows_before} - COUNT(*)) AS excluded_records"
    else:
        base_count = "COUNT(*) AS total_records"

    prefix_str = (", " + ", ".join(unique_entity_measures)) if unique_entity_measures else ""
    measures_str = (", " + ", ".join(kpi_measures)) if kpi_measures else ""
    queries.append({
        "id": "headline_kpi_summary",
        "title": "Headline Key Performance Indicators",
        "business_question": "What are the overall headline metrics and aggregate totals across the dataset?",
        "analytical_purpose": "kpi",
        "sql": f'SELECT {base_count}{prefix_str}{measures_str} FROM {tbl}',
        "x_axis": None,
        "y_axis": None,
        "group_by": None,
        "chart_type": "kpi_card",
        "insight_summary": "Headline performance summary across all observed records.",
        "priority": 1
    })

    def _metric_expr(col_name: str) -> str:
        dt = col_types.get(col_name, "")
        if any(t in dt for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")):
            return f'"{col_name}"'
        return f'TRY_CAST("{col_name}" AS DOUBLE)'

    def _date_expr(col_name: str) -> str:
        dt = col_types.get(col_name, "")
        if any(t in dt for t in ("DATE", "TIMESTAMP", "TIME")):
            return f'"{col_name}"'
        return f'TRY_CAST("{col_name}" AS DATE)'

    # 2. Time Trend (if date column exists)
    if date_cols:
        d_col = date_cols[0]
        if metric_cols:
            m_col = metric_cols[0]
            m_col_lower = m_col.lower()
            if any(k in m_col_lower for k in ("revenue", "sales", "spend", "cost", "budget", "payroll", "volume", "amount", "total")):
                trend_sql = f'SELECT STRFTIME(DATE_TRUNC(\'month\', {_date_expr(d_col)}), \'%Y-%m\') AS period, ROUND(SUM(COALESCE({_metric_expr(m_col)}, 0)), 2) AS "total_{m_col}", COUNT(*) AS record_count FROM {tbl} WHERE "{d_col}" IS NOT NULL GROUP BY 1 ORDER BY 1 ASC'
                y_ax = f"total_{m_col}"
            else:
                trend_sql = f'SELECT STRFTIME(DATE_TRUNC(\'month\', {_date_expr(d_col)}), \'%Y-%m\') AS period, ROUND(AVG({_metric_expr(m_col)}), 2) AS "avg_{m_col}", COUNT(*) AS record_count FROM {tbl} WHERE "{d_col}" IS NOT NULL GROUP BY 1 ORDER BY 1 ASC'
                y_ax = f"avg_{m_col}"
        else:
            trend_sql = f'SELECT STRFTIME(DATE_TRUNC(\'month\', {_date_expr(d_col)}), \'%Y-%m\') AS period, COUNT(*) AS record_count FROM {tbl} WHERE "{d_col}" IS NOT NULL GROUP BY 1 ORDER BY 1 ASC'
            y_ax = "record_count"

        queries.append({
            "id": f"time_trend_{d_col}",
            "title": "Chronological Progression Over Time",
            "business_question": f"How do observed metrics evolve across chronological time intervals based on {d_col}?",
            "analytical_purpose": "trend",
            "sql": trend_sql,
            "x_axis": "period",
            "y_axis": y_ax,
            "group_by": None,
            "chart_type": "line",
            "insight_summary": "Chronological trend tracking metric evolution over time.",
            "priority": 2
        })

    # 3. Categorical Breakdown (Bar)
    if dim_cols:
        dim = dim_cols[0]
        if metric_cols:
            m_col = metric_cols[0]
            bd_sql = f'SELECT COALESCE("{dim}", \'Unassigned / Missing\') AS "{dim}", ROUND(SUM({_metric_expr(m_col)}), 2) AS "total_{m_col}", COUNT(*) AS record_count FROM {tbl} GROUP BY "{dim}" ORDER BY 2 DESC LIMIT 10'
            y_ax = f"total_{m_col}"
        else:
            bd_sql = f'SELECT COALESCE("{dim}", \'Unassigned / Missing\') AS "{dim}", COUNT(*) AS record_count FROM {tbl} GROUP BY "{dim}" ORDER BY 2 DESC LIMIT 10'
            y_ax = "record_count"

        queries.append({
            "id": f"breakdown_by_{dim}",
            "title": f"Performance Breakdown by {dim.replace('_', ' ').title()}",
            "business_question": f"How are key metrics distributed across categories in {dim}?",
            "analytical_purpose": "breakdown",
            "sql": bd_sql,
            "x_axis": dim,
            "y_axis": y_ax,
            "group_by": None,
            "chart_type": "bar",
            "insight_summary": f"Distribution across top {dim} categories.",
            "priority": 3
        })

    # 4. Composition (Donut / Pie)
    if dim_cols:
        comp_dim = dim_cols[1] if len(dim_cols) > 1 else dim_cols[0]
        queries.append({
            "id": f"composition_by_{comp_dim}",
            "title": f"Proportional Distribution by {comp_dim.replace('_', ' ').title()}",
            "business_question": f"What is the relative proportion of each category in {comp_dim}?",
            "analytical_purpose": "composition",
            "sql": f'SELECT "{comp_dim}", COUNT(*) AS record_count FROM {tbl} WHERE "{comp_dim}" IS NOT NULL AND "{comp_dim}" NOT IN (\'Unknown\', \'Unspecified\', \'N/A\') GROUP BY "{comp_dim}" ORDER BY record_count DESC LIMIT 6',
            "x_axis": comp_dim,
            "y_axis": "record_count",
            "group_by": None,
            "chart_type": "donut",
            "insight_summary": f"Part-to-whole categorical distribution across {comp_dim}.",
            "priority": 3
        })

    # 5. Relationship (Scatter) or Distribution (Histogram / Bar)
    if len(metric_cols) >= 2:
        m1, m2 = metric_cols[0], metric_cols[1]
        grp = dim_cols[0] if dim_cols else None
        grp_col = f', "{grp}"' if grp else ""
        queries.append({
            "id": f"correlation_{m1}_vs_{m2}",
            "title": f"Correlation: {m1.replace('_', ' ').title()} vs {m2.replace('_', ' ').title()}",
            "business_question": f"Is there a statistical relationship between {m1} and {m2}?",
            "analytical_purpose": "relationship",
            "sql": f'SELECT "{m1}", "{m2}"{grp_col} FROM {tbl} WHERE "{m1}" IS NOT NULL AND "{m2}" IS NOT NULL LIMIT 100',
            "x_axis": m1,
            "y_axis": m2,
            "group_by": grp,
            "chart_type": "scatter",
            "insight_summary": f"Statistical correlation between {m1} and {m2}.",
            "priority": 4
        })
    elif metric_cols:
        m1 = metric_cols[0]
        queries.append({
            "id": f"distribution_{m1}",
            "title": f"Distribution of {m1.replace('_', ' ').title()}",
            "business_question": f"How are values distributed across {m1}?",
            "analytical_purpose": "distribution",
            "sql": f'SELECT ROUND({_metric_expr(m1)}, 0) AS value_bucket, COUNT(*) AS frequency FROM {tbl} WHERE "{m1}" IS NOT NULL GROUP BY 1 ORDER BY 1 ASC LIMIT 20',
            "x_axis": "value_bucket",
            "y_axis": "frequency",
            "group_by": None,
            "chart_type": "bar",
            "insight_summary": f"Frequency distribution across observed values of {m1}.",
            "priority": 4
        })

    # 6. Statistical Anomaly Detection
    if metric_cols:
        m = metric_cols[0]
        m_calc = _metric_expr(m)
        if id_cols:
            primary_id = id_cols[0]
            queries.append({
                "id": f"statistical_anomalies_{primary_id}_{m}",
                "title": f"Top Outlier {primary_id.replace('_id', '').replace('_', ' ').title()}s by {m.replace('_', ' ').title()}",
                "business_question": f"Which {primary_id.replace('_id', '')}s exhibit total {m} exceeding statistical normal bounds (μ + 1.5σ)?",
                "analytical_purpose": "anomaly",
                "sql": f'SELECT "{primary_id}", ROUND(SUM({m_calc}), 2) AS total_{m}, COUNT(*) AS record_count FROM {tbl} WHERE "{primary_id}" IS NOT NULL GROUP BY "{primary_id}" HAVING total_{m} >= (SELECT AVG(sub_tot) + 1.5 * STDDEV_POP(sub_tot) FROM (SELECT SUM({m_calc}) AS sub_tot FROM {tbl} GROUP BY "{primary_id}")) ORDER BY total_{m} DESC LIMIT 10',
                "x_axis": primary_id,
                "y_axis": f"total_{m}",
                "group_by": None,
                "chart_type": "bar",
                "insight_summary": f"Identifies top outlier entities with total aggregated {m} exceeding the 1.5-sigma threshold.",
                "priority": 4
            })
        else:
            queries.append({
                "id": f"statistical_anomalies_{m}",
                "title": f"Statistical Outliers in {m.replace('_', ' ').title()}",
                "business_question": f"Which records exhibit significant statistical variance in {m} exceeding normal bounds?",
                "analytical_purpose": "anomaly",
                "sql": f'SELECT * FROM {tbl} WHERE {m_calc} >= (SELECT AVG({m_calc}) + 1.5 * STDDEV_POP({m_calc}) FROM {tbl}) ORDER BY {m_calc} DESC LIMIT 10',
                "x_axis": None,
                "y_axis": None,
                "group_by": None,
                "chart_type": "data_table",
                "insight_summary": f"Records exhibiting statistically elevated values of {m}.",
                "priority": 4
            })

    # 7. Comparative Benchmark
    if dim_cols and metric_cols:
        dim = dim_cols[0]
        m = metric_cols[0]
        m_calc = _metric_expr(m)
        queries.append({
            "id": f"benchmark_{dim}_{m}",
            "title": f"Comparative Benchmark by {dim.replace('_', ' ').title()}",
            "business_question": f"How does each {dim} compare against the overall dataset benchmark average for {m}?",
            "analytical_purpose": "comparison",
            "sql": f'SELECT "{dim}", ROUND(AVG({m_calc}), 2) AS category_avg, ROUND((SELECT AVG({m_calc}) FROM {tbl}), 2) AS overall_avg FROM {tbl} WHERE "{dim}" IS NOT NULL GROUP BY "{dim}" ORDER BY category_avg DESC LIMIT 8',
            "x_axis": dim,
            "y_axis": "category_avg",
            "group_by": None,
            "chart_type": "bar",
            "insight_summary": "Comparative evaluation against the dataset-wide mean.",
            "priority": 5
        })

    return queries


async def analytics_node(state: AgentSwarmState, db_engine: DuckDBEngine, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Analytics Engine Agent node.
    - Generates DuckDB SQL queries based on user intent and features.
    - Validates queries using DuckDB EXPLAIN.
    - Attempts self-correction for failed queries (1 loop for this sprint).
    - Falls back gracefully to dynamic schema-based queries if LLM is unavailable.
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    logger.info(f"Starting analytics_node for session {state_dict.get('session_id')}")

    ctx = slice_context(state, "analytics")
    
    raw_query = ctx.get("raw_query") or ""
    intent_class = ctx.get("intent_class", "")
    business_domain = ctx.get("business_domain", "")
    time_dimension = ctx.get("time_dimension", "")
    column_metadata = ctx.get("column_metadata", [])
    feature_definitions = ctx.get("feature_definitions", [])

    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    # 1. Build prompt context
    compressed_schema = compress_column_meta_for_prompt(column_metadata)

    # Optional: include derived features summary so LLM can reference them in SQL
    if feature_definitions:
        feat_lines = []
        for fd in feature_definitions:
            fd_dict = fd if isinstance(fd, dict) else (fd.model_dump() if hasattr(fd, "model_dump") else dict(fd))
            feat_lines.append(
                f"  - {fd_dict.get('name', '')}: {fd_dict.get('sql_expr', '')}  "
                f"# {fd_dict.get('rationale', '')}"
            )
        feature_context = "\n\nDerived Features (available in the 'data' table, created by Feature Architect):\n" + "\n".join(feat_lines)
    else:
        feature_context = ""

    # Adapt gracefully when user did not supply a question
    query_line = (
        f"Business Question: {raw_query}" if raw_query
        else "Business Question: (not provided — generate comprehensive analytical queries for this dataset)"
    )
    user_msg = (
        f"{query_line}\n"
        f"Intent: {intent_class} | Domain: {business_domain}\n"
        f"Time Dimension: {time_dimension}\n\n"
        f"Available Columns:\n{compressed_schema}"
        f"{feature_context}\n"
    )

    raw_queries = []
    logger.info("Requesting SQL Generation from LLM")
    try:
        response = await llm_router.route(
            task_type=TaskType.QUERY_DESIGN,
            messages=[
                {"role": "system", "content": ANALYTICS_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            response_format={"type": "json_object"},
            max_tokens=4000
        )
        parsed_json = parse_json_response(response["content"])
        
        if isinstance(parsed_json, list):
            raw_queries = parsed_json
        elif isinstance(parsed_json, dict):
            raw_queries = next((v for v in parsed_json.values() if isinstance(v, list)), [])
            
    except Exception as e:
        logger.warning(f"LLM SQL Generation failed: {e}. Falling back to dynamic generic schema analytics.")
        raw_queries = []

    # If LLM returned no queries or failed, generate dynamic queries from actual active schema
    if not raw_queries:
        logger.info("Generating dynamic schema-aware queries directly from database schema.")
        raw_queries = generate_generic_analytics_queries(
            db_engine, column_metadata, table_name="data",
            rows_before=state_dict.get("rows_before"),
            rows_after=state_dict.get("rows_after")
        )

    valid_queries: List[QueryDefinition] = []
    failed_queries_ids: List[str] = []
    query_results: List[QueryResult] = []
    
    for idx, q_dict in enumerate(raw_queries):
        if not isinstance(q_dict, dict):
            continue
            
        sql_query = (q_dict.get("sql") or q_dict.get("query") or q_dict.get("sql_query") or "").strip().rstrip(";")
        # DuckDB requires standard SQL double-quotes for identifiers with spaces, not MySQL backticks
        sql_query = sql_query.replace('`', '"')
        q_id = q_dict.get("id") or q_dict.get("query_id") or f"q_{idx+1}"
        if not sql_query:
            continue
            
        # Reconcile headline KPI queries: ensure unique entities and clean vs source records are distinct
        is_kpi_query = (
            idx == 0
            or q_dict.get("priority") == 1
            or q_dict.get("analytical_purpose") == "kpi"
            or q_dict.get("chart_type") == "kpi_card"
        )
        if is_kpi_query and "count(*)" in sql_query.lower() and "group by" not in sql_query.lower():
            # Check for primary ID column
            id_cols = [
                c.get("name") if isinstance(c, dict) else getattr(c, "name", "")
                for c in column_metadata
                if (c.get("semantic_type") if isinstance(c, dict) else getattr(c, "semantic_type", "")).lower() == "identifier"
                or (c.get("is_primary_key") if isinstance(c, dict) else getattr(c, "is_primary_key", False))
                or (c.get("name") if isinstance(c, dict) else getattr(c, "name", "")).lower() in ("patient_id", "customer_id", "employee_id", "user_id")
            ]
            primary_id = id_cols[0] if id_cols else None
            has_distinct_id = "count(distinct" in sql_query.lower()
            rows_before = state_dict.get("rows_before")
            rows_after = state_dict.get("rows_after")

            extra_measures = []
            if primary_id and not has_distinct_id:
                id_clean = primary_id.lower().replace("_id", "s").replace("id_", "").replace("_key", "s").replace("_code", "s").strip()
                if not id_clean.endswith("s"):
                    id_clean += "s"
                extra_measures.append(f'COUNT(DISTINCT "{primary_id}") AS "unique_{id_clean}"')

            if rows_before and rows_after and rows_before > rows_after and "source_records" not in sql_query.lower():
                repl = f"SELECT {rows_before} AS source_records, COUNT(*) AS valid_records, ({rows_before} - COUNT(*)) AS excluded_records"
                if extra_measures:
                    repl += ", " + ", ".join(extra_measures)
                sql_query = re.sub(r"(?i)SELECT\s+COUNT\(\*\)(\s+AS\s+\w+)?", repl, sql_query, count=1)
            elif extra_measures:
                sql_query = re.sub(r"(?i)SELECT\s+", f"SELECT " + ", ".join(extra_measures) + ", ", sql_query, count=1)

        # Mathematical integrity: convert unweighted AVG(profit_margin_pct) to weighted ratio SUM(profit) / NULLIF(SUM(revenue), 0) * 100
        avail_col_names = [c.get("name") if isinstance(c, dict) else getattr(c, "name", "") for c in column_metadata]
        profit_col = next((c for c in avail_col_names if c.lower() in ("profit", "net_profit")), None)
        rev_col = next((c for c in avail_col_names if c.lower() in ("total_order_value", "sales_amount", "revenue")), None)

        if profit_col and rev_col and re.search(r"(?i)avg\(\s*profit_margin(?:_pct)?\s*\)", sql_query):
            weighted_calc = f'ROUND(SUM("{profit_col}") / NULLIF(SUM("{rev_col}"), 0) * 100, 2)'
            weighted_scalar = f'(SELECT ROUND(SUM("{profit_col}") / NULLIF(SUM("{rev_col}"), 0) * 100, 2) FROM data)'
            sql_query = re.sub(r"(?i)\(SELECT\s+AVG\(profit_margin(?:_pct)?\)\s+FROM\s+data\)", weighted_scalar, sql_query)
            sql_query = re.sub(r"(?i)ROUND\(AVG\(profit_margin(?:_pct)?\),\s*\d+\)", weighted_calc, sql_query)
            sql_query = re.sub(r"(?i)AVG\(profit_margin(?:_pct)?\)", f'SUM("{profit_col}") / NULLIF(SUM("{rev_col}"), 0) * 100', sql_query)

        success = False
        max_attempts = 3 # 1 initial + 2 repairs
        exec_res = None
        
        for attempt in range(max_attempts):
            sql_query = sql_query.rstrip(";")
            # Strip invalid typed comparison against empty string (e.g. order_date != '') which crashes on DATE/TIMESTAMP columns
            sql_query = re.sub(r"(?i)\s+AND\s+[\"']?\w+[\"']?\s*(?:!=|<>)\s*['\"]['\"]", "", sql_query)

            # Auto-correct SQL boolean comparisons to case-insensitive truth tokens
            def _fix_sql_bool(m):
                col_name = m.group(1)
                val = m.group(2).strip().lower().replace("'", "").replace('"', '')
                if val in ('true', '1', 'yes', 'y', 't'):
                    return f"LOWER(TRIM(CAST({col_name} AS VARCHAR))) IN ('true', '1', '1.0', 'yes', 'y', 't')"
                elif val in ('false', '0', 'no', 'n', 'f'):
                    return f"LOWER(TRIM(CAST({col_name} AS VARCHAR))) IN ('false', '0', '0.0', 'no', 'n', 'f')"
                return m.group(0)

            sql_query = re.sub(
                r"([a-zA-Z0-9_\"]+)\s*=\s*(['\"](?:true|false|yes|no|1|0)['\"]|TRUE|FALSE)",
                _fix_sql_bool,
                sql_query,
                flags=re.IGNORECASE
            )
            
            # Step 1: Validate with EXPLAIN
            val_result = db_engine.execute_validated(f"EXPLAIN {sql_query}")
            
            if not val_result["success"]:
                error_msg = val_result["error"]
                logger.warning(f"Query {q_id} failed validation on attempt {attempt+1}: {error_msg}")
            else:
                # Step 2: Execute actual query
                exec_res = db_engine.execute_validated(sql_query)
                if exec_res["success"]:
                    data_rows = exec_res.get("data", [])
                    # Guard against empty dataset (0 rows) for anomaly / filtered queries
                    if len(data_rows) == 0 and "stddev" in sql_query.lower():
                        relaxed_sql = re.sub(r"(\*\s*)([2-3](?:\.0)?)\s*\*\s*STDDEV", r"\1 1.0 * STDDEV", sql_query, flags=re.IGNORECASE)
                        if relaxed_sql != sql_query:
                            rel_res = db_engine.execute_validated(relaxed_sql)
                            if rel_res["success"] and len(rel_res.get("data", [])) > 0:
                                sql_query = relaxed_sql
                                exec_res = rel_res
                                success = True
                                break
                    if len(data_rows) > 0:
                        success = True
                        break
                    elif attempt < max_attempts - 1:
                        error_msg = f"Query executed successfully but returned 0 rows (empty dataset). Please generate a query with matching data."
                        logger.warning(f"Query {q_id} returned 0 rows on attempt {attempt+1}")
                else:
                    error_msg = exec_res["error"]
                    logger.warning(f"Query {q_id} failed execution on attempt {attempt+1}: {error_msg}")
                    
            # If we reached here, it failed (either validation or execution)
            if attempt < max_attempts - 1:
                logger.info(f"Attempting repair for query {q_id} (Cycle {attempt+1})")
                repair_msg = (
                    f"The following DuckDB SQL query failed with this error. The table is called 'data' with these columns.\n"
                    f"Fix the SQL: {error_msg} | {sql_query} | Available columns: {compressed_schema}\n"
                    "Ensure you return ONLY a valid JSON object matching the QueryDefinition format with the corrected 'sql' field."
                )
                try:
                    repair_resp = await llm_router.route(
                        task_type=TaskType.QUERY_REPAIR,
                        messages=[
                            {"role": "system", "content": ANALYTICS_SYSTEM_PROMPT},
                            {"role": "user", "content": repair_msg}
                        ],
                        response_format={"type": "json_object"},
                        max_tokens=2000
                    )
                    repaired_json = parse_json_response(repair_resp["content"])
                    
                    if isinstance(repaired_json, list) and len(repaired_json) > 0:
                        repaired_q = repaired_json[0]
                    elif isinstance(repaired_json, dict):
                        if "sql" in repaired_json:
                            repaired_q = repaired_json
                        else:
                            lst = next((v for v in repaired_json.values() if isinstance(v, list)), [])
                            repaired_q = lst[0] if lst else q_dict
                    else:
                        repaired_q = q_dict
                        
                    sql_query = repaired_q.get("sql", sql_query).strip().replace('`', '"')
                    # Also update q_dict with repaired fields (preserves semantic fields from repair)
                    q_dict["sql"] = sql_query
                    for repair_key in ["x_axis", "y_axis", "group_by", "chart_type",
                                       "business_question", "insight_summary", "priority", "analytical_purpose"]:
                        if repair_key in repaired_q and repaired_q[repair_key]:
                            q_dict[repair_key] = repaired_q[repair_key]
                except Exception as e:
                    logger.error(f"Repair attempt failed for query {q_id}: {e}")
                    
        # After max_attempts, check success
        if success and exec_res:
            try:
                # Get result column names for SQL-based axis inference
                col_names = []
                if len(exec_res["data"]) > 0:
                    col_names = list(exec_res["data"][0].keys())
                else:
                    view_name_tmp = f"view_tmp_{q_id}"
                    db_engine.conn.execute(f"CREATE OR REPLACE VIEW {view_name_tmp} AS {sql_query}")
                    desc = db_engine.conn.execute(f"DESCRIBE {view_name_tmp}").fetchall()
                    col_names = [d[0] for d in desc]

                # Normalize the query dict with robust key extraction + SQL inference
                normalized = _normalize_query_dict(q_dict, idx, result_columns=col_names)

                # Construct QueryDefinition with fully normalized fields
                feat_def = QueryDefinition(
                    id=normalized["id"],
                    title=normalized["title"],
                    business_question=normalized["business_question"],
                    analytical_purpose=normalized["analytical_purpose"],
                    sql=sql_query,
                    x_axis=normalized["x_axis"],
                    y_axis=normalized["y_axis"],
                    group_by=normalized["group_by"],
                    chart_type=normalized["chart_type"],
                    insight_summary=normalized["insight_summary"],
                    priority=normalized["priority"]
                )
                valid_queries.append(feat_def)
                
                logger.info(
                    f"Query {feat_def.id}: purpose={feat_def.analytical_purpose}, chart={feat_def.chart_type}, "
                    f"x={feat_def.x_axis}, y={feat_def.y_axis}, "
                    f"group={feat_def.group_by}, priority={feat_def.priority}"
                )
                
                # Process the exec_res to QueryResult and Parquet
                session_id = state_dict.get("session_id", "session")
                user_id = state_dict.get("user_id", "user")
                res_path = f"tmp_storage/{user_id}/{session_id}/result_{feat_def.id}.parquet"
                os.makedirs(os.path.dirname(os.path.abspath(res_path)), exist_ok=True)
                
                view_name = f"view_{feat_def.id}"
                db_engine.conn.execute(f"CREATE OR REPLACE VIEW {view_name} AS {feat_def.sql}")
                db_engine.write_to_parquet(res_path, table_name=view_name)
                
                data_sample = exec_res["data"][:5]
                full_chart_data = exec_res["data"][:1000] if len(exec_res["data"]) > 1000 else exec_res["data"]
                    
                q_res = QueryResult(
                    query_id=feat_def.id,
                    result_storage_path=res_path,
                    row_count=exec_res["row_count"],
                    column_names=col_names,
                    sample_rows=data_sample,
                    chart_data=full_chart_data,
                    execution_ms=int(exec_res["ms"]),
                    error=None
                )
                query_results.append(q_res)
                
            except Exception as e:
                logger.error(f"Failed to process execution results for query {q_id}: {e}")
                failed_queries_ids.append(q_id)
        else:
            logger.error(f"Query {q_id} permanently failed after {max_attempts} attempts.")
            failed_queries_ids.append(q_id)

    state_dict["generated_queries"] = [q.model_dump() if hasattr(q, "model_dump") else dict(q) for q in valid_queries]
    state_dict["query_results"] = [qr.model_dump() if hasattr(qr, "model_dump") else dict(qr) for qr in query_results]
    state_dict["queries_failed"] = failed_queries_ids
    state_dict["pipeline_status"] = "querying"
    state_dict["current_agent"] = "analytics"
    
    logger.info(f"Analytics node complete. Executed {len(query_results)} queries.")
    return return_state()

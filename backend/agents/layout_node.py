import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.core.state import AgentSwarmState, ChartConfig
from backend.core.context_slicer import slice_context
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response

logger = logging.getLogger(__name__)

VALID_CHART_TYPES = {"bar", "line", "scatter", "area", "pie", "heatmap", "kpi_card", "data_table"}

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
    """Deterministic chart type pre-selection. Returns None if LLM should decide."""
    row_count = profile.get("row_count", 0)
    x_type = profile.get("x_axis_type", "unknown")
    num_cols = profile.get("num_columns", 0)
    group_count = profile.get("group_count", 0)
    analytical_purpose = profile.get("analytical_purpose", "")
    business_question = profile.get("business_question", "").lower()

    # Rule A: KPI card — single-row aggregate (1 row, any number of columns)
    if row_count == 1 and not profile.get("group_by"):
        return "kpi_card"

    # Rule B: Temporal x-axis — line
    if x_type == "temporal":
        return "line"

    # Rule C: data_table — only for truly unrenderable results
    is_ranking = any(w in business_question for w in ["top", "rank", "best", "worst", "highest", "lowest"])
    if not is_ranking and (row_count > 100 or group_count > 30):
        return "data_table"

    return None


def correct_chart_type(llm_chart: str, profile: Dict) -> str:
    """Post-LLM deterministic correction to prevent bad chart choices."""
    row_count = profile.get("row_count", 0)
    x_type = profile.get("x_axis_type", "unknown")
    group_count = profile.get("group_count", 0)

    if llm_chart == "pie" and group_count > 7:
        logger.info(f"Correcting pie -> bar (group_count={group_count} > 7)")
        return "bar"
    if llm_chart == "line" and x_type == "categorical":
        logger.info("Correcting line -> bar (x_axis_type=categorical)")
        return "bar"
    if llm_chart == "kpi_card" and row_count > 1:
        logger.info(f"Correcting kpi_card -> bar (row_count={row_count})")
        return "bar"
    if llm_chart == "scatter" and profile.get("y_axis_type") != "numeric":
        logger.info("Correcting scatter -> bar (y_axis not numeric)")
        return "bar"
    return llm_chart


def _format_metric_value(col: str, val: Any) -> str:
    """Format KPI values with proper currency, percentage, or magnitude symbols."""
    try:
        num = float(val)
        col_lower = col.lower()
        is_pct = any(k in col_lower for k in ["pct", "percent", "rate", "ratio", "share"])
        is_curr = any(k in col_lower for k in ["salary", "revenue", "cost", "price", "compensation", "budget", "spend"])

        if is_pct:
            return f"{num:.1f}%"

        if abs(num) >= 1_000_000_000:
            formatted = f"{num/1_000_000_000:.1f}B"
        elif abs(num) >= 1_000_000:
            formatted = f"{num/1_000_000:.1f}M"
        elif abs(num) >= 1_000:
            formatted = f"{num/1_000:.1f}K"
        else:
            formatted = f"{num:,.1f}" if num != int(num) else f"{int(num):,}"

        if is_curr:
            return f"${formatted}"
        return formatted
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
    if chart_type == "pie":
        data = series_0.get("data", [])
        if not data or not isinstance(data, list):
            return False, "Pie chart missing series data"
        if data and not isinstance(data[0], dict):
            return False, "Pie data must be [{name, value}] format"
    if chart_type in ("line", "bar", "area"):
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


def _infer_xaxis_time_type(option: Dict, sample_rows: List[Dict], x_col: Optional[str]) -> Dict:
    """Post-process: upgrade xAxis.type to 'time' if data contains ISO date strings."""
    import re
    ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
    x_axis = option.get("xAxis")
    if not x_axis or not isinstance(x_axis, dict):
        return option
    if x_axis.get("type") == "time":
        return option
    if x_axis.get("type") != "category":
        return option
    categories = x_axis.get("data", [])
    if not categories and x_col and sample_rows:
        categories = [str(r.get(x_col, "")) for r in sample_rows]
    if categories and isinstance(categories[0], str) and ISO_DATE_RE.match(categories[0]):
        x_axis["type"] = "time"
        x_axis.pop("data", None)
        for series in option.get("series", []):
            raw_data = series.get("data", [])
            if raw_data and not isinstance(raw_data[0], (list, tuple)):
                series["data"] = [
                    [categories[i], raw_data[i]]
                    for i in range(min(len(categories), len(raw_data)))
                ]
    return option


def sanitize_echarts_option(option: Dict, sample_rows: Optional[List[Dict]] = None, x_col: Optional[str] = None) -> Dict:
    """Remove NaN/None from series data; strip JS functions from tooltip; infer xAxis.type."""
    # 1. Strip NaN / None from series data, fix encoding typo
    for series in option.get("series", []):
        if not isinstance(series, dict):
            continue
        if "encoding" in series and "encode" not in series:
            series["encode"] = series.pop("encoding")
        if isinstance(series.get("data"), list):
            series["data"] = [
                d for d in series["data"]
                if d is not None and (not isinstance(d, float) or not (d != d))
            ]

    # 2. Strip JS function-valued tooltip formatters
    tooltip = option.get("tooltip")
    if isinstance(tooltip, dict):
        formatter = tooltip.get("formatter")
        if _looks_like_js_function(formatter):
            logger.warning("Stripped JS function from tooltip.formatter")
            trigger = tooltip.get("trigger", "axis")
            tooltip["formatter"] = "{b}: {c}" if trigger == "item" else "{b0}: {c0}"

    # 3. Infer xAxis.type = 'time' when data contains ISO date strings
    if sample_rows is not None:
        option = _infer_xaxis_time_type(option, sample_rows, x_col)

    # 4. Remove any color properties from echarts (frontend controls palette)
    for series in option.get("series", []):
        if isinstance(series, dict):
            series.pop("color", None)
            if isinstance(series.get("itemStyle"), dict):
                series["itemStyle"].pop("color", None)
    option.pop("color", None)

    return option


def build_fallback_config(query_def: Dict, query_result: Dict, chart_type: str) -> Dict:
    """Build a deterministic fallback ECharts config when LLM fails."""
    col_names = query_result.get("column_names", [])
    sample_rows = query_result.get("sample_rows", [])
    title = query_def.get("title", "Chart")
    x_col = query_def.get("x_axis") or (col_names[0] if col_names else "x")
    y_col = query_def.get("y_axis") or (col_names[1] if len(col_names) > 1 else col_names[0] if col_names else "y")

    if chart_type == "kpi_card" and sample_rows:
        row = sample_rows[0] if sample_rows else {}
        metrics = []
        for col in col_names:
            val = row.get(col, 0)
            formatted = _format_metric_value(col, val)
            label = col.replace("_", " ").title()
            metrics.append({"label": label, "value": formatted, "raw_value": val})
        return {"type": "kpi_card", "metrics": metrics}

    if chart_type == "data_table":
        return {"type": "data_table", "columns": col_names, "rows": sample_rows}

    if chart_type == "pie":
        pie_data = []
        for r in sample_rows[:7]:
            if r.get(x_col) is not None:
                try:
                    val = float(r.get(y_col, 0) or 0)
                except (ValueError, TypeError):
                    val = 0
                pie_data.append({"name": str(r.get(x_col, "")), "value": val})
        return {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"type": "scroll", "orient": "vertical", "right": "5%", "top": "center"},
            "series": [{
                "name": y_col.replace("_", " ").title(),
                "type": "pie",
                "radius": ["40%", "70%"],
                "center": ["40%", "50%"],
                "data": pie_data,
            }],
        }

    categories = [str(r.get(x_col, "")) for r in sample_rows]
    values = []
    for r in sample_rows:
        try:
            values.append(float(r.get(y_col, 0) or 0))
        except (ValueError, TypeError):
            values.append(0)
    series_type = "bar" if chart_type not in ("line", "area", "scatter") else chart_type
    return {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "5%", "right": "5%", "bottom": "15%", "top": "12%", "containLabel": True},
        "xAxis": {"type": "category", "data": categories},
        "yAxis": {"type": "value"},
        "series": [{"name": y_col.replace("_", " ").title(), "type": series_type, "data": values}],
    }


def split_kpi_cards(query_def: Dict, query_result: Dict, max_kpis: int = 4) -> List[Dict]:
    """
    Split a single KPI query result with multiple columns into multiple individual KPI card configs.
    Returns up to max_kpis KPI card entries.
    """
    col_names = query_result.get("column_names", [])
    sample_rows = query_result.get("sample_rows", [])
    if not sample_rows:
        return []

    row = sample_rows[0]
    kpi_cards = []
    for col in col_names[:max_kpis]:
        val = row.get(col, 0)
        formatted = _format_metric_value(col, val)
        label = col.replace("_", " ").title()
        kpi_id = f"{query_def.get('id', 'kpi')}_{col}"
        kpi_cards.append({
            "query_id": kpi_id,
            "chart_type": "kpi_card",
            "priority": query_def.get("priority", 1),
            "title": label,
            "subtitle": "",  # KPI cards: no insight paragraph
            "insight_summary": "",
            "echarts_option": {"type": "kpi_card", "value": formatted, "label": label, "raw_value": val},
            "layout": {"x": len(kpi_cards) * 3, "y": 0, "w": 3, "h": 2},
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
        if sem_type in ("identifier", "text_description", "unknown"):
            continue
        if sem_type == "metric" or sem_type == "currency" or sem_type == "percentage":
            continue

        # Date columns are good filter candidates
        if sem_type == "date":
            candidates.append({
                "column": name,
                "label": cm.get("business_label", name.replace("_", " ").title()),
                "type": "date",
                "score": 0.6,
                "values": [],
            })
            continue

        # Dimension/geographic columns with reasonable cardinality
        if sem_type in ("dimension", "geographic"):
            score = 1.0 - unique_pct
            if unique_pct > 0.8:
                continue
            unique_vals = list(dict.fromkeys(str(v).strip() for v in samples if v is not None and str(v).strip())) if samples else []
            candidates.append({
                "column": name,
                "label": cm.get("business_label", name.replace("_", " ").title()),
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

    # Assign KPI positions (row 0, up to 4 cards, 3-col each)
    current_y = 0
    for i, kpi in enumerate(kpi_configs[:4]):
        kpi["layout"] = {"x": (i * 3), "y": 0, "w": 3, "h": 2}
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

    return kpi_configs[:4] + other_configs


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
        logger.warning("Layout node: no queries/results - returning empty dashboard")
        state_dict["dashboard_config"] = []
        state_dict["dashboard_title"] = "Dashboard"
        state_dict["layout_rationale"] = "No analytics results available."
        state_dict["dashboard_filters"] = []
        state_dict["pipeline_status"] = "layouting"
        state_dict["current_agent"] = "layout"
        return AgentSwarmState(**state_dict)

    # Detect dynamic filters from column metadata
    dashboard_filters = detect_dashboard_filters(column_metadata)
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
            kpi_cards = split_kpi_cards(q_dict, q_result, max_kpis=4)
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
                    max_tokens=256,
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
                sample_rows=q_result.get("sample_rows"),
                x_col=q_dict.get("x_axis"),
            )

        if isinstance(echarts_option, dict):
            echarts_option["_chart_type"] = chart_type

        chart_configs_raw.append({
            "query_id": q_id,
            "chart_type": chart_type,
            "priority": q_dict.get("priority", 3),
            "title": q_dict.get("title", ""),
            "subtitle": q_dict.get("business_question", ""),
            "insight_summary": q_dict.get("insight_summary", ""),
            "echarts_option": echarts_option,
            "responsive": True,
        })

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

            cfg = ChartConfig(
                query_id=cc["query_id"],
                echarts_option=cc["echarts_option"],
                layout=cc.get("layout", {"x": 0, "y": 0, "w": 12, "h": 4}),
                title=cc["title"],
                subtitle=cc.get("subtitle", ""),
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
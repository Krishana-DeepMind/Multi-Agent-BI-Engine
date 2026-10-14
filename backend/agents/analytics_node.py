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


async def analytics_node(state: AgentSwarmState, db_engine: DuckDBEngine, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Analytics Engine Agent node.
    - Generates DuckDB SQL queries based on user intent and features.
    - Validates queries using DuckDB EXPLAIN.
    - Attempts self-correction for failed queries (1 loop for this sprint).
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
        
        raw_queries = []
        if isinstance(parsed_json, list):
            raw_queries = parsed_json
        elif isinstance(parsed_json, dict):
            raw_queries = next((v for v in parsed_json.values() if isinstance(v, list)), [])
            
    except Exception as e:
        logger.error(f"LLM SQL Generation failed: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "analytics", "error": str(e), "recoverable": True}]
        return return_state()

    valid_queries: List[QueryDefinition] = []
    failed_queries_ids: List[str] = []
    query_results: List[QueryResult] = []
    
    for idx, q_dict in enumerate(raw_queries):
        if not isinstance(q_dict, dict):
            continue
            
        sql_query = (q_dict.get("sql") or q_dict.get("query") or q_dict.get("sql_query") or "").strip().rstrip(";")
        q_id = q_dict.get("id") or q_dict.get("query_id") or f"q_{idx+1}"
        if not sql_query:
            continue
            
        success = False
        max_attempts = 3 # 1 initial + 2 repairs
        exec_res = None
        
        for attempt in range(max_attempts):
            sql_query = sql_query.rstrip(";")
            
            # Step 1: Validate with EXPLAIN
            val_result = db_engine.execute_validated(f"EXPLAIN {sql_query}")
            
            if not val_result["success"]:
                error_msg = val_result["error"]
                logger.warning(f"Query {q_id} failed validation on attempt {attempt+1}: {error_msg}")
            else:
                # Step 2: Execute actual query
                exec_res = db_engine.execute_validated(sql_query)
                if exec_res["success"]:
                    success = True
                    break
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
                        
                    sql_query = repaired_q.get("sql", sql_query).strip()
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
                    
                q_res = QueryResult(
                    query_id=feat_def.id,
                    result_storage_path=res_path,
                    row_count=exec_res["row_count"],
                    column_names=col_names,
                    sample_rows=data_sample,
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

import logging
import os
from typing import Dict, Any, List

from backend.core.state import AgentSwarmState, QueryDefinition, QueryResult
from backend.core.context_slicer import slice_context
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import ANALYTICS_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

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
    
    raw_query = ctx.get("raw_query", "")
    intent_class = ctx.get("intent_class", "")
    business_domain = ctx.get("business_domain", "")
    time_dimension = ctx.get("time_dimension", "")
    column_metadata = ctx.get("column_metadata", [])

    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    # 1. Build prompt context
    compressed_schema = compress_column_meta_for_prompt(column_metadata)
    
    user_msg = (
        f"Business Question: {raw_query}\n"
        f"Intent: {intent_class} | Domain: {business_domain}\n"
        f"Time Dimension: {time_dimension}\n\n"
        f"Available Columns:\n{compressed_schema}\n"
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
            
        sql_query = q_dict.get("sql", "").strip().rstrip(";")
        q_id = q_dict.get("id", f"q_{idx+1}")
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
                    # Also update q_dict with the new sql in case it succeeds next time
                    q_dict["sql"] = sql_query
                except Exception as e:
                    logger.error(f"Repair attempt failed for query {q_id}: {e}")
                    
        # After max_attempts, check success
        if success and exec_res:
            try:
                # Construct QueryDefinition
                feat_def = QueryDefinition(
                    id=q_dict.get("id", q_id),
                    title=q_dict.get("title", f"Query {q_id}"),
                    business_question=q_dict.get("business_question", ""),
                    sql=sql_query,
                    x_axis=q_dict.get("x_axis"),
                    y_axis=q_dict.get("y_axis"),
                    group_by=q_dict.get("group_by"),
                    chart_type=q_dict.get("chart_type", "data_table"),
                    insight_summary=q_dict.get("insight_summary", ""),
                    priority=int(q_dict.get("priority", 3))
                )
                valid_queries.append(feat_def)
                
                # Process the exec_res to QueryResult and Parquet
                session_id = state_dict.get("session_id", "session")
                user_id = state_dict.get("user_id", "user")
                res_path = f"tmp_storage/{user_id}/{session_id}/result_{feat_def.id}.parquet"
                os.makedirs(os.path.dirname(os.path.abspath(res_path)), exist_ok=True)
                
                view_name = f"view_{feat_def.id}"
                db_engine.conn.execute(f"CREATE OR REPLACE VIEW {view_name} AS {feat_def.sql}")
                db_engine.write_to_parquet(res_path, table_name=view_name)
                
                data_sample = exec_res["data"][:5]
                col_names = []
                if len(exec_res["data"]) > 0:
                    col_names = list(exec_res["data"][0].keys())
                elif exec_res["row_count"] == 0:
                    desc = db_engine.conn.execute(f"DESCRIBE {view_name}").fetchall()
                    col_names = [d[0] for d in desc]
                    
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

import logging
import shutil
import polars as pl
import os
from typing import List

from backend.core.state import AgentSwarmState, FeatureDefinition
from backend.core.context_slicer import slice_context
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import FEATURE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

async def feature_node(state: AgentSwarmState, db_engine: DuckDBEngine, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Feature Architect Agent node.
    - Slices context (columns, cleaning ops, intent, domain, raw_query).
    - Routes to LLM for feature ideation.
    - Validates Polars expressions via parse + 0-row schema dry-run.
    - Executes valid features against Polars LazyFrame.
    - Saves enriched.parquet locally and registers DuckDB table 'data'.
    - Falls back gracefully when 0 valid features are generated.
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    logger.info(f"Starting feature_node for session {state_dict.get('session_id')}")

    ctx = slice_context(state, "feature")

    # raw_query is OPTIONAL — may be empty/None if user did not supply a question.
    raw_query = ctx.get("raw_query") or ""
    intent_class = ctx.get("intent_class", "")
    business_domain = ctx.get("business_domain", "")
    time_dimension = ctx.get("time_dimension", "")
    column_metadata = ctx.get("column_metadata", [])
    cleaning_operations = ctx.get("cleaning_operations", [])

    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    # ── Resolve cleaned parquet path EARLY (needed for per-feature dry-run validation) ──
    cleaned_path = state_dict.get("cleaned_parquet_path", "")
    if not cleaned_path or not os.path.exists(cleaned_path):
        logger.warning("No cleaned parquet path found in state. Cannot run feature node.")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [
            {"agent": "feature", "error": "No cleaned parquet path.", "recoverable": False}
        ]
        return return_state()

    # ── Build prompt context ──
    compressed_schema = compress_column_meta_for_prompt(column_metadata)
    
    if cleaning_operations:
        cleaning_summary_lines = []
        for op in cleaning_operations:
            op_dict = op if isinstance(op, dict) else (op.model_dump() if hasattr(op, "model_dump") else dict(op))
            cleaning_summary_lines.append(
                f"- {op_dict.get('column')}: {op_dict.get('operation')} ({op_dict.get('strategy')})"
            )
        cleaning_summary = "\n".join(cleaning_summary_lines)
    else:
        cleaning_summary = "No cleaning operations applied."

    # Adapt gracefully when user did not supply a question
    query_line = (
        f"Business Question: {raw_query}" if raw_query
        else "Business Question: (not provided — generate generally useful features for this dataset)"
    )
    user_msg = (
        f"{query_line}\n"
        f"Intent: {intent_class} | Domain: {business_domain}\n"
        f"Time Dimension: {time_dimension}\n\n"
        f"Available Columns After Cleaning:\n{compressed_schema}\n\n"
        f"Previously Applied Cleaning:\n{cleaning_summary}\n"
    )

    logger.info("Requesting Feature Ideation from LLM")
    try:
        response = await llm_router.route(
            task_type=TaskType.FEATURE_IDEATION,
            messages=[
                {"role": "system", "content": FEATURE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            response_format={"type": "json_object"},
            max_tokens=4000
        )
        parsed_json = parse_json_response(response["content"])
        
        raw_features = []
        if isinstance(parsed_json, list):
            raw_features = parsed_json
        elif isinstance(parsed_json, dict):
            # Extract from first array value found
            raw_features = next((v for v in parsed_json.values() if isinstance(v, list)), [])
            
    except Exception as e:
        logger.error(f"LLM Feature Ideation failed: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "feature", "error": str(e), "recoverable": True}]
        return return_state()

    # Process and validate features
    valid_features: List[FeatureDefinition] = []
    polars_exprs = []
    
    # We will use eval to parse the string into a polars expression, 
    # but we restrict the globals to only the polars module for safety.
    eval_globals = {"pl": pl}

    for f_dict in raw_features:
        if not isinstance(f_dict, dict):
            continue

        # Defensive normalization of feature dictionary keys
        name = str(f_dict.get("name") or f_dict.get("feature_name") or "").strip()
        polars_code = str(f_dict.get("polars_expr") or f_dict.get("polars") or "").strip()
        sql_code = str(f_dict.get("sql_expr") or f_dict.get("sql") or "").strip()
        rationale = str(f_dict.get("rationale") or f_dict.get("description") or "").strip()
        expected_insight = str(f_dict.get("expected_insight") or f_dict.get("insight") or "").strip()

        if not name or not polars_code or not sql_code:
            logger.warning("Skipping incomplete feature definition (missing name/polars_expr/sql_expr).")
            continue

        # Security check — keyword blacklist
        if any(unsafe in polars_code.lower() for unsafe in ["import ", "__", "eval", "exec", "open", "system"]):
            logger.warning(f"Unsafe code detected in polars_expr for '{name}'. Skipping.")
            continue

        # Stage 1: Parse expression tree (catches syntax errors)
        try:
            expr_obj = eval(polars_code, eval_globals, {})
        except Exception as e:
            logger.warning(f"Failed to parse polars_expr for '{name}': {e}. Expression: {polars_code}")
            continue

        if not isinstance(expr_obj, pl.Expr):
            logger.warning(f"Evaluated polars_expr for '{name}' is not a pl.Expr. Skipping.")
            continue

        # Stage 2: Schema dry-run — verify column references against actual data (0-row, no scan)
        try:
            lf_test = pl.scan_parquet(cleaned_path)
            lf_test.with_columns([expr_obj.alias(name)]).limit(0).collect()
        except Exception as e:
            logger.warning(
                f"Schema validation failed for feature '{name}' (column reference error): {e}. "
                f"Expression: {polars_code}. Skipping — pipeline continues with remaining features."
            )
            continue

        # Both stages passed — accept this feature
        polars_exprs.append(expr_obj.alias(name))
        feat_def = FeatureDefinition(
            name=name,
            polars_expr=polars_code,
            sql_expr=sql_code,
            rationale=rationale,
            expected_insight=expected_insight
        )
        valid_features.append(feat_def)

    # ── Determine enriched parquet path ──
    enriched_parquet_path = cleaned_path.replace("cleaned.parquet", "enriched.parquet")
    os.makedirs(os.path.dirname(os.path.abspath(enriched_parquet_path)), exist_ok=True)

    if not valid_features:
        # P1-1 FALLBACK: 0 valid features — still register cleaned data so Analytics can run.
        logger.info(
            "No valid features generated. Registering cleaned dataset as 'data' table so Analytics can continue."
        )
        try:
            if cleaned_path != enriched_parquet_path:
                shutil.copy2(cleaned_path, enriched_parquet_path)
            cleaned_path_fwd = cleaned_path.replace("\\", "/")
            db_engine.conn.execute(
                f"CREATE OR REPLACE TABLE data AS SELECT * FROM read_parquet('{cleaned_path_fwd}')"
            )
            db_engine.current_table = "data"
            state_dict["enriched_parquet_path"] = enriched_parquet_path
        except Exception as e:
            logger.error(f"Zero-feature fallback failed to register 'data' table: {e}")
            state_dict["pipeline_status"] = "failed"
            state_dict["errors"] = state_dict.get("errors", []) + [
                {"agent": "feature", "error": f"Zero-feature fallback: {e}", "recoverable": False}
            ]
            return return_state()

        state_dict["feature_definitions"] = []
        state_dict["pipeline_status"] = "featuring"
        state_dict["current_agent"] = "feature"
        return return_state()

    # ── Apply valid features ──
    try:
        lf = pl.scan_parquet(cleaned_path)
        lf_enriched = lf.with_columns(polars_exprs)
        lf_enriched.sink_parquet(enriched_parquet_path)
        state_dict["enriched_parquet_path"] = enriched_parquet_path

        enriched_path_fwd = enriched_parquet_path.replace("\\", "/")
        db_engine.conn.execute(
            f"CREATE OR REPLACE TABLE data AS SELECT * FROM read_parquet('{enriched_path_fwd}')"
        )
        db_engine.current_table = "data"

    except Exception as e:
        logger.error(f"Failed to apply features and save enriched dataset: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [
            {"agent": "feature", "error": str(e), "recoverable": False}
        ]
        return return_state()

    # Update state
    # We should add the new features to column_metadata so Analytics Engine knows about them.
    # The new features will be treated as metric/dimension dynamically, we can make a best guess 
    # or leave semantic_type='unknown'.
    new_cols = []
    for f in valid_features:
        new_cols.append({
            "name": f.name,
            "original_name": f.name,
            "dtype": "DOUBLE", # We assume new features are often numeric measures
            "semantic_type": "metric", 
            "business_label": f.name.replace("_", " ").title(),
            "null_pct": 0.0,
            "unique_pct": 1.0,
            "sample_values": [],
            "is_primary_key": False,
            "is_candidate_kpi": False
        })
    
    state_dict["column_metadata"] = state_dict.get("column_metadata", []) + new_cols
    state_dict["feature_definitions"] = [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in valid_features]
    state_dict["pipeline_status"] = "featuring"
    state_dict["current_agent"] = "feature"
    
    logger.info(f"Feature node complete. Generated {len(valid_features)} features.")
    return return_state()

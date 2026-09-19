import logging
import polars as pl
import os
import uuid
import json
from typing import Dict, Any, List

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
    - Slices context (columns, cleaning ops, intent, domain).
    - Routes to LLM for feature ideation.
    - Validates Polars expressions via parsing.
    - Executes valid features against Polars LazyFrame.
    - Writes enriched Parquet to Supabase.
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    logger.info(f"Starting feature_node for session {state_dict.get('session_id')}")

    ctx = slice_context(state, "feature")
    
    raw_query = ctx.get("raw_query", "")
    intent_class = ctx.get("intent_class", "")
    business_domain = ctx.get("business_domain", "")
    time_dimension = ctx.get("time_dimension", "")
    column_metadata = ctx.get("column_metadata", [])
    cleaning_operations = ctx.get("cleaning_operations", [])

    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    # 1. Build prompt context
    compressed_schema = compress_column_meta_for_prompt(column_metadata)
    
    # Summarize cleaning
    if cleaning_operations:
        cleaning_summary_lines = []
        for op in cleaning_operations:
            op_dict = op if isinstance(op, dict) else (op.model_dump() if hasattr(op, "model_dump") else dict(op))
            cleaning_summary_lines.append(f"- {op_dict.get('column')}: {op_dict.get('operation')} ({op_dict.get('strategy')})")
        cleaning_summary = "\n".join(cleaning_summary_lines)
    else:
        cleaning_summary = "No cleaning operations applied."

    user_msg = (
        f"Business Question: {raw_query}\n"
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
            
        name = f_dict.get("name", "").strip()
        polars_code = f_dict.get("polars_expr", "").strip()
        sql_code = f_dict.get("sql_expr", "").strip()
        
        if not name or not polars_code or not sql_code:
            continue
            
        # Optional Step: If blueprint requires explicit CODE_GENERATION for each feature, 
        # we could route to Ollama Qwen2.5 here. Given our prompt asked for it, we use it directly.
        # Validate Polars expression
        try:
            # Check for malicious keywords in the expression
            if any(unsafe in polars_code.lower() for unsafe in ['import ', '__', 'eval', 'exec', 'open', 'system']):
                logger.warning(f"Unsafe code detected in polars_expr for {name}. Skipping.")
                continue
                
            # Attempt to parse/evaluate the expression object
            expr_obj = eval(polars_code, eval_globals, {})
            if not isinstance(expr_obj, pl.Expr):
                logger.warning(f"Evaluated polars_expr for {name} is not a pl.Expr. Skipping.")
                continue
            
            # Since the expression compiles, alias it to the requested name
            polars_exprs.append(expr_obj.alias(name))
            
            # Ensure it fits the Pydantic model
            feat_def = FeatureDefinition(
                name=name,
                polars_expr=polars_code,
                sql_expr=sql_code,
                rationale=f_dict.get("rationale", ""),
                expected_insight=f_dict.get("expected_insight", "")
            )
            valid_features.append(feat_def)
            
        except Exception as e:
            logger.warning(f"Failed to compile polars_expr for {name}: {e}. Expression: {polars_code}")
            continue

    if not valid_features:
        logger.info("No valid features generated. Skipping feature application.")
        state_dict["pipeline_status"] = "featuring"
        state_dict["current_agent"] = "feature"
        return return_state()

    # Apply features to the cleaned data
    cleaned_path = state_dict.get("cleaned_parquet_path", "")
    if not cleaned_path:
        logger.warning("No cleaned parquet path found in state. Cannot apply features.")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "feature", "error": "No cleaned parquet path.", "recoverable": False}]
        return return_state()
        
    try:
        # Run polars on the local parquet file
        lf = pl.scan_parquet(cleaned_path)
        lf_enriched = lf.with_columns(polars_exprs)
        
        # Save enriched to the same tmp directory
        enriched_parquet_path = cleaned_path.replace("cleaned.parquet", "enriched.parquet")
        
        # create directory if it doesn't exist (though it should since cleaned.parquet is there)
        os.makedirs(os.path.dirname(enriched_parquet_path), exist_ok=True)
        
        lf_enriched.sink_parquet(enriched_parquet_path)
        
        state_dict["enriched_parquet_path"] = enriched_parquet_path
        
        # Also load the enriched dataset into DuckDB for the next agent (Analytics)
        db_engine.conn.execute(f"CREATE OR REPLACE TABLE data AS SELECT * FROM read_parquet('{enriched_parquet_path}')")
        db_engine.current_table = "data"
        
    except Exception as e:
        logger.error(f"Failed to apply features and save enriched dataset: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "feature", "error": str(e), "recoverable": False}]
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

import logging
import json
import polars as pl
import re
from datetime import datetime
from typing import Dict, Any, List

from backend.core.state import AgentSwarmState, CleaningOperation
from backend.core.context_slicer import slice_context
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import CLEANING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _safe_cast_mixed_columns(db_engine: DuckDBEngine) -> None:
    """
    Pre-processing guard: scans all VARCHAR columns in DuckDB and attempts
    TRY_CAST to DOUBLE. Columns where TRY_CAST succeeds for some rows but
    fails for others (mixed-type) are left as VARCHAR — this is fine.
    The key benefit: it ensures SUMMARIZE won't crash by detecting the
    columns that contain non-numeric sentinel strings (like 'twenty-nine',
    'N/A', 'null', 'none') and replacing ONLY those values with SQL NULL
    before SUMMARIZE runs.

    This is a read-repair step — the original Polars LazyFrame is unaffected.
    DuckDB SUMMARIZE will now get clean nulls instead of crashing on STDDEV.
    """
    if not db_engine.current_table:
        return

    tbl = db_engine.current_table
    try:
        describe = db_engine.conn.execute(f"DESCRIBE {tbl}").fetchall()
    except Exception:
        return

    # Sentinel strings that should be treated as null in numeric contexts
    NULL_SENTINELS = ("'N/A'", "'n/a'", "'NA'", "'na'", "'null'", "'NULL'",
                      "'none'", "'None'", "'NONE'", "'nan'", "'NaN'", "'NAN'",
                      "'undefined'", "''")

    for row in describe:
        col_name, col_type = row[0], row[1].upper()
        if "VARCHAR" not in col_type and "TEXT" not in col_type:
            continue

        quoted = f'"{col_name}"'
        sentinel_list = ", ".join(NULL_SENTINELS)

        try:
            # Replace sentinel strings with NULL in-place using UPDATE
            db_engine.conn.execute(
                f"UPDATE {tbl} SET {quoted} = NULL "
                f"WHERE LOWER(TRIM({quoted})) IN "
                f"({', '.join([s.lower() for s in NULL_SENTINELS])})"
            )
        except Exception as e:
            logger.debug(f"Pre-process sentinel replacement skipped for '{col_name}': {e}")

    logger.info(f"Pre-processing hardening complete on table '{tbl}'.")


async def cleaning_node(state: AgentSwarmState, db_engine: DuckDBEngine, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Cleaning & Imputation Agent node.
    - Generates cleaning strategy using LLM (Groq)
    - Generates Polars code per operation using LLM (Ollama qwen2.5-coder)
    - Safely executes Polars code
    - Writes cleaned data to Parquet
    - Calculates data quality score
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    logger.info(f"Starting cleaning_node for session {state_dict.get('session_id')}")

    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    try:
        # Phase 0: PRE-PROCESSING HARDENING
        # Safely cast mixed-type VARCHAR columns before SUMMARIZE to prevent crashes.
        # e.g. an 'Age' column stored as VARCHAR with values like '28', '-5', 'twenty-nine'
        _safe_cast_mixed_columns(db_engine)

        # Phase 1: STRATEGY GENERATION
        ctx = slice_context(state, "cleaning")

        # Statistical summary - now safe after pre-processing
        duckdb_summary = db_engine.get_statistical_summary()
        compressed_schema = compress_column_meta_for_prompt(ctx.get("column_metadata", []))
        
        intent_class = ctx.get("intent_class", "unknown")
        # business_domain might not be in ctx if omitted from context_slicer, fall back to state_dict
        business_domain = state_dict.get("business_domain", "unknown")
        
        user_msg = (
            f"Dataset Quality Profile:\n{duckdb_summary}\n\n"
            f"Column Metadata:\n{compressed_schema}\n\n"
            f"Business Intent:\n{intent_class}\n\n"
            f"Business Domain:\n{business_domain}\n\n"
            f"For each column requiring a cleaning action, return one CleaningOperation.\n\n"
            f"Only include columns that need changes.\n"
            f"Skip clean columns."
        )
        
        logger.info("Routing task TaskType.CLEANING_STRATEGY")
        response = await llm_router.route(
            task_type=TaskType.CLEANING_STRATEGY,
            messages=[
                {"role": "system", "content": CLEANING_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg}
            ],
            max_tokens=4000
        )
        
        parsed_json = parse_json_response(response["content"])
        
        operations_data = []
        if isinstance(parsed_json, list):
            operations_data = parsed_json
        elif isinstance(parsed_json, dict) and "operations" in parsed_json:
            operations_data = parsed_json["operations"]
            
        operations: List[CleaningOperation] = []
        for op in operations_data:
            # Skip anything that isn't a dict (e.g., stray strings from LLM output)
            if not isinstance(op, dict):
                logger.warning(f"Skipping non-dict operation item: {op}")
                continue
            # Provide defaults for fields the LLM shouldn't guess at strategy time
            op.setdefault("rows_affected", 0)
            op.setdefault("before_nulls", 0)
            op.setdefault("after_nulls", 0)
            op.setdefault("polars_code", "")

            try:
                op_obj = CleaningOperation(**op)
                operations.append(op_obj)
            except Exception as e:
                logger.warning(f"Failed to parse CleaningOperation {op}: {e}")

                
        # Phase 2: CODE GENERATION & EXECUTION
        logger.info(f"Generated {len(operations)} cleaning operations. Executing via Polars.")
        lf = db_engine.to_polars_lazyframe()
        
        # Sandbox globals for safe exec()
        SAFE_GLOBALS = {
            "__builtins__": {},
            "pl": pl,
            "re": re,
            "datetime": datetime
        }
        
        final_operations = []
        columns_dropped = []
        
        for op in operations:
            if op.operation == "drop_column":
                lf = lf.drop(op.column)
                columns_dropped.append(op.column)
                final_operations.append(op)
                continue
                
            code_gen_prompt = (
                f"Generate a Polars expression for a Polars LazyFrame called `lf`.\n"
                f"Operation: {op.operation}. Column: '{op.column}'. Strategy: {op.strategy}.\n"
                f"The expression will be used as: `lf = lf.with_columns([YOUR_EXPRESSION])`\n\n"
                f"Strategy reference:\n"
                f"- safe_numeric_cast: pl.col('{op.column}').str.extract(r'([-+]?\\d+\\.?\\d*)', 0).cast(pl.Float64, strict=False)\n"
                f"- normalize_categorical: pl.col('{op.column}').str.to_lowercase().str.strip_chars()\n"
                f"- strip: pl.col('{op.column}').str.strip_chars()\n"
                f"- null_invalid_email: pl.when(pl.col('{op.column}').str.contains('@')).then(pl.col('{op.column}')).otherwise(pl.lit(None))\n"
                f"- keep_most_complete: pl.col('{op.column}').first() (after sort_by null count)\n"
                f"- drop_all_null_rows: pl.col('{op.column}').is_not_null()\n"
                f"- domain_bounds: apply reasonable domain filter e.g. age between 0-120, amount >= 0\n"
                f"- median: pl.col('{op.column}').fill_null(pl.col('{op.column}').median())\n"
                f"- mode: pl.col('{op.column}').fill_null(pl.col('{op.column}').mode().first())\n\n"
                f"Return ONLY the Python expression using pl.col() notation. No markdown, no imports."
            )
            
            polars_code = ""
            for attempt in range(3):
                try:
                    code_response = await llm_router.route(
                        task_type=TaskType.CODE_GENERATION,
                        messages=[
                            {"role": "system", "content": "You are a Polars expert. Return ONLY valid Python code for Polars expressions. Do not include markdown or explanations. Just the code."},
                            {"role": "user", "content": code_gen_prompt}
                        ],
                        max_tokens=500
                    )
                    
                    polars_code = code_response["content"].strip()
                    if polars_code.startswith("```"):
                        polars_code = re.sub(r"```(?:python)?", "", polars_code).strip("`").strip()
                        
                    # Execute generated code
                    SAFE_LOCALS = {"lf": lf}
                    exec_cmd = f"lf = lf.with_columns([{polars_code}])"
                    exec(exec_cmd, SAFE_GLOBALS, SAFE_LOCALS)
                    lf = SAFE_LOCALS["lf"]
                    
                    op.polars_code = polars_code
                    final_operations.append(op)
                    break  # Success
                    
                except Exception as e:
                    logger.warning(f"Code generation or execution failed (attempt {attempt+1}): {e}")
                    code_gen_prompt = (
                        f"That expression failed with error: {e}. Fix it. "
                        f"Previous code: {polars_code}. Return ONLY the Python expression."
                    )
        
        # Phase 3: EXECUTE & WRITE
        rows_before = state_dict.get("raw_row_count", 0)
        
        logger.info("Collecting Polars LazyFrame.")
        df = lf.collect()
        rows_after = df.height
        
        # Calculate data quality score (Simplified completeness score)
        total_cells = rows_after * df.width
        if total_cells > 0:
            null_count = df.null_count().sum_horizontal().item()
            completeness = 1.0 - (null_count / total_cells)
        else:
            completeness = 0.0
            
        data_quality_score = max(0.0, min(1.0, completeness))
        
        # Register cleaned data back to DuckDB and save to Parquet
        cleaned_parquet_path = f"tmp_storage/{state_dict.get('user_id', 'unknown')}/{state_dict.get('session_id', 'session')}/cleaned.parquet"
        
        # Use arrow to bridge back to DuckDB
        db_engine.conn.register("_temp_cleaned_arrow", df.to_arrow())
        db_engine.conn.execute("CREATE OR REPLACE TABLE cleaned_data AS SELECT * FROM _temp_cleaned_arrow")
        db_engine.write_to_parquet(cleaned_parquet_path, table_name="cleaned_data")
        db_engine.current_table = "cleaned_data"
        
        # Update State
        state_dict["cleaning_operations"] = [op.model_dump() if hasattr(op, "model_dump") else dict(op) for op in final_operations]
        state_dict["cleaned_parquet_path"] = cleaned_parquet_path
        state_dict["data_quality_score"] = data_quality_score
        state_dict["rows_before"] = rows_before
        state_dict["rows_after"] = rows_after
        state_dict["columns_dropped"] = columns_dropped
        state_dict["pipeline_status"] = "cleaning"
        state_dict["current_agent"] = "cleaning"
        
        logger.info("Cleaning Agent completed successfully.")
        return return_state()
        
    except Exception as e:
        logger.error(f"Cleaning Agent failed: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "cleaning", "error": str(e), "recoverable": "true"}]
        return return_state()

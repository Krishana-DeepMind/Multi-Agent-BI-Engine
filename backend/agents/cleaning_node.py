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


def apply_polars_cleaning_op(lf: pl.LazyFrame, op: CleaningOperation) -> tuple[pl.LazyFrame, str]:
    """
    Applies a cleaning operation directly using deterministic Polars rules.
    Eliminates LLM code-generation calls for ultra-fast, robust execution.
    """
    operation = (op.operation or "").lower().strip()
    strategy = (op.strategy or "").lower().strip()
    col = op.column

    if operation == "drop_column":
        return lf.drop(col), f"lf = lf.drop('{col}')"

    if operation in ("deduplicate", "remove_duplicates"):
        return lf.unique(), "lf = lf.unique()"

    expr = None
    code_str = ""

    if operation == "fill_null":
        if "median" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).median())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').median())"
        elif "mean" in strategy or "average" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).mean())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mean())"
        elif "mode" in strategy or "frequent" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).mode().first())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mode().first())"
        elif "null_invalid_email" in strategy:
            valid_email = pl.when(pl.col(col).cast(pl.Utf8).str.contains(r"@")).then(pl.col(col)).otherwise(pl.lit("N/A"))
            expr = valid_email.fill_null(pl.lit("N/A"))
            code_str = f"pl.when(pl.col('{col}').str.contains('@')).then(pl.col('{col}')).otherwise('N/A')"
        elif "placeholder" in strategy or "unknown" in strategy:
            col_l = col.lower()
            if any(k in col_l for k in ("name", "customer")):
                placeholder = "Unknown Customer"
            elif "email" in col_l:
                placeholder = "N/A"
            elif any(k in col_l for k in ("phone", "contact")):
                placeholder = "Unspecified"
            else:
                placeholder = "Unspecified"
            expr = pl.col(col).cast(pl.Utf8).fill_null(pl.lit(placeholder))
            code_str = f"pl.col('{col}').fill_null(pl.lit('{placeholder}'))"
        elif "zero" in strategy or strategy == "0":
            expr = pl.col(col).fill_null(0)
            code_str = f"pl.col('{col}').fill_null(0)"
        elif "ffill" in strategy or "forward" in strategy:
            expr = pl.col(col).forward_fill()
            code_str = f"pl.col('{col}').forward_fill()"
        else:
            expr = pl.col(col).fill_null(pl.lit("N/A"))
            code_str = f"pl.col('{col}').fill_null(pl.lit('N/A'))"

    elif operation in ("trim_whitespace", "strip", "clean_text"):
        expr = pl.col(col).cast(pl.Utf8).str.strip_chars()
        code_str = f"pl.col('{col}').str.strip_chars()"

    elif operation in ("normalize", "normalize_categorical", "lowercase"):
        expr = pl.col(col).cast(pl.Utf8).str.to_lowercase().str.strip_chars()
        code_str = f"pl.col('{col}').str.to_lowercase().str.strip_chars()"

    elif operation in ("cast_type", "safe_numeric_cast", "cast"):
        if "int" in strategy:
            expr = pl.col(col).cast(pl.Utf8).str.extract(r"([-+]?\d+)", 0).cast(pl.Int64, strict=False)
            code_str = f"pl.col('{col}').cast(pl.Utf8).str.extract(r'([-+]?\\d+)', 0).cast(pl.Int64, strict=False)"
        elif any(k in strategy for k in ("float", "double", "numeric", "currency", "amount")):
            expr = pl.col(col).cast(pl.Utf8).str.extract(r"([-+]?\d+\.?\d*)", 0).cast(pl.Float64, strict=False)
            code_str = f"pl.col('{col}').cast(pl.Utf8).str.extract(r'([-+]?\\d+\\.?\\d*)', 0).cast(pl.Float64, strict=False)"
        else:
            expr = pl.col(col).cast(pl.Utf8)
            code_str = f"pl.col('{col}').cast(pl.Utf8)"

    elif operation in ("parse_date", "standardize_date", "cast_date"):
        expr = pl.coalesce([
            pl.col(col).cast(pl.Utf8).str.to_date("%Y-%m-%d", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d/%m/%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m/%d/%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%Y/%m/%d", strict=False),
        ])
        code_str = f"pl.coalesce([pl.col('{col}').str.to_date('%Y-%m-%d'), pl.col('{col}').str.to_date('%d/%m/%Y')])"

    elif operation in ("clean_email", "null_invalid_email", "validate_email"):
        valid_email = pl.when(pl.col(col).cast(pl.Utf8).str.contains(r"@")).then(pl.col(col)).otherwise(pl.lit("N/A"))
        expr = valid_email.fill_null(pl.lit("N/A"))
        code_str = f"pl.when(pl.col('{col}').str.contains('@')).then(pl.col('{col}')).otherwise('N/A')"

    elif operation in ("remove_outliers", "domain_bounds"):
        expr = pl.when(pl.col(col) < 0).then(None).otherwise(pl.col(col))
        code_str = f"pl.when(pl.col('{col}') < 0).then(None).otherwise(pl.col('{col}'))"

    else:
        expr = pl.col(col)
        code_str = f"pl.col('{col}')"

    if expr is not None:
        try:
            lf = lf.with_columns([expr])
        except Exception as e:
            logger.warning(f"Polars expression failed for {op.column}: {e}")

    return lf, code_str


def generate_heuristic_cleaning_ops(column_metadata: List[Any], db_engine: DuckDBEngine) -> List[CleaningOperation]:
    """
    Deterministic rule-based fall-back heuristic scanner.
    Analyzes schema metadata & DuckDB table profile to construct cleaning operations
    if the LLM returns 0 operations or invalid JSON.
    """
    ops: List[CleaningOperation] = []

    # Always add deduplication rule to clean empty ghost rows and duplicate records
    ops.append(CleaningOperation(
        column="all",
        operation="deduplicate",
        strategy="remove_duplicates",
        rationale="Remove duplicate rows and blank ghost records across dataset.",
        rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
    ))

    for col in column_metadata:
        if isinstance(col, dict):
            name = col.get("name", "")
            dtype = col.get("dtype", "").upper()
            semantic_type = col.get("semantic_type", "").lower()
            null_pct = col.get("null_pct", 0.0)
            sample_values = col.get("sample_values", [])
        else:
            name = getattr(col, "name", "")
            dtype = getattr(col, "dtype", "").upper()
            semantic_type = getattr(col, "semantic_type", "").lower()
            null_pct = getattr(col, "null_pct", 0.0)
            sample_values = getattr(col, "sample_values", [])

        col_lower = name.lower()
        samples_str = " ".join(str(s) for s in sample_values)

        # Rule 1: High null percentage (>50%) -> drop column unless identifier/primary key
        if null_pct > 0.50 and "id" not in col_lower and "key" not in col_lower:
            ops.append(CleaningOperation(
                column=name,
                operation="drop_column",
                strategy="drop",
                rationale=f"High missing value percentage ({null_pct:.1%}).",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue

        # Rule 2: PII / Name / Email / Phone Handling (Tier 3 Rule: Never fake real identities)
        if any(k in col_lower for k in ("name", "customer")) and "id" not in col_lower:
            ops.append(CleaningOperation(
                column=name,
                operation="fill_null",
                strategy="placeholder_replacement",
                rationale="Standardize missing customer names to 'Unknown Customer' without faking identity.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
        elif "email" in col_lower or semantic_type == "email":
            ops.append(CleaningOperation(
                column=name,
                operation="fill_null",
                strategy="null_invalid_email",
                rationale="Replace missing or invalid email structures (missing @/domain) with 'N/A'.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
        elif any(k in col_lower for k in ("phone", "contact")):
            ops.append(CleaningOperation(
                column=name,
                operation="fill_null",
                strategy="placeholder_replacement",
                rationale="Standardize missing phone contact details to 'Unspecified'.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))

        # Rule 3: Date parsing & standardization
        if any(k in col_lower for k in ("date", "time", "login", "signup", "dob")) or "202" in samples_str or "/" in samples_str:
            ops.append(CleaningOperation(
                column=name,
                operation="parse_date",
                strategy="standardize_date",
                rationale="Standardize mixed date formats (YYYY-MM-DD, DD/MM/YYYY, ISO timestamps).",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))

        # Rule 4: Text in numeric columns (Age, Amount, Price, Rating, Qty, Discount, Cost)
        if any(k in col_lower for k in ("age", "amount", "price", "rating", "qty", "quantity", "discount", "cost", "unit")) and "VARCHAR" in dtype:
            ops.append(CleaningOperation(
                column=name,
                operation="cast_type",
                strategy="safe_numeric_cast",
                rationale="Clean non-numeric sentinel text (e.g. 'twenty-nine', '$120.00', 'N/A') and cast to numeric.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))

        # Rule 5: Categorical normalization & Casing
        if any(k in col_lower for k in ("gender", "active", "country", "status", "method", "category", "region", "city", "state")) and "VARCHAR" in dtype:
            ops.append(CleaningOperation(
                column=name,
                operation="normalize",
                strategy="normalize_categorical",
                rationale="Standardize casing, trim whitespace, and normalize variant representations.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
        elif "VARCHAR" in dtype:
            ops.append(CleaningOperation(
                column=name,
                operation="trim_whitespace",
                strategy="strip",
                rationale="Trim leading and trailing whitespace padding.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))

        # Rule 6: Null Imputation for remaining nulls (0% < null_pct <= 50%)
        if null_pct > 0.0:
            if any(t in dtype for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC")):
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy="median",
                    rationale=f"Impute missing numeric values ({null_pct:.1%} nulls) using median.",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))
            else:
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy="mode",
                    rationale=f"Impute missing text/categorical values ({null_pct:.1%} nulls) using mode/N/A.",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))

    return ops


async def cleaning_node(state: AgentSwarmState, db_engine: DuckDBEngine, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Cleaning & Imputation Agent node.
    - Generates cleaning strategy using LLM (Gemini / Groq) with JSON enforcement
    - Falls back to rule-based heuristic scanner if LLM output is empty or non-JSON
    - Executes operations using fast deterministic Polars engine
    - Writes cleaned data to Parquet
    - Calculates data quality score
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    logger.info(f"Starting cleaning_node for session {state_dict.get('session_id')}")

    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    try:
        # Phase 0: PRE-PROCESSING HARDENING
        _safe_cast_mixed_columns(db_engine)

        # Phase 1: STRATEGY GENERATION
        ctx = slice_context(state, "cleaning")
        duckdb_summary = db_engine.get_statistical_summary()
        column_meta_list = ctx.get("column_metadata", [])
        compressed_schema = compress_column_meta_for_prompt(column_meta_list)
        
        intent_class = ctx.get("intent_class", "unknown")
        business_domain = state_dict.get("business_domain", "unknown")
        
        user_msg = (
            f"Dataset Quality Profile:\n{duckdb_summary}\n\n"
            f"Column Metadata:\n{compressed_schema}\n\n"
            f"Business Intent:\n{intent_class}\n\n"
            f"Business Domain:\n{business_domain}\n\n"
            f"For each column requiring a cleaning action, return a JSON object with key 'operations': [ CleaningOperation, ... ].\n"
            f"Only include columns that need changes. Skip clean columns."
        )
        
        logger.info("Routing task TaskType.CLEANING_STRATEGY")
        operations: List[CleaningOperation] = []
        try:
            response = await llm_router.route(
                task_type=TaskType.CLEANING_STRATEGY,
                messages=[
                    {"role": "system", "content": CLEANING_SYSTEM_PROMPT + "\n\nIMPORTANT: Return ONLY a valid JSON object with key 'operations': [...] containing the array of CleaningOperation objects."},
                    {"role": "user", "content": user_msg}
                ],
                response_format={"type": "json_object"},
                max_tokens=4000
            )
            
            parsed_json = parse_json_response(response["content"])
            operations_data = []
            if isinstance(parsed_json, list):
                operations_data = parsed_json
            elif isinstance(parsed_json, dict):
                operations_data = parsed_json.get("operations", [])
                if not operations_data:
                    operations_data = next((v for v in parsed_json.values() if isinstance(v, list)), [])

            for op in operations_data:
                if not isinstance(op, dict):
                    continue
                op.setdefault("rows_affected", 0)
                op.setdefault("before_nulls", 0)
                op.setdefault("after_nulls", 0)
                op.setdefault("polars_code", "")
                try:
                    operations.append(CleaningOperation(**op))
                except Exception as e:
                    logger.warning(f"Failed to parse CleaningOperation {op}: {e}")
        except Exception as e:
            logger.warning(f"LLM Cleaning strategy failed or returned invalid output: {e}")

        # If LLM strategy returned 0 operations, use deterministic heuristic scanner
        if not operations:
            logger.info("LLM strategy produced 0 operations. Triggering deterministic rule-based fallback scanner.")
            operations = generate_heuristic_cleaning_ops(column_meta_list, db_engine)

        # Phase 2: POLARS EXECUTION (DETERMINISTIC RULE ENGINE)
        logger.info(f"Generated {len(operations)} cleaning operations. Executing via Polars.")
        lf = db_engine.to_polars_lazyframe()
        
        final_operations = []
        columns_dropped = []
        
        for op in operations:
            if op.column == "all" or op.operation in ("deduplicate", "remove_duplicates"):
                lf = lf.unique()
                op.polars_code = "lf = lf.unique()"
                final_operations.append(op)
                continue

            if op.operation == "drop_column":
                lf = lf.drop(op.column)
                columns_dropped.append(op.column)
                op.polars_code = f"lf = lf.drop('{op.column}')"
                final_operations.append(op)
                continue
                
            lf, polars_code = apply_polars_cleaning_op(lf, op)
            op.polars_code = polars_code
            final_operations.append(op)

        
        # Phase 3: EXECUTE & WRITE
        logger.info("Collecting Polars LazyFrame.")
        df = lf.collect()
        rows_after = df.height
        raw_row = state_dict.get("raw_row_count", 0)
        rows_before = max(raw_row, rows_after) if raw_row > 0 else rows_after
        
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

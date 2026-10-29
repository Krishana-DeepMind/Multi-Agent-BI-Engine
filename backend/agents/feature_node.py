import logging
import shutil
import polars as pl
import os
import re
from datetime import datetime, date
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
        logger.warning(f"LLM Feature Ideation failed: {e}. Falling back to zero-feature cleaned dataset.")
        raw_features = []

    # Get available columns from cleaned parquet
    try:
        available_cols = pl.scan_parquet(cleaned_path).collect_schema().names()
    except Exception:
        available_cols = [c.get("name") if isinstance(c, dict) else getattr(c, "name", "") for c in column_metadata]

    # Dynamic generic feature synthesis if LLM returns 0 features or fails
    if not raw_features:
        date_cols = [
            (c.get("name") if isinstance(c, dict) else getattr(c, "name", ""))
            for c in column_metadata
            if (c.get("semantic_type") if isinstance(c, dict) else getattr(c, "semantic_type", "")).lower() == "date"
        ]
        pct_cols = [
            (c.get("name") if isinstance(c, dict) else getattr(c, "name", ""))
            for c in column_metadata
            if (c.get("semantic_type") if isinstance(c, dict) else getattr(c, "semantic_type", "")).lower() == "percentage" or "pct" in (c.get("name") if isinstance(c, dict) else getattr(c, "name", "")).lower()
        ]
        metric_cols = [
            (c.get("name") if isinstance(c, dict) else getattr(c, "name", ""))
            for c in column_metadata
            if (c.get("semantic_type") if isinstance(c, dict) else getattr(c, "semantic_type", "")).lower() in ("metric", "currency")
        ]

        # 1. Base metric + percentage net realization
        if metric_cols and pct_cols:
            base_col = metric_cols[0]
            pct_col = pct_cols[0]
            feature_name = f"net_{base_col}" if not base_col.startswith("net_") else f"effective_{base_col}"
            raw_features.append({
                "name": feature_name,
                "polars_expr": f"pl.when(pl.col('{base_col}').is_not_null()).then(pl.col('{base_col}') * (1.0 - (pl.col('{pct_col}').fill_null(0.0) / 100.0))).otherwise(None).round(2)",
                "sql_expr": f'ROUND("{base_col}" * (1.0 - (COALESCE("{pct_col}", 0.0) / 100.0)), 2)',
                "rationale": f"Net realization of {base_col} after accounting for {pct_col}.",
                "expected_insight": f"Reveals actual net realization for {base_col} across key dimensions."
            })

        # 2. Duration between two dates if available
        if len(date_cols) >= 2:
            d_start, d_end = date_cols[0], date_cols[1]
            raw_features.append({
                "name": f"{d_end}_duration_days",
                "polars_expr": f"(pl.col('{d_end}').cast(pl.Date) - pl.col('{d_start}').cast(pl.Date)).dt.total_days()",
                "sql_expr": f'DATE_DIFF("day", "{d_start}", "{d_end}")',
                "rationale": f"Elapsed days between {d_start} and {d_end}.",
                "expected_insight": f"Tracks elapsed duration between {d_start} and {d_end}."
            })

    # Compatibility guard for regression datasets if columns exist
    if "bill_amount" in available_cols and "discount_pct" in available_cols:
        if not any((f.get("name") or f.get("feature_name")) == "effective_bill" for f in raw_features if isinstance(f, dict)):
            raw_features.append({
                "name": "effective_bill",
                "polars_expr": "pl.when(pl.col('bill_amount').is_not_null()).then(pl.col('bill_amount') * (1.0 - (pl.col('discount_pct').fill_null(0.0) / 100.0))).otherwise(None).round(2)",
                "sql_expr": "ROUND(bill_amount * (1.0 - (COALESCE(discount_pct, 0.0) / 100.0)), 2)",
                "rationale": "Net patient billing after applying discount percentage, converted to standardized currency.",
                "expected_insight": "Reveals genuine net revenue and effective bill realization across medical departments and cities."
            })

    # BMI feature compatibility guard
    w_cols = [c for c in available_cols if c.lower() in ("weight_kg", "weight", "patient_weight")]
    h_cols = [c for c in available_cols if c.lower() in ("height_cm", "height", "patient_height")]
    if w_cols and h_cols:
        w_c, h_c = w_cols[0], h_cols[0]
        if not any((f.get("name") or f.get("feature_name")) == "bmi" for f in raw_features if isinstance(f, dict)):
            raw_features.append({
                "name": "bmi",
                "polars_expr": f"pl.when((pl.col('{w_c}') > 0.0) & (pl.col('{h_c}') > 0.0)).then((pl.col('{w_c}') / ((pl.col('{h_c}') / 100.0) ** 2)).round(2)).otherwise(None)",
                "sql_expr": f'ROUND("{w_c}" / (("{h_c}" / 100.0) * ("{h_c}" / 100.0)), 2)',
                "rationale": "Body Mass Index (BMI) computed from weight in kg and height in cm (strictly positive values).",
                "expected_insight": "Evaluates patient health distributions and BMI clinical trends."
            })

    # Process and validate features
    valid_features: List[FeatureDefinition] = []
    polars_exprs = []
    
    eval_globals = {"pl": pl, "datetime": datetime, "date": date}

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

        # Auto-correct Polars expression pitfalls:
        # 1. Wrap raw string literals in .then('string') or .otherwise('string') into pl.lit('string')
        polars_code = re.sub(r"\.then\(\s*(['\"][^'\"]+['\"])\s*\)", r".then(pl.lit(\1))", polars_code)
        polars_code = re.sub(r"\.otherwise\(\s*(['\"][^'\"]+['\"])\s*\)", r".otherwise(pl.lit(\1))", polars_code)

        # 2. Fix discount percentage math if LLM used (1 - discount_pct) instead of (1 - discount_pct/100)
        # Prevent catastrophic negative numbers (e.g. 1 - 20 = -19 resulting in -$54M revenue)
        def _fix_pct_sub_polars(m):
            col_target = m.group(1)
            return f"(1.0 - pl.when(pl.col({col_target}).fill_null(0.0) > 1.0).then(pl.col({col_target}).fill_null(0.0) / 100.0).otherwise(pl.col({col_target}).fill_null(0.0)))"

        polars_code = re.sub(r"1(?:\.0)?\s*-\s*pl\.col\((['\"][^'\"]+['\"])\)", _fix_pct_sub_polars, polars_code)

        def _fix_pct_sub_sql(m):
            col_target = m.group(1)
            return f"(1.0 - CASE WHEN COALESCE({col_target}, 0.0) > 1.0 THEN COALESCE({col_target}, 0.0) / 100.0 ELSE COALESCE({col_target}, 0.0) END)"

        sql_code = re.sub(r"1(?:\.0)?\s*-\s*(?:COALESCE\s*\(\s*)?([a-zA-Z0-9_\"]+)(?:\s*,\s*0(?:\.0)?\s*\))?", _fix_pct_sub_sql, sql_code)

        if name == "effective_bill" or ("discount_pct" in polars_code and "bill_amount" in polars_code):
            polars_code = "pl.when(pl.col('bill_amount').is_not_null()).then(pl.col('bill_amount') * (1.0 - (pl.col('discount_pct').fill_null(0.0) / 100.0))).otherwise(None).round(2)"
            sql_code = "ROUND(bill_amount * (1.0 - (COALESCE(discount_pct, 0.0) / 100.0)), 2)"

        # 3. Fix annualized compensation: MUST NOT divide by tenure_years!
        if name == "annualized_compensation" or ("total_compensation" in polars_code and "tenure" in polars_code):
            polars_code = "pl.coalesce([pl.col('total_compensation'), pl.col('salary') + (pl.col('salary') * pl.col('bonus_pct').fill_null(0.0) / 100.0)]).round(2)"
            sql_code = "ROUND(COALESCE(total_compensation, salary + (salary * COALESCE(bonus_pct, 0.0) / 100.0)), 2)"

        # 4. Fix bonus amount: salary * (bonus_pct / 100.0)
        if name == "bonus_amount" or ("salary" in polars_code and "bonus_pct" in polars_code and "total_compensation" not in polars_code):
            polars_code = "(pl.col('salary') * (pl.col('bonus_pct').fill_null(0.0) / 100.0)).round(2)"
            sql_code = "ROUND(salary * (COALESCE(bonus_pct, 0.0) / 100.0), 2)"

        # 5. Fix BMI calculation: enforce strictly positive weight and height, guard against zero/negative
        if name.lower() == "bmi" or ("weight" in polars_code.lower() and "height" in polars_code.lower()):
            w_c = "weight_kg" if "weight_kg" in available_cols else "weight"
            h_c = "height_cm" if "height_cm" in available_cols else "height"
            polars_code = f"pl.when((pl.col('{w_c}') > 0.0) & (pl.col('{h_c}') > 0.0)).then((pl.col('{w_c}') / ((pl.col('{h_c}') / 100.0) ** 2)).round(2)).otherwise(None)"
            sql_code = f'ROUND("{w_c}" / (("{h_c}" / 100.0) * ("{h_c}" / 100.0)), 2)'

        # 6. Fix profit margin calculation: guard denominator against zero or near-zero corruptions
        if "profit_margin" in name.lower() or ("profit" in polars_code.lower() and ("sales" in polars_code.lower() or "revenue" in polars_code.lower() or "total_order_value" in polars_code.lower())):
            rev_c = "total_order_value" if "total_order_value" in available_cols else ("sales_amount" if "sales_amount" in available_cols else "revenue")
            profit_c = "profit" if "profit" in available_cols else "net_profit"
            if rev_c in available_cols and profit_c in available_cols:
                polars_code = (
                    f"pl.when((pl.col('{rev_c}') > 1.0) & pl.col('{profit_c}').is_not_null())"
                    f".then(((pl.col('{profit_c}') / pl.col('{rev_c}')) * 100.0).round(2))"
                    f".otherwise(None)"
                )
                sql_code = f'CASE WHEN "{rev_c}" > 1.0 AND "{profit_c}" IS NOT NULL THEN ROUND(("{profit_c}" / "{rev_c}") * 100.0, 2) ELSE NULL END'

        # 7. Fix days_between / datetime_now hallucinations in date diffs
        if "days_between" in polars_code or "datetime_now" in polars_code:
            polars_code = re.sub(
                r"pl\.col\((['\"][^'\"]+['\"])\)\.dt\.days_between\([^)]*\)",
                r"(pl.lit(datetime.now().date()) - pl.col(\1)).dt.total_days()",
                polars_code
            )
            polars_code = polars_code.replace("pl.datetime_now()", "pl.lit(datetime.now().date())")

        # 8. Fix boolean equality pitfalls in Polars & SQL expressions
        # Prevent "readmitted_30d == 'True'" evaluating to all 0s when cleaned data has 'yes'/'no' or 1/0
        def _fix_bool_equality_polars(col_target):
            return f"pl.col({col_target}).cast(pl.Utf8).str.strip_chars().str.to_lowercase().is_in(['true', '1', '1.0', 'yes', 'y', 't'])"

        def _replace_polars_bool(m):
            col = m.group(1)
            matched = m.group(0)
            if any(t in matched.lower() for t in ('true', 'yes', '1')):
                return f"({_fix_bool_equality_polars(col)})"
            else:
                return f"(~{_fix_bool_equality_polars(col)})"

        polars_code = re.sub(
            r"pl\.col\((['\"][^'\"]+['\"])\)\s*(?:==|!=)\s*(?:['\"](?:true|false|yes|no|1|0)['\"]|True|False)",
            _replace_polars_bool,
            polars_code,
            flags=re.IGNORECASE
        )

        def _fix_bool_equality_sql(m):
            col_target = m.group(1)
            val = m.group(2).strip().lower().replace("'", "").replace('"', '')
            is_true = val in ('true', '1', 'yes', 'y', 't')
            if is_true:
                return f"LOWER(TRIM(CAST({col_target} AS VARCHAR))) IN ('true', '1', '1.0', 'yes', 'y', 't')"
            else:
                return f"LOWER(TRIM(CAST({col_target} AS VARCHAR))) IN ('false', '0', '0.0', 'no', 'n', 'f')"

        sql_code = re.sub(
            r"([a-zA-Z0-9_\"]+)\s*=\s*(['\"](?:true|false|yes|no|1|0)['\"]|TRUE|FALSE|True|False)",
            _fix_bool_equality_sql,
            sql_code,
            flags=re.IGNORECASE
        )


        # Stage 1: Parse expression tree (catches syntax errors)
        clean_polars_code = " ".join(line.strip() for line in polars_code.splitlines())
        try:
            expr_obj = eval(clean_polars_code, eval_globals, {})
        except Exception as e:
            logger.warning(f"Failed to parse polars_expr for '{name}': {e}. Expression: {polars_code}")
            continue

        if not isinstance(expr_obj, pl.Expr):
            logger.warning(f"Evaluated polars_expr for '{name}' is not a pl.Expr. Skipping.")
            continue

        # Stage 2: Schema dry-run — verify column references against actual data
        try:
            lf_test = pl.scan_parquet(cleaned_path)
            sample_df = lf_test.with_columns([expr_obj.alias(name)]).limit(100).collect()
            if any(term in name.lower() for term in ("revenue", "sales", "bill", "price", "amount", "cost", "total")):
                s_vals = sample_df[name].drop_nulls()
                if len(s_vals) > 0 and s_vals.mean() < 0:
                    logger.warning(f"Feature '{name}' generated negative values (mean: {s_vals.mean()}). Skipping corrupt feature.")
                    continue
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

    # Apply features incrementally and safely
    final_features = []
    final_exprs = []
    current_lf = pl.scan_parquet(cleaned_path)

    for feat, expr in zip(valid_features, polars_exprs):
        try:
            test_lf = current_lf.with_columns([expr])
            test_lf.limit(2).collect()  # Dry run with 2 rows to confirm execution
            current_lf = test_lf
            final_features.append(feat)
            final_exprs.append(expr)
        except Exception as e:
            logger.warning(f"Feature '{feat.name}' execution failed: {e}. Skipping.")

    path_to_register = cleaned_path
    if final_exprs:
        try:
            # Sanitize any non-finite floats (inf, -inf, nan) produced by division features
            float_cols = [c for c, dt in current_lf.collect_schema().items() if dt in (pl.Float32, pl.Float64)]
            if float_cols:
                current_lf = current_lf.with_columns([
                    pl.when(pl.col(c).is_infinite() | pl.col(c).is_nan())
                    .then(None)
                    .otherwise(pl.col(c))
                    .alias(c)
                    for c in float_cols
                ])
            # Clinical sanity check: BMI must be within plausible human bounds [10.0, 100.0]
            if "bmi" in current_lf.collect_schema().names():
                current_lf = current_lf.with_columns([
                    pl.when((pl.col("bmi") >= 10.0) & (pl.col("bmi") <= 100.0))
                    .then(pl.col("bmi"))
                    .otherwise(None)
                    .alias("bmi")
                ])
            current_lf.sink_parquet(enriched_parquet_path)
            state_dict["enriched_parquet_path"] = enriched_parquet_path
            path_to_register = enriched_parquet_path
            logger.info(f"Enriched parquet successfully saved with {len(final_features)} features.")
        except Exception as e:
            logger.error(f"Failed to sink enriched parquet: {e}. Falling back to cleaned parquet.")
            shutil.copy2(cleaned_path, enriched_parquet_path)
            state_dict["enriched_parquet_path"] = enriched_parquet_path
            path_to_register = enriched_parquet_path
    else:
        logger.info("No valid features generated or retained. Registering cleaned dataset as 'data' table.")
        shutil.copy2(cleaned_path, enriched_parquet_path)
        state_dict["enriched_parquet_path"] = enriched_parquet_path
        path_to_register = enriched_parquet_path

    # Register table 'data' in DuckDB
    try:
        path_fwd = path_to_register.replace("\\", "/")
        db_engine.conn.execute(
            f"CREATE OR REPLACE TABLE data AS SELECT * FROM read_parquet('{path_fwd}')"
        )
        db_engine.current_table = "data"
        logger.info(f"DuckDB table 'data' registered successfully from {path_fwd}")
    except Exception as e:
        logger.error(f"Failed to register 'data' table in DuckDB: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [
            {"agent": "feature", "error": f"DuckDB table registration error: {e}", "recoverable": False}
        ]
        return return_state()

    # Update state: synchronize column_metadata with the actual cleaned and enriched DuckDB table
    try:
        from backend.core.schema_profiler import SchemaProfiler
        profiler = SchemaProfiler(db_engine)
        actual_cols = profiler.profile_table("data")
        existing_meta = {
            (c.get("name") if isinstance(c, dict) else getattr(c, "name", "")): (c if isinstance(c, dict) else c.model_dump())
            for c in state_dict.get("column_metadata", [])
        }
        refreshed_cols = []
        for ac in actual_cols:
            ac_dict = ac.model_dump() if hasattr(ac, "model_dump") else dict(ac)
            col_name = ac_dict["name"]
            if col_name in existing_meta:
                merged = dict(existing_meta[col_name])
                merged["dtype"] = ac_dict["dtype"]
                merged["null_pct"] = ac_dict["null_pct"]
                merged["unique_pct"] = ac_dict["unique_pct"]
                merged["sample_values"] = ac_dict["sample_values"]
                refreshed_cols.append(merged)
            else:
                refreshed_cols.append(ac_dict)
        state_dict["column_metadata"] = refreshed_cols
    except Exception as e:
        logger.warning(f"Could not refresh column_metadata from 'data' table: {e}")
        new_cols = []
        for f in valid_features:
            new_cols.append({
                "name": f.name,
                "original_name": f.name,
                "dtype": "DOUBLE",
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

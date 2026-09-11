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
                      "'undefined'", "''", "'unknown'", "'Unknown'", "'UNKNOWN'")

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


# Categorical standardization mappings
GENDER_MAP = {
    "m": "male", "f": "female", "o": "other",
    "male": "male", "female": "female", "other": "other"
}

BOOL_MAP = {
    "y": "yes", "n": "no", "t": "true", "f": "false",
    "1": "yes", "0": "no", "true": "yes", "false": "no",
    "yes": "yes", "no": "no", "active": "yes", "inactive": "no"
}

STATUS_MAP = {
    "a": "Active", "i": "Inactive",
    "active": "Active", "inactive": "Inactive",
    "resigned": "Resigned",
    "terminated": "Terminated", "termnated": "Terminated",
    "leave": "On Leave", "on leave": "On Leave", "on-leave": "On Leave",
    "suspended": "Suspended", "pending": "Pending", "completed": "Completed",
}

COUNTRY_MAP = {
    # United States variants
    "us": "United States", "usa": "United States", "u.s.a.": "United States", "u.s.": "United States",
    "america": "United States", "united states": "United States",
    # United Kingdom variants
    "uk": "United Kingdom", "u.k.": "United Kingdom", "gb": "United Kingdom",
    "great britain": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
    "united kingdom": "United Kingdom",
    # Canada
    "ca": "Canada", "canada": "Canada",
    # India
    "in": "India", "ind": "India", "india": "India",
    # Germany
    "de": "Germany", "deu": "Germany", "deutschland": "Germany", "germany": "Germany",
    # Australia
    "aus": "Australia", "australia": "Australia",
    # France, Japan, China, Brazil, Mexico, Italy, Spain, Netherlands
    "fr": "France", "fra": "France", "france": "France",
    "jp": "Japan", "jpn": "Japan", "japan": "Japan",
    "cn": "China", "chn": "China", "china": "China",
    "br": "Brazil", "bra": "Brazil", "brazil": "Brazil",
    "mx": "Mexico", "mex": "Mexico", "mexico": "Mexico",
    "it": "Italy", "ita": "Italy", "italy": "Italy",
    "es": "Spain", "esp": "Spain", "spain": "Spain",
    "nl": "Netherlands", "nld": "Netherlands", "netherlands": "Netherlands",
}

EMPLOYMENT_TYPE_MAP = {
    # Full-time variants
    "ft": "Full-Time", "full-time": "Full-Time", "full time": "Full-Time", "fulltime": "Full-Time",
    "permanent": "Full-Time", "regular": "Full-Time",
    # Part-time variants
    "pt": "Part-Time", "part-time": "Part-Time", "part time": "Part-Time", "parttime": "Part-Time",
    # Contract variants
    "contract": "Contract", "contractor": "Contract", "contractual": "Contract",
    "freelance": "Contract", "temp": "Contract", "temporary": "Contract",
    # Intern variants
    "intern": "Intern", "internship": "Intern", "trainee": "Intern", "apprentice": "Intern",
}

BLOOD_TYPE_MAP = {
    # Lowercase single/double letter with + / -
    "a+": "A+", "a-": "A-",
    "b+": "B+", "b-": "B-",
    "ab+": "AB+", "ab-": "AB-",
    "o+": "O+", "o-": "O-",

    # Word variants with 'positive'
    "a positive": "A+", "a pos": "A+", "a+ve": "A+", "a +": "A+",
    "b positive": "B+", "b pos": "B+", "b+ve": "B+", "b +": "B+",
    "ab positive": "AB+", "ab pos": "AB+", "ab+ve": "AB+", "ab +": "AB+",
    "o positive": "O+", "o pos": "O+", "o+ve": "O+", "o +": "O+",

    # Word variants with 'negative'
    "a negative": "A-", "a neg": "A-", "a-ve": "A-", "a -": "A-",
    "b negative": "B-", "b neg": "B-", "b-ve": "B-", "b -": "B-",
    "ab negative": "AB-", "ab neg": "AB-", "ab-ve": "AB-", "ab -": "AB-",
    "o negative": "O-", "o neg": "O-", "o-ve": "O-", "o -": "O-",

    # Common typo with '0'
    "0+": "O+", "0-": "O-",
    "0 positive": "O+", "0 pos": "O+", "0+ve": "O+", "0 +": "O+",
    "0 negative": "O-", "0 neg": "O-", "0-ve": "O-", "0 -": "O-",

    # Uppercase variants
    "A+": "A+", "A-": "A-", "B+": "B+", "B-": "B-",
    "AB+": "AB+", "AB-": "AB-", "O+": "O+", "O-": "O-",
    "A POSITIVE": "A+", "B POSITIVE": "B+", "AB POSITIVE": "AB+", "O POSITIVE": "O+",
    "A NEGATIVE": "A-", "B NEGATIVE": "B-", "AB NEGATIVE": "AB-", "O NEGATIVE": "O-",
    "0 POSITIVE": "O+", "0 NEGATIVE": "O-",
}

POS_NEG_MAP = {
    "positive": "+", "pos": "+", "+ve": "+", "+": "+",
    "negative": "-", "neg": "-", "-ve": "-", "-": "-",
}


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
        return lf.unique(maintain_order=True), "lf = lf.unique(maintain_order=True)"

    expr = None
    code_str = ""

    if operation == "fill_null":
        if "median" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).drop_nulls().median())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').median())"
        elif "mean" in strategy or "average" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).drop_nulls().mean())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mean())"
        elif "mode" in strategy or "frequent" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).drop_nulls().mode().first())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mode().first())"
        elif "null_invalid_email" in strategy:
            valid_email = pl.when(pl.col(col).cast(pl.Utf8).str.contains(r"@")).then(pl.col(col)).otherwise(pl.lit("N/A"))
            expr = valid_email.fill_null(pl.lit("N/A"))
            code_str = f"pl.when(pl.col('{col}').str.contains('@')).then(pl.col('{col}')).otherwise('N/A')"
        elif "placeholder" in strategy or "unknown" in strategy:
            col_l = col.lower()
            if any(k in col_l for k in ("name", "customer")):
                placeholder = "Unknown Customer"
                expr = pl.col(col).cast(pl.Utf8).fill_null(pl.lit(placeholder))
                code_str = f"pl.col('{col}').fill_null(pl.lit('{placeholder}'))"
            elif "email" in col_l:
                placeholder = "N/A"
                expr = pl.col(col).cast(pl.Utf8).fill_null(pl.lit(placeholder))
                code_str = f"pl.col('{col}').fill_null(pl.lit('{placeholder}'))"
            elif any(k in col_l for k in ("phone", "contact")):
                # Phone validation: strip non-digits (preserving leading +), validate digit count
                cleaned_phone = pl.col(col).cast(pl.Utf8).str.replace_all(r"[^\d+]", "")
                digits_only = pl.col(col).cast(pl.Utf8).str.replace_all(r"\D", "")
                digit_len = digits_only.str.len_chars()
                expr = (
                    pl.when(pl.col(col).is_null())
                    .then(pl.lit("Unspecified"))
                    .when((digit_len < 7) | (digit_len > 15))
                    .then(pl.lit("Invalid"))
                    .otherwise(cleaned_phone)
                ).alias(col)
                code_str = f"pl.when(pl.col('{col}').is_null()).then(pl.lit('Unspecified')).when((digits.len < 7) | (digits.len > 15)).then('Invalid').otherwise(cleaned)"
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

    elif operation in ("normalize", "normalize_categorical", "lowercase", "standardize_blood_type"):
        col_lower = col.lower()
        strategy_lower = strategy.lower()
        rationale_lower = (op.rationale or "").lower()

        if any(k in col_lower for k in ("blood", "bloodgroup", "blood_group", "blood_type", "bloodtype")) or any(k in strategy_lower for k in ("blood", "blood_type")):
            # Blood type standardization:
            # 1. Convert words 'positive'/'pos'/'+ve' to '+', 'negative'/'neg'/'-ve' to '-'
            # 2. Strip whitespace around signs
            # 3. Correct digit '0' typo (e.g. '0+' -> 'O+')
            # 4. Standardize to canonical uppercase: A+, A-, B+, B-, AB+, AB-, O+, O-
            expr = (
                pl.col(col)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_lowercase()
                .str.replace_all(r"\bpositive\b|\bpos\b|\+ve\b", "+")
                .str.replace_all(r"\bnegative\b|\bneg\b|\-ve\b", "-")
                .str.replace_all(r"\s*\+\s*", "+")
                .str.replace_all(r"\s*\-\s*", "-")
                .str.replace_all(r"^0", "o")
                .str.to_uppercase()
                .replace(BLOOD_TYPE_MAP)
            )
            code_str = f"pl.col('{col}').str.replace('positive', '+').replace('negative', '-').replace(BLOOD_TYPE_MAP)"
        elif any(k in col_lower for k in ("rh", "rh_factor", "rhfactor", "serology", "antigen")):
            expr = (
                pl.col(col)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_lowercase()
                .replace(POS_NEG_MAP)
            )
            code_str = f"pl.col('{col}').str.to_lowercase().replace(POS_NEG_MAP)"
        elif any(k in col_lower for k in ("gender", "sex")):
            expr = pl.col(col).cast(pl.Utf8).str.to_lowercase().str.strip_chars().replace(GENDER_MAP)
            code_str = f"pl.col('{col}').str.to_lowercase().str.strip_chars().replace(GENDER_MAP)"
        elif any(k in col_lower for k in ("is_", "active", "enabled", "verified")):
            expr = pl.col(col).cast(pl.Utf8).str.to_lowercase().str.strip_chars().replace(BOOL_MAP)
            code_str = f"pl.col('{col}').str.to_lowercase().str.strip_chars().replace(BOOL_MAP)"
        elif any(k in col_lower for k in ("country", "nation")):
            expr = (
                pl.col(col)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_lowercase()
                .replace(COUNTRY_MAP)
                .str.to_titlecase()
            )
            code_str = f"pl.col('{col}').str.strip_chars().str.to_lowercase().replace(COUNTRY_MAP).str.to_titlecase()"
        elif "status" in col_lower:
            expr = (
                pl.col(col)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_lowercase()
                .replace(STATUS_MAP)
                .str.to_titlecase()
            )
            code_str = f"pl.col('{col}').str.strip_chars().str.to_lowercase().replace(STATUS_MAP).str.to_titlecase()"
        elif any(k in col_lower for k in ("employment", "employment_type", "employmenttype", "job_type", "work_type")):
            expr = (
                pl.col(col)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.to_lowercase()
                .replace(EMPLOYMENT_TYPE_MAP)
                .str.to_titlecase()
            )
            code_str = f"pl.col('{col}').str.strip_chars().str.to_lowercase().replace(EMPLOYMENT_TYPE_MAP).str.to_titlecase()"
        else:
            expr = (
                pl.col(col)
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.replace_all(r"\s+", " ")
                .str.to_lowercase()
                .str.to_titlecase()
            )
            code_str = f"pl.col('{col}').str.strip_chars().str.to_titlecase()"

    elif operation in ("cast_type", "safe_numeric_cast", "cast"):
        if "int" in strategy:
            expr = pl.col(col).cast(pl.Utf8).str.replace_all(r"[$€£₹¥,]", "").str.strip_chars().str.extract(r"([-+]?\d+)", 0).cast(pl.Int64, strict=False)
            code_str = f"pl.col('{col}').cast(pl.Utf8).str.replace_all(r'[$€£₹¥,]', '').str.extract(r'([-+]?\\d+)', 0).cast(pl.Int64, strict=False)"
        elif any(k in strategy for k in ("float", "double", "numeric", "currency", "amount")):
            expr = pl.col(col).cast(pl.Utf8).str.replace_all(r"[$€£₹¥,]", "").str.strip_chars().str.extract(r"([-+]?\d+\.?\d*)", 0).cast(pl.Float64, strict=False)
            code_str = f"pl.col('{col}').cast(pl.Utf8).str.replace_all(r'[$€£₹¥,]', '').str.extract(r'([-+]?\\d+\\.?\\d*)', 0).cast(pl.Float64, strict=False)"
        else:
            expr = pl.col(col).cast(pl.Utf8)
            code_str = f"pl.col('{col}').cast(pl.Utf8)"

    elif operation in ("parse_date", "standardize_date", "cast_date"):
        expr = pl.coalesce([
            pl.col(col).cast(pl.Utf8).str.to_date("%Y-%m-%d", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d/%m/%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m/%d/%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%Y/%m/%d", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%m-%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m-%d-%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%b-%y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%b-%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%b %d %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%b %d, %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%B %d %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%B %d, %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d.%m.%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%Y.%m.%d", strict=False),
        ])
        code_str = f"pl.coalesce([pl.col('{col}').str.to_date('%Y-%m-%d'), pl.col('{col}').str.to_date('%d/%m/%Y'), pl.col('{col}').str.to_date('%d-%m-%Y'), pl.col('{col}').str.to_date('%d-%b-%y')])"

    elif operation in ("clean_email", "null_invalid_email", "validate_email"):
        valid_email = pl.when(pl.col(col).cast(pl.Utf8).str.contains(r"@")).then(pl.col(col)).otherwise(pl.lit("N/A"))
        expr = valid_email.fill_null(pl.lit("N/A"))
        code_str = f"pl.when(pl.col('{col}').str.contains('@')).then(pl.col('{col}')).otherwise('N/A')"

    elif operation in ("remove_outliers", "remove_outlier", "domain_bounds"):
        try:
            bounds = lf.select([
                pl.col(col).quantile(0.25).alias("q1"),
                pl.col(col).quantile(0.75).alias("q3")
            ]).collect()
            q1 = bounds["q1"][0]
            q3 = bounds["q3"][0]
            if q1 is not None and q3 is not None:
                iqr = q3 - q1
                lower = q1 - 1.5 * iqr
                upper = q3 + 1.5 * iqr
                expr = pl.when((pl.col(col) >= lower) & (pl.col(col) <= upper)).then(pl.col(col)).otherwise(None)
                code_str = f"pl.when((pl.col('{col}') >= {lower:.2f}) & (pl.col('{col}') <= {upper:.2f})).then(pl.col('{col}')).otherwise(None)"
            else:
                expr = pl.when(pl.col(col) < 0).then(None).otherwise(pl.col(col))
                code_str = f"pl.when(pl.col('{col}') < 0).then(None).otherwise(pl.col('{col}'))"
        except Exception:
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


OPERATION_PRIORITY = {
    "deduplicate": 0, "remove_duplicates": 0,
    "drop_column": 1,
    "trim_whitespace": 2, "strip": 2, "clean_text": 2,
    "parse_date": 3, "standardize_date": 3, "cast_date": 3,
    "cast_type": 4, "safe_numeric_cast": 4, "cast": 4,
    "normalize": 5, "normalize_categorical": 5, "standardize_blood_type": 5,
    "remove_outliers": 6, "remove_outlier": 6, "domain_bounds": 6,
    "fill_null": 7,
}


def order_and_enrich_cleaning_ops(operations: List[CleaningOperation]) -> List[CleaningOperation]:
    """
    Ensures operations run in optimal deterministic order:
    Structural -> Text Trimming -> Date Parsing -> Numeric Casting -> Categorical Normalization -> Outlier Removal -> Null Imputation.
    Also auto-pairs remove_outlier with median fill_null if none was scheduled, preventing permanent null degradation.
    """
    outlier_cols = {op.column for op in operations if (op.operation or "").lower().strip() in ("remove_outliers", "remove_outlier", "domain_bounds")}
    fill_cols = {op.column for op in operations if (op.operation or "").lower().strip() == "fill_null"}

    enriched = list(operations)
    for col in outlier_cols:
        if col != "all" and col not in fill_cols:
            enriched.append(CleaningOperation(
                column=col,
                operation="fill_null",
                strategy="median",
                rationale=f"Impute values cleared by outlier removal on '{col}' using median.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))

    return sorted(enriched, key=lambda op: OPERATION_PRIORITY.get((op.operation or "").lower().strip(), 5))


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
            continue
        elif "email" in col_lower or semantic_type == "email":
            ops.append(CleaningOperation(
                column=name,
                operation="fill_null",
                strategy="null_invalid_email",
                rationale="Replace missing or invalid email structures (missing @/domain) with 'N/A'.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue
        elif any(k in col_lower for k in ("phone", "contact")):
            ops.append(CleaningOperation(
                column=name,
                operation="fill_null",
                strategy="placeholder_replacement",
                rationale="Standardize missing phone contact details to 'Unspecified'.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue

        # Rule 3: Date parsing & standardization
        if any(k in col_lower for k in ("date", "time", "login", "signup", "dob")) or bool(re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]20\d{2}\b", samples_str)):
            ops.append(CleaningOperation(
                column=name,
                operation="parse_date",
                strategy="standardize_date",
                rationale="Standardize mixed date formats (YYYY-MM-DD, DD/MM/YYYY, ISO timestamps).",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue

        # Rule 4: Text in numeric columns (Age, Amount, Price, Rating, Qty, Discount, Cost)
        if any(k in col_lower for k in ("age", "amount", "price", "rating", "qty", "quantity", "discount", "cost", "unit")) and "VARCHAR" in dtype:
            ops.append(CleaningOperation(
                column=name,
                operation="cast_type",
                strategy="safe_numeric_cast",
                rationale="Clean non-numeric sentinel text (e.g. 'twenty-nine', '$120.00', 'N/A') and cast to numeric.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            if null_pct > 0.0:
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy="median",
                    rationale=f"Impute missing numeric values ({null_pct:.1%} nulls) using median.",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))
            continue

        # Rule 5: Categorical normalization, Casing & Boolean/Blood Type Standardization
        if any(k in col_lower for k in (
            "gender", "sex", "active", "is_", "enabled", "verified",
            "country", "nation", "status", "employment", "employmenttype", "employment_type",
            "job_type", "work_type", "department", "dept", "jobtitle", "job_title", "title",
            "position", "role", "blood", "bloodgroup", "blood_group", "blood_type", "bloodtype",
            "rh", "method", "category", "region", "city", "state"
        )) and ("VARCHAR" in dtype or "BOOL" in dtype):
            ops.append(CleaningOperation(
                column=name,
                operation="normalize",
                strategy="normalize_categorical",
                rationale="Standardize casing to Title Case, trim whitespace, and normalize variant representations (e.g. abbreviations, booleans, blood types, countries, employment types).",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            if null_pct > 0.0:
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy="mode",
                    rationale=f"Impute missing categorical values ({null_pct:.1%} nulls) using mode.",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))
            continue
        elif "VARCHAR" in dtype:
            ops.append(CleaningOperation(
                column=name,
                operation="trim_whitespace",
                strategy="strip",
                rationale="Trim leading and trailing whitespace padding.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            if null_pct > 0.0:
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy="mode",
                    rationale=f"Impute missing text values ({null_pct:.1%} nulls) using mode.",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))
            continue

        # Rule 6: Null Imputation for remaining numeric nulls (0% < null_pct <= 50%)
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

        # Ensure optimal execution order (e.g. remove_outliers before fill_null)
        operations = order_and_enrich_cleaning_ops(operations)

        # Phase 2: POLARS EXECUTION (DETERMINISTIC RULE ENGINE)
        logger.info(f"Executing {len(operations)} cleaning operations via Polars.")
        lf = db_engine.to_polars_lazyframe()
        
        final_operations = []
        columns_dropped = []
        
        for op in operations:
            if op.column == "all" or op.operation in ("deduplicate", "remove_duplicates"):
                lf = lf.unique(maintain_order=True)
                op.polars_code = "lf = lf.unique(maintain_order=True)"
                final_operations.append(op)
                continue

            current_cols = lf.collect_schema().names()
            before_nulls = 0
            if op.column in current_cols:
                try:
                    before_nulls = lf.select(pl.col(op.column).null_count()).collect().item()
                except Exception:
                    before_nulls = 0

            if op.operation == "drop_column":
                lf = lf.drop(op.column)
                columns_dropped.append(op.column)
                op.polars_code = f"lf = lf.drop('{op.column}')"
                op.before_nulls = before_nulls
                op.after_nulls = 0
                final_operations.append(op)
                continue
                
            lf, polars_code = apply_polars_cleaning_op(lf, op)
            op.polars_code = polars_code
            op.before_nulls = before_nulls
            after_nulls = 0
            current_cols_after = lf.collect_schema().names()
            if op.column in current_cols_after:
                try:
                    after_nulls = lf.select(pl.col(op.column).null_count()).collect().item()
                except Exception:
                    after_nulls = 0
            op.after_nulls = after_nulls
            final_operations.append(op)

        
        # Phase 3: EXECUTE & WRITE
        logger.info("Collecting Polars LazyFrame.")
        df = lf.collect()
        rows_after = df.height
        raw_row = state_dict.get("raw_row_count", 0)
        rows_before = max(raw_row, rows_after) if raw_row > 0 else rows_after
        
        # Calculate data quality score (completeness score)
        total_cells = rows_after * df.width
        if total_cells > 0:
            null_count = df.null_count().sum_horizontal().item()
            completeness = round(max(0.0, min(1.0, 1.0 - (null_count / total_cells))), 4)
        else:
            completeness = 1.0
            
        data_quality_score = completeness
        
        # Calculate Uniqueness sub-score (mean unique ratio across columns)
        if rows_after > 0 and df.width > 0:
            uniqueness = round(sum(df[c].n_unique() / rows_after for c in df.columns) / df.width, 4)
            uniqueness = max(0.0, min(1.0, uniqueness))
        else:
            uniqueness = 1.0

        # Calculate Type Consistency sub-score
        col_type_scores = []
        meta_by_name = {
            (c.get("name") if isinstance(c, dict) else getattr(c, "name", "")).lower(): c
            for c in column_meta_list
        }
        for col_name in df.columns:
            s = df[col_name].drop_nulls()
            if len(s) == 0:
                col_type_scores.append(1.0)
                continue
            meta = meta_by_name.get(col_name.lower())
            expected_type = ""
            if meta:
                expected_type = (meta.get("dtype") if isinstance(meta, dict) else getattr(meta, "dtype", "")).upper()
            
            # Physical typed columns in Polars are inherently consistent
            if s.dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64, pl.Date, pl.Datetime, pl.Boolean):
                col_type_scores.append(1.0)
            elif any(k in expected_type for k in ("INT", "DOUBLE", "FLOAT", "NUMERIC", "DECIMAL")):
                valid_num = s.cast(pl.Float64, strict=False).is_not_null().sum()
                col_type_scores.append(valid_num / len(s))
            elif "DATE" in expected_type or any(k in col_name.lower() for k in ("date", "time")):
                valid_date = pl.coalesce([
                    s.str.to_date("%Y-%m-%d", strict=False),
                    s.str.to_date("%d/%m/%Y", strict=False),
                    s.str.to_date("%m/%d/%Y", strict=False),
                    s.str.to_date("%Y/%m/%d", strict=False),
                    s.str.to_date("%d-%m-%Y", strict=False),
                    s.str.to_date("%m-%d-%Y", strict=False),
                    s.str.to_date("%d-%b-%y", strict=False),
                    s.str.to_date("%d-%b-%Y", strict=False),
                    s.str.to_date("%b %d %Y", strict=False),
                    s.str.to_date("%b %d, %Y", strict=False),
                    s.str.to_date("%B %d %Y", strict=False),
                    s.str.to_date("%B %d, %Y", strict=False),
                    s.str.to_date("%d.%m.%Y", strict=False),
                    s.str.to_date("%Y.%m.%d", strict=False),
                ]).is_not_null().sum()
                col_type_scores.append(valid_date / len(s))
            else:
                col_type_scores.append(1.0)

        type_consistency = round(sum(col_type_scores) / len(col_type_scores), 4) if col_type_scores else 1.0
        type_consistency = max(0.0, min(1.0, type_consistency))

        quality_sub_scores = {
            "completeness": completeness,
            "uniqueness": uniqueness,
            "type_consistency": type_consistency
        }
        
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
        state_dict["quality_sub_scores"] = quality_sub_scores
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

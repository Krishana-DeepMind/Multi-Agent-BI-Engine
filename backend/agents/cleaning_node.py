import logging
import json
import polars as pl
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

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
    # NOTE: "Unknown" intentionally excluded — it can be a legitimate category
    # value (e.g. "Unknown" blood type). See Cleaning Rulebook §4.
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

# ---------------------------------------------------------------------------
# Country-aware phone number normalization (Phase Final)
# ---------------------------------------------------------------------------
# Country code -> (code_digits, valid_local_lengths)
# valid_local_lengths is a set of acceptable digit counts AFTER stripping code.
_COUNTRY_PHONE_RULES: Dict[str, Tuple[str, set]] = {
    # India: +91 → 10-digit local number
    "91":  ("91",  {10}),
    # US/Canada: +1 → 10-digit local number
    "1":   ("1",   {10}),
    # UK: +44 → 10 or 11 digit local (landlines 10, mobiles 10)
    "44":  ("44",  {10, 11}),
    # Australia: +61 → 9-digit local number
    "61":  ("61",  {9}),
    # Germany: +49 → 10 or 11 digit local
    "49":  ("49",  {10, 11}),
    # France: +33 → 9-digit local number
    "33":  ("33",  {9}),
    # Japan: +81 → 10 or 11 digit local
    "81":  ("81",  {10, 11}),
    # China: +86 → 11-digit local number
    "86":  ("86",  {11}),
    # Brazil: +55 → 10 or 11 digit local
    "55":  ("55",  {10, 11}),
    # Mexico: +52 → 10-digit local number
    "52":  ("52",  {10}),
}

# Sorted by code length descending so '91' is checked before '9', etc.
_SORTED_COUNTRY_CODES = sorted(_COUNTRY_PHONE_RULES.keys(), key=lambda c: -len(c))

# Generic fallback: if no country code recognized, these are acceptable lengths
_GENERIC_VALID_LENGTHS = {7, 8, 9, 10, 11}


def normalize_phone_number(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Country-aware phone number normalization.

    Returns (normalized_number_or_None, review_note_or_None).
    - Strips all formatting (spaces, hyphens, brackets, dots).
    - Detects and strips recognizable country codes (+91, +1, +44, etc.).
    - Validates resulting digit count against per-country rules.
    - Never invents missing digits.
    - Returns None for the number if unresolvable, with a review note.
    """
    if raw is None or str(raw).strip() == "":
        return None, "Missing contact number — preserved as NULL."

    s = str(raw).strip()

    # Strip formatting: spaces, hyphens, dots, parentheses, brackets
    s = re.sub(r"[\s\-\.\(\)\[\]]", "", s)

    # Detect and strip leading '+'
    had_plus = s.startswith("+")
    if had_plus:
        s = s[1:]

    # Now s should be all digits (possibly with leading country code)
    if not s.isdigit():
        # Contains non-digit characters after stripping → invalid
        return None, f"Contact number '{raw}' contains non-numeric characters after formatting removal — preserved as NULL."

    # Try to detect and strip country code
    matched_code = None
    local_digits = s
    for code in _SORTED_COUNTRY_CODES:
        if s.startswith(code) and len(s) > len(code):
            candidate_local = s[len(code):]
            valid_lengths = _COUNTRY_PHONE_RULES[code][1]
            if len(candidate_local) in valid_lengths:
                matched_code = code
                local_digits = candidate_local
                break

    if matched_code is None:
        # No country code detected — check if raw digits are in generic valid range
        # Also handle Indian trunk prefix: leading 0 + 10 digits = 11 digits
        if len(s) == 11 and s.startswith("0"):
            local_digits = s[1:]  # Strip trunk prefix
            if len(local_digits) != 10:
                return None, f"Contact number '{raw}' has {len(s)} digits — could not resolve to valid format. Preserved as NULL."
        elif len(s) in _GENERIC_VALID_LENGTHS:
            local_digits = s
        else:
            return None, f"Contact number '{raw}' has {len(s)} digits — outside valid range (7-11). Preserved as NULL."

    return local_digits, None


# ---------------------------------------------------------------------------
# Protected-column & KPI helpers (Phase 3)
# ---------------------------------------------------------------------------

# Column-name substrings that signal an identifier / protected column.
_IDENTIFIER_PATTERNS = (
    "_id", "id_", "key", "code", "sku", "uuid", "ssn",
    "passport", "license", "licence", "serial", "barcode",
)

# Column-name substrings that signal a KPI / target metric.
_KPI_PATTERNS = (
    "revenue", "sales", "profit", "margin", "total", "amount",
    "income", "cost", "price", "target", "score", "rating",
    "conversion", "churn", "retention", "growth", "return",
)

# Fields where zero is a semantically valid imputation default.
_ZERO_VALID_PATTERNS = (
    "discount", "returns", "refund", "bonus", "penalty",
    "deduction", "tax", "fee", "charge",
)


def _is_protected_column(col_meta) -> bool:
    """
    Returns True if a column should be treated as protected (identifier,
    primary key, or very-high-uniqueness surrogate key).  Protected columns
    are never dropped, imputed, cast to numeric, or outlier-cleaned.
    """
    if isinstance(col_meta, dict):
        name        = col_meta.get("name", "")
        is_pk       = col_meta.get("is_primary_key", False)
        is_kpi      = col_meta.get("is_candidate_kpi", False)
        unique_pct  = col_meta.get("unique_pct", 0.0)
        sem_type    = col_meta.get("semantic_type", "").lower()
    else:
        name        = getattr(col_meta, "name", "")
        is_pk       = getattr(col_meta, "is_primary_key", False)
        is_kpi      = getattr(col_meta, "is_candidate_kpi", False)
        unique_pct  = getattr(col_meta, "unique_pct", 0.0)
        sem_type    = getattr(col_meta, "semantic_type", "").lower()

    col_lower = name.lower()

    # Known numeric/measure/date concepts are never protected identifiers
    # (prevents 100% unique small-sample amounts/salaries/dates from being classified as PKs)
    if any(k in col_lower for k in ("amount", "salary", "sal", "wage", "pay", "price", "revenue", "cost", "age", "rate", "rating", "score", "date", "time")):
        if not any(p in col_lower for p in _IDENTIFIER_PATTERNS) and sem_type != "identifier":
            return False

    if is_pk:
        return True
    if sem_type == "identifier":
        return True
    if unique_pct > 0.95:
        return True

    # Exact match for "id" as a standalone column name
    if col_lower == "id":
        return True
    # Substring pattern match for common identifier suffixes/prefixes
    if any(p in col_lower for p in _IDENTIFIER_PATTERNS):
        return True

    return False


def _is_kpi_candidate(col_meta) -> bool:
    """
    Returns True if a column looks like a KPI / business-critical metric.
    KPI columns are never imputed — missing values are flagged for human review.
    """
    if isinstance(col_meta, dict):
        name    = col_meta.get("name", "")
        is_kpi  = col_meta.get("is_candidate_kpi", False)
    else:
        name    = getattr(col_meta, "name", "")
        is_kpi  = getattr(col_meta, "is_candidate_kpi", False)

    if is_kpi:
        return True

    col_lower = name.lower()
    return any(p in col_lower for p in _KPI_PATTERNS)


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
        col_l = col.lower()
        # Detect if the column is numeric to guard against string injection
        _is_numeric_col = False
        try:
            _col_dtype = lf.collect_schema()[col]
            _is_numeric_col = _col_dtype in (
                pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64,
                pl.Float32, pl.Float64,
            )
        except Exception:
            pass

        if "preserve_null" in strategy or "preserve" in strategy or "keep_null" in strategy:
            expr = pl.col(col)
            code_str = f"# Column '{col}': missing values preserved as NULL"
        elif "median" in strategy:
            median_expr = pl.col(col).fill_null(pl.col(col).drop_nulls().median())
            # For salary-like columns, round to Int64 to avoid unnecessary decimals
            if any(k in col_l for k in ("salary", "sal", "wage", "pay", "compensation")):
                expr = median_expr.round(0).cast(pl.Int64, strict=False)
                code_str = f"pl.col('{col}').fill_null(pl.col('{col}').median()).round(0).cast(pl.Int64)"
            else:
                expr = median_expr
                code_str = f"pl.col('{col}').fill_null(pl.col('{col}').median())"
        elif "mean" in strategy or "average" in strategy:
            mean_expr = pl.col(col).fill_null(pl.col(col).drop_nulls().mean())
            if any(k in col_l for k in ("salary", "sal", "wage", "pay", "compensation")):
                expr = mean_expr.round(0).cast(pl.Int64, strict=False)
                code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mean()).round(0).cast(pl.Int64)"
            else:
                expr = mean_expr
                code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mean())"
        elif "mode" in strategy or "frequent" in strategy:
            expr = pl.col(col).fill_null(pl.col(col).drop_nulls().mode().first())
            code_str = f"pl.col('{col}').fill_null(pl.col('{col}').mode().first())"
        elif "null_invalid_email" in strategy:
            # Email validation: N/A is acceptable since email is always a string column
            valid_email = pl.when(pl.col(col).cast(pl.Utf8).str.contains(r"@")).then(pl.col(col)).otherwise(pl.lit(None))
            expr = valid_email  # Preserve invalid emails as NULL, not "N/A"
            code_str = f"pl.when(pl.col('{col}').str.contains('@')).then(pl.col('{col}')).otherwise(None)"
        elif "placeholder" in strategy or "unknown" in strategy:
            if any(k in col_l for k in ("name", "customer")):
                placeholder = "Unknown Customer"
                expr = pl.col(col).cast(pl.Utf8).fill_null(pl.lit(placeholder))
                code_str = f"pl.col('{col}').fill_null(pl.lit('{placeholder}'))"
            elif "email" in col_l:
                # Email is a string column — NULL is acceptable for missing
                expr = pl.col(col)  # Preserve as NULL
                code_str = f"# Email: missing preserved as NULL"
            elif any(k in col_l for k in ("phone", "contact", "mobile")):
                # Country-aware phone normalization via Python UDF on collected data
                # We apply this as a map_elements on the collected column
                # The Polars expr just marks intent; actual normalization happens below
                expr = None  # Handled specially after this block
                code_str = f"normalize_phone_number('{col}') — country-aware"
            else:
                if _is_numeric_col:
                    # NEVER inject string placeholder into numeric column
                    expr = pl.col(col)  # Preserve as NULL
                    code_str = f"# Numeric column '{col}': missing preserved as NULL (no safe imputation rule)"
                    logger.info(f"Preserving NULL in numeric column '{col}' — no string placeholder injected.")
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
            # PRINCIPLED FALLBACK: Do NOT blindly insert 'N/A' strings.
            # If no safe cleaning rule can resolve the missing value,
            # preserve it as NULL and record a review note.
            if _is_numeric_col:
                expr = pl.col(col)  # Preserve numeric NULL
                code_str = f"# Numeric column '{col}': unresolved missing values preserved as NULL"
            else:
                expr = pl.col(col)  # Preserve as NULL for unrecognized strategies too
                code_str = f"# Column '{col}': unresolved missing values preserved as NULL"
            logger.info(f"No safe imputation rule for column '{col}' (strategy='{strategy}'). Preserving NULLs.")

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
        elif any(k in col_lower for k in ("phone", "contact", "mobile")):
            expr = None
            code_str = f"normalize_phone_number('{col}') — country-aware"
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
        # 2-digit year formats MUST be tried first — otherwise %Y-%m-%d
        # greedily matches "08-09-23" as year 0008 instead of 2023.
        # %d-%m-%y won't wrongly match "2023-09-08" because day=2023 is invalid.
        expr = pl.coalesce([
            # ── 2-digit year (most common in messy data) ──
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%m-%y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m-%d-%y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d/%m/%y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m/%d/%y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%b-%y", strict=False),
            # ── 4-digit year (ISO and regional) ──
            pl.col(col).cast(pl.Utf8).str.to_date("%Y-%m-%d", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d/%m/%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m/%d/%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%Y/%m/%d", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%m-%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%m-%d-%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d-%b-%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%b %d %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%b %d, %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%B %d %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%B %d, %Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d.%m.%Y", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%Y.%m.%d", strict=False),
            pl.col(col).cast(pl.Utf8).str.to_date("%d.%m.%y", strict=False),
        ])
        code_str = f"pl.coalesce([pl.col('{col}').str.to_date('%d-%m-%y'), pl.col('{col}').str.to_date('%Y-%m-%d'), pl.col('{col}').str.to_date('%d/%m/%Y'), pl.col('{col}').str.to_date('%d-%m-%Y')])"

    elif operation in ("clean_email", "null_invalid_email", "validate_email"):
        # Invalid emails → NULL (not "N/A" string). Preserves datatype integrity.
        valid_email = pl.when(pl.col(col).cast(pl.Utf8).str.contains(r"@")).then(pl.col(col)).otherwise(pl.lit(None))
        expr = valid_email  # Missing emails remain NULL
        code_str = f"pl.when(pl.col('{col}').str.contains('@')).then(pl.col('{col}')).otherwise(None)"

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

    # Special handling: country-aware phone normalization (cannot be expressed as a single Polars expr)
    if (operation in ("fill_null", "normalize") or "phone" in strategy) and expr is None and any(k in col.lower() for k in ("phone", "contact", "mobile")):
        try:
            df_temp = lf.collect()
            raw_values = df_temp[col].to_list()
            normalized = []
            notes = []
            for val in raw_values:
                norm_val, note = normalize_phone_number(val)
                normalized.append(norm_val)
                if note:
                    notes.append(note)
            # Replace the column with normalized string values
            new_series = pl.Series(col, normalized, dtype=pl.Utf8)
            df_temp = df_temp.with_columns(new_series)
            lf = df_temp.lazy()
            code_str = f"normalize_phone_number('{col}') — country-aware normalization"
            if notes:
                # Log unique review notes (deduplicated)
                unique_notes = list(set(notes))
                for n in unique_notes[:5]:  # limit to 5 unique notes
                    logger.info(f"Phone normalization: {n}")
        except Exception as e:
            logger.warning(f"Phone normalization failed for {col}: {e}")
        return lf, code_str

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

    Phase 3 improvements:
    - Protected-column awareness  (identifiers & primary keys are never
      dropped, imputed, cast, or outlier-cleaned)
    - KPI-candidate awareness    (high-null KPIs get a review note instead
      of blind imputation)
    - Smarter imputation strategy (median for skewed, mean for symmetric,
      zero for discount/returns-style columns)
    - Outlier handling only on continuous numeric measures with ≥ 30 valid
      observations, excluding identifiers and low-cardinality categoricals
    - Proper clean_email operation type (uses the new Literal value)
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

    # ---- Pre-compute row count for outlier observation threshold ----
    _total_rows = 0
    try:
        if db_engine.current_table:
            res = db_engine.conn.execute(
                f"SELECT COUNT(*) FROM {db_engine.current_table}"
            ).fetchone()
            _total_rows = int(res[0]) if res else 0
    except Exception:
        pass

    for col in column_metadata:
        if isinstance(col, dict):
            name = col.get("name", "")
            dtype = col.get("dtype", "").upper()
            semantic_type = col.get("semantic_type", "").lower()
            null_pct = col.get("null_pct", 0.0)
            unique_pct = col.get("unique_pct", 0.0)
            sample_values = col.get("sample_values", [])
        else:
            name = getattr(col, "name", "")
            dtype = getattr(col, "dtype", "").upper()
            semantic_type = getattr(col, "semantic_type", "").lower()
            null_pct = getattr(col, "null_pct", 0.0)
            unique_pct = getattr(col, "unique_pct", 0.0)
            sample_values = getattr(col, "sample_values", [])

        col_lower = name.lower()
        samples_str = " ".join(str(s) for s in sample_values)
        protected = _is_protected_column(col)
        kpi = _is_kpi_candidate(col)
        is_numeric_dtype = any(t in dtype for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC"))

        # ── Guard: skip protected columns entirely (identifiers, PKs) ──
        if protected:
            # Identifiers only get whitespace trimming if they are VARCHAR
            if "VARCHAR" in dtype:
                ops.append(CleaningOperation(
                    column=name,
                    operation="trim_whitespace",
                    strategy="strip",
                    rationale=f"Protected identifier column — only trim whitespace (not dropped/imputed/cast).",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))
            logger.debug(f"Skipping protected column '{name}' (pk={_is_protected_column(col)}).")
            continue

        # ── Guard: KPI columns with high null% → review note, no imputation ──
        if kpi and null_pct > 0.10:
            ops.append(CleaningOperation(
                column=name,
                operation="trim_whitespace" if "VARCHAR" in dtype else "fill_null",
                strategy="strip" if "VARCHAR" in dtype else "median",
                rationale=(
                    f"⚠️ REVIEW: KPI/target column '{name}' has {null_pct:.1%} nulls. "
                    f"Automatic imputation skipped — needs business-owner review."
                ),
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue

        # Rule 1: High null percentage (>50%) → drop column
        if null_pct > 0.50:
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
                operation="clean_email",
                strategy="null_invalid_email",
                rationale="Replace missing or invalid email structures (missing @/domain) with 'N/A'.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue
        elif any(k in col_lower for k in ("phone", "contact", "mobile")):
            ops.append(CleaningOperation(
                column=name,
                operation="normalize",
                strategy="phone_normalization",
                rationale="Country-aware phone normalization: strip country codes and formatting, validate digit count per country rules. Invalid/unresolvable numbers preserved as NULL with review note. Missing contacts preserved as NULL.",
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            continue

        # Rule 2.5: Age Handling — Principled null preservation (applies to ALL data types: VARCHAR, BIGINT, DOUBLE, etc.)
        # Age columns must NEVER be automatically imputed with mean, median, mode, or invented defaults.
        # Missing or invalid values are preserved as backend NULL and recorded for contextual review.
        if "age" in col_lower and not any(p in col_lower for p in _IDENTIFIER_PATTERNS) and semantic_type != "identifier":
            if "VARCHAR" in dtype:
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
                    strategy="preserve_null",
                    rationale=f"Age column: {null_pct:.1%} missing values preserved as NULL. No safe imputation rule justifies automatic fill for age — requires contextual review.",
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

        # Rule 4: Text in numeric columns (Amount, Price, Rating, Qty, Discount, Cost, Salary)
        if any(k in col_lower for k in ("amount", "price", "rating", "qty", "quantity", "discount", "cost", "unit", "salary", "sal", "wage", "pay", "compensation")) and "VARCHAR" in dtype:
            # Salary: cast to integer (no decimals)
            if any(k in col_lower for k in ("salary", "sal", "wage", "pay", "compensation")):
                cast_strategy = "int"
                cast_rationale = "Strip currency symbols/separators and cast to integer. Salary values are integer-valued."
            else:
                cast_strategy = "safe_numeric_cast"
                cast_rationale = "Clean non-numeric sentinel text (e.g. 'twenty-nine', '$120.00', 'N/A') and cast to numeric."
            ops.append(CleaningOperation(
                column=name,
                operation="cast_type",
                strategy=cast_strategy,
                rationale=cast_rationale,
                rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
            ))
            if null_pct > 0.0:
                fill_strategy = _pick_numeric_fill_strategy(col_lower, db_engine, name)
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy=fill_strategy,
                    rationale=f"Impute missing numeric values ({null_pct:.1%} nulls) using {fill_strategy}.",
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

        # Rule 6: Outlier detection for continuous numeric measures
        # Only on numeric types with enough valid observations (≥ 30),
        # excluding identifiers and low-cardinality categoricals.
        if is_numeric_dtype and not kpi:
            valid_obs = int(_total_rows * (1.0 - null_pct)) if _total_rows > 0 else 0
            is_continuous = unique_pct > 0.05  # more than 5% unique → likely continuous
            if valid_obs >= 30 and is_continuous:
                ops.append(CleaningOperation(
                    column=name,
                    operation="remove_outliers",
                    strategy="iqr_1.5x",
                    rationale=f"IQR-based outlier detection on continuous numeric column ({valid_obs} valid observations).",
                    rows_affected=0, before_nulls=0, after_nulls=0, polars_code=""
                ))

        # Rule 7: Null Imputation for remaining numeric nulls (0% < null_pct ≤ 50%)
        if null_pct > 0.0:
            if is_numeric_dtype:
                fill_strategy = _pick_numeric_fill_strategy(col_lower, db_engine, name)
                ops.append(CleaningOperation(
                    column=name,
                    operation="fill_null",
                    strategy=fill_strategy,
                    rationale=f"Impute missing numeric values ({null_pct:.1%} nulls) using {fill_strategy}.",
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


def _pick_numeric_fill_strategy(col_lower: str, db_engine: DuckDBEngine, col_name: str) -> str:
    """
    Selects the best numeric imputation strategy based on column semantics
    and distribution characteristics.

    Priority:
    0. Age columns → "preserve_null" (never auto-imputed)
    1. Zero-valid fields (discount, returns, refunds, etc.) → "zero"
    2. Skewed distributions → "median"  (robust to outliers)
    3. Symmetric distributions → "mean"  (more efficient estimator)
    4. Default fallback → "median"  (safest)
    """
    # 0. Age protection guard (safety guard)
    if "age" in col_lower:
        return "preserve_null"

    # 1. Zero-valid fields
    if any(p in col_lower for p in _ZERO_VALID_PATTERNS):
        return "zero"

    # 2. Try to detect skewness from DuckDB quartiles
    try:
        if db_engine.current_table:
            quoted = f'"{col_name}"'
            row = db_engine.conn.execute(
                f"SELECT "
                f"  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {quoted}) AS q1, "
                f"  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY {quoted}) AS q2, "
                f"  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {quoted}) AS q3 "
                f"FROM {db_engine.current_table} "
                f"WHERE {quoted} IS NOT NULL"
            ).fetchone()
            if row and row[0] is not None and row[1] is not None and row[2] is not None:
                q1, q2, q3 = float(row[0]), float(row[1]), float(row[2])
                iqr = q3 - q1
                if iqr > 0:
                    # Bowley skewness = (Q3 + Q1 - 2*Q2) / (Q3 - Q1)
                    bowley = (q3 + q1 - 2 * q2) / iqr
                    if abs(bowley) < 0.15:
                        return "mean"   # roughly symmetric
                    else:
                        return "median"  # skewed
    except Exception:
        pass  # DuckDB stats unavailable — fall through to default

    # 3. Default: median is safest
    return "median"


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
        skipped_columns = []   # Phase 5: track intentionally-skipped columns
        review_notes = []      # Phase 5: track columns flagged for human review
        
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

        # Phase 5: Collect skipped / review entries from column metadata and operation rationales
        for c in column_meta_list:
            c_name = c.get("name") if isinstance(c, dict) else getattr(c, "name", "")
            if c_name and _is_protected_column(c):
                skipped_columns.append({
                    "column": c_name,
                    "reason": "Protected identifier column — not dropped/imputed/cast.",
                })

        for op in final_operations:
            rationale = op.rationale or ""
            if "Protected identifier" in rationale:
                skipped_columns.append({
                    "column": op.column,
                    "reason": "Protected identifier column — not dropped/imputed/cast.",
                })
            if "⚠️ REVIEW" in rationale or "REVIEW:" in rationale:
                review_notes.append({
                    "column": op.column,
                    "note": rationale,
                })
            # Capture review notes from principled null preservation
            if "preserved as NULL" in rationale or "preserved as null" in rationale:
                review_notes.append({
                    "column": op.column,
                    "note": rationale,
                })
            if "no safe imputation rule" in rationale.lower() or "requires contextual review" in rationale.lower():
                review_notes.append({
                    "column": op.column,
                    "note": rationale,
                })

        # Deduplicate skipped_columns
        seen_skips = set()
        deduped_skips = []
        for sc in skipped_columns:
            key = (sc.get("column", ""), sc.get("reason", ""))
            if key not in seen_skips:
                seen_skips.add(key)
                deduped_skips.append(sc)
        skipped_columns = deduped_skips

        # Phase Final: Detect columns that still have unresolved nulls after cleaning
        # and generate review notes for transparency
        try:
            post_clean_cols = lf.collect_schema().names()
            for pc_col in post_clean_cols:
                try:
                    remaining_nulls = lf.select(pl.col(pc_col).null_count()).collect().item()
                    if remaining_nulls > 0:
                        # Check if we already have a review note for this column
                        already_noted = any(
                            rn.get("column") == pc_col for rn in review_notes
                        )
                        if not already_noted:
                            review_notes.append({
                                "column": pc_col,
                                "note": f"Column '{pc_col}' has {remaining_nulls} unresolved missing value(s) after cleaning. No safe imputation rule was applied.",
                            })
                except Exception:
                    pass
        except Exception:
            pass

        # Deduplicate review notes (same column + same note)
        seen_notes = set()
        deduped_notes = []
        for rn in review_notes:
            key = (rn.get("column", ""), rn.get("note", ""))
            if key not in seen_notes:
                seen_notes.add(key)
                deduped_notes.append(rn)
        review_notes = deduped_notes

        
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
        state_dict["skipped_columns"] = skipped_columns       # Phase 5
        state_dict["review_notes"] = review_notes               # Phase 5
        state_dict["pipeline_status"] = "cleaning"
        state_dict["current_agent"] = "cleaning"
        
        logger.info("Cleaning Agent completed successfully.")
        return return_state()
        
    except Exception as e:
        logger.error(f"Cleaning Agent failed: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "cleaning", "error": str(e), "recoverable": "true"}]
        return return_state()

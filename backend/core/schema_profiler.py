import logging
import re
from datetime import datetime, date
from typing import List, Optional, Any, Tuple
import pandas as pd
from backend.core.state import ColumnMeta
from backend.core.duckdb_engine import DuckDBEngine

logger = logging.getLogger(__name__)

# Common date regex patterns for sample value inspection
_DATE_PATTERNS = [
    re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"),       # YYYY-MM-DD or YYYY/MM/DD
    re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"),       # DD-MM-YYYY or MM/DD/YYYY
    re.compile(r"^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}"),    # DD Mon YYYY
    re.compile(r"^[A-Za-z]{3,9}\s+\d{1,2},\s*\d{2,4}"),   # Mon DD, YYYY
]

_CURRENCY_SYMBOLS = {"$", "€", "£", "₹", "¥"}
_IDENTIFIER_SUFFIXES = ("_id", "id_", "_key", "_code", "_sku", "_uuid", "_ssn", "_num", "_number")


_NUMERIC_SAMPLE_REGEX = re.compile(
    r"^\s*[$€£₹¥]?\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*(?:[kKmMbB%]|ppm|hpa|°?[cCfF]))?\s*$",
    re.IGNORECASE
)

def _is_sample_numeric(sample: Any) -> bool:
    if sample is None:
        return False
    s = str(sample).strip()
    if not s or s.lower() in ("nan", "none", "null", "n/a", "na", "-", "--", "?", "#n/a", "unknown", "unspecified"):
        return False
    cleaned = s.replace(",", "").strip()
    return bool(_NUMERIC_SAMPLE_REGEX.match(cleaned))


def _build_business_label(col_name: str) -> str:
    cleaned = col_name.replace("_", " ").replace("-", " ").strip()
    words = cleaned.split()
    abbreviations = {
        "id": "ID", "kpi": "KPI", "usd": "USD", "eur": "EUR", "inr": "INR", "gbp": "GBP",
        "co2": "CO2", "ppm": "PPM", "hpa": "hPa", "aqi": "AQI", "url": "URL", "sku": "SKU",
        "dob": "DOB", "ssn": "SSN", "hr": "HR", "avg": "Avg", "pct": "Pct"
    }
    titled = [abbreviations.get(w.lower(), w.capitalize()) for w in words]
    return " ".join(titled)


def _infer_unit(col_name: str, samples_str: List[str], semantic_type: str) -> Optional[str]:
    """Dataset-agnostic inference of physical, financial, or percentage units."""
    # 1. Samples explicit symbols
    for s in samples_str:
        for sym in _CURRENCY_SYMBOLS:
            if sym in s:
                return sym
        if "%" in s:
            return "%"

    col_lower = col_name.lower().strip()

    # 2. Parenthetical or bracketed unit: e.g. "speed (km/h)", "temp [°C]", "price ($)"
    bracket_match = re.search(r"[\(\[]([A-Za-z0-9°µ$%€£/_-]+)[\)\]]", col_name)
    if bracket_match:
        cand = bracket_match.group(1).strip()
        if cand:
            return cand

    # 3. Known suffix patterns
    suffix_match = re.search(
        r"_(ppm|ppb|hpa|kpa|pa|bar|psi|c|f|kelvin|kg|g|mg|lbs|oz|km|miles|ms|sec|seconds|hours|days|pct|percent|usd|eur|inr|gbp|kwh|mwh|mw|hz|khz|mhz|bpm|rpm|db|aqi)$",
        col_lower
    )
    if suffix_match:
        suf = suffix_match.group(1)
        units_map = {
            "ppm": "ppm", "ppb": "ppb", "hpa": "hPa", "kpa": "kPa", "pa": "Pa",
            "bar": "bar", "psi": "psi", "c": "°C", "f": "°F", "kelvin": "K",
            "kg": "kg", "g": "g", "mg": "mg", "lbs": "lbs", "oz": "oz",
            "km": "km", "miles": "mi", "ms": "ms", "sec": "s", "seconds": "s",
            "hours": "hrs", "days": "days", "pct": "%", "percent": "%",
            "usd": "$", "eur": "€", "inr": "₹", "gbp": "£",
            "kwh": "kWh", "mwh": "MWh", "mw": "MW", "hz": "Hz", "khz": "kHz", "mhz": "MHz",
            "bpm": "bpm", "rpm": "rpm", "db": "dB", "aqi": "AQI"
        }
        if suf in units_map:
            return units_map[suf]

    if semantic_type == "currency":
        for s in samples_str:
            for sym in _CURRENCY_SYMBOLS:
                if sym in s:
                    return sym
        for sym in _CURRENCY_SYMBOLS:
            if sym in col_name:
                return sym
        if "eur" in col_lower:
            return "€"
        if "gbp" in col_lower:
            return "£"
        if "inr" in col_lower:
            return "₹"
        if "jpy" in col_lower or "cny" in col_lower:
            return "¥"
        return "$"
    if semantic_type == "percentage":
        return "%"
    return None


def _infer_semantic_role(
    col_name: str,
    col_type: str,
    unique_pct: float,
    null_pct: float,
    sample_values: List[Any],
    row_count: int
) -> Tuple[str, bool]:
    """
    Cautiously infer (semantic_type, is_candidate_kpi) using:
    - Data type reported by the database engine
    - Uniqueness and missingness distributions
    - Statistical shape and sample patterns
    - Generic structural naming cues (never assuming fixed domain schemas)
    """
    col_type_upper = col_type.upper()
    col_lower = col_name.lower().strip()
    samples_str = [str(s).strip() for s in sample_values if s is not None and str(s).strip()]

    # 1. Date / Timestamp
    if any(dt in col_type_upper for dt in ("DATE", "TIME", "TIMESTAMP")):
        return "date", False

    if "VARCHAR" in col_type_upper and samples_str:
        date_matches = sum(
            1 for s in samples_str if any(p.search(s) for p in _DATE_PATTERNS)
        )
        if date_matches / max(1, len(samples_str)) >= 0.50:
            return "date", False

    # 2. Boolean
    if "BOOL" in col_type_upper:
        return "boolean", False

    if samples_str and len(samples_str) >= 2:
        bool_tokens = {"true", "false", "0", "1", "yes", "no", "y", "n", "t", "f"}
        if all(s.lower() in bool_tokens for s in samples_str) and unique_pct <= 0.05:
            return "boolean", False

    # 3. Primary Identifier / Key
    if (col_lower == "id" or col_lower.endswith(_IDENTIFIER_SUFFIXES)) and not any(k in col_lower for k in ("phone", "contact", "mobile")):
        if unique_pct > 0.80:
            return "identifier", False

    # Generic high-uniqueness identifier detection (excluding continuous measures)
    if unique_pct > 0.95 and null_pct < 0.05 and row_count >= 10:
        if not any(k in col_lower for k in ("amount", "price", "val", "cost", "revenue", "salary", "score", "rate", "pct", "total", "meas", "temp", "co2", "aqi", "press")):
            return "identifier", False

    # 3.5 Contact / Personal / Year non-metrics
    if any(k in col_lower for k in ("phone", "fax", "mobile", "telephone", "email", "ssn", "license", "tax_id")):
        return "dimension", False

    # 3.6 Geographic dimension (checked before generic numeric to prevent lat/lon from becoming metrics)
    if any(k in col_lower for k in ("country", "nation", "state", "province", "city", "region", "postal_code", "postal", "zip", "zipcode", "lat", "lon", "lng", "latitude", "longitude", "coord", "coordinate")):
        return "geographic", False

    # 3.7 Bare years (e.g. year, birth_year) are time dimensions, not summable KPIs
    if col_lower in ("year", "yr", "birth_year", "hire_year", "grad_year"):
        return "dimension", False

    # 4. Numeric & Metric Detection
    is_db_numeric = any(t in col_type_upper for t in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL"))
    numeric_samples_count = sum(1 for s in sample_values if _is_sample_numeric(s))
    is_sample_numeric_flag = (len(samples_str) > 0 and (numeric_samples_count / len(samples_str)) >= 0.50)

    if is_db_numeric or is_sample_numeric_flag:
        # Check percentage / ratio / margin / share / rate: genuine percentages/ratios
        has_pct_sample = any("%" in s for s in samples_str)
        is_ratio_or_rate = bool(re.search(r"(^|[_ \-\(\[])(pct|percent|percentage|margin|ratio|share|proportion|attrition|churn|discount|conversion)([_ \-\)\]]|$)", col_lower))
        if has_pct_sample or is_ratio_or_rate:
            return "percentage", True

        # Check currency: genuine currencies have currency symbols in samples or financial naming tokens
        has_currency_sample = any(any(sym in s for sym in _CURRENCY_SYMBOLS) for s in samples_str)
        has_currency_token = bool(re.search(r"(^|[_ \-\(\[])(price|cost|revenue|sales|salary|bill|fee|charge|spend|paid|wage|budget|amount|payroll|compensation)([_ \-\)\]]|$)", col_lower))
        if (has_currency_sample or has_currency_token) and not is_ratio_or_rate:
            return "currency", True

        # Candidate KPI if numeric and continuous distribution
        if unique_pct > 0.02 or len(set(samples_str)) >= 5 or "FLOAT" in col_type_upper or "DOUBLE" in col_type_upper:
            return "metric", True

        # Low-cardinality discrete numeric (e.g. rating 1-5, status code 1-3)
        return "dimension", False

    # 6. Long text / description
    if "VARCHAR" in col_type_upper and samples_str:
        avg_len = sum(len(s) for s in samples_str) / len(samples_str)
        if avg_len > 60 or any(k in col_lower for k in ("description", "notes", "comment", "summary", "details", "text", "bio")):
            return "text_description", False

    # 7. Generic categorical dimension or identifier fallback
    if unique_pct > 0.95 and row_count >= 50 and not any(k in col_lower for k in ("name", "title", "label", "desc", "text", "city", "country", "state", "dept", "department")):
        return "identifier", False

    return "dimension", False


class SchemaProfiler:
    """
    Analyzes DuckDB tables to extract comprehensive column metadata,
    including null percentages, uniqueness, sample values, and cautious
    dataset-agnostic semantic hypotheses.
    """

    def __init__(self, engine: DuckDBEngine):
        self.engine = engine

    def profile_table(self, table_name: Optional[str] = None) -> List[ColumnMeta]:
        tbl = table_name or self.engine.current_table
        if not tbl:
            raise ValueError("No active table to profile.")

        # 1. Get total row count
        count_res = self.engine.conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
        row_count = count_res[0] if count_res else 0

        # 2. Get column definitions
        desc_res = self.engine.conn.execute(f"DESCRIBE {tbl}").fetchall()
        columns_info = [(row[0], row[1]) for row in desc_res]

        if row_count == 0:
            return [
                self._build_empty_meta(col_name, col_type)
                for col_name, col_type in columns_info
            ]

        # 3. Batch stats query (approx_count_distinct for speed)
        agg_exprs = []
        for col_name, _ in columns_info:
            quoted_col = f'"{col_name}"'
            agg_exprs.append(f"COUNT({quoted_col})")
            agg_exprs.append(f"APPROX_COUNT_DISTINCT({quoted_col})")
            
        stats_sql = f"SELECT {', '.join(agg_exprs)} FROM {tbl}"
        try:
            stats_row = self.engine.conn.execute(stats_sql).fetchone()
        except Exception as e:
            logger.error(f"Failed to profile stats for {tbl}: {e}")
            stats_row = [0] * (len(columns_info) * 2)

        # 4. Fetch samples (up to 5 non-null rows)
        samples_df = self.engine.conn.execute(f"SELECT * FROM {tbl} LIMIT 5").fetchdf()

        # 5. Build ColumnMeta objects with dynamic semantic profiling
        meta_list = []
        for i, (col_name, col_type) in enumerate(columns_info):
            non_null_count = stats_row[i * 2]
            unique_count = stats_row[i * 2 + 1]

            null_pct = (row_count - non_null_count) / row_count
            unique_pct = min(1.0, unique_count / row_count) if row_count > 0 else 0.0

            raw_samples = samples_df[col_name].tolist() if col_name in samples_df.columns else []
            clean_samples = []
            for val in raw_samples:
                if val is None or pd.isna(val):
                    continue
                s_str = str(val).strip()
                if not s_str or s_str.lower() in ("nan", "nat", "<na>", "none", "null"):
                    continue
                # Normalize types to JSON/msgpack serializable primitives
                if isinstance(val, (datetime, date, pd.Timestamp)):
                    clean_samples.append(val.isoformat())
                elif hasattr(val, "item"):  # numpy / pandas scalar
                    clean_samples.append(val.item())
                elif isinstance(val, (int, float, str, bool)):
                    clean_samples.append(val)
                else:
                    clean_samples.append(str(val))
                if len(clean_samples) == 5:
                    break

            # Infer semantic role and candidate KPI flag
            semantic_type, is_kpi = _infer_semantic_role(
                col_name=col_name,
                col_type=col_type,
                unique_pct=unique_pct,
                null_pct=null_pct,
                sample_values=clean_samples,
                row_count=row_count
            )

            # Heuristic for primary key: 100% unique, zero nulls, and identifier / id name
            col_lower = col_name.lower()
            is_pk = (
                unique_pct > 0.99 and null_pct == 0.0 and (
                    semantic_type == "identifier" or
                    col_lower == "id" or
                    col_lower.endswith(("_id", "id_", "_key", "_code"))
                )
            )

            unit = _infer_unit(col_name, [str(s) for s in clean_samples], semantic_type)
            meta = ColumnMeta(
                name=col_name,
                original_name=col_name,
                dtype=col_type,
                semantic_type=semantic_type,
                business_label=_build_business_label(col_name),
                null_pct=round(null_pct, 4),
                unique_pct=round(unique_pct, 4),
                sample_values=clean_samples[:5],
                is_primary_key=is_pk,
                is_candidate_kpi=is_kpi,
                unit=unit,
            )
            meta_list.append(meta)

        return meta_list

    def _build_empty_meta(self, col_name: str, col_type: str) -> ColumnMeta:
        return ColumnMeta(
            name=col_name,
            original_name=col_name,
            dtype=col_type,
            semantic_type="unknown",
            business_label=_build_business_label(col_name),
            null_pct=1.0,
            unique_pct=0.0,
            sample_values=[],
            is_primary_key=False,
            is_candidate_kpi=False,
        )


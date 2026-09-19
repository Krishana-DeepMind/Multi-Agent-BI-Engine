from typing import List, Any
from .state import ColumnMeta

def compress_column_meta_for_prompt(columns: List[Any]) -> str:
    """
    Converts full ColumnMeta list (or dict representations) into a compact prompt-friendly table.
    Reduces ~8,000 tokens to ~600 tokens.
    
    Output format:
    | column_name | dtype | semantic_type | null_pct | uniq% | PK | KPI | sample_values |
    |-------------|-------|---------------|----------|-------|----|-----|---------------|
    | revenue     | DOUBLE| currency      | 0.02     | 0.85  | N  |  Y  | 1200, 3400    |
    """
    lines = ["| Column | Type | Semantic | Null% | Uniq% | PK | KPI | Sample |"]
    lines.append("|--------|------|----------|-------|-------|----|-----|--------|")
    for col in columns:
        if isinstance(col, dict):
            name = str(col.get("name", ""))
            dtype = str(col.get("dtype", ""))
            semantic_type = str(col.get("semantic_type", ""))
            null_pct_val = col.get("null_pct", 0.0)
            unique_pct_val = col.get("unique_pct", 0.0)
            sample_values = col.get("sample_values", [])
            is_pk = col.get("is_primary_key", False)
            is_kpi = col.get("is_candidate_kpi", False)
        else:
            name = getattr(col, "name", "")
            dtype = getattr(col, "dtype", "")
            semantic_type = getattr(col, "semantic_type", "")
            null_pct_val = getattr(col, "null_pct", 0.0)
            unique_pct_val = getattr(col, "unique_pct", 0.0)
            sample_values = getattr(col, "sample_values", [])
            is_pk = getattr(col, "is_primary_key", False)
            is_kpi = getattr(col, "is_candidate_kpi", False)

        samples = ", ".join(str(v) for v in (sample_values[:2] if sample_values else []))
        col_name = (name[:20] + ' ' * max(0, 20 - len(name)))[:20]
        null_pct = f"{null_pct_val:.0%}" if isinstance(null_pct_val, (int, float)) else str(null_pct_val)
        uniq_pct = f"{unique_pct_val:.0%}" if isinstance(unique_pct_val, (int, float)) else str(unique_pct_val)
        pk_flag = "Y" if is_pk else "N"
        kpi_flag = "Y" if is_kpi else "N"
        lines.append(
            f"| {col_name} | {dtype} | "
            f"{semantic_type} | {null_pct} | {uniq_pct} | {pk_flag} | {kpi_flag} | {samples} |"
        )
    return "\n".join(lines)


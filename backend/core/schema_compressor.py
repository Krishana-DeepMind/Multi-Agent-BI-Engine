from typing import List, Any
from .state import ColumnMeta

def compress_column_meta_for_prompt(columns: List[Any]) -> str:
    """
    Converts full ColumnMeta list (or dict representations) into a compact prompt-friendly table.
    Reduces ~8,000 tokens to ~600 tokens.
    
    Output format:
    | column_name | dtype | semantic_type | null_pct | sample_values |
    |-------------|-------|---------------|----------|---------------|
    | revenue     | DOUBLE| currency      | 0.02     | 1200, 3400    |
    """
    lines = ["| Column | Type | Semantic | Null% | Sample |"]
    lines.append("|--------|------|----------|-------|--------|")
    for col in columns:
        if isinstance(col, dict):
            name = str(col.get("name", ""))
            dtype = str(col.get("dtype", ""))
            semantic_type = str(col.get("semantic_type", ""))
            null_pct_val = col.get("null_pct", 0.0)
            sample_values = col.get("sample_values", [])
        else:
            name = getattr(col, "name", "")
            dtype = getattr(col, "dtype", "")
            semantic_type = getattr(col, "semantic_type", "")
            null_pct_val = getattr(col, "null_pct", 0.0)
            sample_values = getattr(col, "sample_values", [])

        samples = ", ".join(str(v) for v in (sample_values[:2] if sample_values else []))
        col_name = (name[:20] + ' ' * max(0, 20 - len(name)))[:20]
        null_pct = f"{null_pct_val:.0%}" if isinstance(null_pct_val, (int, float)) else str(null_pct_val)
        lines.append(
            f"| {col_name} | {dtype} | "
            f"{semantic_type} | {null_pct} | {samples} |"
        )
    return "\n".join(lines)

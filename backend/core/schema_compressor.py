from typing import List
from .state import ColumnMeta

def compress_column_meta_for_prompt(columns: List[ColumnMeta]) -> str:
    """
    Converts full ColumnMeta list into a compact prompt-friendly table.
    Reduces ~8,000 tokens to ~600 tokens.
    
    Output format:
    | column_name | dtype | semantic_type | null_pct | sample_values |
    |-------------|-------|---------------|----------|---------------|
    | revenue     | DOUBLE| currency      | 0.02     | 1200, 3400    |
    """
    lines = ["| Column | Type | Semantic | Null% | Sample |"]
    lines.append("|--------|------|----------|-------|--------|")
    for col in columns:
        samples = ", ".join(str(v) for v in col.sample_values[:2])
        # Using string formatting carefully to keep table neat
        col_name = (col.name[:20] + ' ' * max(0, 20 - len(col.name)))[:20]
        null_pct = f"{col.null_pct:.0%}"
        lines.append(
            f"| {col_name} | {col.dtype} | "
            f"{col.semantic_type} | {null_pct} | {samples} |"
        )
    return "\n".join(lines)

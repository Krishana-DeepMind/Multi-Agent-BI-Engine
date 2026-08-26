import logging
from typing import List, Optional
from backend.core.state import ColumnMeta
from backend.core.duckdb_engine import DuckDBEngine

logger = logging.getLogger(__name__)

class SchemaProfiler:
    """
    Analyzes DuckDB tables to extract comprehensive column metadata,
    including null percentages, uniqueness, and sample values.
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
            # properly quote column names just in case
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
        # We'll fetch 5 rows where at least some columns are non-null
        # A simple LIMIT 5 works well enough for sample values in most cases.
        samples_df = self.engine.conn.execute(f"SELECT * FROM {tbl} LIMIT 5").fetchdf()

        # 5. Build ColumnMeta objects
        meta_list = []
        for i, (col_name, col_type) in enumerate(columns_info):
            non_null_count = stats_row[i * 2]
            unique_count = stats_row[i * 2 + 1]

            null_pct = (row_count - non_null_count) / row_count
            unique_pct = min(1.0, unique_count / row_count) if row_count > 0 else 0.0

            # Extract samples for this specific column, filtering out None/NaN
            raw_samples = samples_df[col_name].tolist() if col_name in samples_df.columns else []
            clean_samples = []
            for val in raw_samples:
                # Handle pandas NaN/NaT
                if val is None or str(val).lower() == "nan" or str(val).lower() == "nat":
                    continue
                clean_samples.append(val)
                if len(clean_samples) == 5:
                    break

            meta = ColumnMeta(
                name=col_name,
                original_name=col_name,
                dtype=col_type,
                semantic_type="unknown",
                business_label="",
                null_pct=round(null_pct, 4),
                unique_pct=round(unique_pct, 4),
                sample_values=clean_samples[:5],
                is_primary_key=(unique_pct > 0.99 and null_pct == 0.0), # simple heuristic
                is_candidate_kpi=False,
            )
            meta_list.append(meta)

        return meta_list

    def _build_empty_meta(self, col_name: str, col_type: str) -> ColumnMeta:
        return ColumnMeta(
            name=col_name,
            original_name=col_name,
            dtype=col_type,
            semantic_type="unknown",
            business_label="",
            null_pct=1.0,
            unique_pct=0.0,
            sample_values=[],
            is_primary_key=False,
            is_candidate_kpi=False,
        )

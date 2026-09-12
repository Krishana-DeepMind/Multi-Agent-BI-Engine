import os
import io
import time
import json
import duckdb
import polars as pl
import pyarrow as pa
import httpx
from typing import Optional, Dict, List, Any


class DuckDBEngine:
    """
    DuckDB SQL analytics engine for high-performance in-memory queries,
    schema profiling, statistical summaries, and Parquet data serialization.
    """

    def __init__(self):
        self.conn = duckdb.connect(":memory:")
        self._init_extensions()
        self.current_table: Optional[str] = None

    def _init_extensions(self):
        """Install and load required DuckDB extensions."""
        for ext in ("httpfs", "parquet", "json"):
            try:
                self.conn.execute(f"INSTALL {ext}; LOAD {ext};")
            except Exception:
                try:
                    self.conn.execute(f"LOAD {ext};")
                except Exception:
                    pass

    def load_from_supabase(self, signed_url: str, file_type: str, table_name: str = "raw_data") -> Dict[str, Any]:
        """
        Load dataset from a Supabase Storage signed URL into DuckDB.
        Supports: 'csv', 'xlsx', 'json', 'parquet'.
        """
        file_type = file_type.lower().strip(".")
        table_name = table_name or "raw_data"

        try:
            if file_type == "parquet":
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet('{signed_url}')")
            elif file_type == "csv":
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto('{signed_url}')")
            elif file_type in ("json", "jsonl", "ndjson"):
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_json_auto('{signed_url}')")
            elif file_type == "xlsx":
                # Download and register via Polars/Arrow
                resp = httpx.get(signed_url, follow_redirects=True, timeout=60.0)
                resp.raise_for_status()
                return self.load_from_bytes(resp.content, "xlsx", table_name=table_name)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

            self.current_table = table_name
            count_res = self.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            row_count = count_res[0] if count_res else 0
            cols_res = self.conn.execute(f"DESCRIBE {table_name}").fetchall()
            columns = [c[0] for c in cols_res]

            return {
                "success": True,
                "table_name": table_name,
                "row_count": row_count,
                "column_count": len(columns),
                "columns": columns
            }

        except Exception as e:
            # Fallback: download bytes directly with httpx and load from memory
            try:
                resp = httpx.get(signed_url, follow_redirects=True, timeout=60.0)
                if resp.status_code == 200:
                    return self.load_from_bytes(resp.content, file_type, table_name=table_name)
            except Exception:
                pass
            raise RuntimeError(f"Failed to load dataset from Supabase URL into DuckDB: {str(e)}")

    def load_from_bytes(self, file_bytes: bytes, file_type: str, table_name: str = "raw_data") -> Dict[str, Any]:
        """
        Load dataset directly from in-memory bytes into DuckDB.
        """
        file_type = file_type.lower().strip(".")
        table_name = table_name or "raw_data"

        def _clean_columns(df: pl.DataFrame) -> pl.DataFrame:
            new_cols = []
            seen = set()
            for col in df.columns:
                c = col.strip()
                if not c:
                    c = "unnamed"
                original_c = c
                counter = 1
                while c in seen:
                    c = f"{original_c}_{counter}"
                    counter += 1
                seen.add(c)
                new_cols.append(c)
            return df.rename(dict(zip(df.columns, new_cols)))

        try:
            if file_type == "parquet":
                df = pl.read_parquet(io.BytesIO(file_bytes))
                df = _clean_columns(df)
                arrow_table = df.to_arrow()
                self.conn.register(f"_{table_name}_arrow", arrow_table)
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _{table_name}_arrow")

            elif file_type == "csv":
                df = pl.read_csv(io.BytesIO(file_bytes), ignore_errors=True)
                df = _clean_columns(df)
                arrow_table = df.to_arrow()
                self.conn.register(f"_{table_name}_arrow", arrow_table)
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _{table_name}_arrow")

            elif file_type in ("json", "jsonl", "ndjson"):
                # Try NDJSON or JSON array
                try:
                    df = pl.read_json(io.BytesIO(file_bytes))
                except Exception:
                    df = pl.read_ndjson(io.BytesIO(file_bytes))
                df = _clean_columns(df)
                arrow_table = df.to_arrow()
                self.conn.register(f"_{table_name}_arrow", arrow_table)
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _{table_name}_arrow")

            elif file_type == "xlsx":
                df = pl.read_excel(io.BytesIO(file_bytes))
                df = _clean_columns(df)
                arrow_table = df.to_arrow()
                self.conn.register(f"_{table_name}_arrow", arrow_table)
                self.conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _{table_name}_arrow")
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

            self.current_table = table_name
            count_res = self.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            row_count = count_res[0] if count_res else 0
            cols_res = self.conn.execute(f"DESCRIBE {table_name}").fetchall()
            columns = [c[0] for c in cols_res]

            return {
                "success": True,
                "table_name": table_name,
                "row_count": row_count,
                "column_count": len(columns),
                "columns": columns
            }
        except Exception as e:
            raise RuntimeError(f"Failed to load bytes into DuckDB: {str(e)}")

    def get_schema_profile(self, table_name: Optional[str] = None) -> str:
        """
        Returns DESCRIBE + sample rows formatted cleanly for LLM prompt construction.
        """
        tbl = table_name or self.current_table
        if not tbl:
            raise ValueError("No active table in DuckDB engine.")

        # 1. Column Metadata
        desc_res = self.conn.execute(f"DESCRIBE {tbl}").fetchall()
        # desc columns: column_name, column_type, null, key, default, extra
        schema_lines = [f"Table: `{tbl}`", "Columns:"]
        for row in desc_res:
            col_name, col_type, is_null = row[0], row[1], row[2]
            nullable_str = "NULL" if is_null == "YES" else "NOT NULL"
            schema_lines.append(f"  - `{col_name}` ({col_type}, {nullable_str})")

        # 2. Sample Data (First 5 Rows)
        sample_df = self.conn.execute(f"SELECT * FROM {tbl} LIMIT 5").fetchdf()
        sample_json = sample_df.to_json(orient="records", date_format="iso")
        parsed_samples = json.loads(sample_json)

        schema_lines.append("\nSample Rows (First 5 records):")
        schema_lines.append(json.dumps(parsed_samples, indent=2))

        return "\n".join(schema_lines)

    def get_statistical_summary(self, table_name: Optional[str] = None) -> str:
        """
        Returns SUMMARIZE output formatted for the Cleaning Agent prompt.
        Includes a fallback column profiler if DuckDB SUMMARIZE fails on extreme/invalid values.
        """
        tbl = table_name or self.current_table
        if not tbl:
            raise ValueError("No active table in DuckDB engine.")

        try:
            summary_df = self.conn.execute(f"SUMMARIZE {tbl}").fetchdf()
        except Exception:
            # Fall back to custom column summary if SUMMARIZE fails
            describe_res = self.conn.execute(f"DESCRIBE {tbl}").fetchall()
            col_summaries = []
            total_rows_res = self.conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            total_rows = total_rows_res[0] if total_rows_res else 0

            for row in describe_res:
                col_name, col_type = row[0], row[1]
                quoted = f'"{col_name}"'
                try:
                    stats = self.conn.execute(
                        f"SELECT COUNT({quoted}) as cnt, COUNT(*) - COUNT({quoted}) as null_cnt, "
                        f"COUNT(DISTINCT {quoted}) as dist_cnt FROM {tbl}"
                    ).fetchone()
                    cnt, null_cnt, dist_cnt = stats[0], stats[1], stats[2]
                except Exception:
                    cnt, null_cnt, dist_cnt = total_rows, 0, 0

                null_pct_str = f"{round((null_cnt / total_rows) * 100, 2)}%" if total_rows > 0 else "0%"
                col_summaries.append({
                    "column_name": col_name,
                    "column_type": col_type,
                    "min": "N/A",
                    "max": "N/A",
                    "approx_unique": dist_cnt,
                    "avg": "N/A",
                    "std": "N/A",
                    "q25": "N/A",
                    "q50": "N/A",
                    "q75": "N/A",
                    "count": cnt,
                    "null_percentage": null_pct_str
                })
            import pandas as pd
            summary_df = pd.DataFrame(col_summaries)
        
        # Convert to clean markdown / structured report
        summary_lines = [f"### Statistical Summary for `{tbl}` (DuckDB SUMMARIZE)\n"]
        
        # Header
        headers = list(summary_df.columns)
        summary_lines.append("| " + " | ".join(headers) + " |")
        summary_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for _, row in summary_df.iterrows():
            row_vals = []
            for col in headers:
                val = row[col]
                if val is None or str(val).lower() == "nan" or str(val).lower() == "nat":
                    row_vals.append("N/A")
                elif isinstance(val, float):
                    row_vals.append(f"{val:.3f}")
                else:
                    row_vals.append(str(val).replace("\n", " "))
            summary_lines.append("| " + " | ".join(row_vals) + " |")

        return "\n".join(summary_lines)

    def execute_validated(self, sql: str) -> Dict[str, Any]:
        """
        Execute SQL with error catching and timing.
        Returns: {success: bool, data: List[Dict], error: Optional[str], row_count: int, ms: float}
        """
        start_time = time.perf_counter()
        try:
            rel = self.conn.execute(sql)
            df = rel.fetchdf()
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            
            records_json = df.to_json(orient="records", date_format="iso")
            data = json.loads(records_json)

            return {
                "success": True,
                "data": data,
                "error": None,
                "row_count": len(data),
                "ms": elapsed_ms
            }
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return {
                "success": False,
                "data": [],
                "error": str(e),
                "row_count": 0,
                "ms": elapsed_ms
            }

    def to_polars_lazyframe(self, table_name: Optional[str] = None) -> pl.LazyFrame:
        """
        Convert DuckDB table to Polars LazyFrame via PyArrow Table export.
        """
        tbl = table_name or self.current_table
        if not tbl:
            raise ValueError("No active table in DuckDB engine.")
        arrow_table = self.conn.execute(f"SELECT * FROM {tbl}").fetch_arrow_table()
        return pl.from_arrow(arrow_table).lazy()

    def write_to_parquet(self, output_path: str, table_name: Optional[str] = None) -> str:
        """
        Write current table to Parquet file and return path.
        """
        tbl = table_name or self.current_table
        if not tbl:
            raise ValueError("No active table to export.")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        # DuckDB copy to parquet
        sanitized_path = output_path.replace("\\", "/")
        self.conn.execute(f"COPY {tbl} TO '{sanitized_path}' (FORMAT PARQUET)")
        return output_path

    def close(self):
        """Close connection."""
        try:
            self.conn.close()
        except Exception:
            pass


import asyncio
import os
import json
import uuid
from datetime import datetime, timezone
import polars as pl
import duckdb

from backend.core.state import AgentSwarmState, ColumnMeta
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_profiler import SchemaProfiler
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import INGESTION_SYSTEM_PROMPT
from backend.agents.feature_node import feature_node

async def run_feature_verification():
    print("=" * 60)
    print("FOCUSED FEATURE AGENT REAL-DATA VERIFICATION")
    print("=" * 60)
    session_id = str(uuid.uuid4())
    user_id = "verification_user"
    
    # 1. Read real Excel clean dataset
    excel_path = r"C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_analytics_test_dataset.xlsx"
    cleaned_parquet_path = r"C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_cleaned.parquet"
    
    df = pl.read_excel(excel_path)
    df.write_parquet(cleaned_parquet_path)
    print(f"Clean dataset: {excel_path}")
    print(f"Cleaned parquet written: {cleaned_parquet_path} ({len(df)} rows, {len(df.columns)} cols)")
    print(f"Original Columns ({len(df.columns)}): {df.columns}")
    
    # 2. DuckDB Engine & Profiler
    db = DuckDBEngine()
    cleaned_fwd = cleaned_parquet_path.replace("\\", "/")
    db.conn.execute(f"CREATE OR REPLACE TABLE data AS SELECT * FROM read_parquet('{cleaned_fwd}')")
    db.current_table = "data"
    
    profiler = SchemaProfiler(db)
    raw_columns = profiler.profile_table("data")
    
    llm = LLMRouter()
    compressed = compress_column_meta_for_prompt(raw_columns)
    user_msg = f"Domain: hr\n\nSchema Profile:\n{compressed}\n\nReturn a JSON object with a single key 'columns' containing the array of ColumnMeta objects for ALL {len(raw_columns)} columns."
    
    resp = await llm.route(TaskType.SCHEMA_INFERENCE, [{"role": "system", "content": INGESTION_SYSTEM_PROMPT}, {"role": "user", "content": user_msg}], max_tokens=2000)
    parsed = parse_json_response(resp["content"])
    inferred_cols = {c["name"]: c for c in parsed.get("columns", [])}
    
    column_metadata = []
    for rc in raw_columns:
        inf = inferred_cols.get(rc.name, {})
        sem_type = inf.get("semantic_type", "unknown")
        if sem_type not in ["identifier", "metric", "dimension", "date", "currency", "percentage", "boolean", "text_description", "geographic", "unknown"]:
            sem_type = "unknown"
        
        cm = ColumnMeta(
            name=rc.name,
            original_name=rc.name,
            dtype=rc.dtype,
            semantic_type=sem_type,
            business_label=inf.get("business_label", rc.name.replace("_", " ").title()),
            null_pct=rc.null_pct,
            unique_pct=rc.unique_pct,
            sample_values=rc.sample_values,
            is_primary_key=inf.get("is_primary_key", rc.name == "Employee_Name"),
            is_candidate_kpi=inf.get("is_candidate_kpi", rc.name in ["Salary", "Performance_Score"])
        )
        column_metadata.append(cm)
    
    raw_query = "Provide comprehensive executive insights on employee demographics, compensation, performance, and regional distribution"
    
    initial_state = AgentSwarmState(
        session_id=uuid.UUID(session_id),
        user_id=user_id,
        pipeline_status="cleaning",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        current_agent="cleaning",
        raw_query=raw_query,
        intent_class="trend_analysis",
        business_domain="hr",
        key_entities=["Employee", "Department", "Salary", "Performance_Score", "Region"],
        time_dimension="Joining_Date",
        raw_file_path=excel_path,
        file_type="xlsx",
        raw_row_count=len(df),
        raw_col_count=len(df.columns),
        schema_fingerprint="",
        schema_embedding_id=None,
        column_metadata=column_metadata,
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path=cleaned_parquet_path,
        data_quality_score=1.0,
        rows_before=len(df),
        rows_after=len(df),
        columns_dropped=[],
        feature_definitions=[],
        enriched_parquet_path="",
        feature_rationale="",
        generated_queries=[],
        query_results=[],
        queries_failed=[],
        dashboard_config=[],
        dashboard_title="",
        dashboard_theme="dark",
        layout_rationale="",
        qa_report=None,
        errors=[],
        retry_count=0,
        token_usage={}
    )
    
    print("\n" + "=" * 60)
    print("RUNNING FEATURE AGENT NODE")
    print("=" * 60)
    start_time = datetime.now()
    feature_state = await feature_node(initial_state, db, llm)
    duration = (datetime.now() - start_time).total_seconds()
    
    print(f"\nFeature Agent completed in {duration:.2f}s")
    print(f"Pipeline Status: {feature_state.pipeline_status}")
    print(f"Current Agent: {feature_state.current_agent}")
    print(f"Enriched Parquet Path: {feature_state.enriched_parquet_path}")
    print(f"Feature Definitions Count: {len(feature_state.feature_definitions)}")
    
    for i, fd in enumerate(feature_state.feature_definitions, 1):
        print(f"\n--- Accepted Feature {i} ---")
        print(f"  name: {fd.name}")
        print(f"  polars_expr: {fd.polars_expr}")
        print(f"  sql_expr: {fd.sql_expr}")
        print(f"  rationale: {fd.rationale}")
        print(f"  expected_insight: {fd.expected_insight}")
        
    # Check enriched parquet file on disk
    if os.path.exists(feature_state.enriched_parquet_path):
        enriched_df = pl.read_parquet(feature_state.enriched_parquet_path)
        print("\n" + "=" * 60)
        print("ENRICHED DATASET ON DISK")
        print("=" * 60)
        print(f"Row count: {len(enriched_df)}")
        print(f"Original column count: {len(df.columns)}")
        print(f"Final column count: {len(enriched_df.columns)}")
        print(f"All columns: {enriched_df.columns}")
        print("\nFirst 3 rows of enriched data:")
        for r in enriched_df.head(3).to_dicts():
            print(r)
    else:
        print("ERROR: Enriched parquet path does not exist on disk!")
        
    # Check DuckDB table 'data'
    print("\n" + "=" * 60)
    print("DUCKDB REGISTRATION CHECK")
    print("=" * 60)
    try:
        cnt = db.conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
        cols = [c[0] for c in db.conn.execute("DESCRIBE data").fetchall()]
        print(f"DuckDB table 'data' row count: {cnt}")
        print(f"DuckDB table 'data' column count: {len(cols)}")
        print(f"DuckDB table 'data' columns: {cols}")
        print("DuckDB Registration Status: SUCCESS (table 'data' is ready for downstream Analytics)")
    except Exception as e:
        print(f"DuckDB Registration Failed: {e}")
        
    # Save output for record
    with open("feature_verification_output.json", "w") as f:
        json.dump(feature_state.model_dump(), f, indent=2, default=str)
    print("\nSaved feature_verification_output.json successfully.")

if __name__ == "__main__":
    asyncio.run(run_feature_verification())

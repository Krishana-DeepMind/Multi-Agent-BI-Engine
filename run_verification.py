import asyncio
import os
import json
import uuid
from datetime import datetime, timezone
import polars as pl
import duckdb

from backend.core.state import AgentSwarmState, ColumnMeta, QAReport
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_profiler import SchemaProfiler
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import INGESTION_SYSTEM_PROMPT
from backend.agents.feature_node import feature_node
from backend.agents.analytics_node import analytics_node

async def run_pipeline():
    print("=" * 60)
    print("STARTING CONTROLLED VERIFICATION: FEATURE -> ANALYTICS")
    print("=" * 60)
    session_id = str(uuid.uuid4())
    user_id = "verification_user"
    
    # 1. Read Excel clean dataset
    excel_path = r"C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_analytics_test_dataset.xlsx"
    cleaned_parquet_path = r"C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_cleaned.parquet"
    
    df = pl.read_excel(excel_path)
    df.write_parquet(cleaned_parquet_path)
    print(f"Clean dataset saved to Parquet: {cleaned_parquet_path} ({len(df)} rows, {len(df.columns)} cols)")
    
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
    
    print("\nInferring semantic column metadata with LLM...")
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
    
    print(f"Constructed column_metadata ({len(column_metadata)} columns):")
    for cm in column_metadata:
        print(f"  - {cm.name}: {cm.dtype}, semantic={cm.semantic_type}, label='{cm.business_label}', kpi={cm.is_candidate_kpi}")
    
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
    
    # 3. Run Feature Node
    print("\n" + "=" * 60)
    print("STEP 1: RUNNING ACTUAL FEATURE AGENT")
    print("=" * 60)
    feature_start = datetime.now()
    feature_state = await feature_node(initial_state, db, llm)
    feature_duration = (datetime.now() - feature_start).total_seconds()
    print(f"Feature Agent completed in {feature_duration:.2f}s")
    print(f"Pipeline Status: {feature_state.pipeline_status}")
    print(f"Feature Definitions Generated: {len(feature_state.feature_definitions)}")
    print(f"Enriched Parquet Path: {feature_state.enriched_parquet_path}")
    print(f"Feature Rationale: {feature_state.feature_rationale}")
    
    # Inspect enriched parquet schema and sample data
    enriched_df = None
    if os.path.exists(feature_state.enriched_parquet_path):
        enriched_df = pl.read_parquet(feature_state.enriched_parquet_path)
        print(f"Enriched Dataset Shape: {enriched_df.shape}")
        print("Enriched Columns:", enriched_df.columns)
    
    # 4. Run Analytics Node
    print("\n" + "=" * 60)
    print("STEP 2: RUNNING ACTUAL ANALYTICS AGENT")
    print("=" * 60)
    analytics_start = datetime.now()
    analytics_state = await analytics_node(feature_state, db, llm)
    analytics_duration = (datetime.now() - analytics_start).total_seconds()
    print(f"Analytics Agent completed in {analytics_duration:.2f}s")
    print(f"Pipeline Status: {analytics_state.pipeline_status}")
    print(f"Queries Generated: {len(analytics_state.generated_queries)}")
    print(f"Query Results: {len(analytics_state.query_results)}")
    print(f"Queries Failed: {analytics_state.queries_failed}")
    
    # Save full state dumps for verification documentation
    with open("feature_state_raw.json", "w") as f:
        json.dump(feature_state.model_dump(), f, indent=2, default=str)
    with open("analytics_state_raw.json", "w") as f:
        json.dump(analytics_state.model_dump(), f, indent=2, default=str)
        
    # Also save enriched dataset sample
    if enriched_df is not None:
        sample_dict = {
            "columns": enriched_df.columns,
            "schema": {c: str(t) for c, t in zip(enriched_df.columns, enriched_df.dtypes)},
            "sample_rows": enriched_df.head(5).to_dicts()
        }
        with open("enriched_dataset_sample.json", "w") as f:
            json.dump(sample_dict, f, indent=2, default=str)
            
    print("\nSaved feature_state_raw.json, analytics_state_raw.json, and enriched_dataset_sample.json successfully.")

if __name__ == "__main__":
    asyncio.run(run_pipeline())

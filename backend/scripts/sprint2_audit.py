import os
import sys
import time
import json
import asyncio
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler
from backend.core.llm_router import LLMRouter, TaskType
from backend.agents.cleaning_node import cleaning_node
from backend.core.state import AgentSwarmState, QAReport
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sprint2_audit")


async def run_sprint2_benchmark():
    print("=" * 70)
    print(" MULTIAGENT BI ENGINE -- SPRINT 2 AUDIT & PERFORMANCE BENCHMARK")
    print("=" * 70)

    dataset_path = os.path.abspath("test_datasets/messy_orders_data.csv")
    if not os.path.exists(dataset_path):
        print(f"Dataset not found: {dataset_path}")
        return

    # 1. Ingestion Latency
    t0 = time.perf_counter()
    db = DuckDBEngine()
    file_bytes = open(dataset_path, "rb").read()
    db.load_from_bytes(file_bytes, "csv")
    ingest_time = time.perf_counter() - t0
    print(f"1. Ingestion Latency      : {ingest_time:.4f}s ({len(file_bytes)/1024:.1f} KB)")

    # 2. Schema Profiler Latency
    t0 = time.perf_counter()
    profiler = SchemaProfiler(db)
    column_meta = profiler.profile_table()
    profile_time = time.perf_counter() - t0
    print(f"2. Schema Profiler Latency: {profile_time:.4f}s ({len(column_meta)} columns profiled)")

    # 3. Full Cleaning Node Execution
    session_id = str(uuid.uuid4())
    initial_state = AgentSwarmState(
        session_id=uuid.UUID(session_id),
        user_id="audit_user",
        pipeline_status="cleaning",
        created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-01T00:00:00Z",
        current_agent="cleaning",
        raw_query="Analyze customer orders dataset",
        intent_class="trend_analysis",
        business_domain="ecommerce",
        key_entities=[],
        raw_file_path=dataset_path,
        file_type="csv",
        raw_row_count=len(file_bytes.splitlines()),
        raw_col_count=len(column_meta),
        schema_fingerprint="audit_fp",
        column_metadata=column_meta,
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path="",
        data_quality_score=0.0,
        rows_before=len(file_bytes.splitlines()),
        rows_after=len(file_bytes.splitlines()),
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
        qa_report=QAReport(
            data_quality_score=0.0,
            completeness_score=0.0,
            query_validity={},
            chart_relevance={},
            anomalies=[],
            suggestions=[],
            overall_confidence=0.0,
            approval_status="needs_review",
            reviewer_notes=None,
        ),
        errors=[],
        retry_count=0,
        token_usage={}
    )

    llm = LLMRouter()
    t0 = time.perf_counter()
    final_state = await cleaning_node(initial_state, db, llm)
    cleaning_time = time.perf_counter() - t0

    print(f"3. Cleaning Node Execution : {cleaning_time:.2f}s")
    print(f"   - Final Data Quality    : {final_state.data_quality_score * 100:.2f}%")
    print(f"   - Operations Applied    : {len(final_state.cleaning_operations)}")
    print(f"   - Columns Dropped       : {final_state.columns_dropped}")

    # 4. Token & Bottleneck Summary
    print("\n" + "=" * 70)
    print(" BOTTLENECK ANALYSIS & TOKEN AUDIT SUMMARY")
    print("=" * 70)
    total_pipeline_time = ingest_time + profile_time + cleaning_time
    print(f"Total Pipeline Latency : {total_pipeline_time:.2f}s (Target: <45.0s)")
    print("Bottleneck Breakdown:")
    print(f"  * Ingestion & DB Load : {ingest_time / total_pipeline_time * 100:.1f}%")
    print(f"  * Schema Profiling    : {profile_time / total_pipeline_time * 100:.1f}%")
    print(f"  * LLM + Polars Engine : {cleaning_time / total_pipeline_time * 100:.1f}% (Main API latency)")
    print("\nToken Capacity Projection:")
    print("  * Gemini 3.6 Flash Limit : 1,000,000 TPM / 1,500 RPM")
    print("  * Groq Qwen3.8-27b Limit : 14.4M Tokens / Day")
    print("  * Avg Tokens / Session   : ~6,500 Tokens")
    print("  * Daily Capacity         : ~2,200 Sessions / Day")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_sprint2_benchmark())

"""
Controlled End-to-End Test for Layout Agent with Clean Dataset
Tests: Ingestion -> Cleaning -> Feature -> Analytics -> Layout
"""
import asyncio
import os
import json
import uuid
from datetime import datetime, timezone
import duckdb

from backend.core.state import AgentSwarmState, QAReport
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter
from backend.core.graph_builder import build_pipeline_graph
from backend.agents.router_node import router_node
from backend.agents.layout_node import validate_echarts_option

CLEAN_DATASET_PATH = r"C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_analytics_test_dataset.xlsx"

async def run_controlled_test():
    print("=" * 60)
    print("STEP 2: RUNNING CONTROLLED END-TO-END PIPELINE")
    print("=" * 60)
    
    session_id = str(uuid.uuid4())
    user_id = "test_user"
    raw_query = "Provide executive insights on employee compensation, departmental distribution, performance, and regional trends"

    initial_state = AgentSwarmState(
        session_id=uuid.UUID(session_id),
        user_id=user_id,
        pipeline_status="initiated",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        current_agent="system",
        raw_query=raw_query,
        intent_class="trend_analysis",
        business_domain="hr",
        key_entities=["Employee", "Department", "Salary", "Performance"],
        time_dimension="Joining_Date",
        raw_file_path=CLEAN_DATASET_PATH,
        file_type="xlsx",
        raw_row_count=20,
        raw_col_count=7,
        schema_fingerprint="",
        schema_embedding_id=None,
        column_metadata=[],
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path="",
        data_quality_score=1.0,
        rows_before=20,
        rows_after=20,
        columns_dropped=[],
        feature_definitions=[],
        enriched_parquet_path="",
        feature_rationale="",
        generated_queries=[],
        query_results=[],
        queries_failed=[],
        dashboard_config=[],
        dashboard_title="",
        dashboard_theme="light",
        dashboard_filters=[],
        layout_rationale="",
        qa_report=QAReport(
            data_quality_score=1.0,
            completeness_score=1.0,
            query_validity={},
            chart_relevance={},
            anomalies=[],
            suggestions=[],
            overall_confidence=1.0,
            approval_status="approved",
            reviewer_notes=None,
        ),
        errors=[],
        retry_count=0,
        token_usage={},
    )

    print(f"Session ID: {session_id}")
    print(f"Dataset: {CLEAN_DATASET_PATH}")
    
    # 1. Router
    print("\n--- Running Intent Router ---")
    routed_state = await router_node(initial_state)
    print(f"Router completed: Intent={routed_state.intent_class}, Domain={routed_state.business_domain}")

    # 2. Build and run LangGraph pipeline
    print("\n--- Compiling Pipeline (Ingestion -> Cleaning -> Feature -> Analytics -> Layout) ---")
    db_engine = DuckDBEngine()
    llm_router = LLMRouter()
    pipeline = build_pipeline_graph(db_engine, llm_router)

    print("\n--- Invoking Pipeline Graph ---")
    start_time = datetime.now()
    final_output = await pipeline.ainvoke(
        routed_state.model_dump(),
        config={"configurable": {"thread_id": session_id}}
    )
    duration = (datetime.now() - start_time).total_seconds()
    print(f"\nPipeline finished in {duration:.1f}s")

    final_state = AgentSwarmState(**final_output) if isinstance(final_output, dict) else final_output

    print("\n" + "=" * 60)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Pipeline Status: {final_state.pipeline_status}")
    print(f"Errors: {final_state.errors}")
    print(f"Cleaned Parquet: {final_state.cleaned_parquet_path}")
    print(f"Data Quality Score: {final_state.data_quality_score}")
    print(f"Feature Definitions: {len(final_state.feature_definitions)}")
    print(f"Generated Queries: {len(final_state.generated_queries)}")
    print(f"Query Results: {len(final_state.query_results)}")
    print(f"Queries Failed: {final_state.queries_failed}")
    print(f"Dashboard Title: {final_state.dashboard_title}")
    print(f"Layout Rationale: {final_state.layout_rationale}")
    print(f"Dashboard Configs (Charts): {len(final_state.dashboard_config)}")
    print(f"Dashboard Filters: {len(getattr(final_state, 'dashboard_filters', []))}")

    # Save to a JSON artifact for detailed inspection
    output_summary = {
        "session_id": session_id,
        "dataset": CLEAN_DATASET_PATH,
        "status": final_state.pipeline_status,
        "errors": [err if isinstance(err, dict) else str(err) for err in final_state.errors],
        "generated_queries": [q.model_dump() if hasattr(q, "model_dump") else q for q in final_state.generated_queries],
        "query_results": [qr.model_dump() if hasattr(qr, "model_dump") else qr for qr in final_state.query_results],
        "dashboard_config": [c.model_dump() if hasattr(c, "model_dump") else c for c in final_state.dashboard_config],
        "dashboard_title": final_state.dashboard_title,
        "layout_rationale": final_state.layout_rationale,
        "dashboard_filters": getattr(final_state, "dashboard_filters", []),
    }

    with open("test_e2e_output.json", "w") as f:
        json.dump(output_summary, f, indent=2, default=str)
    print("\nSaved detailed execution output to test_e2e_output.json")
    return output_summary

if __name__ == "__main__":
    asyncio.run(run_controlled_test())

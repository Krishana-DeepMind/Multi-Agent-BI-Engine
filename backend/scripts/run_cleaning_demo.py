import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

# Add repository root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.core.state import AgentSwarmState, QAReport
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter
from backend.core.schema_profiler import SchemaProfiler
from backend.agents.cleaning_node import cleaning_node


async def main():
    print("=================================================================")
    print(" MULTIAGENT BI ENGINE -- CLEANING AGENT DEMO RUNNER")
    print("=================================================================")

    dataset_path = os.path.abspath("test_datasets/messy_orders_data.csv")
    if not os.path.exists(dataset_path):
        print(f"Dataset not found at {dataset_path}")
        return

    print(f"\n1. Loading dataset: {dataset_path}")
    db = DuckDBEngine()
    with open(dataset_path, "rb") as f:
        db.load_from_bytes(f.read(), "csv", table_name="raw_data")

    profiler = SchemaProfiler(db)
    column_metadata = profiler.profile_table("raw_data")

    print("\n2. Initial Dataset Profile:")
    for col in column_metadata:
        print(f"  - {col.name:<18} | Type: {col.dtype:<8} | Null%: {col.null_pct:<6.1%} | Samples: {col.sample_values[:2]}")

    state = AgentSwarmState(
        session_id=uuid.uuid4(),
        user_id="test_user",
        pipeline_status="ingesting",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        current_agent="ingestion",
        raw_query="Clean customer data",
        intent_class="distribution",
        business_domain="ecommerce",
        key_entities=["customers"],
        raw_file_path=dataset_path,
        file_type="csv",
        raw_row_count=15,
        raw_col_count=len(column_metadata),
        schema_fingerprint="test_fingerprint",
        similar_schemas_found=False,
        column_metadata=column_metadata,
        qa_report=QAReport(
            data_quality_score=0.5,
            completeness_score=0.5,
            query_validity={},
            chart_relevance={},
            anomalies=[],
            suggestions=[],
            overall_confidence=0.5,
            approval_status="needs_review",
        ),
    )

    llm = LLMRouter()

    print("\n3. Executing Cleaning Agent Node...")
    new_state = await cleaning_node(state, db, llm)

    print("\n" + "=" * 65)
    print(" CLEANING RESULTS")
    print("=" * 65)
    print(f"Status              : {new_state.pipeline_status}")
    print(f"Rows Before / After : {new_state.rows_before} -> {new_state.rows_after}")
    print(f"Data Quality Score  : {new_state.data_quality_score:.2%}")
    print(f"Columns Dropped     : {new_state.columns_dropped}")
    print(f"Cleaned Parquet     : {new_state.cleaned_parquet_path}")

    print("\nGenerated Operations:")
    for op in new_state.cleaning_operations:
        col = op.get("column") if isinstance(op, dict) else op.column
        operation = op.get("operation") if isinstance(op, dict) else op.operation
        strategy = op.get("strategy") if isinstance(op, dict) else op.strategy
        code = op.get("polars_code") if isinstance(op, dict) else op.polars_code
        print(f"  * Column: '{col}' -> Op: {operation} ({strategy})")
        if code:
            print(f"    Code: {code}")


if __name__ == "__main__":
    asyncio.run(main())

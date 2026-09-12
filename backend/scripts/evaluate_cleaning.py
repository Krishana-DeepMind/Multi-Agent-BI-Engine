import sys
import os
import asyncio
import uuid
import pandas as pd

# Add backend to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter
from backend.core.graph_builder import build_pipeline_graph

async def main():
    print("="*60)
    print("🧹 MULTI-AGENT BI ENGINE: CLEANING AGENT EVALUATION")
    print("="*60)
    
    file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../messy_customer_data.csv'))
    
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    # Initialize dependencies
    db_engine = DuckDBEngine()
    llm_router = LLMRouter()
    
    app = build_pipeline_graph(db_engine, llm_router)
    
    session_id = str(uuid.uuid4())
    
    initial_state = {
        "session_id": session_id,
        "user_id": "eval_user",
        "pipeline_status": "routing",
        "created_at": "2026-08-27T10:00:00Z",
        "updated_at": "2026-08-27T10:00:00Z",
        "current_agent": "router",
        "raw_query": "Clean my customer data for sales analysis",
        "intent_class": "trend_analysis",
        "business_domain": "ecommerce",
        "key_entities": ["customer", "purchase"],
        "raw_file_path": file_path,
        "file_type": "csv",
        "raw_row_count": 0,
        "raw_col_count": 0,
        "schema_fingerprint": "",
        "similar_schemas_found": False,
        "column_metadata": [],
    }
    
    print("\n🚀 [1/3] Starting Ingestion Agent...")
    
    # We use stream to intercept state after ingestion
    final_state = None
    config = {"configurable": {"thread_id": "eval_1"}}
    try:
        async for output in app.astream(initial_state, config):
            for node_name, state in output.items():
                if node_name == "ingestion":
                    print(f"✅ Ingestion Complete!")
                    print(f"📊 Schema Profiled: {state.get('raw_col_count')} columns, {state.get('raw_row_count')} rows.")
                    
                    # Calculate BEFORE quality score
                    # We can use duckdb to calculate nulls
                    df = db_engine.conn.execute(f"SELECT * FROM {db_engine.current_table}").fetchdf()
                    total_cells = len(df) * len(df.columns)
                    if total_cells > 0:
                        nulls = df.isna().sum().sum()
                        completeness_before = 1.0 - (nulls / total_cells)
                    else:
                        completeness_before = 0.0
                    print(f"📉 DATA QUALITY SCORE (BEFORE CLEANING): {completeness_before:.1%}")
                    
                    print("\n🚀 [2/3] Starting Cleaning Agent...")
                    print("   Generating strategy & writing Polars code...")
                    
                elif node_name == "cleaning":
                    final_state = state
                    print(f"✅ Cleaning Complete!")
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
                
    if final_state:
        print("\n" + "="*60)
        print("📋 CLEANING PROGRESS & OPERATIONS APPLIED:")
        print("="*60)
        for i, op in enumerate(final_state.get("cleaning_operations", [])):
            print(f"[{i+1}] Column: {op.get('column')} | Operation: {op.get('operation')} | Strategy: {op.get('strategy')}")
            print(f"    ↳ Code: {op.get('polars_code')}")
            
        print("\n" + "="*60)
        print("🏆 FINAL RESULTS:")
        print("="*60)
        
        score_after = final_state.get("data_quality_score", 0.0)
        rows_before = final_state.get("rows_before", 0)
        rows_after = final_state.get("rows_after", 0)
        
        print(f"📈 DATA QUALITY SCORE (AFTER CLEANING):  {score_after:.1%}")
        print(f"📉 Rows Before: {rows_before} | Rows After: {rows_after} ({(rows_before - rows_after)} rows dropped)")
        print(f"🗑️ Columns Dropped: {', '.join(final_state.get('columns_dropped', [])) or 'None'}")
        print(f"💾 Cleaned Data saved to: {final_state.get('cleaned_parquet_path')}")
        print("="*60)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import uuid
import sys
import os
sys.path.insert(0, os.path.abspath('.'))

from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter
from backend.core.graph_builder import build_pipeline_graph

async def main():
    db = DuckDBEngine()
    router = LLMRouter()
    app = build_pipeline_graph(db, router)
    state = {
        'session_id': str(uuid.uuid4()),
        'user_id': 'eval_user',
        'pipeline_status': 'routing',
        'created_at': '2026-08-27T10:00:00Z',
        'updated_at': '2026-08-27T10:00:00Z',
        'current_agent': 'router',
        'raw_query': 'Clean my customer data for sales analysis',
        'intent_class': 'trend_analysis',
        'business_domain': 'ecommerce',
        'key_entities': ['customer'],
        'raw_file_path': 'messy_customer_data.csv',
        'file_type': 'csv',
        'raw_row_count': 0,
        'raw_col_count': 0,
        'schema_fingerprint': '',
        'similar_schemas_found': False,
        'column_metadata': [],
    }
    config = {'configurable': {'thread_id': 'eval_debug_2'}}
    async for out in app.astream(state, config):
        for node_name, node_state in out.items():
            d = node_state if isinstance(node_state, dict) else (node_state.model_dump() if hasattr(node_state, 'model_dump') else {})
            ops = d.get('cleaning_operations', [])
            print(f"\n[NODE: {node_name}]")
            print(f"  Status: {d.get('pipeline_status')}")
            print(f"  Quality Score: {d.get('data_quality_score')}")
            print(f"  Rows Before: {d.get('rows_before')} | After: {d.get('rows_after')}")
            print(f"  Operations ({len(ops)}):")
            for op in ops:
                col = op.get('column', '?') if isinstance(op, dict) else op.column
                operation = op.get('operation', '?') if isinstance(op, dict) else op.operation
                strategy = op.get('strategy', '?') if isinstance(op, dict) else op.strategy
                code = op.get('polars_code', '') if isinstance(op, dict) else op.polars_code
                print(f"    - [{operation}] col={col} | strategy={strategy}")
                if code:
                    print(f"        code: {code[:80]}")

asyncio.run(main())

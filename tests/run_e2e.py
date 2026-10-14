import sys, os, asyncio, uuid, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

from backend.core.state import AgentSwarmState
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.llm_router import LLMRouter
from backend.agents.feature_node import feature_node
from backend.agents.analytics_node import analytics_node

CAFE_META = [
    {"name":"Transaction ID","original_name":"Transaction ID","dtype":"VARCHAR","semantic_type":"identifier","business_label":"Transaction ID","null_pct":0.0,"unique_pct":1.0,"sample_values":["TXN_1"],"is_primary_key":True,"is_candidate_kpi":False},
    {"name":"Item","original_name":"Item","dtype":"VARCHAR","semantic_type":"dimension","business_label":"Item","null_pct":0.05,"unique_pct":0.03,"sample_values":["Coffee"],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Quantity","original_name":"Quantity","dtype":"INT","semantic_type":"metric","business_label":"Quantity","null_pct":0.0,"unique_pct":0.03,"sample_values":[2],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Price Per Unit","original_name":"Price Per Unit","dtype":"DOUBLE","semantic_type":"currency","business_label":"Price Per Unit","null_pct":0.0,"unique_pct":0.02,"sample_values":[2.0],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Total Spent","original_name":"Total Spent","dtype":"DOUBLE","semantic_type":"currency","business_label":"Total Spent","null_pct":0.05,"unique_pct":0.1,"sample_values":[4.0],"is_primary_key":False,"is_candidate_kpi":True},
    {"name":"Payment Method","original_name":"Payment Method","dtype":"VARCHAR","semantic_type":"dimension","business_label":"Payment Method","null_pct":0.05,"unique_pct":0.02,"sample_values":["Cash"],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Location","original_name":"Location","dtype":"VARCHAR","semantic_type":"dimension","business_label":"Location","null_pct":0.05,"unique_pct":0.01,"sample_values":["In-store"],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Transaction Date","original_name":"Transaction Date","dtype":"VARCHAR","semantic_type":"date","business_label":"Transaction Date","null_pct":0.0,"unique_pct":0.5,"sample_values":["2023-09-08"],"is_primary_key":False,"is_candidate_kpi":False},
]

EMP_META = [
    {"name":"Employee_ID","original_name":"Employee_ID","dtype":"VARCHAR","semantic_type":"identifier","business_label":"Employee ID","null_pct":0.0,"unique_pct":1.0,"sample_values":["EMP1000"],"is_primary_key":True,"is_candidate_kpi":False},
    {"name":"Department_Region","original_name":"Department_Region","dtype":"VARCHAR","semantic_type":"dimension","business_label":"Department Region","null_pct":0.0,"unique_pct":0.1,"sample_values":["DevOps-California"],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Status","original_name":"Status","dtype":"VARCHAR","semantic_type":"dimension","business_label":"Status","null_pct":0.0,"unique_pct":0.04,"sample_values":["Active"],"is_primary_key":False,"is_candidate_kpi":False},
    {"name":"Salary","original_name":"Salary","dtype":"DOUBLE","semantic_type":"currency","business_label":"Salary","null_pct":0.0,"unique_pct":1.0,"sample_values":[59767.65],"is_primary_key":False,"is_candidate_kpi":True},
    {"name":"Performance_Score","original_name":"Performance_Score","dtype":"VARCHAR","semantic_type":"dimension","business_label":"Performance Score","null_pct":0.0,"unique_pct":0.04,"sample_values":["Average"],"is_primary_key":False,"is_candidate_kpi":False},
]

async def run_test(label, cleaned_path, col_meta, raw_query, intent, domain):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  raw_query: {repr(raw_query) if raw_query else '(empty)'}")
    print(f"{'='*60}")

    db = DuckDBEngine()
    llm = LLMRouter()
    sid = uuid.uuid4()

    state = AgentSwarmState(
        session_id=sid, user_id="e2e", pipeline_status="cleaning",
        created_at="2026-09-23T00:00:00Z", updated_at="2026-09-23T00:00:00Z",
        current_agent="cleaning", raw_query=raw_query, intent_class=intent,
        business_domain=domain, key_entities=[], raw_file_path="local",
        file_type="csv", raw_row_count=200, raw_col_count=len(col_meta),
        schema_fingerprint="e2e", column_metadata=col_meta,
        similar_schemas_found=False, cleaned_parquet_path=cleaned_path,
    )

    print("[FEATURE NODE]")
    s2 = await feature_node(state, db, llm)
    enriched = s2.enriched_parquet_path or ""
    feat_ok = s2.pipeline_status == "featuring" and os.path.exists(enriched)
    print(f"  status={s2.pipeline_status}, features={len(s2.feature_definitions)}, enriched_exists={os.path.exists(enriched)}")

    try:
        cnt = db.conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
        duckdb_ok = cnt > 0
        print(f"  DuckDB 'data' table: {cnt} rows  [PASS]")
    except Exception as ex:
        duckdb_ok = False
        print(f"  DuckDB 'data' table: ERROR {ex}  [FAIL]")

    print("[ANALYTICS NODE]")
    s3 = await analytics_node(s2, db, llm)
    anal_ok = s3.pipeline_status == "querying"
    print(f"  status={s3.pipeline_status}, queries_ok={len(s3.generated_queries)}, results={len(s3.query_results)}, failed={len(s3.queries_failed)}")

    raw_q_in_analytics = repr(s3.generated_queries[0].sql[:60] if s3.generated_queries else "(no queries)")
    print(f"  sample SQL: {raw_q_in_analytics}")

    overall = feat_ok and duckdb_ok and anal_ok
    print(f"\nFEATURE={('PASS' if feat_ok else 'FAIL')}  DUCKDB={('PASS' if duckdb_ok else 'FAIL')}  ANALYTICS={('PASS' if anal_ok else 'FAIL')}  OVERALL={('PASS' if overall else 'FAIL')}")
    return overall

async def main():
    r1 = await run_test(
        "TEST A: dirty_cafe_sales (NO user query)",
        "tests/e2e_tmp/cafe/cleaned.parquet",
        CAFE_META, "", "trend_analysis", "ecommerce",
    )
    r2 = await run_test(
        "TEST B: Messy_Employee (WITH user query)",
        "tests/e2e_tmp/employee/cleaned.parquet",
        EMP_META, "Analyze salary performance by region and department",
        "comparison", "hr",
    )
    print("\n" + "="*60)
    print("FINAL RESULTS")
    print(f"  Test A (no query):   {'PASS' if r1 else 'FAIL'}")
    print(f"  Test B (with query): {'PASS' if r2 else 'FAIL'}")
    print("="*60)
    sys.exit(0 if (r1 and r2) else 1)

asyncio.run(main())

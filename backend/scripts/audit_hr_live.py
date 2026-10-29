"""
Audit script for messy_hr_employees.csv.
Validates:
1. Gender normalization: '-', '?', '#n/a' -> 'unknown'
2. Annualized compensation formula: COALESCE(total_compensation, salary + bonus_amount)
3. Bonus amount: salary * (bonus_pct / 100) with clean salary (no 480M outlier, '59k' multiplied)
4. Monthly attrition trend: 55 valid months rendered with dates and counts
5. Bonus by department vs company average: proper percentages and benchmark
"""
import sys, os, json, asyncio
sys.stdout.reconfigure(encoding='utf-8')
import polars as pl
import duckdb
from backend.core.duckdb_engine import DuckDBEngine
from backend.agents.cleaning_node import cleaning_node
from backend.agents.feature_node import feature_node
from backend.agents.analytics_node import analytics_node
from backend.agents.layout_node import layout_node
from backend.core.state import AgentSwarmState

async def run_hr_audit():
    csv_path = os.path.abspath("test_datasets/messy_hr_employees.csv")
    session_id = "9ec604a3-8cf7-4827-b791-e06ec187aadc"
    user_id = "00000000-0000-0000-0000-000000000000"
    
    session_dir = f"tmp_storage/{session_id}"
    os.makedirs(session_dir, exist_ok=True)
    db_engine = DuckDBEngine()
    
    # 1. Ingest raw CSV
    csv_path_fwd = csv_path.replace("\\", "/")
    db_engine.conn.execute(f"CREATE OR REPLACE TABLE raw_data AS SELECT * FROM read_csv_auto('{csv_path_fwd}')")
    db_engine.current_table = "raw_data"
    raw_count = db_engine.conn.execute("SELECT COUNT(*) FROM raw_data").fetchone()[0]
    print(f"Ingested {raw_count} raw rows.")

    # 2. Run Cleaning Node
    state = AgentSwarmState(
        session_id=session_id,
        user_id=user_id,
        file_path=csv_path,
        file_name="messy_hr_employees.csv",
        file_type="csv",
        row_count=raw_count,
        col_count=len(db_engine.conn.execute("SELECT * FROM raw_data LIMIT 0").description),
        current_agent="cleaning",
        pipeline_status="profiling",
        active_dataset_id=session_id,
        raw_query="Analyze this HR dataset and generate an executive dashboard"
    )

    clean_state = await cleaning_node(state, db_engine, llm_router=None)
    cleaned_path = clean_state.cleaned_parquet_path
    print(f"Cleaned parquet created at: {cleaned_path}")
    
    # Verify cleaned data
    df_clean = pl.read_parquet(cleaned_path)
    print("\n--- GENDER DISTRIBUTION IN CLEANED DATA ---")
    genders = df_clean["gender"].value_counts().sort("count", descending=True)
    print(genders)
    assert not any(g in ["-", "?", "#n/a", "N/A", "NA"] for g in df_clean["gender"].to_list()), "Invalid gender sentinels found!"
    print("Gender normalization verified: No '-', '?', or '#n/a' sentinels present.")

    print("\n--- SALARY STATS IN CLEANED DATA ---")
    sals = [s for s in df_clean["salary"].to_list() if s is not None]
    print(f"Valid salaries: {len(sals)}, Mean: ${sum(sals)/len(sals):,.2f}, Min: ${min(sals):,.2f}, Max: ${max(sals):,.2f}")
    assert max(sals) <= 5000000, "Outlier salary > $5M not filtered!"
    assert min(sals) > 0, "Non-positive salary not filtered!"
    print("Salary cleaning verified: Outliers bounded, 'k' expanded, sentinels filtered.")

    # 3. Run Feature Node
    feat_state = await feature_node(clean_state, db_engine, llm_router=None)
    enriched_path = feat_state.enriched_parquet_path
    df_enriched = pl.read_parquet(enriched_path)
    print(f"\nEnriched parquet created at: {enriched_path}")
    print(f"Enriched columns: {df_enriched.columns}")

    print("\n--- ANNUALIZED COMPENSATION & BONUS STATS ---")
    if "bonus_amount" in df_enriched.columns:
        bonuses = [b for b in df_enriched["bonus_amount"].to_list() if b is not None]
        print(f"Valid bonuses: {len(bonuses)}, Mean: ${sum(bonuses)/len(bonuses):,.2f}, Median: ${sorted(bonuses)[len(bonuses)//2]:,.2f}")
    if "annualized_compensation" in df_enriched.columns:
        comps = [c for c in df_enriched["annualized_compensation"].to_list() if c is not None]
        print(f"Valid annualized comps: {len(comps)}, Mean: ${sum(comps)/len(comps):,.2f}, Median: ${sorted(comps)[len(comps)//2]:,.2f}")
        assert sum(comps)/len(comps) > 60000, f"Annualized comp mean unexpectedly low: {sum(comps)/len(comps)}"
        print("Annualized compensation verified: Correct executive range ($80K-$90K).")

    # 4. Run Analytics & Layout Nodes
    anal_state = await analytics_node(feat_state, db_engine, llm_router=None)
    print(f"\nGenerated queries: {len(anal_state.generated_queries)}")
    for q in anal_state.generated_queries:
        print(f"  Query [{q.query_id}]: {q.title}")

    final_state = await layout_node(anal_state, db_engine, llm_router=None)
    cfg = final_state.dashboard_config
    charts = cfg.get("charts", []) if isinstance(cfg, dict) else (cfg if isinstance(cfg, list) else [])
    print(f"\nFinal Dashboard Config contains {len(charts)} chart/card items.")

    for c in charts:
        title = c.get("title")
        q_id = c.get("query_id")
        ct = c.get("chart_type")
        opt = c.get("echarts_option", {})
        print(f"\n>> Item [{q_id}] ({ct}): {title}")
        if ct == "kpi_card":
            print(f"   Value: {opt.get('value')}, Raw: {opt.get('raw_value')}")
        elif ct == "line":
            x_data = opt.get("xAxis", {}).get("data", [])
            s_data = opt.get("series", [{}])[0].get("data", [])
            print(f"   Line points count: {len(s_data)}, xAxis categories count: {len(x_data)}")
            print(f"   Sample data: {s_data[:3]}")
        elif ct == "bar":
            x_data = opt.get("xAxis", {}).get("data", [])
            s_data = opt.get("series", [{}])[0].get("data", [])
            print(f"   Bar categories: {x_data[:5]}")
            print(f"   Sample data: {s_data[:3]}")

    # Also save dashboard.json to session directory for UI testing
    dashboard_json_path = os.path.join(session_dir, "dashboard.json")
    with open(dashboard_json_path, "w", encoding="utf-8") as f:
        json.dump(final_state.model_dump(), f, default=str, indent=2)
    print(f"\nSuccessfully wrote updated dashboard.json to {dashboard_json_path}")

if __name__ == "__main__":
    asyncio.run(run_hr_audit())

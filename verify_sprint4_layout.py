"""
Sprint 4 -- Focused Layout Agent Verification
Tests the Layout Agent with real Analytics output from test_e2e_output.json
"""
import json
import asyncio
import os

EOUTPUT = "test_e2e_output.json"

def check_e2e_output():
    if not os.path.exists(EOUTPUT):
        print(f"MISSING: {EOUTPUT} -- run full pipeline first")
        return

    with open(EOUTPUT, encoding="utf-8") as f:
        data = json.load(f)

    charts    = data.get("dashboard_config", [])
    queries   = data.get("generated_queries", [])
    rationale = data.get("layout_rationale", "")
    title     = data.get("dashboard_title", "")
    filters   = data.get("dashboard_filters", [])

    print("=" * 55)
    print("SPRINT 4 -- LAYOUT VERIFICATION REPORT")
    print("=" * 55)
    print(f"Status          : {data.get('status', 'unknown')}")
    print(f"Dashboard Title : {title}")
    print(f"Layout Rationale: {rationale}")
    print(f"Queries Generated: {len(queries)}")
    print(f"Charts Generated : {len(charts)}")
    print(f"Filters Detected : {len(filters)}")

    kpis = [c for c in charts if c.get("echarts_option", {}).get("_chart_type") == "kpi_card"
            or c.get("echarts_option", {}).get("type") == "kpi_card"]
    regular = [c for c in charts if c not in kpis]

    print(f"\nKPI Cards        : {len(kpis)} (max 4)")
    print(f"Regular Charts   : {len(regular)}")

    print("\n--- KPI Cards ---")
    for c in kpis[:4]:
        opt = c.get("echarts_option", {})
        print(f"  [{c['query_id'][:12]}] {c['title']} | value={opt.get('value','?')}")

    print("\n--- Regular Charts ---")
    for c in regular:
        opt     = c.get("echarts_option", {})
        ct      = opt.get("_chart_type", "unknown")
        insight = opt.get("_insight_summary", "")[:60]
        layout  = c.get("layout", {})
        print(f"  [{c['query_id'][:12]}] {c['title'][:35]:<35} type={ct:<10} w={layout.get('w')} insight={'YES' if insight else 'NO'}")

    print("\n--- Dynamic Filters ---")
    if filters:
        for f in filters:
            print(f"  {f['label']} ({f['column']}) type={f.get('type','?')}")
    else:
        print("  None (may be normal if column_metadata not detected)")

    print("\n--- ECharts Validation ---")
    from backend.agents.layout_node import validate_echarts_option
    ok = 0; bad = 0
    for c in regular:
        opt = c.get("echarts_option", {})
        ct  = str(opt.get("_chart_type", "bar"))
        valid, msg = validate_echarts_option(opt, ct)
        if valid:
            ok += 1
        else:
            bad += 1
            print(f"  INVALID [{c['query_id'][:12]}] {msg}")
    print(f"  Valid: {ok}/{len(regular)} regular charts")

    print("\n--- PASS/FAIL Checklist ---")
    checks = [
        ("Charts generated",        len(charts) > 0),
        ("KPI cards <= 4",          len(kpis) <= 4),
        ("Insight lines present",   any(c.get("echarts_option",{}).get("_insight_summary","") for c in regular)),
        ("Rationale populated",     bool(rationale)),
        ("Dashboard title set",     bool(title)),
        ("ECharts config valid",    bad == 0),
    ]
    for name, result in checks:
        print(f"  {'PASS' if result else 'FAIL'} {name}")

    print("\n" + "=" * 55)
    errors = data.get("errors", [])
    if errors:
        print(f"Pipeline Errors: {len(errors)}")
        for e in errors[:3]:
            print(f"  {e}")

if __name__ == "__main__":
    check_e2e_output()

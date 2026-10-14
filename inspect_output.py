import json

with open("test_e2e_output.json") as f:
    d = json.load(f)

print("=" * 60)
print("ANALYSIS OF GENERATED CHARTS & ECHARTS OPTIONS")
print("=" * 60)
print(f"Total Charts: {len(d['dashboard_config'])}")
print(f"Total Queries: {len(d['generated_queries'])}")
print(f"Total Results: {len(d['query_results'])}")

q_map = {q["id"]: q for q in d["generated_queries"]}
qr_map = {qr["query_id"]: qr for qr in d["query_results"]}

for i, c in enumerate(d["dashboard_config"]):
    qid = c["query_id"]
    q = q_map.get(qid, {})
    qr = qr_map.get(qid, {})
    opt = c["echarts_option"]
    ctype = opt.get("_chart_type", "unknown")
    layout = c.get("layout", {})
    
    print(f"\n--- Chart #{i+1}: {qid} ---")
    print(f"Title: {c.get('title')}")
    print(f"Subtitle: {c.get('subtitle')}")
    print(f"Chart Type: {ctype}")
    print(f"Layout: x={layout.get('x')}, y={layout.get('y')}, w={layout.get('w')}, h={layout.get('h')}")
    print(f"Result rows: {qr.get('row_count')}, cols: {qr.get('column_names')}")
    print(f"X-Axis: {q.get('x_axis')}, Y-Axis: {q.get('y_axis')}, Group-by: {q.get('group_by')}")
    
    if opt.get("_is_kpi"):
        print(f"  [KPI Card] value={opt.get('value')}, label={opt.get('label')}")
    elif opt.get("_is_table"):
        print(f"  [Data Table] columns={opt.get('columns')}, rows count={len(opt.get('rows', []))}")
    else:
        series = opt.get("series", [])
        stypes = [s.get("type") for s in series] if isinstance(series, list) else []
        print(f"  [ECharts] Series count: {len(series)}, Types: {stypes}")
        print(f"  [ECharts] Tooltip: {bool(opt.get('tooltip'))}, Legend: {bool(opt.get('legend'))}")
        print(f"  [ECharts] X-Axis: {opt.get('xAxis')}")
        print(f"  [ECharts] Y-Axis: {opt.get('yAxis')}")

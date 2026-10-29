import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('tmp_storage/b45f1cda-3d16-425b-a723-a6a8bc25cbdf/dashboard.json', encoding='utf-8'))
print('Session ID:', d.get('session_id'))
print('File Name:', d.get('file_name'))
print('Raw Query:', d.get('raw_query'))
print('Row Count:', d.get('row_count'))
print('Data Quality Score:', d.get('data_quality_score'))

queries = d.get('generated_queries', [])
print(f'\nTotal SQL Queries Executed: {len(queries)}')
for q in queries:
    print(f"  • {q.get('title')} ({q.get('analytical_purpose')}): {q.get('sql')}")

charts = d.get('dashboard_config', [])
print(f'\nTotal Configured Visualizations: {len(charts)}')
for idx, c in enumerate(charts):
    ctype = c.get('chart_type')
    title = c.get('title')
    print(f'\n[{idx+1}] Type: {ctype} | Title: {title}')
    opt = c.get('echarts_option', {})
    if ctype == 'kpi_card':
        metrics = opt.get('metrics') or [{'label': title, 'value': opt.get('value')}]
        print('    KPI Value:', [(m.get('label'), m.get('value')) for m in metrics])
    else:
        series = opt.get('series', [])
        s0 = series[0] if series else {}
        d_pts = s0.get('data', [])
        print(f"    Series Type: {s0.get('type')} | Data Points: {len(d_pts)}")
        if d_pts and len(d_pts) <= 10:
            print(f"    Data: {d_pts}")
        elif d_pts:
            print(f"    Data sample (first 4): {d_pts[:4]}")
        if 'xAxis' in opt:
            print(f"    X-Axis Categories: {opt['xAxis'].get('data', [])[:6]}")
    print(f"    Insight: {c.get('insight_summary', '')[:90]}")


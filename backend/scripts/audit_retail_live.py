import requests
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

file_path = 'test_datasets/messy_retail_orders.csv'
display_name = 'messy_retail_orders.csv'

with open(file_path, 'rb') as f:
    up_res = requests.post(
        'http://127.0.0.1:8000/api/upload',
        files={'file': (display_name, f, 'text/csv')}
    )
assert up_res.status_code == 201, f'Upload failed: {up_res.text}'
up_data = up_res.json()
sid = up_data['session_id']
print(f'Uploaded successfully! Session ID: {sid}')

start_res = requests.post(f'http://127.0.0.1:8000/api/pipeline/{sid}/start?raw_query=Analyze retail orders, sales revenue, margins, and anomaly patterns')
assert start_res.status_code == 200, f'Start failed: {start_res.text}'
print('Pipeline started!')

stream_url = f'http://127.0.0.1:8000/api/pipeline/{sid}/stream'
with requests.get(stream_url, stream=True, timeout=300) as resp:
    for line in resp.iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8')
        if line_str.startswith('data: '):
            try:
                payload = json.loads(line_str[6:])
            except Exception:
                continue
            status = payload.get('status')
            agent = payload.get('agent', '')
            msg = payload.get('message', '')
            print(f'  [{agent or status}] {msg}')
            if status in ('complete', 'failed'):
                break

dash_res = requests.get(f'http://127.0.0.1:8000/api/dashboard/{sid}')
if dash_res.status_code == 200:
    dash = dash_res.json()
    print('\n=== DASHBOARD SUMMARY ===')
    print('Title:', dash.get('title'))
    print('DQS:', dash.get('data_quality_score'))
    print('Sub-scores:', dash.get('quality_sub_scores'))
    charts = dash.get('charts', [])
    print(f'Charts count: {len(charts)}')
    for i, c in enumerate(charts):
        c_type = c.get('type')
        c_title = c.get('title')
        c_insight = c.get('insight_summary')
        print(f'\nChart {i+1}: [{c_type}] {c_title}')
        print(f'  Insight: {c_insight}')
        if c_type == 'kpi_card':
            print(f'  Metrics: {c.get("metrics")}')
        elif c.get('echarts_option'):
            series = c.get('echarts_option', {}).get('series', [])
            s_types = [s.get('type') for s in series if isinstance(s, dict)]
            s_smooth = [s.get('smooth') for s in series if isinstance(s, dict)]
            print(f'  Series Types: {s_types}, Smooth: {s_smooth}')
else:
    print('Dashboard fetch failed:', dash_res.status_code, dash_res.text)

import requests
import json
import time

fname = '3_retail_orders_1000rows.csv'
fpath = 'test_datasets/evaluation/' + fname
query = 'Analyze order volume, product category sales, profit, and discount distribution'

with open(fpath, 'rb') as f:
    up = requests.post('http://127.0.0.1:8000/api/upload', files={'file': (fname, f, 'text/csv')}).json()
sid = up['session_id']
print('Uploaded. Session:', sid)

start_url = f'http://127.0.0.1:8000/api/pipeline/{sid}/start?raw_query={requests.utils.quote(query)}'
requests.post(start_url, json={})

with requests.get(f'http://127.0.0.1:8000/api/pipeline/{sid}/stream', stream=True, timeout=120) as r:
    for line in r.iter_lines():
        if line and line.startswith(b'data: '):
            p = json.loads(line[6:])
            status = p.get('status')
            agent = p.get('agent')
            if agent:
                print(f"  [{agent}] {p.get('message', '')[:60]}")
            if status in ('complete', 'failed'):
                print('Stream finished:', status)
                break

dash = requests.get(f'http://127.0.0.1:8000/api/dashboards/{sid}').json()
cfg = dash.get('config') or dash.get('charts', [])
charts = cfg if isinstance(cfg, list) else cfg.get('charts', [])
print(f'\nTotal Charts: {len(charts)}')
for c in charts:
    ctype = c.get('chart_type')
    title = c.get('title')
    opt = c.get('echarts_option', {})
    val = opt.get('value') or (opt.get('metrics', [{}])[0].get('value') if opt.get('metrics') else '')
    print(f'  • [{ctype}] {title}: {val}')
print('\nDashboard URL: http://localhost:3000/dashboard/' + sid)


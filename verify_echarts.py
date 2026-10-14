import json
from backend.agents.layout_node import validate_echarts_option

with open("test_e2e_output.json") as f:
    d = json.load(f)

print("=" * 60)
print("STEP 5: VERIFY ECHARTS GENERATION & SANITIZATION")
print("=" * 60)

for i, c in enumerate(d["dashboard_config"]):
    qid = c["query_id"]
    opt = c["echarts_option"]
    ctype = opt.get("_chart_type")
    
    # Check for NaN or undefined in JSON string
    raw_str = json.dumps(opt)
    has_nan = "NaN" in raw_str
    has_undefined = "undefined" in raw_str
    
    print(f"\nChecking Chart #{i+1}: {qid} (type: {ctype})")
    print(f"  No NaN: {not has_nan}")
    print(f"  No undefined: {not has_undefined}")
    
    if opt.get("_is_kpi"):
        print(f"  Special KPI format verified: value='{opt.get('value')}', label='{opt.get('label')}'")
    elif opt.get("_is_table"):
        print(f"  Special Data Table format verified: cols={len(opt.get('columns', []))}, rows={len(opt.get('rows', []))}")
    else:
        is_valid, err = validate_echarts_option(opt, ctype)
        print(f"  validate_echarts_option result: valid={is_valid}, error='{err}'")
        print(f"  Title present: {bool(opt.get('title'))}")
        print(f"  Tooltip present: {bool(opt.get('tooltip'))}")
        print(f"  Grid present: {bool(opt.get('grid'))}")
        print(f"  Series present: {len(opt.get('series', []))} series")
        if opt.get("xAxis"):
            print(f"  xAxis type: {opt.get('xAxis', {}).get('type')}")
        if opt.get("yAxis"):
            y_ax = opt.get('yAxis')
            if isinstance(y_ax, list):
                print(f"  yAxis types: {[y.get('type') for y in y_ax]}")
            else:
                print(f"  yAxis type: {y_ax.get('type')}")

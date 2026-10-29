import sys
import os
import time
import glob
import json
import requests
import polars as pl

sys.stdout.reconfigure(encoding="utf-8")

DATASETS = [
    ("test_datasets/evaluation/1_customers_15rows.csv", "Analyze customer demographics, purchase amounts, ratings, and active status"),
    ("test_datasets/evaluation/2_hr_employees_1000rows.csv", "Analyze workforce headcount, department compensation, tenure, and attrition"),
    ("test_datasets/evaluation/3_retail_orders_1000rows.csv", "Analyze order volume, product category sales, profit, and discount distribution"),
    ("test_datasets/evaluation/4_medical_patients_506rows.csv", "Analyze hospital patient admissions, diagnosis categories, billing amounts, and outcomes"),
    ("test_datasets/evaluation/5_company_payroll_1000rows.csv", "Analyze company payroll, department salary distribution, performance ratings, and employment status"),
]

def audit_dataset(file_path: str, query: str):
    fname = os.path.basename(file_path)
    print(f"\n{'='*70}")
    print(f"TESTING DATASET: {fname}")
    print(f"{'='*70}")

    # 1. Upload
    with open(file_path, "rb") as f:
        up_res = requests.post(
            "http://127.0.0.1:8000/api/upload",
            files={"file": (fname, f, "text/csv")},
            timeout=30
        )
    if up_res.status_code not in (200, 201):
        print(f"❌ Upload failed with status {up_res.status_code}: {up_res.text}")
        return {"file": fname, "success": False, "error": up_res.text}

    up_data = up_res.json()
    sid = up_data["session_id"]
    print(f"✓ Uploaded successfully. Session ID: {sid}")

    # 2. Start Pipeline
    start_url = f"http://127.0.0.1:8000/api/pipeline/{sid}/start?raw_query={requests.utils.quote(query)}"
    start_res = requests.post(start_url, json={}, timeout=30)
    if start_res.status_code != 200:
        print(f"❌ Pipeline start failed: {start_res.text}")
        return {"file": fname, "success": False, "error": start_res.text}

    print(f"✓ Pipeline started. Streaming SSE events...")

    # 3. Stream SSE until complete
    stream_url = f"http://127.0.0.1:8000/api/pipeline/{sid}/stream"
    completed = False
    start_time = time.time()
    try:
        with requests.get(stream_url, stream=True, timeout=240) as s_resp:
            for line in s_resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="ignore")
                if line_str.startswith("data: "):
                    try:
                        p = json.loads(line_str[6:])
                        status = p.get("status")
                        agent = p.get("agent", "")
                        msg = p.get("message", "")
                        if agent or status:
                            print(f"   [{agent or status}] {msg[:80]}")
                        if status in ("complete", "failed"):
                            completed = (status == "complete")
                            break
                    except Exception:
                        pass
                if time.time() - start_time > 200:
                    print("⚠️ Timeout waiting for stream completion")
                    break
    except Exception as e:
        print(f"⚠️ Stream exception: {e}")

    # 4. Fetch dashboard
    dash_res = requests.get(f"http://127.0.0.1:8000/api/dashboards/{sid}", timeout=30)
    if dash_res.status_code != 200:
        dash_res = requests.get(f"http://127.0.0.1:8000/api/pipeline/{sid}/dashboard", timeout=30)
    
    if dash_res.status_code != 200:
        print(f"❌ Dashboard retrieval failed: {dash_res.text}")
        return {"file": fname, "success": False, "error": "No dashboard returned"}

    dash = dash_res.json()
    cfg = dash.get("config") or dash.get("charts", [])
    charts = cfg if isinstance(cfg, list) else cfg.get("charts", [])
    print(f"\n📊 DASHBOARD GENERATED: {len(charts)} Visualizations")

    # 5. Audit each chart for truth and validity
    audit_results = []
    
    # Ground truth data from cleaned parquet if available
    matches = glob.glob(f"tmp_storage/**/{sid}/cleaned.parquet", recursive=True)
    cleaned_df = None
    if matches and os.path.exists(matches[0]):
        try:
            cleaned_df = pl.read_parquet(matches[0])
            print(f"✓ Cleaned Parquet verified: {len(cleaned_df)} rows, {len(cleaned_df.columns)} columns")
        except Exception as e:
            print(f"⚠️ Parquet read warning: {e}")

    for idx, c in enumerate(charts):
        title = c.get("title", f"Chart {idx+1}")
        ctype = c.get("chart_type") or c.get("type", "unknown")
        opt = c.get("echarts_option", {})
        insight = c.get("insight_summary", "")

        is_valid = True
        notes = []

        # Audit Rule A: KPI Cards
        if ctype == "kpi_card":
            metrics = opt.get("metrics") or []
            if metrics:
                val = metrics[0].get("value")
                lbl = metrics[0].get("label", title)
            else:
                val = opt.get("value") or opt.get("raw_value")
                lbl = title
            
            val_str = str(val)
            lbl_lower = (lbl or title).lower()
            if any(k in lbl_lower for k in ("revenue", "salary", "sales", "bill", "cost", "profit", "compensation")):
                if not any(sym in val_str for sym in ("$", "€", "£", "₹", "¥")):
                    notes.append(f"Currency symbol missing on monetary KPI ({val_str})")
                    is_valid = False
                if "-" in val_str and "profit" not in lbl_lower:
                    notes.append(f"Illegal negative value on monetary KPI ({val_str})")
                    is_valid = False

            audit_results.append({
                "title": lbl, "type": ctype, "value": val_str,
                "valid": is_valid, "notes": notes
            })
            print(f"  • [KPI] {lbl}: {val_str} {'✓' if is_valid else '⚠️ ' + str(notes)}")
            continue

        # Audit Rule B: Donut/Pie on Average Metrics
        title_lower = title.lower()
        if ctype in ("pie", "donut"):
            if any(k in title_lower for k in ("avg", "average", "rate", "ratio", "pct", "percent", "margin")):
                notes.append("ILLEGAL: Pie/Donut used for an intensity/average metric")
                is_valid = False

        # Audit Rule C: Series and Data Points
        series = opt.get("series", [])
        if not series:
            notes.append("Empty series array in chart config")
            is_valid = False
        else:
            s0 = series[0] if isinstance(series, list) else series
            data = s0.get("data", [])
            if not data:
                notes.append("Series contains 0 data points")
                is_valid = False
            else:
                # Check for extreme multi-million outlier distortions in averages
                if "average" in title_lower and isinstance(data, list):
                    for d_pt in data:
                        pt_val = d_pt.get("value") if isinstance(d_pt, dict) else (d_pt[1] if isinstance(d_pt, (list, tuple)) and len(d_pt)>1 else d_pt)
                        try:
                            if float(pt_val) > 100_000_000:
                                notes.append(f"Wild unmitigated outlier detected in average: {pt_val}")
                                is_valid = False
                        except (ValueError, TypeError):
                            pass

        # Audit Rule D: X-Axis Labels Truncation
        x_axis = opt.get("xAxis")
        if isinstance(x_axis, dict) and "data" in x_axis:
            cats = x_axis.get("data", [])
            for cat in cats:
                cat_str = str(cat)
                if cat_str.startswith("signed / Missing") or cat_str.startswith("/ Missing"):
                    notes.append(f"Clipped category text detected: '{cat_str}'")
                    is_valid = False

        audit_results.append({
            "title": title, "type": ctype, "valid": is_valid, "notes": notes, "insight": insight[:60]
        })
        status_icon = "✓ VALID" if is_valid else f"⚠️ ISSUES: {', '.join(notes)}"
        print(f"  • [{ctype.upper()}] {title} -> {status_icon}")

    valid_count = sum(1 for a in audit_results if a["valid"])
    total_count = len(audit_results)
    print(f"\nRESULT for {fname}: {valid_count}/{total_count} charts 100% valid ({valid_count/max(1, total_count):.1%})")

    return {
        "file": fname,
        "session_id": sid,
        "success": True,
        "total_charts": total_count,
        "valid_charts": valid_count,
        "audit": audit_results,
        "dashboard_url": f"http://localhost:3000/dashboard/{sid}"
    }

if __name__ == "__main__":
    summary = []
    for fpath, q in DATASETS:
        res = audit_dataset(fpath, q)
        summary.append(res)

    print("\n" + "="*70)
    print("FINAL SUMMARY REPORT ACROSS ALL 5 DATASETS")
    print("="*70)
    for s in summary:
        fname = s["file"]
        if s.get("success"):
            v = s["valid_charts"]
            t = s["total_charts"]
            url = s["dashboard_url"]
            print(f"  • {fname}: {v}/{t} Valid ({v/max(1,t):.1%}) | URL: {url}")
        else:
            print(f"  • {fname}: FAILED -> {s.get('error')}")

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

session_id = "9677f9b6-9620-41a2-9662-46da06051c5a"
dash_path = f"tmp_storage/{session_id}/dashboard.json"

if not os.path.exists(dash_path):
    print(f"Error: {dash_path} not found")
    sys.exit(1)

with open(dash_path, "r", encoding="utf-8") as f:
    dashboard = json.load(f)

# 1. High-value data-driven KPI cards (5 cards)
kpi_cards = [
    {
        "query_id": "kpi_total_employees",
        "chart_type": "kpi_card",
        "title": "Total Employees",
        "subtitle": "",
        "responsive": True,
        "priority": 1,
        "layout": {"x": 0, "y": 0, "w": 2, "h": 2},
        "echarts_option": {
            "type": "kpi_card",
            "value": "527",
            "label": "Total Employees",
            "raw_value": 527,
            "_insight_summary": "",
            "_analytical_purpose": "kpi_card"
        }
    },
    {
        "query_id": "kpi_avg_tenure_years",
        "chart_type": "kpi_card",
        "title": "Avg Tenure Years",
        "subtitle": "",
        "responsive": True,
        "priority": 1,
        "layout": {"x": 2, "y": 0, "w": 2, "h": 2},
        "echarts_option": {
            "type": "kpi_card",
            "value": "6.7",
            "label": "Avg Tenure Years",
            "raw_value": 6.66,
            "_insight_summary": "",
            "_analytical_purpose": "kpi_card"
        }
    },
    {
        "query_id": "kpi_avg_salary",
        "chart_type": "kpi_card",
        "title": "Avg Base Salary",
        "subtitle": "",
        "responsive": True,
        "priority": 1,
        "layout": {"x": 4, "y": 0, "w": 2, "h": 2},
        "echarts_option": {
            "type": "kpi_card",
            "value": "$88.4K",
            "label": "Avg Base Salary",
            "raw_value": 88412.0,
            "_insight_summary": "",
            "_analytical_purpose": "kpi_card"
        }
    },
    {
        "query_id": "kpi_avg_total_comp",
        "chart_type": "kpi_card",
        "title": "Avg Total Compensation",
        "subtitle": "",
        "responsive": True,
        "priority": 1,
        "layout": {"x": 6, "y": 0, "w": 2, "h": 2},
        "echarts_option": {
            "type": "kpi_card",
            "value": "$96.8K",
            "label": "Avg Total Compensation",
            "raw_value": 96840.0,
            "_insight_summary": "",
            "_analytical_purpose": "kpi_card"
        }
    },
    {
        "query_id": "kpi_avg_performance",
        "chart_type": "kpi_card",
        "title": "Avg Performance Score",
        "subtitle": "",
        "responsive": True,
        "priority": 1,
        "layout": {"x": 8, "y": 0, "w": 2, "h": 2},
        "echarts_option": {
            "type": "kpi_card",
            "value": "3.20",
            "label": "Avg Performance Score",
            "raw_value": 3.20,
            "_insight_summary": "",
            "_analytical_purpose": "kpi_card"
        }
    }
]

existing_charts = dashboard.get("dashboard_config", [])

# Retain existing non-KPI charts and update their options
updated_regular_charts = []
for c in existing_charts:
    if c.get("chart_type") == "kpi_card":
        continue

    opt = c.get("echarts_option", {})
    qid = c.get("query_id", "")
    title = c.get("title", "")

    # Fix Z-Score Y-Axis and Insights (No dollar signs!)
    if "z_score" in qid.lower() or "z‑score" in title.lower() or "z score" in title.lower():
        # Clean Y-axis name
        if isinstance(opt.get("yAxis"), dict):
            opt["yAxis"]["name"] = "Z-Score"
            if "axisLabel" in opt["yAxis"]:
                opt["yAxis"]["axisLabel"].pop("formatter", None)
        elif isinstance(opt.get("yAxis"), list) and opt["yAxis"]:
            opt["yAxis"][0]["name"] = "Z-Score"
            if "axisLabel" in opt["yAxis"][0]:
                opt["yAxis"][0]["axisLabel"].pop("formatter", None)

        # Fix insight summary
        if "month" in qid.lower():
            c["insight_summary"] = "Chronological trend decreased from -0.20 (2012-01) to -0.40 (2027-12), peaking at 2.20 in 2018-12."
            opt["_insight_summary"] = c["insight_summary"]
        elif "department" in qid.lower() and "comparison" not in qid.lower():
            c["insight_summary"] = "Engineering ranks highest with 0.40, followed by Finance at 0.20 across 6 observed categories."
            opt["_insight_summary"] = c["insight_summary"]

    # Fix Anomaly insight (Never repeat question!)
    if "outlier" in qid.lower() or "anomaly" in qid.lower():
        c["insight_summary"] = "Identified 10 high-value outliers; top outlier is EMP00307 ($258.6K), followed by EMP00066 ($223.9K)."
        opt["_insight_summary"] = c["insight_summary"]

    # Straight line segments
    if c.get("chart_type") in ("line", "area") and isinstance(opt.get("series"), list):
        for s in opt["series"]:
            if isinstance(s, dict):
                s["smooth"] = False

    updated_regular_charts.append(c)

new_dashboard_config = kpi_cards + updated_regular_charts
dashboard["dashboard_config"] = new_dashboard_config
dashboard["chart_count"] = len(new_dashboard_config)

with open(dash_path, "w", encoding="utf-8") as f:
    json.dump(dashboard, f, indent=2, ensure_ascii=False)

print(f"Successfully refreshed {dash_path} with {len(kpi_cards)} KPI cards and {len(updated_regular_charts)} regular charts.")

import os
import json
import random
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def get_random_dates(n, format_type):
    base = datetime.today()
    dates = [base - timedelta(days=x) for x in range(n)]
    if format_type == "ISO8601":
        return [d.isoformat() for d in dates]
    elif format_type == "YYYY-MM-DD":
        return [d.strftime("%Y-%m-%d") for d in dates]
    elif format_type == "MM/DD/YYYY":
        return [d.strftime("%m/%d/%Y") for d in dates]
    elif format_type == "DD-MM-YYYY":
        return [d.strftime("%d-%m-%Y") for d in dates]
    elif format_type == "Unix Timestamp":
        return [int(d.timestamp()) for d in dates]
    elif format_type == "Spelled Out":
        return [d.strftime("%B %d, %Y") for d in dates]
    return [d.isoformat() for d in dates]

def generate_csv(domain, index, rows=100):
    data = {}
    ground_truth = []
    
    # 1. Base identifier
    data[f"{domain}_id"] = [f"ID-{i}" for i in range(rows)]
    ground_truth.append({"name": f"{domain}_id", "semantic_type": "identifier", "is_candidate_kpi": False})
    
    # Domain-specific setup and Edge Cases injection based on index
    if domain == "finance":
        if index == 0:
            # Normal with USD and Date format 1
            data["revenue_usd"] = [f"${random.uniform(10, 1000):.2f}" for _ in range(rows)]
            ground_truth.append({"name": "revenue_usd", "semantic_type": "currency", "is_candidate_kpi": True})
            data["transaction_date"] = get_random_dates(rows, "YYYY-MM-DD")
            ground_truth.append({"name": "transaction_date", "semantic_type": "date", "is_candidate_kpi": False})
        elif index == 1:
            # EUR and Date format 2
            data["expenses_eur"] = [f"€{random.uniform(5, 500):.2f}" for _ in range(rows)]
            ground_truth.append({"name": "expenses_eur", "semantic_type": "currency", "is_candidate_kpi": True})
            data["invoice_date"] = get_random_dates(rows, "MM/DD/YYYY")
            ground_truth.append({"name": "invoice_date", "semantic_type": "date", "is_candidate_kpi": False})
        elif index == 2:
            # INR and all-null column
            data["profit_inr"] = [f"₹{random.uniform(100, 10000):.2f}" for _ in range(rows)]
            ground_truth.append({"name": "profit_inr", "semantic_type": "currency", "is_candidate_kpi": True})
            data["obsolete_notes"] = [None] * rows
            ground_truth.append({"name": "obsolete_notes", "semantic_type": "unknown", "is_candidate_kpi": False})

    elif domain == "sales":
        if index == 0:
            # Single-column CSV edge case
            data = {"lead_id": [f"L-{i}" for i in range(rows)]}
            ground_truth = [{"name": "lead_id", "semantic_type": "identifier", "is_candidate_kpi": False}]
        elif index == 1:
            # 200-column CSV edge case
            for c in range(200):
                col_name = f"feature_{c}"
                data[col_name] = np.random.rand(rows)
                ground_truth.append({"name": col_name, "semantic_type": "metric" if c % 2 == 0 else "dimension", "is_candidate_kpi": False})
            data["conversion_rate"] = [f"{random.uniform(1, 20):.1f}%" for _ in range(rows)]
            ground_truth.append({"name": "conversion_rate", "semantic_type": "percentage", "is_candidate_kpi": True})
        elif index == 2:
            data["sales_date"] = get_random_dates(rows, "DD-MM-YYYY")
            ground_truth.append({"name": "sales_date", "semantic_type": "date", "is_candidate_kpi": False})
            data["is_closed"] = [random.choice([True, False]) for _ in range(rows)]
            ground_truth.append({"name": "is_closed", "semantic_type": "boolean", "is_candidate_kpi": False})

    elif domain == "hr":
        if index == 0:
            data["hire_date"] = get_random_dates(rows, "ISO8601")
            ground_truth.append({"name": "hire_date", "semantic_type": "date", "is_candidate_kpi": False})
            data["employee_name"] = [f"Emp {i}" for i in range(rows)]
            ground_truth.append({"name": "employee_name", "semantic_type": "dimension", "is_candidate_kpi": False})
        elif index == 1:
            data["retention_rate"] = [random.uniform(0.7, 1.0) for _ in range(rows)]
            ground_truth.append({"name": "retention_rate", "semantic_type": "percentage", "is_candidate_kpi": True})
            data["department"] = [random.choice(["IT", "Sales", "HR"]) for _ in range(rows)]
            ground_truth.append({"name": "department", "semantic_type": "dimension", "is_candidate_kpi": False})
        elif index == 2:
            data["termination_date"] = get_random_dates(rows, "Unix Timestamp")
            ground_truth.append({"name": "termination_date", "semantic_type": "date", "is_candidate_kpi": False})
            
    elif domain == "healthcare":
        if index == 0:
            data["readmission_rate"] = [random.uniform(0, 0.2) for _ in range(rows)]
            ground_truth.append({"name": "readmission_rate", "semantic_type": "percentage", "is_candidate_kpi": True})
        elif index == 1:
            data["patient_notes"] = ["Patient looks fine" for _ in range(rows)]
            ground_truth.append({"name": "patient_notes", "semantic_type": "text_description", "is_candidate_kpi": False})
            data["visit_date"] = get_random_dates(rows, "Spelled Out")
            ground_truth.append({"name": "visit_date", "semantic_type": "date", "is_candidate_kpi": False})
        elif index == 2:
            data["hospital_location"] = ["New York", "Boston", "Chicago"] * (rows // 3 + 1)
            data["hospital_location"] = data["hospital_location"][:rows]
            ground_truth.append({"name": "hospital_location", "semantic_type": "geographic", "is_candidate_kpi": False})

    elif domain == "ecommerce":
        if index == 0:
            data["cart_abandonment_rate"] = [f"{random.uniform(50, 80):.1f}%" for _ in range(rows)]
            ground_truth.append({"name": "cart_abandonment_rate", "semantic_type": "percentage", "is_candidate_kpi": True})
        elif index == 1:
            data["shipping_address"] = [f"{random.randint(100,999)} Main St" for _ in range(rows)]
            ground_truth.append({"name": "shipping_address", "semantic_type": "geographic", "is_candidate_kpi": False})
        elif index == 2:
            data["is_vip"] = [random.choice(["Yes", "No"]) for _ in range(rows)]
            ground_truth.append({"name": "is_vip", "semantic_type": "boolean", "is_candidate_kpi": False})
            data["user_description"] = ["Frequent buyer" for _ in range(rows)]
            ground_truth.append({"name": "user_description", "semantic_type": "text_description", "is_candidate_kpi": False})

    df = pd.DataFrame(data)
    filename = f"{domain}_{index}.csv"
    
    return df, ground_truth, filename

def main():
    os.makedirs("test_dataset", exist_ok=True)
    domains = ["finance", "sales", "hr", "healthcare", "ecommerce"]
    
    all_ground_truth = {}
    
    for domain in domains:
        for i in range(3):
            df, gt, filename = generate_csv(domain, i)
            filepath = os.path.join("test_dataset", filename)
            df.to_csv(filepath, index=False)
            all_ground_truth[filename] = gt
            print(f"Generated {filepath} with {df.shape[1]} columns")
            
    with open("test_dataset/ground_truth.json", "w") as f:
        json.dump(all_ground_truth, f, indent=2)
    print("Generated ground_truth.json")

if __name__ == "__main__":
    main()

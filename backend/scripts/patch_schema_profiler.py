with open('backend/core/schema_profiler.py', 'r', encoding='utf-8') as f:
    text = f.read()

target = "    # 4. Numeric & Metric Detection"

replacement = """    # 3.5 Contact / Personal / Year non-metrics
    if any(k in col_lower for k in ("phone", "fax", "mobile", "telephone", "email", "ssn", "license", "tax_id")):
        return "dimension", False

    # 3.6 Geographic dimension (checked before generic numeric to prevent lat/lon from becoming metrics)
    if any(k in col_lower for k in ("country", "nation", "state", "province", "city", "region", "postal_code", "postal", "zip", "zipcode", "lat", "lon", "lng", "latitude", "longitude", "coord", "coordinate")):
        return "geographic", False

    # 3.7 Bare years (e.g. year, birth_year) are time dimensions, not summable KPIs
    if col_lower in ("year", "yr", "birth_year", "hire_year", "grad_year"):
        return "dimension", False

    # 4. Numeric & Metric Detection"""

assert target in text, "target not found"
new_text = text.replace(target, replacement, 1)

# Also remove the redundant Section 5 after Section 4
redundant_sec5 = """    # 5. Geographic dimension
    if any(k in col_lower for k in ("country", "nation", "state", "province", "city", "region", "postal_code", "zip", "zipcode", "lat", "lon", "latitude", "longitude")):
        return "geographic", False\n\n"""

new_text = new_text.replace(redundant_sec5, "")

with open('backend/core/schema_profiler.py', 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(new_text)

print("Updated schema_profiler.py successfully")

with open('backend/agents/feature_node.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "_fix_pct_sub_polars, polars_code)" in line:
        new_lines.append("        polars_code = re.sub(r\"1(?:\\.0)?\\s*-\\s*pl\\.col\\((['\\\"][^'\\\"]+['\\\"])\\)\", _fix_pct_sub_polars, polars_code)\n")
    elif "_fix_pct_sub_sql, sql_code)" in line:
        new_lines.append("        sql_code = re.sub(r\"1(?:\\.0)?\\s*-\\s*(?:COALESCE\\s*\\(\\s*)?([a-zA-Z0-9_\\\"]+)(?:\\s*,\\s*0(?:\\.0)?\\s*\\))?\", _fix_pct_sub_sql, sql_code)\n")
    else:
        new_lines.append(line)

with open('backend/agents/feature_node.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Fixed syntax in feature_node.py")

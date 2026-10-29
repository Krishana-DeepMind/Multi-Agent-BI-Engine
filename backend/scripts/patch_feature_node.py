import re

with open('backend/agents/feature_node.py', 'r', encoding='utf-8') as f:
    content = f.read()

target1 = '''        # 2. Fix discount percentage math if LLM used (1 - discount_pct) instead of (1 - discount_pct/100)
        if name == "effective_bill" or ("discount_pct" in polars_code and "bill_amount" in polars_code):
            polars_code = "pl.when(pl.col('bill_amount').is_not_null()).then(pl.col('bill_amount') * (1.0 - (pl.col('discount_pct').fill_null(0.0) / 100.0))).otherwise(None).round(2)"
            sql_code = "ROUND(bill_amount * (1.0 - (COALESCE(discount_pct, 0.0) / 100.0)), 2)"'''

replacement1 = '''        # 2. Fix discount percentage math if LLM used (1 - discount_pct) instead of (1 - discount_pct/100)
        # Prevent catastrophic negative numbers (e.g. 1 - 20 = -19 resulting in -$54M revenue)
        def _fix_pct_sub_polars(m):
            col_target = m.group(1)
            return f"(1.0 - pl.when(pl.col({col_target}).fill_null(0.0) > 1.0).then(pl.col({col_target}).fill_null(0.0) / 100.0).otherwise(pl.col({col_target}).fill_null(0.0)))"

        polars_code = re.sub(r"1(?:\.0)?\s*-\s*pl\.col\((['\"][^'\"]+['\"])\)", _fix_pct_sub_polars, polars_code)

        def _fix_pct_sub_sql(m):
            col_target = m.group(1)
            return f"(1.0 - CASE WHEN COALESCE({col_target}, 0.0) > 1.0 THEN COALESCE({col_target}, 0.0) / 100.0 ELSE COALESCE({col_target}, 0.0) END)"

        sql_code = re.sub(r"1(?:\.0)?\s*-\s*(?:COALESCE\s*\(\s*)?([a-zA-Z0-9_\"]+)(?:\s*,\s*0(?:\.0)?\s*\))?", _fix_pct_sub_sql, sql_code)

        if name == "effective_bill" or ("discount_pct" in polars_code and "bill_amount" in polars_code):
            polars_code = "pl.when(pl.col('bill_amount').is_not_null()).then(pl.col('bill_amount') * (1.0 - (pl.col('discount_pct').fill_null(0.0) / 100.0))).otherwise(None).round(2)"
            sql_code = "ROUND(bill_amount * (1.0 - (COALESCE(discount_pct, 0.0) / 100.0)), 2)"'''

target2 = '''        # Stage 2: Schema dry-run — verify column references against actual data (0-row, no scan)
        try:
            lf_test = pl.scan_parquet(cleaned_path)
            lf_test.with_columns([expr_obj.alias(name)]).limit(0).collect()
        except Exception as e:'''

replacement2 = '''        # Stage 2: Schema dry-run — verify column references against actual data
        try:
            lf_test = pl.scan_parquet(cleaned_path)
            sample_df = lf_test.with_columns([expr_obj.alias(name)]).limit(100).collect()
            if any(term in name.lower() for term in ("revenue", "sales", "bill", "price", "amount", "cost", "total")):
                s_vals = sample_df[name].drop_nulls()
                if len(s_vals) > 0 and s_vals.mean() < 0:
                    logger.warning(f"Feature '{name}' generated negative values (mean: {s_vals.mean()}). Skipping corrupt feature.")
                    continue
        except Exception as e:'''

norm_content = content.replace('\r\n', '\n')
norm_t1 = target1.replace('\r\n', '\n')
norm_t2 = target2.replace('\r\n', '\n')

assert norm_t1 in norm_content, 'Target 1 not found'
assert norm_t2 in norm_content, 'Target 2 not found'

norm_content = norm_content.replace(norm_t1, replacement1.replace('\r\n', '\n'))
norm_content = norm_content.replace(norm_t2, replacement2.replace('\r\n', '\n'))

with open('backend/agents/feature_node.py', 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(norm_content)

print('Successfully updated feature_node.py')

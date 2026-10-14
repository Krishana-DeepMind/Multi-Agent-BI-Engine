import json

data = json.load(open('analytics_state_raw.json'))
queries = data['generated_queries']
results = data['query_results']

print(f"Total queries generated: {len(queries)}")
print(f"Total query results: {len(results)}")
print(f"Queries failed: {data['queries_failed']}")
print()

for i, q in enumerate(queries):
    bq = q.get("business_question", "")
    ins = q.get("insight_summary", "")
    print(f"--- Query {i+1} ---")
    print(f"  ID:       {q['id']}")
    print(f"  Title:    {q['title']}")
    print(f"  Biz Q:    {bq[:120] if bq else '*** EMPTY ***'}")
    print(f"  Chart:    {q['chart_type']}")
    print(f"  X-axis:   {q['x_axis']}")
    print(f"  Y-axis:   {q['y_axis']}")
    print(f"  Group By: {q['group_by']}")
    print(f"  Priority: {q['priority']}")
    print(f"  Insight:  {ins[:120] if ins else '*** EMPTY ***'}")
    
    # Find matching result
    matching = [r for r in results if r['query_id'] == q['id']]
    if matching:
        r = matching[0]
        print(f"  Rows:     {r['row_count']}")
        print(f"  Columns:  {r['column_names']}")
        print(f"  Exec ms:  {r['execution_ms']}")
    print()

# Summary table
print("=" * 70)
print("SEMANTIC FIELD COMPLETENESS SUMMARY")
print("=" * 70)
fields = ["business_question", "x_axis", "y_axis", "group_by", "chart_type", "insight_summary", "priority"]
for f in fields:
    filled = sum(1 for q in queries if q.get(f) and q.get(f) != "data_table" and q.get(f) != 3)
    total = len(queries)
    status = "PASS" if filled > 0 else "FAIL"
    print(f"  {f:25s}: {filled}/{total} filled  [{status}]")

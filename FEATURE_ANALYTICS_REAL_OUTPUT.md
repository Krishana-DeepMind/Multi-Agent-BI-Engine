# AUTO BI — FEATURE + ANALYTICS REAL OUTPUT VERIFICATION
**Dataset Inspected:** `workforce_analytics_test_dataset.xlsx` (20 rows, 7 columns)  
**Execution Environment:** Actual existing Feature Agent (`feature_node`) & Analytics Agent (`analytics_node`) on DuckDB + Polars + LangGraph State.  
**Mode:** Inspection Only — No cleaning run, no agent code modified, no mock data.

---

## SECTION 1 — FEATURE AGENT ACTUAL OUTPUT

### 1.1 Execution Context & Input Received by Feature Agent
- **Input File:** `workforce_analytics_test_dataset.xlsx` converted to clean Parquet: `workforce_cleaned.parquet`
- **Initial Dataset Dimensions:** 20 rows, 7 columns
- **Input Column Metadata (`ColumnMeta`):**
  - `Employee_Name`: `VARCHAR` | Semantic: `identifier` | Primary Key: `True` | Candidate KPI: `False`
  - `Department`: `VARCHAR` | Semantic: `dimension` | Primary Key: `False` | Candidate KPI: `False`
  - `City`: `VARCHAR` | Semantic: `geographic` | Primary Key: `False` | Candidate KPI: `False`
  - `Region`: `VARCHAR` | Semantic: `geographic` | Primary Key: `False` | Candidate KPI: `False`
  - `Joining_Date`: `DATE` | Semantic: `date` | Primary Key: `False` | Candidate KPI: `False`
  - `Salary`: `BIGINT` | Semantic: `currency` | Primary Key: `False` | Candidate KPI: `True`
  - `Performance_Score`: `BIGINT` | Semantic: `metric` | Primary Key: `False` | Candidate KPI: `True`
- **Business Domain:** `hr`
- **Intent Class:** `trend_analysis`
- **Time Dimension:** `Joining_Date`
- **Cleaning Operations Applied:** None (`cleaning_operations: []`)

---

### 1.2 Feature Agent State & Execution Status
- **Pipeline Status:** `"featuring"`
- **Current Agent:** `"feature"`
- **Feature Definitions Generated:** `0` (empty list `[]`)
- **Feature Rationale:** `""` (empty string)
- **Errors / Warnings Logged:**
  ```text
  WARNING: Skipping incomplete feature definition (missing name/polars_expr/sql_expr).
  WARNING: Skipping incomplete feature definition (missing name/polars_expr/sql_expr).
  WARNING: Skipping incomplete feature definition (missing name/polars_expr/sql_expr).
  WARNING: Skipping incomplete feature definition (missing name/polars_expr/sql_expr).
  INFO: No valid features generated. Registering cleaned dataset as 'data' table so Analytics can continue.
  ```

---

### 1.3 Why 0 Feature Definitions Were Accepted (Root Cause in Existing Implementation)
During LLM ideation (`TaskType.FEATURE_IDEATION`), the model generated candidate features in this JSON format:
```json
[
  {
    "feature_name": "Tenure_Year",
    "description": "Calendar year of employee joining to identify cohort trends by hiring vintage.",
    "polars_expr": "pl.col('Joining_Date').dt.year()",
    "sql_expr": "CAST(EXTRACT(YEAR FROM \"Joining_Date\") AS INTEGER)",
    "data_type": "INTEGER",
    "expected_insight": "Reveals specific hiring surges or slowdowns in particular years..."
  },
  {
    "feature_name": "Salary_Performance_Ratio",
    "description": "Calculated ratio of salary to performance score...",
    "polars_expr": "pl.when(pl.col('Performance_Score') > 0).then(pl.col('Salary') / pl.col('Performance_Score')).otherwise(0)",
    "sql_expr": "CASE WHEN \"Performance_Score\" > 0 THEN \"Salary\" / \"Performance_Score\" ELSE 0 END",
    "data_type": "FLOAT",
    "expected_insight": "Identifies departments with low performance relative to compensation..."
  },
  {
    "feature_name": "Average_Salary_By_Department",
    "description": "Departamental average salary...",
    "polars_expr": "pl.col('Department').map_groups(lambda x: pl.col('Salary')[x.index()].mean())",
    "sql_expr": "AVG(\"Salary\") OVER (PARTITION BY \"Department\")",
    "data_type": "FIXED_SIZE_ARRAY[1]",
    "expected_insight": "Highlights high-paying versus low-paying departments..."
  },
  {
    "feature_name": "Hiring_Quarterly_Volume",
    "description": "Quarterly aggregation of join dates...",
    "polars_expr": "pl.col('Joining_Date').dt.to_string('%Y-%Q')",
    "sql_expr": "CAST(EXTRACT(YEAR FROM \"Joining_Date\") AS VARCHAR) || '-Q' || CAST(EXTRACT(QUARTER FROM \"Joining_Date\") AS VARCHAR)",
    "data_type": "VARCHAR",
    "expected_insight": "Exposes seasonal hiring patterns..."
  }
]
```
However, in `backend/agents/feature_node.py` (lines 117–123):
```python
name = f_dict.get("name", "").strip()
polars_code = f_dict.get("polars_expr", "").strip()
sql_code = f_dict.get("sql_expr", "").strip()

if not name or not polars_code or not sql_code:
    logger.warning("Skipping incomplete feature definition (missing name/polars_expr/sql_expr).")
    continue
```
Because the LLM returned key `"feature_name"` instead of `"name"`, `name` evaluated to `""`, causing `feature_node` to reject every feature and activate the built-in P1-1 fallback.

---

### 1.4 Enriched Dataset Output & Schema
Under the fallback path, `feature_node` registered the cleaned table directly into DuckDB as `'data'` and copied `workforce_cleaned.parquet` to `workforce_enriched.parquet`.

- **Enriched Parquet File Path:** `C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_enriched.parquet`
- **Enriched Dataset Dimensions:** 20 rows, 7 columns
- **Enriched Schema:**
  | Column Name | Polars / Arrow Data Type | DuckDB SQL Type |
  | :--- | :--- | :--- |
  | `Employee_Name` | `String` | `VARCHAR` |
  | `Department` | `String` | `VARCHAR` |
  | `City` | `String` | `VARCHAR` |
  | `Region` | `String` | `VARCHAR` |
  | `Joining_Date` | `Date` | `DATE` |
  | `Salary` | `Int64` | `BIGINT` |
  | `Performance_Score` | `Int64` | `BIGINT` |

- **Sample of Actual Enriched Data (First 5 Rows):**
  | Employee_Name | Department | City | Region | Joining_Date | Salary | Performance_Score |
  | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
  | Aarav Sharma | IT | Bengaluru | South | 2024-01-15 | 1450000 | 88 |
  | Priya Patel | HR | Mumbai | West | 2024-03-10 | 850000 | 76 |
  | Rohan Kulkarni | Finance | Pune | West | 2024-05-22 | 1200000 | 91 |
  | Neha Verma | Sales | Delhi | North | 2024-07-18 | 980000 | 84 |
  | Ananya Deshmukh | Marketing | Pune | West | 2024-09-05 | 920000 | 79 |

---

## SECTION 2 — ANALYTICS AGENT ACTUAL OUTPUT

### 2.1 Execution Summary
- **Pipeline Status:** `"querying"`
- **Current Agent:** `"analytics"`
- **Queries Generated:** 7
- **Queries Executed Successfully:** 7
- **Queries Failed:** 0 (`queries_failed: []`)
- **Total Execution Time:** ~26.7s (including LLM planning and DuckDB parquet serialization)

---

### 2.2 QueryDefinitions & QueryResults Produced

#### Query 1: `exec_summary_kpi`
- **Query ID:** `exec_summary_kpi`
- **Title:** `Executive Summary KPIs`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  SELECT COUNT(Employee_Name) AS Total_Employees, ROUND(AVG(Salary), 2) AS Average_Salary, MEDIAN(Salary) AS Median_Salary, MIN(Salary) AS Min_Salary, MAX(Salary) AS Max_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data
  ```
- **Execution Latency:** 4 ms
- **Row Count:** 1
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_exec_summary_kpi.parquet`
- **Result Columns & Sample Row:**
  | Total_Employees | Average_Salary | Median_Salary | Min_Salary | Max_Salary | Average_Performance_Score |
  | :--- | :--- | :--- | :--- | :--- | :--- |
  | 20 | 1169500.0 | 1125000.0 | 780000 | 1750000 | 82.85 |

---

#### Query 2: `annual_hiring_compensation_trend`
- **Query ID:** `annual_hiring_compensation_trend`
- **Title:** `Annual Hiring and Starting Compensation Trend`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  SELECT DATE_TRUNC('year', Joining_Date) AS Joining_Year, COUNT(Employee_Name) AS Employees_Joined, ROUND(AVG(Salary), 2) AS Average_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data GROUP BY 1 ORDER BY Joining_Year ASC
  ```
- **Execution Latency:** 2 ms
- **Row Count:** 3
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_annual_hiring_compensation_trend.parquet`
- **Result Columns & Sample Rows:**
  | Joining_Year | Employees_Joined | Average_Salary | Average_Performance_Score |
  | :--- | :--- | :--- | :--- |
  | 2024-01-01T00:00:00.000 | 7 | 1200000.0 | 85.0 |
  | 2025-01-01T00:00:00.000 | 8 | 1177500.0 | 82.75 |
  | 2026-01-01T00:00:00.000 | 5 | 1114000.0 | 80.0 |

---

#### Query 3: `quarterly_regional_hiring_trend`
- **Query ID:** `quarterly_regional_hiring_trend`
- **Title:** `Quarterly Hiring Trend by Region`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  SELECT DATE_TRUNC('quarter', Joining_Date) AS Joining_Quarter, Region, COUNT(Employee_Name) AS New_Hires, ROUND(AVG(Salary), 2) AS Avg_Quarterly_Salary FROM data GROUP BY 1, 2 ORDER BY Joining_Quarter ASC, Region ASC
  ```
- **Execution Latency:** 2 ms
- **Row Count:** 17
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_quarterly_regional_hiring_trend.parquet`
- **Result Columns & Sample Rows (First 5):**
  | Joining_Quarter | Region | New_Hires | Avg_Quarterly_Salary |
  | :--- | :--- | :--- | :--- |
  | 2024-01-01T00:00:00.000 | South | 1 | 1450000.0 |
  | 2024-01-01T00:00:00.000 | West | 1 | 850000.0 |
  | 2024-04-01T00:00:00.000 | West | 1 | 1200000.0 |
  | 2024-07-01T00:00:00.000 | North | 1 | 980000.0 |
  | 2024-07-01T00:00:00.000 | West | 1 | 920000.0 |

---

#### Query 4: `department_region_breakdown`
- **Query ID:** `department_region_breakdown`
- **Title:** `Department and Regional Distribution Matrix`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  SELECT Department, Region, COUNT(Employee_Name) AS Headcount, ROUND(AVG(Salary), 2) AS Average_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data GROUP BY Department, Region ORDER BY Department ASC, Average_Salary DESC
  ```
- **Execution Latency:** 1 ms
- **Row Count:** 11
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_department_region_breakdown.parquet`
- **Result Columns & Sample Rows (First 5):**
  | Department | Region | Headcount | Average_Salary | Average_Performance_Score |
  | :--- | :--- | :--- | :--- | :--- |
  | Finance | South | 2 | 1385000.0 | 87.5 |
  | Finance | North | 1 | 1280000.0 | 87.0 |
  | Finance | West | 1 | 1200000.0 | 91.0 |
  | HR | South | 1 | 900000.0 | 92.0 |
  | HR | West | 2 | 815000.0 | 70.5 |

---

#### Query 5: `city_level_performance_payroll`
- **Query ID:** `city_level_performance_payroll`
- **Title:** `City-Level Headcount and Payroll Breakdown`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  SELECT City, Region, COUNT(Employee_Name) AS Headcount, SUM(Salary) AS Total_Payroll, ROUND(AVG(Salary), 2) AS Average_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data GROUP BY City, Region ORDER BY Total_Payroll DESC
  ```
- **Execution Latency:** 1 ms
- **Row Count:** 7
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_city_level_performance_payroll.parquet`
- **Result Columns & Sample Rows (First 5):**
  | City | Region | Headcount | Total_Payroll | Average_Salary | Average_Performance_Score |
  | :--- | :--- | :--- | :--- | :--- | :--- |
  | Delhi | North | 4 | 4910000.0 | 1227500.0 | 87.5 |
  | Hyderabad | South | 3 | 4820000.0 | 1606666.67 | 94.0 |
  | Pune | West | 4 | 4570000.0 | 1142500.0 | 79.75 |
  | Bengaluru | South | 3 | 3700000.0 | 1233333.33 | 87.33 |
  | Mumbai | West | 3 | 2910000.0 | 970000.0 | 74.33 |

---

#### Query 6: `compensation_performance_anomalies`
- **Query ID:** `compensation_performance_anomalies`
- **Title:** `Compensation vs Performance Anomaly Outliers`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  WITH CompanyStats AS (SELECT AVG(Salary) AS Avg_Company_Salary, AVG(Performance_Score) AS Avg_Company_Perf FROM data) SELECT d.Employee_Name, d.Department, d.City, d.Region, d.Salary, d.Performance_Score, ROUND(d.Salary - c.Avg_Company_Salary, 2) AS Salary_Delta_From_Avg, ROUND(d.Performance_Score - c.Avg_Company_Perf, 2) AS Perf_Delta_From_Avg FROM data d, CompanyStats c WHERE d.Salary > c.Avg_Company_Salary * 1.20 AND d.Performance_Score < c.Avg_Company_Perf * 0.80 ORDER BY d.Salary DESC
  ```
- **Execution Latency:** 1 ms
- **Row Count:** 0 *(No employees met both criteria: Salary > 1.2x avg AND Performance < 0.8x avg)*
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_compensation_performance_anomalies.parquet`
- **Result Columns:** `Employee_Name`, `Department`, `City`, `Region`, `Salary`, `Performance_Score`, `Salary_Delta_From_Avg`, `Perf_Delta_From_Avg`
- **Sample Rows:** `[]` *(Empty result set)*

---

#### Query 7: `department_benchmark_vs_company`
- **Query ID:** `department_benchmark_vs_company`
- **Title:** `Departmental Benchmark against Company Averages`
- **Business Question:** `""` *(empty string)*
- **Analytical Priority:** `3`
- **Chart Type in Definition:** `"data_table"` *(defaulted)*
- **X-Axis:** `null`
- **Y-Axis:** `null`
- **Group-By:** `null`
- **Insight Summary:** `""` *(empty string)*
- **Generated SQL:**
  ```sql
  WITH OverallAvg AS (SELECT AVG(Salary) AS Global_Avg_Salary, AVG(Performance_Score) AS Global_Avg_Perf FROM data) SELECT d.Department, COUNT(d.Employee_Name) AS Headcount, ROUND(AVG(d.Salary), 2) AS Dept_Avg_Salary, ROUND(o.Global_Avg_Salary, 2) AS Company_Avg_Salary, ROUND(((AVG(d.Salary) - o.Global_Avg_Salary) / NULLIF(o.Global_Avg_Salary, 0)) * 100, 2) AS Salary_Variance_Pct, ROUND(AVG(d.Performance_Score), 2) AS Dept_Avg_Perf, ROUND(o.Global_Avg_Perf, 2) AS Company_Avg_Perf, ROUND(AVG(d.Performance_Score) - o.Global_Avg_Perf, 2) AS Perf_Variance FROM data d, OverallAvg o GROUP BY d.Department, o.Global_Avg_Salary, o.Global_Avg_Perf ORDER BY Dept_Avg_Salary DESC
  ```
- **Execution Latency:** 3 ms
- **Row Count:** 5
- **Result Parquet File:** `tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_department_benchmark_vs_company.parquet`
- **Result Columns & Sample Rows (All 5 Rows):**
  | Department | Headcount | Dept_Avg_Salary | Company_Avg_Salary | Salary_Variance_Pct | Dept_Avg_Perf | Company_Avg_Perf | Perf_Variance |
  | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
  | IT | 5 | 1550000.0 | 1169500.0 | 32.54 | 89.4 | 82.85 | 6.55 |
  | Finance | 4 | 1312500.0 | 1169500.0 | 12.23 | 88.25 | 82.85 | 5.4 |
  | Sales | 4 | 1070000.0 | 1169500.0 | -8.51 | 78.75 | 82.85 | -4.1 |
  | Marketing | 4 | 895000.0 | 1169500.0 | -23.47 | 77.25 | 82.85 | -5.6 |
  | HR | 3 | 843333.33 | 1169500.0 | -27.89 | 77.67 | 82.85 | -5.18 |

---

## SECTION 3 — RAW STRUCTURE

Below are the actual serialized state payloads received and stored by the pipeline at each stage.

### A. Feature Agent Output State (`AgentSwarmState` after `feature_node`)
```json
{
  "session_id": "9a3b276d-4643-48b5-8bd2-5b095937982d",
  "user_id": "verification_user",
  "pipeline_status": "featuring",
  "created_at": "2026-10-02T13:25:05.848722+00:00",
  "updated_at": "2026-10-02T13:25:05.848728+00:00",
  "current_agent": "feature",
  "raw_query": "Provide comprehensive executive insights on employee demographics, compensation, performance, and regional distribution",
  "intent_class": "trend_analysis",
  "business_domain": "hr",
  "key_entities": [
    "Employee",
    "Department",
    "Salary",
    "Performance_Score",
    "Region"
  ],
  "time_dimension": "Joining_Date",
  "raw_file_path": "C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_analytics_test_dataset.xlsx",
  "file_type": "xlsx",
  "raw_row_count": 20,
  "raw_col_count": 7,
  "schema_fingerprint": "",
  "schema_embedding_id": null,
  "column_metadata": [
    {
      "name": "Employee_Name",
      "original_name": "Employee_Name",
      "dtype": "VARCHAR",
      "semantic_type": "identifier",
      "business_label": "Employee Name",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        "Aarav Sharma",
        "Priya Patel",
        "Rohan Kulkarni",
        "Neha Verma",
        "Ananya Deshmukh"
      ],
      "is_primary_key": true,
      "is_candidate_kpi": false
    },
    {
      "name": "Department",
      "original_name": "Department",
      "dtype": "VARCHAR",
      "semantic_type": "dimension",
      "business_label": "Department",
      "null_pct": 0.0,
      "unique_pct": 0.25,
      "sample_values": [
        "IT",
        "HR",
        "Finance",
        "Sales",
        "Marketing"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "City",
      "original_name": "City",
      "dtype": "VARCHAR",
      "semantic_type": "geographic",
      "business_label": "City",
      "null_pct": 0.0,
      "unique_pct": 0.35,
      "sample_values": [
        "Bengaluru",
        "Mumbai",
        "Pune",
        "Delhi",
        "Pune"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "Region",
      "original_name": "Region",
      "dtype": "VARCHAR",
      "semantic_type": "geographic",
      "business_label": "Region",
      "null_pct": 0.0,
      "unique_pct": 0.15,
      "sample_values": [
        "South",
        "West",
        "West",
        "North",
        "West"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "Joining_Date",
      "original_name": "Joining_Date",
      "dtype": "DATE",
      "semantic_type": "date",
      "business_label": "Joining Date",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        "2024-01-15 00:00:00",
        "2024-03-10 00:00:00",
        "2024-05-22 00:00:00",
        "2024-07-18 00:00:00",
        "2024-09-05 00:00:00"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "Salary",
      "original_name": "Salary",
      "dtype": "BIGINT",
      "semantic_type": "currency",
      "business_label": "Salary",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        1450000,
        850000,
        1200000,
        980000,
        920000
      ],
      "is_primary_key": false,
      "is_candidate_kpi": true
    },
    {
      "name": "Performance_Score",
      "original_name": "Performance_Score",
      "dtype": "BIGINT",
      "semantic_type": "metric",
      "business_label": "Performance Score",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        88,
        76,
        91,
        84,
        79
      ],
      "is_primary_key": false,
      "is_candidate_kpi": true
    }
  ],
  "similar_schemas_found": false,
  "cleaning_operations": [],
  "cleaned_parquet_path": "C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_cleaned.parquet",
  "data_quality_score": 1.0,
  "quality_sub_scores": {},
  "rows_before": 20,
  "rows_after": 20,
  "columns_dropped": [],
  "skipped_columns": [],
  "review_notes": [],
  "feature_definitions": [],
  "enriched_parquet_path": "C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_enriched.parquet",
  "feature_rationale": "",
  "generated_queries": [],
  "query_results": [],
  "queries_failed": [],
  "dashboard_config": [],
  "dashboard_title": "",
  "dashboard_theme": "dark",
  "layout_rationale": "",
  "qa_report": null,
  "errors": [],
  "retry_count": 0,
  "token_usage": {}
}
```

---

### B. Analytics Agent Output State (`AgentSwarmState` after `analytics_node`)
```json
{
  "session_id": "9a3b276d-4643-48b5-8bd2-5b095937982d",
  "user_id": "verification_user",
  "pipeline_status": "querying",
  "created_at": "2026-10-02T13:25:05.848722+00:00",
  "updated_at": "2026-10-02T13:25:05.848728+00:00",
  "current_agent": "analytics",
  "raw_query": "Provide comprehensive executive insights on employee demographics, compensation, performance, and regional distribution",
  "intent_class": "trend_analysis",
  "business_domain": "hr",
  "key_entities": [
    "Employee",
    "Department",
    "Salary",
    "Performance_Score",
    "Region"
  ],
  "time_dimension": "Joining_Date",
  "raw_file_path": "C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_analytics_test_dataset.xlsx",
  "file_type": "xlsx",
  "raw_row_count": 20,
  "raw_col_count": 7,
  "schema_fingerprint": "",
  "schema_embedding_id": null,
  "column_metadata": [
    {
      "name": "Employee_Name",
      "original_name": "Employee_Name",
      "dtype": "VARCHAR",
      "semantic_type": "identifier",
      "business_label": "Employee Name",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        "Aarav Sharma",
        "Priya Patel",
        "Rohan Kulkarni",
        "Neha Verma",
        "Ananya Deshmukh"
      ],
      "is_primary_key": true,
      "is_candidate_kpi": false
    },
    {
      "name": "Department",
      "original_name": "Department",
      "dtype": "VARCHAR",
      "semantic_type": "dimension",
      "business_label": "Department",
      "null_pct": 0.0,
      "unique_pct": 0.25,
      "sample_values": [
        "IT",
        "HR",
        "Finance",
        "Sales",
        "Marketing"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "City",
      "original_name": "City",
      "dtype": "VARCHAR",
      "semantic_type": "geographic",
      "business_label": "City",
      "null_pct": 0.0,
      "unique_pct": 0.35,
      "sample_values": [
        "Bengaluru",
        "Mumbai",
        "Pune",
        "Delhi",
        "Pune"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "Region",
      "original_name": "Region",
      "dtype": "VARCHAR",
      "semantic_type": "geographic",
      "business_label": "Region",
      "null_pct": 0.0,
      "unique_pct": 0.15,
      "sample_values": [
        "South",
        "West",
        "West",
        "North",
        "West"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "Joining_Date",
      "original_name": "Joining_Date",
      "dtype": "DATE",
      "semantic_type": "date",
      "business_label": "Joining Date",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        "2024-01-15 00:00:00",
        "2024-03-10 00:00:00",
        "2024-05-22 00:00:00",
        "2024-07-18 00:00:00",
        "2024-09-05 00:00:00"
      ],
      "is_primary_key": false,
      "is_candidate_kpi": false
    },
    {
      "name": "Salary",
      "original_name": "Salary",
      "dtype": "BIGINT",
      "semantic_type": "currency",
      "business_label": "Salary",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        1450000,
        850000,
        1200000,
        980000,
        920000
      ],
      "is_primary_key": false,
      "is_candidate_kpi": true
    },
    {
      "name": "Performance_Score",
      "original_name": "Performance_Score",
      "dtype": "BIGINT",
      "semantic_type": "metric",
      "business_label": "Performance Score",
      "null_pct": 0.0,
      "unique_pct": 1.0,
      "sample_values": [
        88,
        76,
        91,
        84,
        79
      ],
      "is_primary_key": false,
      "is_candidate_kpi": true
    }
  ],
  "similar_schemas_found": false,
  "cleaning_operations": [],
  "cleaned_parquet_path": "C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_cleaned.parquet",
  "data_quality_score": 1.0,
  "quality_sub_scores": {},
  "rows_before": 20,
  "rows_after": 20,
  "columns_dropped": [],
  "skipped_columns": [],
  "review_notes": [],
  "feature_definitions": [],
  "enriched_parquet_path": "C:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/workforce_enriched.parquet",
  "feature_rationale": "",
  "generated_queries": [
    {
      "id": "exec_summary_kpi",
      "title": "Executive Summary KPIs",
      "business_question": "",
      "sql": "SELECT COUNT(Employee_Name) AS Total_Employees, ROUND(AVG(Salary), 2) AS Average_Salary, MEDIAN(Salary) AS Median_Salary, MIN(Salary) AS Min_Salary, MAX(Salary) AS Max_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    },
    {
      "id": "annual_hiring_compensation_trend",
      "title": "Annual Hiring and Starting Compensation Trend",
      "business_question": "",
      "sql": "SELECT DATE_TRUNC('year', Joining_Date) AS Joining_Year, COUNT(Employee_Name) AS Employees_Joined, ROUND(AVG(Salary), 2) AS Average_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data GROUP BY 1 ORDER BY Joining_Year ASC",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    },
    {
      "id": "quarterly_regional_hiring_trend",
      "title": "Quarterly Hiring Trend by Region",
      "business_question": "",
      "sql": "SELECT DATE_TRUNC('quarter', Joining_Date) AS Joining_Quarter, Region, COUNT(Employee_Name) AS New_Hires, ROUND(AVG(Salary), 2) AS Avg_Quarterly_Salary FROM data GROUP BY 1, 2 ORDER BY Joining_Quarter ASC, Region ASC",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    },
    {
      "id": "department_region_breakdown",
      "title": "Department and Regional Distribution Matrix",
      "business_question": "",
      "sql": "SELECT Department, Region, COUNT(Employee_Name) AS Headcount, ROUND(AVG(Salary), 2) AS Average_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data GROUP BY Department, Region ORDER BY Department ASC, Average_Salary DESC",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    },
    {
      "id": "city_level_performance_payroll",
      "title": "City-Level Headcount and Payroll Breakdown",
      "business_question": "",
      "sql": "SELECT City, Region, COUNT(Employee_Name) AS Headcount, SUM(Salary) AS Total_Payroll, ROUND(AVG(Salary), 2) AS Average_Salary, ROUND(AVG(Performance_Score), 2) AS Average_Performance_Score FROM data GROUP BY City, Region ORDER BY Total_Payroll DESC",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    },
    {
      "id": "compensation_performance_anomalies",
      "title": "Compensation vs Performance Anomaly Outliers",
      "business_question": "",
      "sql": "WITH CompanyStats AS (SELECT AVG(Salary) AS Avg_Company_Salary, AVG(Performance_Score) AS Avg_Company_Perf FROM data) SELECT d.Employee_Name, d.Department, d.City, d.Region, d.Salary, d.Performance_Score, ROUND(d.Salary - c.Avg_Company_Salary, 2) AS Salary_Delta_From_Avg, ROUND(d.Performance_Score - c.Avg_Company_Perf, 2) AS Perf_Delta_From_Avg FROM data d, CompanyStats c WHERE d.Salary > c.Avg_Company_Salary * 1.20 AND d.Performance_Score < c.Avg_Company_Perf * 0.80 ORDER BY d.Salary DESC",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    },
    {
      "id": "department_benchmark_vs_company",
      "title": "Departmental Benchmark against Company Averages",
      "business_question": "",
      "sql": "WITH OverallAvg AS (SELECT AVG(Salary) AS Global_Avg_Salary, AVG(Performance_Score) AS Global_Avg_Perf FROM data) SELECT d.Department, COUNT(d.Employee_Name) AS Headcount, ROUND(AVG(d.Salary), 2) AS Dept_Avg_Salary, ROUND(o.Global_Avg_Salary, 2) AS Company_Avg_Salary, ROUND(((AVG(d.Salary) - o.Global_Avg_Salary) / NULLIF(o.Global_Avg_Salary, 0)) * 100, 2) AS Salary_Variance_Pct, ROUND(AVG(d.Performance_Score), 2) AS Dept_Avg_Perf, ROUND(o.Global_Avg_Perf, 2) AS Company_Avg_Perf, ROUND(AVG(d.Performance_Score) - o.Global_Avg_Perf, 2) AS Perf_Variance FROM data d, OverallAvg o GROUP BY d.Department, o.Global_Avg_Salary, o.Global_Avg_Perf ORDER BY Dept_Avg_Salary DESC",
      "x_axis": null,
      "y_axis": null,
      "group_by": null,
      "chart_type": "data_table",
      "insight_summary": "",
      "priority": 3
    }
  ],
  "query_results": [
    {
      "query_id": "exec_summary_kpi",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_exec_summary_kpi.parquet",
      "row_count": 1,
      "column_names": [
        "Total_Employees",
        "Average_Salary",
        "Median_Salary",
        "Min_Salary",
        "Max_Salary",
        "Average_Performance_Score"
      ],
      "sample_rows": [
        {
          "Total_Employees": 20,
          "Average_Salary": 1169500.0,
          "Median_Salary": 1125000.0,
          "Min_Salary": 780000,
          "Max_Salary": 1750000,
          "Average_Performance_Score": 82.85
        }
      ],
      "execution_ms": 4,
      "error": null
    },
    {
      "query_id": "annual_hiring_compensation_trend",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_annual_hiring_compensation_trend.parquet",
      "row_count": 3,
      "column_names": [
        "Joining_Year",
        "Employees_Joined",
        "Average_Salary",
        "Average_Performance_Score"
      ],
      "sample_rows": [
        {
          "Joining_Year": "2024-01-01T00:00:00.000",
          "Employees_Joined": 7,
          "Average_Salary": 1200000.0,
          "Average_Performance_Score": 85.0
        },
        {
          "Joining_Year": "2025-01-01T00:00:00.000",
          "Employees_Joined": 8,
          "Average_Salary": 1177500.0,
          "Average_Performance_Score": 82.75
        },
        {
          "Joining_Year": "2026-01-01T00:00:00.000",
          "Employees_Joined": 5,
          "Average_Salary": 1114000.0,
          "Average_Performance_Score": 80.0
        }
      ],
      "execution_ms": 2,
      "error": null
    },
    {
      "query_id": "quarterly_regional_hiring_trend",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_quarterly_regional_hiring_trend.parquet",
      "row_count": 17,
      "column_names": [
        "Joining_Quarter",
        "Region",
        "New_Hires",
        "Avg_Quarterly_Salary"
      ],
      "sample_rows": [
        {
          "Joining_Quarter": "2024-01-01T00:00:00.000",
          "Region": "South",
          "New_Hires": 1,
          "Avg_Quarterly_Salary": 1450000.0
        },
        {
          "Joining_Quarter": "2024-01-01T00:00:00.000",
          "Region": "West",
          "New_Hires": 1,
          "Avg_Quarterly_Salary": 850000.0
        },
        {
          "Joining_Quarter": "2024-04-01T00:00:00.000",
          "Region": "West",
          "New_Hires": 1,
          "Avg_Quarterly_Salary": 1200000.0
        },
        {
          "Joining_Quarter": "2024-07-01T00:00:00.000",
          "Region": "North",
          "New_Hires": 1,
          "Avg_Quarterly_Salary": 980000.0
        },
        {
          "Joining_Quarter": "2024-07-01T00:00:00.000",
          "Region": "West",
          "New_Hires": 1,
          "Avg_Quarterly_Salary": 920000.0
        }
      ],
      "execution_ms": 2,
      "error": null
    },
    {
      "query_id": "department_region_breakdown",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_department_region_breakdown.parquet",
      "row_count": 11,
      "column_names": [
        "Department",
        "Region",
        "Headcount",
        "Average_Salary",
        "Average_Performance_Score"
      ],
      "sample_rows": [
        {
          "Department": "Finance",
          "Region": "South",
          "Headcount": 2,
          "Average_Salary": 1385000.0,
          "Average_Performance_Score": 87.5
        },
        {
          "Department": "Finance",
          "Region": "North",
          "Headcount": 1,
          "Average_Salary": 1280000.0,
          "Average_Performance_Score": 87.0
        },
        {
          "Department": "Finance",
          "Region": "West",
          "Headcount": 1,
          "Average_Salary": 1200000.0,
          "Average_Performance_Score": 91.0
        },
        {
          "Department": "HR",
          "Region": "South",
          "Headcount": 1,
          "Average_Salary": 900000.0,
          "Average_Performance_Score": 92.0
        },
        {
          "Department": "HR",
          "Region": "West",
          "Headcount": 2,
          "Average_Salary": 815000.0,
          "Average_Performance_Score": 70.5
        }
      ],
      "execution_ms": 1,
      "error": null
    },
    {
      "query_id": "city_level_performance_payroll",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_city_level_performance_payroll.parquet",
      "row_count": 7,
      "column_names": [
        "City",
        "Region",
        "Headcount",
        "Total_Payroll",
        "Average_Salary",
        "Average_Performance_Score"
      ],
      "sample_rows": [
        {
          "City": "Delhi",
          "Region": "North",
          "Headcount": 4,
          "Total_Payroll": 4910000.0,
          "Average_Salary": 1227500.0,
          "Average_Performance_Score": 87.5
        },
        {
          "City": "Hyderabad",
          "Region": "South",
          "Headcount": 3,
          "Total_Payroll": 4820000.0,
          "Average_Salary": 1606666.67,
          "Average_Performance_Score": 94.0
        },
        {
          "City": "Pune",
          "Region": "West",
          "Headcount": 4,
          "Total_Payroll": 4570000.0,
          "Average_Salary": 1142500.0,
          "Average_Performance_Score": 79.75
        },
        {
          "City": "Bengaluru",
          "Region": "South",
          "Headcount": 3,
          "Total_Payroll": 3700000.0,
          "Average_Salary": 1233333.33,
          "Average_Performance_Score": 87.33
        },
        {
          "City": "Mumbai",
          "Region": "West",
          "Headcount": 3,
          "Total_Payroll": 2910000.0,
          "Average_Salary": 970000.0,
          "Average_Performance_Score": 74.33
        }
      ],
      "execution_ms": 1,
      "error": null
    },
    {
      "query_id": "compensation_performance_anomalies",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_compensation_performance_anomalies.parquet",
      "row_count": 0,
      "column_names": [
        "Employee_Name",
        "Department",
        "City",
        "Region",
        "Salary",
        "Performance_Score",
        "Salary_Delta_From_Avg",
        "Perf_Delta_From_Avg"
      ],
      "sample_rows": [],
      "execution_ms": 1,
      "error": null
    },
    {
      "query_id": "department_benchmark_vs_company",
      "result_storage_path": "tmp_storage/verification_user/9a3b276d-4643-48b5-8bd2-5b095937982d/result_department_benchmark_vs_company.parquet",
      "row_count": 5,
      "column_names": [
        "Department",
        "Headcount",
        "Dept_Avg_Salary",
        "Company_Avg_Salary",
        "Salary_Variance_Pct",
        "Dept_Avg_Perf",
        "Company_Avg_Perf",
        "Perf_Variance"
      ],
      "sample_rows": [
        {
          "Department": "IT",
          "Headcount": 5,
          "Dept_Avg_Salary": 1550000.0,
          "Company_Avg_Salary": 1169500.0,
          "Salary_Variance_Pct": 32.54,
          "Dept_Avg_Perf": 89.4,
          "Company_Avg_Perf": 82.85,
          "Perf_Variance": 6.55
        },
        {
          "Department": "Finance",
          "Headcount": 4,
          "Dept_Avg_Salary": 1312500.0,
          "Company_Avg_Salary": 1169500.0,
          "Salary_Variance_Pct": 12.23,
          "Dept_Avg_Perf": 88.25,
          "Company_Avg_Perf": 82.85,
          "Perf_Variance": 5.4
        },
        {
          "Department": "Sales",
          "Headcount": 4,
          "Dept_Avg_Salary": 1070000.0,
          "Company_Avg_Salary": 1169500.0,
          "Salary_Variance_Pct": -8.51,
          "Dept_Avg_Perf": 78.75,
          "Company_Avg_Perf": 82.85,
          "Perf_Variance": -4.1
        },
        {
          "Department": "Marketing",
          "Headcount": 4,
          "Dept_Avg_Salary": 895000.0,
          "Company_Avg_Salary": 1169500.0,
          "Salary_Variance_Pct": -23.47,
          "Dept_Avg_Perf": 77.25,
          "Company_Avg_Perf": 82.85,
          "Perf_Variance": -5.6
        },
        {
          "Department": "HR",
          "Headcount": 3,
          "Dept_Avg_Salary": 843333.33,
          "Company_Avg_Salary": 1169500.0,
          "Salary_Variance_Pct": -27.89,
          "Dept_Avg_Perf": 77.67,
          "Company_Avg_Perf": 82.85,
          "Perf_Variance": -5.18
        }
      ],
      "execution_ms": 3,
      "error": null
    }
  ],
  "queries_failed": [],
  "dashboard_config": [],
  "dashboard_title": "",
  "dashboard_theme": "dark",
  "layout_rationale": "",
  "qa_report": null,
  "errors": [],
  "retry_count": 0,
  "token_usage": {}
}
```

---

## SECTION 4 — LAYOUT READINESS CHECK

This section analyzes the exact compatibility between the current agent outputs and what `Layout Agent` (`layout_node.py` and `layout_chart_selection.txt`) requires to perform chart classification, grid layout placement, and ECharts generation.

### 4.1 Summary Comparison Table

| Information Dimension | Required by Layout Agent | Current Status from Upstream | Actual Field in State |
| :--- | :--- | :--- | :--- |
| **Query ID** | Yes (keys charts) | **AVAILABLE NOW** | `QueryDefinition.id` / `QueryResult.query_id` |
| **Query Title** | Yes (chart cards & header) | **AVAILABLE NOW** | `QueryDefinition.title` |
| **Row Count** | Yes (KPI vs Table vs Chart) | **AVAILABLE NOW** | `QueryResult.row_count` |
| **Column Names** | Yes (dimension / measure matching) | **AVAILABLE NOW** | `QueryResult.column_names` |
| **Sample Rows** | Yes (value parsing & data typing) | **AVAILABLE NOW** | `QueryResult.sample_rows` (up to 5 dicts) |
| **SQL Query Text** | Optional (context & explain) | **AVAILABLE NOW** | `QueryDefinition.sql` |
| **Storage Parquet Path** | Yes (full data extraction for charts) | **AVAILABLE NOW** | `QueryResult.result_storage_path` |
| **Execution Latency** | Optional (diagnostics) | **AVAILABLE NOW** | `QueryResult.execution_ms` |
| **Enriched Parquet Path** | Optional (underlying dataset) | **AVAILABLE NOW** | `state.enriched_parquet_path` |
| **Base Dataset Column Meta**| Optional (domain context) | **AVAILABLE NOW** | `state.column_metadata` |
| **Derived Features List** | Desirable (feature explanations) | **MISSING / NOT PROVIDED** | `state.feature_definitions` is `[]` (empty) |
| **Feature Rationale** | Desirable (layout narrative) | **MISSING / NOT PROVIDED** | `state.feature_rationale` is `""` (empty) |
| **Business Question** | Critical (drives analytical purpose) | **MISSING / NOT PROVIDED** | `QueryDefinition.business_question` is `""` (empty) |
| **Explicit X-Axis Column** | Highly Useful (chart configuration) | **MISSING / NOT PROVIDED** | `QueryDefinition.x_axis` is `null` |
| **Explicit Y-Axis Column** | Highly Useful (chart configuration) | **MISSING / NOT PROVIDED** | `QueryDefinition.y_axis` is `null` |
| **Explicit Group-By Column** | Useful (multi-series & pie breakdown)| **MISSING / NOT PROVIDED** | `QueryDefinition.group_by` is `null` |
| **Query Chart Type Hint** | Useful (upstream guidance) | **MISLEADING / HARD-CODED** | `QueryDefinition.chart_type` is `"data_table"` for all |
| **Query Insight Summary** | Desirable (chart subtitles / insights) | **MISSING / NOT PROVIDED** | `QueryDefinition.insight_summary` is `""` (empty) |
| **Query Priority** | Critical (grid sorting & layout order) | **UNINFORMATIVE** | `QueryDefinition.priority` is `3` for all queries |
| **Column Data Types in Results** | Useful (avoids string inference) | **MISSING / NOT PROVIDED** | Only present in base schema, not in QueryResult |

---

### 4.2 Detailed Breakdown

#### A. AVAILABLE NOW
1. **Query Identifier and Labels:**
   - `id` (e.g. `exec_summary_kpi`, `annual_hiring_compensation_trend`) is clearly formatted and unique.
   - `title` (e.g. `Executive Summary KPIs`, `Annual Hiring and Starting Compensation Trend`) is clean, professional, and business-ready.
2. **Execution Results & Data Payloads:**
   - `row_count` is exact and immediately lets Layout identify single-row KPIs (`row_count == 1`) vs multi-row aggregates.
   - `column_names` lists the exact output columns produced by DuckDB.
   - `sample_rows` contains actual typed data (Python floats, ints, ISO timestamps, and strings), allowing Layout's `profile_query_result` to test whether a column is numeric, temporal, or categorical.
   - `result_storage_path` points to a real, valid Parquet file on disk with the full query result dataset for ECharts compilation.
3. **Execution Safety:**
   - `queries_failed` is empty (`[]`), meaning 100% of generated queries are guaranteed to have corresponding data.

---

#### B. MISSING / NOT PROVIDED (Mismatches & Gaps)

1. **`business_question` is completely blank (`""`) in `QueryDefinition`:**
   - **Impact on Layout Agent:** In `layout_node.py` (lines 54–65), `analytical_purpose` is derived by scanning `business_question` for keywords like `"trend"`, `"relationship"`, `"breakdown"`, or `"ranking"`. Because `business_question` is empty, **every query defaults to `analytical_purpose = "comparison"`**.
   - **Consequence for Chart Selection:** Prompt rules in `layout_chart_selection.txt` that check if `analytical_purpose` is `"composition"`, `"relationship"`, or `"breakdown"` (e.g., Pie, Area, Scatter) cannot match on analytical purpose.

2. **`x_axis`, `y_axis`, and `group_by` are `null`:**
   - **Impact on Layout Agent:** Layout must fall back to heuristics: choosing `col_names[0]` as `x_axis` and `col_names[1]` as `y_axis`. For multi-measure queries (like `exec_summary_kpi` with 6 numeric aggregates, or `city_level_performance_payroll` with both Headcount and Payroll), picking `col_names[0]` (`City`) and `col_names[1]` (`Region`) mistakenly treats `Region` as the Y-axis rather than `Total_Payroll`!

3. **`chart_type` in `QueryDefinition` is hardcoded to `"data_table"`:**
   - **Impact on Layout Agent:** In `analytics_node.py` (line 175), `chart_type=q_dict.get("chart_type", "data_table")`. Because the prompt for Analytics Agent does not request a chart type, `q_dict.get("chart_type")` is always `None`, and all queries arrive in the state labeled as `"data_table"`. Layout cannot use `QueryDefinition.chart_type` as a prior hint.

4. **`priority` is uniform (`3`) across all queries:**
   - **Impact on Layout Agent:** The Analytics prompt instructs the LLM to generate Priority 1 (KPI), Priority 2 (Trends), Priority 3 (Breakdowns), Priority 4 (Anomalies), Priority 5 (Benchmarks). However, `analytics_node.py` line 177 uses `int(q_dict.get("priority", 3))`. Since the LLM returns dictionaries without an explicit `"priority"` key, all queries receive priority `3`, depriving Layout of the intended visual hierarchy (KPI at top, trends in middle, tables at bottom).

5. **`feature_definitions` is `[]` (empty list):**
   - **Impact on Layout Agent:** No derived feature definitions are available in state. Layout's `layout_rationale` cannot cite engineered features (such as tenure cohorts or salary-to-performance ratios).

6. **Zero-Row Result Handling (`compensation_performance_anomalies`):**
   - **Impact on Layout Agent:** Query `compensation_performance_anomalies` returned `row_count: 0` and `sample_rows: []`. Layout must handle zero-row datasets gracefully (e.g. either suppressing the chart or rendering an informative empty state / notice rather than crashing on `sample_rows[0]`).

# AutoBI – Sprint 4 Layout Agent Reference & Execution Guide

## Purpose

This document is an additional reference for Sprint 4 implementation. The **Sprint 4 blueprint remains the primary implementation specification**.

This file explains the intended Layout Agent behavior in simple, practical terms so Antigravity can implement it without over-engineering.

---

## 1. First: Layout Agent should NOT simply ask the LLM “Which chart?”

Avoid:

```text
Analytics Result
    ↓
LLM: Which chart?
    ↓
Chart
```

Instead use:

```text
Analytics Result
    ↓
Result Profile
    ↓
Simple Chart Selection Rules
    ↓
LLM Chart Selection
    ↓
Basic Validation / Fallback
    ↓
ECharts Configuration
    ↓
Dashboard Layout
    ↓
Next.js + ECharts
```

The goal is **not** to create a huge expert system.

The goal is:

> LLM provides intelligent visualization selection, while simple deterministic rules prevent obviously inappropriate charts.

---

## 2. What the Layout Agent receives

Work from Analytics Agent outputs. Do not clean data or redo the main analysis.

For each query/result, provide a compact profile:

```text
query_id
query_title
business_question
columns
column_types
row_count
x_axis_column
x_axis_type
y_axis_column
y_axis_type
group_by_column
group_count / distinct_count
aggregation
is_time_series
sample_rows
```

Example:

```text
Query:
"Show monthly revenue trend"

Result:
month | revenue
Jan   | 125000
Feb   | 138000
Mar   | 151000
Apr   | 147000

x_axis = month
x_type = temporal
y_axis = revenue
y_type = numeric
row_count = 4
aggregation = SUM
```

---

## 3. Main principle

The Layout Agent should answer:

> **“What is the clearest visual representation of this analytical result for a business user?”**

Not:

> “Which chart looks impressive?”

Consider:

1. Analytical purpose
2. Data types
3. Number of groups
4. Time dimension
5. Relationship between measures
6. Readability
7. Business interpretation

---

# 4. Existing BI principles

The underlying visualization principles are based on established BI guidance such as Microsoft Power BI and Tableau:

- Line → trends over time
- Bar/column → category comparison
- Scatter → relationships between numerical variables
- Pie → part-to-whole with a small number of categories
- Area → magnitude/change over time
- Table → exact values and detailed inspection
- Visualization choice → data type + analytical purpose
- Dashboard → prioritize important information and avoid clutter

AutoBI does **not** claim to invent these principles. AutoBI operationalizes them into a simple Layout Agent workflow.

---

# 5. Simple chart-selection rulebook

### Rule A — KPI

If:

```text
1 row
1 meaningful numeric value
```

then:

```text
kpi_card
```

Example:

```text
Total Revenue = ₹12.5M
```

---

### Rule B — Time trend

If X-axis is temporal/date/month/year and the result represents an ordered trend:

```text
line
```

Example:

```text
Month | Revenue
Jan   | 100K
Feb   | 120K
Mar   | 150K
Apr   | 140K
```

Business meaning: the user wants to understand how a metric changed over time.

---

### Rule C — Category comparison

If a numeric measure is compared across categories:

```text
bar
```

Example:

```text
Department | Sales
IT         | 450K
HR         | 250K
Finance    | 390K
Sales      | 620K
```

For rankings/top-N, sort bars appropriately.

---

### Rule D — Numeric relationship

If the analytical purpose is to understand the relationship between two meaningful numeric variables:

```text
scatter
```

Example:

```text
Advertising Spend | Revenue
10K               | 80K
20K               | 110K
30K               | 150K
40K               | 190K
```

Do not select scatter merely because two numeric columns exist.

---

### Rule E — Part-to-whole

Use pie only when:

```text
part-to-whole relationship
+
small number of categories
+
composition makes business sense
```

Practical AutoBI limit:

```text
≤ 7 categories
```

If there are many categories, prefer bar/table.

---

### Rule F — Heatmap

Use heatmap when there are:

```text
two categorical dimensions
+
meaningful numeric measure
```

Example:

```text
             Mon   Tue   Wed
North        20    25    18
South        35    40    32
West         15    22    19
```

---

### Rule G — Area

Use area when a time/ordered sequence should emphasize magnitude or accumulated volume.

Keep **line as the normal default** for ordinary time trends.

---

### Rule H — Data table

Use a table when:

- exact values matter
- there are many rows/categories
- detailed inspection is required
- a chart would become crowded

A table is not a failure state.

---

## 6. Safe default

If no specialized rule clearly applies:

```text
bar
```

Do not force unusual visualizations.

---

## 7. Cardinality

Keep this simple:

```text
Low cardinality
→ bar / pie when appropriate

Medium cardinality
→ bar

High cardinality
→ table or top-N bar
```

Avoid high-cardinality pie charts.

Example:

```text
200 product categories
```

Do not create a 200-slice pie. Use Top-N bar or table.

---

## 8. Time-series rule

When X is a real temporal field:

```text
date / month / quarter / year / timestamp
```

default:

```text
line
```

For:

```text
Month | Revenue | Cost | Profit
```

a multi-series line can be appropriate.

---

## 9. Ranking / Top-N

For:

```text
Top 10 Products by Revenue
```

use:

```text
sorted bar chart
```

because the business purpose is immediate comparison/ranking.

---

# 10. Business question matters

Do not select charts from column types alone.

### Example 1

Question:

> How did revenue change every month?

Result:

```text
Month + Revenue
```

→ **Line**

### Example 2

Question:

> Which region generated the most revenue?

Result:

```text
Region + Revenue
```

→ **Bar**

### Example 3

Question:

> What is the relationship between advertising spend and revenue?

Result:

```text
Advertising Spend + Revenue
```

→ **Scatter**

The same data types can lead to different visualizations depending on analytical purpose.

---

# 11. Complete AutoBI example

User uploads:

```text
sales.csv
```

Question:

> “Why did revenue drop in Q3?”

### Analytics produces:

```text
Query 1:
Quarter | Revenue
Q1      | 1.2M
Q2      | 1.4M
Q3      | 1.0M
Q4      | 1.5M
```

```text
Query 2:
Region | Revenue Change
North  | -8%
South  | -4%
West   | -15%
East   | +2%
```

### Layout profiles Query 1

```text
X = Quarter
X type = temporal/ordered
Y = Revenue
Y type = numeric
```

→ **Line Chart**

### Layout profiles Query 2

```text
X = Region
Y = Revenue Change
```

→ **Bar Chart**

### Resulting dashboard

```text
------------------------------------------------
| Revenue Trend                    | KPI        |
|                                  | Revenue    |
------------------------------------------------
| Revenue Change by Region                     |
------------------------------------------------
| Detailed Analysis / Table                    |
------------------------------------------------
```

Then Next.js + ECharts renders the interactive dashboard.

---

# 12. Layout Agent has two main AI jobs

Keep implementation simple.

### Phase 1 — Chart selection

Input:

```text
QueryDefinition
+
Result Profile
```

LLM returns only one of:

```text
bar
line
scatter
area
pie
heatmap
kpi_card
data_table
```

### Phase 2 — ECharts configuration

Input:

```text
QueryDefinition
+
sample_rows
+
selected chart type
```

Output:

```text
ChartConfig.echarts_option
```

Then validate it.

---

# 13. LLM should not control everything

Recommended flow:

```text
Analytics Result
      ↓
Result Profiling
      ↓
Chart Selection LLM
      ↓
Simple Rule Check
      ↓
ECharts Config LLM
      ↓
Basic Validator
      ↓
Dashboard Config
      ↓
Next.js + ECharts
```

The rule check should be lightweight.

---

# 14. LLM fallback / correction

Example:

LLM returns:

```text
pie
```

but result has:

```text
45 categories
```

Then:

```text
pie
 ↓
invalid for high cardinality
 ↓
fallback = bar
```

Another example:

```text
LLM = line
X-axis = Department
```

For an unordered category comparison, fall back to:

```text
bar
```

The purpose is to prevent obvious mistakes, not constantly fight the LLM.

---

# 15. ECharts validation

Keep validation practical.

### Universal

```text
title exists
series exists
tooltip valid
no undefined
no NaN
```

### Line

```text
valid x-axis
valid series data
temporal x-axis for time-series
```

### Bar

```text
categories exist
numeric values exist
```

### Pie

```text
data = [{name, value}]
values valid
```

### Scatter

```text
valid x/y values
```

### Heatmap

```text
valid x/y/value structure
```

### KPI

```text
single meaningful value
```

### Table

```text
columns exist
rows exist
```

Do not build an excessively complicated validator in Sprint 4.

---

# 16. Dashboard layout

Use the blueprint's 12-column layout.

Concept:

```text
--------------------------------------------
|          Primary KPI / Main Chart        |
--------------------------------------------
|       Chart A       |       Chart B      |
--------------------------------------------
|       Chart C       |       Chart D      |
--------------------------------------------
|             Detailed Table               |
--------------------------------------------
```

Priority:

```text
Priority 1 → Primary KPI / main insight
Priority 2 → Major trends/comparisons
Priority 3 → Supporting analysis
Priority 4 → Detailed table
```

Keep the arrangement simple and responsive.

---

# 17. Interactivity

Implement the blueprint's professional BI interactions without over-engineering:

- tooltips
- responsive charts
- PNG chart download
- draggable/resizable dashboard cards
- save layout
- data table
- dashboard export
- Ask Another Question / follow-up
- responsive mobile layout

Layout Agent provides visualization configuration and dashboard structure; Next.js/ECharts handles rendering and interaction.

---

# 18. What Layout Agent should NOT do

Do not make Layout responsible for:

```text
❌ Data cleaning
❌ Data type repair
❌ Main SQL analytics
❌ Feature engineering
❌ Re-running ingestion
```

Those belong to previous stages.

Layout answers:

> **“How should this analytical result be presented clearly?”**

---

# 19. Final Layout Agent architecture

```text
                    USER
                     │
                     ↓
              Original Question
                     │
                     ↓
        ┌────────────────────────┐
        │      Analytics Agent   │
        └───────────┬────────────┘
                    │
                    ↓
          QueryDefinitions
                    +
             QueryResults
                    │
                    ↓
        ┌────────────────────────┐
        │     Layout Agent       │
        │                        │
        │  1. Result Profiling   │
        │  2. Chart Selection    │
        │  3. Rule Check         │
        │  4. ECharts Config     │
        │  5. Basic Validation   │
        │  6. Dashboard Grid     │
        └───────────┬────────────┘
                    │
                    ↓
              ChartConfig[]
                    │
                    ↓
        ┌────────────────────────┐
        │      Next.js           │
        │      ECharts           │
        └───────────┬────────────┘
                    │
                    ↓
             INTERACTIVE
              BI DASHBOARD
```

### Final principle

> **Keep the Layout Agent intelligent but simple.**

Do not build the most advanced chart-selection AI possible.

Build a reliable Layout Agent that automatically converts Analytics Agent results into appropriate, readable, interactive BI visualizations.

Use:

```text
Business Question
      +
Analytical Result
      +
Data Characteristics
      ↓
LLM Chart Selection
      ↓
Simple Deterministic Rules
      ↓
ECharts Configuration
      ↓
Basic Validation
      ↓
Professional Interactive Dashboard
```

---

# Antigravity execution instructions

The **Sprint 4 blueprint is the PRIMARY implementation specification**. This document is a behavioral/reference guide.

Before modifying code:

1. Inspect the existing Sprint 4 branch.
2. Understand current Analytics Agent output structures.
3. Reuse existing QueryDefinition, QueryResult, state, LangGraph, API, and session patterns wherever possible.
4. Do not rebuild completed Ingestion, Cleaning, or Analytics agents.
5. Do not introduce unnecessary frameworks.
6. Keep Layout modular so QA Agent integration remains possible.
7. Follow the blueprint's specified routing:
   - Groq llama-3.1-8b-instant → chart selection
   - Gemini 2.0 Flash → ECharts configuration
8. Use only the chart types specified in the blueprint.
9. Validate generated configurations before rendering.
10. Keep the implementation dataset-driven and business-question-aware.

## Suggested implementation progression

### Phase 1

```text
Analytics result
→ Result Profile
→ Chart Selection
→ Chart Type
```

Test multiple query shapes.

### Phase 2

```text
Chart Type
+
QueryDefinition
+
Sample Rows
→ ECharts Option
```

### Phase 3

```text
ChartConfig
→ DashboardGrid
→ Next.js Dashboard
→ ECharts Rendering
```

### Phase 4

```text
Follow-up Question
→ reuse cleaned/enriched data
→ Feature + Analytics
→ Layout
→ new charts
```

### Phase 5

Run the five Sprint 4 E2E business scenarios from the blueprint.

---

## Final instruction to Antigravity

**Implement Sprint 4 according to the original blueprint, using this document as the behavioral reference. Keep the Layout Agent simple, explainable, business-oriented, and interactive. Do not over-engineer the rulebook. Reuse existing pipeline contracts and completed agents. First inspect the existing code and report what is already implemented before making changes.**

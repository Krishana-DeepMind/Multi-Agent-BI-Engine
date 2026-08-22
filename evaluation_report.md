## Final Metrics
**Total Columns in Dataset Sample:** 5
- **Columns dropped (rate limit / file failure)**: 0
- **Columns dropped (no response from LLM)**: 1
- **Columns evaluated (real completion)**: 4
**Semantic Type Accuracy:** 100.0%
**Business Label Quality (LLM Judge):** 100.0%
**is_candidate_kpi Metrics:** Precision: 100.0%, Recall: 100.0%

### KPI Precision & Recall by Domain
- **finance**: Precision: 100.0%, Recall: 100.0%

# Ingestion Agent Evaluation Report
## Setup
- 2 files
- Mocked Embedding Cache to force LLM generation
- Semantic types checked via exact match
- Business labels judged by LLM

## Results per File
### ecommerce_0.csv
| Column | Expected Sem | Output Sem | Expected KPI | Output KPI | Generated Label | Label Score |
|---|---|---|---|---|---|---|
| ecommerce_id | identifier | identifier ✅ | False | False ✅ (TN) | Ecommerce Id | ✅ |
| cart_abandonment_rate | percentage | ⚠️ NO RESPONSE | True | ⚠️ | ⚠️ | ⚠️ |

**File Accuracy**: 1/2 (50%)

### finance_0.csv
| Column | Expected Sem | Output Sem | Expected KPI | Output KPI | Generated Label | Label Score |
|---|---|---|---|---|---|---|
| finance_id | identifier | identifier ✅ | False | False ✅ (TN) | Finance ID | ✅ |
| revenue_usd | currency | currency ✅ | True | True ✅ (TP) | Revenue USD | ✅ |
| transaction_date | date | date ✅ | False | False ✅ (TN) | Transaction Date | ✅ |

**File Accuracy**: 3/3 (100%)


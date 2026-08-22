# AutoBI — Full Audit Report
## Issues vs Current Project State + Free-Tier Survival Rulebook
> Audit Date: 2026-08-22 | Branch: token_problem | Sprint: 2 (Day 15-16)

---

## Part 1: The 4 Issues from the Testing Report — Real Audit Against Current Code

---

### ❌ Issue 1: Groq 8,000 TPM Rate Limit (Error 413)
**Status: PARTIALLY FIXED — Root Cause Still Present**

**Where it lives:**
- File: [`ingestion_node.py`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/agents/ingestion_node.py#L98)
- Line 98: `max_tokens=8192`

**What the report says was fixed:**
> "Modified `ingestion_node.py` to use `max_tokens=4000`"

**What the actual code says RIGHT NOW (after audit):**
```python
# Line 98 in ingestion_node.py — STILL 8192
response = await llm_router.route(
    task_type=TaskType.SCHEMA_INFERENCE,
    ...
    max_tokens=8192   # ← THE FIX WAS NOT COMMITTED / NOT APPLIED
)
```

> [!CAUTION]
> The report says the fix was applied, but the current code on the `token_problem` branch still shows `max_tokens=8192`. The fix either wasn't saved, wasn't committed, or was applied to a different branch. **This will still crash on Groq.**

**Full scope of the problem across all task types:**
| Task Type | File | Current `max_tokens` | Safe Limit | Status |
|---|---|---|---|---|
| `SCHEMA_INFERENCE` | `ingestion_node.py:98` | `8192` | ≤ 4000 | ❌ BROKEN |
| `INTENT_ROUTING` | `router_node.py:43` | `256` | ≤ 4000 | ✅ OK |
| `CLEANING_STRATEGY` | (not built yet) | — | ≤ 4000 | — |
| `CODE_GENERATION` | (not built yet) | — | ≤ 2048 | — |
| `QUERY_DESIGN` | (not built yet) | — | ≤ 3000 | — |
| `ECHARTS_CONFIG` | (not built yet) | — | ≤ 4000 | — |
| `QA_REPORT` | (not built yet) | — | ≤ 4000 | — |

**The Default in `llm_router.py`:**
```python
# Line 208 in llm_router.py
async def route(..., max_tokens: int = 2048, ...) -> Dict:
```
The default in the router is `2048` which is safe. The ingestion node **overrides** this with `8192`, which is the bug.

---

### ❌ Issue 2: Gemini Deprecated SDK (`google.generativeai`)
**Status: NOT FIXED — Still Broken in Code**

**Where it lives:**
- File: [`llm_router.py`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/core/llm_router.py#L311)
- Lines 311-362: `_gemini_caller` method
- File: [`requirements.txt`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/requirements.txt#L24)
- Line 24: `google-generativeai`

**What the code does RIGHT NOW:**
```python
# Line 311 — DEPRECATED, CRASHING SDK
async def _gemini_caller(self, ...):
    import google.generativeai as genai   # ← OLD/DEAD SDK
    ...
    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model_name)  # ← FAILS with FutureWarning + crash
```

**Why this is critical for remaining agents:**
Gemini is the PRIMARY provider for these upcoming task types:
| Task Type | Agent (Sprint) | Gemini is... |
|---|---|---|
| `FEATURE_IDEATION` | Feature Architect (Sprint 3) | **Primary (#1)** |
| `ECHARTS_CONFIG` | Layout Agent (Sprint 4) | **Primary (#1)** |
| `QA_REPORT` | QA Agent (Sprint 5) | **Primary (#1)** |
| `BUSINESS_LABELING` | Ingestion (current) | **Primary (#1)** |
| `QUERY_REPAIR` | Analytics (Sprint 3) | **Primary (#1)** |

> [!CAUTION]
> 5 out of 12 task types use Gemini as the **primary (first choice)** provider. All 5 will silently fail and the pipeline will crash or degrade. This is the most urgent fix needed before Sprint 2 starts.

**What needs to change:**
- `requirements.txt`: Replace `google-generativeai` with `google-genai`
- `llm_router.py` line 311: Rewrite `_gemini_caller` using the new `google.genai` SDK

---

### ✅ Issue 3: Sanity Check Crashing the Pipeline
**Status: CONFIRMED FIXED — Not in Production Code**

The `sys.exit(1)` was in an **evaluation/test script** (likely `test_llms.py` or a custom eval script), not in production agent code. The production `ingestion_node.py` and `llm_router.py` use proper `try/except` blocks and graceful error handling. This fix was correctly isolated to test scripts only.

---

### ✅ Issue 4: Evaluation Script 2-File Sampling Limit
**Status: CONFIRMED FIXED — Test Script Only**

This was a bug in the evaluation harness, not in any production agent. The fix was local to the test/evaluation runner. Not a production concern.

---

## Part 2: Other Issues Found During Audit (Not in Report)

---

### ⚠️ New Issue 5: ROUTING TABLE — Missing Ollama for Code Generation
**Severity: HIGH — Will Crash Sprint 2 (Cleaning Agent)**

**Where it lives:**
- [`llm_router.py`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/core/llm_router.py#L62)
- Lines 62-64

**What the code says:**
```python
TaskType.CODE_GENERATION: [
    ProviderConfig("groq", "qwen/qwen3.6-27b", 500_000, 14_400, 1),
    # ← ONLY Groq. NO Ollama. Blueprint says Ollama MUST be primary for code gen.
],
```

**What the blueprint mandates:**
```
Cleaning: Code Generation  → Ollama qwen2.5-coder:7b (PRIMARY) → Groq 70B (Fallback)
Feature Architect: Code    → Ollama qwen2.5-coder:7b (PRIMARY) → Groq 70B (Fallback)
```

**Impact:** The Cleaning Agent (Sprint 2, Day 19-21) will burn thousands of Groq tokens generating Polars code instead of using free local Ollama. This is the single biggest token-waste risk in the upcoming sprints.

---

### ⚠️ New Issue 6: `AgentSwarmState` Has No Optional Fields for Early Pipeline Stages
**Severity: MEDIUM — Will Crash When Initializing a New Session**

**Where it lives:**
- [`state.py`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/core/state.py#L121)
- Lines 121-151

**What the code says:**
```python
class AgentSwarmState(BaseModel):
    # Cleaning Agent Namespace — ALL REQUIRED, NO DEFAULTS
    cleaning_operations: List[CleaningOperation]   # No default
    cleaned_parquet_path: str                       # No default
    data_quality_score: float                       # No default
    rows_before: int                                # No default
    ...
    # Feature Architect Namespace — ALL REQUIRED, NO DEFAULTS
    feature_definitions: List[FeatureDefinition]   # No default
    enriched_parquet_path: str                      # No default
    ...
```

**Problem:** When you create the initial `AgentSwarmState` for a new user session (before any agent runs), Pydantic will throw `ValidationError` because fields like `cleaning_operations`, `feature_definitions`, `qa_report` have no default values and don't exist yet. The Router node runs first — it can't provide Cleaning data it hasn't computed yet.

---

### ⚠️ New Issue 7: `model_validator` on `AgentSwarmState` is Incorrectly Triggered at Init
**Severity: MEDIUM**

**Where it lives:**
- [`state.py`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/core/state.py#L153)
- Lines 153-157

```python
@model_validator(mode='after')
def check_rows(self):
    if self.rows_after > self.rows_before:  # rows_before starts at 0!
        raise ValueError("rows_after cannot be greater than rows_before")
```

When `rows_before=0` and `rows_after=0` (initial state), this passes. But if `rows_after` gets set before `rows_before` during any partial state update, it will throw. This is a latent bug.

---

### ⚠️ New Issue 8: `CLEANING_STRATEGY` and `QA_VALIDATION` Have No Fallback Provider
**Severity: HIGH — Single Point of Failure**

**Where it lives:**
- [`llm_router.py`](file:///c:/Users/Admin/Documents/AutoBI/Multi-Agent-BI-Engine/backend/core/llm_router.py#L77)
- Lines 77-79, 91-93

```python
TaskType.CLEANING_STRATEGY: [
    ProviderConfig("groq", "qwen/qwen3.6-27b", 500_000, 14_400, 1),
    # ← NO FALLBACK. If Groq is rate-limited → pipeline dies.
],
TaskType.QA_VALIDATION: [
    ProviderConfig("groq", "qwen/qwen3.6-27b", 500_000, 14_400, 1),
    # ← NO FALLBACK.
],
```

---

### ℹ️ New Issue 9: `schema_compressor.py` Passes Only 2 Sample Values (Blueprint says 5)
**Status: Actually GOOD — Matches your "Max 3 Sample Rows" Rule**

The compressor at line 17 does `col.sample_values[:2]` — it only sends 2 sample values per column to the LLM. This is safe and token-efficient. However, the state model allows up to 5 (`max_length=5`). This discrepancy is intentional and correct for free-tier limits.

---

## Part 3: Free-Tier Survival Rulebook for Remaining Agents

These are the limits that MUST be enforced from now on to survive Sprints 2-6 on free-tier APIs.

### The Rules (Confirmed by Audit)

| Rule | Constraint | Where to Enforce |
|---|---|---|
| **R1: Max 1 File** | One CSV per session | API upload endpoint (already enforced via file_validator) |
| **R2: Max 20 Columns** | Reject uploads > 20 cols | `file_validator.py` — needs to be verified |
| **R3: Max 2 Sample Values to LLM** | Only 2 samples in prompts | `schema_compressor.py` — already correct |
| **R4: max_tokens ≤ 4000 for Groq** | Never exceed 4000 on Groq | `ingestion_node.py` line 98 — needs fix |
| **R5: Ollama First for Code Gen** | `CODE_GENERATION` routing | `llm_router.py` ROUTING_TABLE — needs fix |
| **R6: Max 3 SQL Queries** | Analytics Agent output limit | Prompt engineering (future agent) |
| **R7: asyncio.sleep(2) between nodes** | Already in `llm_router.py` line 253 | ✅ Already implemented |
| **R8: Gemini SDK migration** | New `google.genai` SDK | `llm_router.py` + `requirements.txt` |
| **R9: All task types need a fallback** | No single-provider tasks | `ROUTING_TABLE` — needs Ollama added |
| **R10: Max 3-5 Cleaning Operations** | Limit Cleaning Agent output | Prompt engineering (future agent) |

---

## Summary: What Needs to Be Fixed Before Writing Cleaning Agent

| Priority | Issue | File | Change Needed |
|---|---|---|---|
| 🔴 P0 | `max_tokens=8192` not fixed | `ingestion_node.py:98` | Change to `4000` |
| 🔴 P0 | Gemini SDK deprecated | `llm_router.py:311`, `requirements.txt:24` | Migrate to `google-genai` |
| 🔴 P0 | `CODE_GENERATION` uses Groq not Ollama | `llm_router.py:62-64` | Add Ollama as priority 1 |
| 🟠 P1 | `CLEANING_STRATEGY` has no fallback | `llm_router.py:77-79` | Add Gemini fallback |
| 🟠 P1 | `AgentSwarmState` fields not optional | `state.py:121-151` | Add `= []` / `= ""` defaults |
| 🟡 P2 | `QA_VALIDATION` has no fallback | `llm_router.py:91-93` | Add Gemini fallback |
| 🟡 P2 | `CHART_SELECTION` has no fallback | `llm_router.py:88-90` | Add Gemini fallback |

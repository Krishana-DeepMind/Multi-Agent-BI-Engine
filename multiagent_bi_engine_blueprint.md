# Autonomous Multi-Agent Data Analytics & BI Engine
## Enterprise Blueprint & 90-Day Vibe Coding Execution Plan
### Version 1.0 | Principal Enterprise AI Architect Review

---

> **Reading Guide:** This document is the single source of truth for your build. Sections 1–4 are the engineering specification. The 90-Day Plan in Section 3 references specific contracts, classes, and prompt templates defined in Sections 1–2. Read the architecture first, then execute the sprint plan.

---

# SECTION 1: FREE TECH STACK & LLM API ROUTING STRATEGY

## 1.1 Complete Technology Matrix

### Infrastructure Layer

| Component | Tool | Free Tier Limit | Purpose |
|---|---|---|---|
| Primary Database | Supabase (PostgreSQL 15) | 500MB DB, 2GB bandwidth/mo | Sessions, state, user data, audit logs |
| Vector Store | pgvector (via Supabase) | Included with Supabase | Schema embeddings, semantic similarity search |
| Object Storage | Supabase Storage | 1GB storage | Raw uploads, cleaned Parquet, dashboard configs |
| Backend Hosting | Railway.app | 500 exec-hours/mo | FastAPI + LangGraph orchestration service |
| Frontend Hosting | Vercel | Unlimited (Hobby) | Next.js 14 SaaS frontend |
| CI/CD | GitHub Actions | 2,000 min/mo | Test + deploy on push to main |
| Message Queue | Upstash Redis | 10,000 commands/day | Agent job queuing, SSE fan-out, rate limit tracking |
| Local Dev Runtime | Docker Compose | Free | Full-stack parity with production |
| Local LLM Inference | Ollama | Unlimited | Fallback + code generation, no token cost |

### Compute & Analytics Layer

| Component | Tool | Why This One |
|---|---|---|
| Analytics Engine | DuckDB 0.10+ | In-process columnar, zero-config, handles 10GB+ without a server, native Parquet/CSV/JSON reader |
| DataFrame Library | Polars 0.20+ | 5–10× faster than Pandas, lazy evaluation prevents OOM on large files, Arrow-native |
| Data Format | Apache Parquet + Arrow IPC | Column-oriented, the native exchange format between agents — never pass raw rows in state |
| Schema Inference (non-LLM) | DuckDB `DESCRIBE` + Polars `.schema` | Deterministic type inference before spending any LLM tokens |
| Embeddings | Nomic `nomic-embed-text` via Ollama | Free, 768-dim, runs locally, stores schema fingerprints in pgvector |
| Orchestration | LangGraph 0.2+ | Stateful graph execution, native checkpointing to PostgreSQL, human-in-the-loop hooks |

---

## 1.2 LLM API Routing Strategy — The Core of Cost Control

### Why Multi-Provider is Non-Negotiable

A single user session triggers ~15–22 LLM calls consuming ~30,000–55,000 tokens. No single free tier handles this alone. You must architect a **token budget router** that treats LLM APIs as pooled resources.

### Daily Free Tier Budget (as of 2025–2026)

| Provider | Model | Requests/Day | Tokens/Min | Tokens/Day | Best For |
|---|---|---|---|---|---|
| Groq | `llama-3.3-70b-versatile` | 14,400 | 6,000 TPM | 500,000 | Fast structured JSON, schema ops |
| Groq | `llama-3.1-8b-instant` | 14,400 | 20,000 TPM | 500,000 | Intent routing, quick decisions |
| Google AI | `gemini-2.0-flash-exp` | 1,500 | ~32,000 TPM | ~1,000,000 | Long-context reasoning, layout |
| Google AI | `gemini-1.5-pro` | 50 | 2,000 TPM | Reserve | Complex multi-step reasoning only |
| Mistral | `mistral-small-latest` | ~500 | 2,000 TPM | ~50,000 | Overflow failover |
| Ollama Local | `qwen2.5-coder:7b` | Unlimited | CPU/GPU bound | Unlimited | **All code generation — always local** |
| Ollama Local | `llama3.2:latest` | Unlimited | CPU/GPU bound | Unlimited | Dev/test fallback |
| Ollama Local | `nomic-embed-text` | Unlimited | CPU/GPU bound | Unlimited | All embeddings |

### Master Routing Table — Agent to Model Assignment

```
┌─────────────────────────────┬───────────────────────────────┬────────────┬─────────────────────────────────────────────┐
│ Agent / Task                │ Primary Model                 │ Fallback   │ Rationale                                   │
├─────────────────────────────┼───────────────────────────────┼────────────┼─────────────────────────────────────────────┤
│ Intent Router               │ Groq llama-3.1-8b-instant     │ Ollama     │ ~400 tokens, must be <1s, binary output     │
│ Ingestion: Type Inference   │ Groq llama-3.3-70b-versatile  │ Gemini     │ Structured JSON, strong instruction follow  │
│ Ingestion: Business Labels  │ Gemini 2.0 Flash              │ Groq 70B   │ Ambiguous schemas need broader reasoning    │
│ Cleaning: Strategy Select   │ Groq llama-3.3-70b-versatile  │ Ollama     │ Fast iterative decisions per column         │
│ Cleaning: Code Generation   │ Ollama qwen2.5-coder:7b       │ Groq 70B   │ ALWAYS local — code gen is token-expensive  │
│ Feature Architect: Ideas    │ Gemini 2.0 Flash              │ Groq 70B   │ Needs analytical creativity + long context  │
│ Feature Architect: Code     │ Ollama qwen2.5-coder:7b       │ Groq 70B   │ ALWAYS local — Polars/SQL expression gen    │
│ Analytics: Query Design     │ Groq llama-3.3-70b-versatile  │ Gemini     │ SQL is structured, fast reasoning wins      │
│ Analytics: Query Repair     │ Gemini 2.0 Flash              │ Groq 70B   │ Error analysis needs careful reasoning      │
│ Layout: Chart Type Select   │ Groq llama-3.1-8b-instant     │ Ollama     │ Simple classification, fast                 │
│ Layout: ECharts Config Gen  │ Gemini 2.0 Flash              │ Groq 70B   │ Complex nested JSON, benefits from context  │
│ QA: Validation Checks       │ Groq llama-3.3-70b-versatile  │ Ollama     │ Rule-based checks, fast throughput          │
│ QA: Final Report            │ Gemini 2.0 Flash              │ Groq 70B   │ Comprehensive synthesis needs context       │
└─────────────────────────────┴───────────────────────────────┴────────────┴─────────────────────────────────────────────┘
```

**Critical Rule:** All code generation (Python/Polars expressions, DuckDB SQL, ECharts JSON configs) is routed to **Ollama local first**. This is your single biggest token-saving measure.

### The LLMRouter Class (Full Implementation Contract)

```python
# backend/core/llm_router.py

import os
import asyncio
from enum import Enum
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import httpx
import redis.asyncio as aioredis

class TaskType(str, Enum):
    INTENT_ROUTING       = "intent_routing"
    SCHEMA_INFERENCE     = "schema_inference"
    BUSINESS_LABELING    = "business_labeling"
    CLEANING_STRATEGY    = "cleaning_strategy"
    CODE_GENERATION      = "code_generation"
    FEATURE_IDEATION     = "feature_ideation"
    QUERY_DESIGN         = "query_design"
    QUERY_REPAIR         = "query_repair"
    CHART_SELECTION      = "chart_selection"
    ECHARTS_CONFIG       = "echarts_config"
    QA_VALIDATION        = "qa_validation"
    QA_REPORT            = "qa_report"

@dataclass
class ProviderConfig:
    provider: str
    model: str
    daily_limit: int     # tokens
    rpm_limit: int       # requests per minute
    priority: int        # lower = higher priority

ROUTING_TABLE: Dict[TaskType, List[ProviderConfig]] = {
    TaskType.INTENT_ROUTING: [
        ProviderConfig("groq",   "llama-3.1-8b-instant",      500_000, 14_400, 1),
        ProviderConfig("ollama", "llama3.2:latest",            999_999, 9999,   2),
    ],
    TaskType.CODE_GENERATION: [
        ProviderConfig("ollama", "qwen2.5-coder:7b",          999_999, 9999,   1),  # Always local first
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.SCHEMA_INFERENCE: [
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 1),
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 2),
        ProviderConfig("ollama", "llama3.2:latest",            999_999,  9999,  3),
    ],
    TaskType.FEATURE_IDEATION: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.ECHARTS_CONFIG: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    # ... (remaining task types follow same pattern)
}

class LLMRouter:
    def __init__(self, redis_url: str):
        self.redis = aioredis.from_url(redis_url)
        self._providers = self._init_providers()

    def _init_providers(self) -> Dict[str, Any]:
        return {
            "groq":   self._groq_caller,
            "gemini": self._gemini_caller,
            "ollama": self._ollama_caller,
        }

    async def _get_daily_usage(self, provider: str) -> int:
        """Fetch today's token count from Redis. Resets at midnight UTC."""
        key = f"token_usage:{provider}:{self._today()}"
        val = await self.redis.get(key)
        return int(val) if val else 0

    async def _increment_usage(self, provider: str, tokens: int):
        key = f"token_usage:{provider}:{self._today()}"
        await self.redis.incrby(key, tokens)
        await self.redis.expire(key, 86400)  # 24h TTL

    async def route(
        self,
        task_type: TaskType,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        max_tokens: int = 2048,
    ) -> Dict[str, Any]:
        """Route to the optimal available provider. Returns {content, provider, model, tokens_used}"""
        configs = ROUTING_TABLE.get(task_type, [])
        for config in sorted(configs, key=lambda c: c.priority):
            usage = await self._get_daily_usage(config.provider)
            if usage >= config.daily_limit * 0.95:  # 5% buffer
                continue
            try:
                caller = self._providers[config.provider]
                result = await caller(config.model, messages, response_format, max_tokens)
                await self._increment_usage(config.provider, result["tokens_used"])
                result["routed_via"] = f"{config.provider}/{config.model}"
                return result
            except Exception as e:
                if "rate_limit" in str(e).lower():
                    continue  # Try next provider
                raise
        raise RuntimeError(f"All providers exhausted for task: {task_type}")

    @staticmethod
    def _today() -> str:
        from datetime import date
        return date.today().isoformat()

    # --- Provider-specific callers ---
    async def _groq_caller(self, model, messages, response_format, max_tokens) -> Dict:
        # Uses openai-compatible SDK with groq base_url
        ...

    async def _gemini_caller(self, model, messages, response_format, max_tokens) -> Dict:
        # Uses google-generativeai SDK
        ...

    async def _ollama_caller(self, model, messages, response_format, max_tokens) -> Dict:
        # Uses httpx to hit localhost:11434/api/chat
        ...
```

---

# SECTION 2: SYSTEM ARCHITECTURE & AGENT CONTRACTS

## 2.1 LangGraph Pipeline — State Machine Topology

```
                        ┌────────────────────────────────────────────────────┐
                        │              USER ENTRY POINT                      │
                        │  Query: "Show me why sales dropped in Q3"          │
                        │  File: sales_data_2024.csv (18MB, 120K rows)       │
                        └────────────────────┬───────────────────────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────┐
                              │  [ROUTER NODE]           │
                              │  Groq llama-3.1-8b       │
                              │  Intent: "root_cause"    │
                              │  Domain: "sales"         │
                              └──────────────┬───────────┘
                                             │
                        ┌────────────────────▼──────────────────────────┐
                        │  [INGESTION & SCHEMA AGENT]                   │
                        │  • DuckDB DESCRIBE → raw schema               │
                        │  • Groq 70B: semantic type labeling           │
                        │  • Gemini Flash: business label inference     │
                        │  • nomic-embed-text: schema fingerprint       │
                        │  • pgvector: store + lookup similar schemas   │
                        │  OUTPUT → column_metadata[], schema_id        │
                        └────────────────────┬──────────────────────────┘
                                             │
                        ┌────────────────────▼──────────────────────────┐
                        │  [CLEANING & IMPUTATION AGENT]                │
                        │  • DuckDB SUMMARIZE → quality profile         │
                        │  • Groq 70B: outlier/null strategy per col    │
                        │  • Ollama Qwen2.5: generate Polars ops code   │
                        │  • Execute cleaning pipeline on Polars DF     │
                        │  • Write cleaned.parquet → Supabase Storage   │
                        │  OUTPUT → cleaning_log[], quality_score       │
                        └────────────────────┬──────────────────────────┘
                                             │
                        ┌────────────────────▼──────────────────────────┐
                        │  [FEATURE ARCHITECT AGENT]                    │
                        │  • Gemini Flash: generate feature hypotheses  │
                        │  • Ollama Qwen2.5: Polars + SQL expressions  │
                        │  • DuckDB: validate SQL expressions compile   │
                        │  • Write enriched.parquet → Supabase Storage  │
                        │  OUTPUT → feature_definitions[]               │
                        └────────────────────┬──────────────────────────┘
                                             │
                        ┌────────────────────▼──────────────────────────┐
                        │  [ANALYTICS ENGINE AGENT]                     │
                        │  • Groq 70B: generate 5-8 targeted DuckDB SQL │
                        │  • DuckDB: execute all queries against Parquet │
                        │  • Results → Supabase Storage as result.json  │
                        │  • State gets: summaries only (5-row samples)  │
                        │  OUTPUT → query_definitions[], result_refs[]   │
                        └────────────────────┬──────────────────────────┘
                                             │
                        ┌────────────────────▼──────────────────────────┐
                        │  [LAYOUT & UI/UX AGENT]                       │
                        │  • Groq 8B: classify chart type per query     │
                        │  • Gemini Flash: generate ECharts config JSON │
                        │  • Assign grid positions (responsive 12-col)  │
                        │  OUTPUT → dashboard_config[] (ECharts options) │
                        └────────────────────┬──────────────────────────┘
                                             │
                        ┌────────────────────▼──────────────────────────┐
                        │  [QA & VERIFICATION AGENT]                    │
                        │  • Groq 70B: validate SQL correctness         │
                        │  • Cross-check: query results ↔ user question │
                        │  • Score: data_quality, chart_relevance       │
                        │  • Approve | Flag | Request human review      │
                        │  OUTPUT → qa_report{}, approval_status        │
                        └────────────────────┬──────────────────────────┘
                                             │
                                             ▼
                        ┌────────────────────────────────────────────────┐
                        │           DASHBOARD RENDERER                   │
                        │  Next.js reads dashboard_config from Supabase  │
                        │  Hydrates ECharts components with result data  │
                        │  User sees interactive BI dashboard            │
                        └────────────────────────────────────────────────┘
```

## 2.2 The Master State Contract

This TypedDict is the single most important artifact in your codebase. Every agent reads it. Every agent writes only to its designated namespace. Never mutate another agent's fields.

```python
# backend/core/state.py

from typing import TypedDict, List, Dict, Optional, Any, Literal
from datetime import datetime

# ─── Sub-types ────────────────────────────────────────────────────────────────

class ColumnMeta(TypedDict):
    name:            str
    original_name:   str                # Before sanitization
    dtype:           str                # DuckDB native: VARCHAR, DOUBLE, DATE, etc.
    semantic_type:   Literal[
        "identifier", "metric", "dimension", "date", "currency",
        "percentage", "boolean", "text_description", "geographic", "unknown"
    ]
    business_label:  str                # Human-readable: "Monthly Revenue" not "rev_mth_usd"
    null_pct:        float              # 0.0–1.0
    unique_pct:      float              # 0.0–1.0
    sample_values:   List[Any]          # MAX 5 values — never more
    is_primary_key:  bool
    is_candidate_kpi: bool             # Is this what the user is asking about?

class CleaningOperation(TypedDict):
    column:          str
    operation:       Literal["fill_null", "remove_outlier", "normalize", "cast_type",
                             "drop_column", "deduplicate", "trim_whitespace", "parse_date"]
    strategy:        str               # "median", "mode", "knn", "iqr", "z_score", "drop"
    rows_affected:   int
    before_nulls:    int
    after_nulls:     int
    polars_code:     str               # Actual generated Polars expression
    rationale:       str               # Why this strategy was chosen

class FeatureDefinition(TypedDict):
    name:            str               # snake_case column name
    polars_expr:     str               # e.g., "pl.col('revenue') / pl.col('units')"
    sql_expr:        str               # DuckDB equivalent: "revenue / units AS revenue_per_unit"
    rationale:       str               # Business justification
    expected_insight: str             # What pattern this feature should reveal

class QueryDefinition(TypedDict):
    id:              str               # "q_001", "q_002", etc.
    title:           str               # "Monthly Revenue Trend"
    business_question: str            # "How did revenue change month over month?"
    sql:             str               # Full DuckDB SQL statement
    x_axis:          Optional[str]     # Column name for x-axis
    y_axis:          Optional[str]     # Column name for y-axis
    group_by:        Optional[str]     # Column for series grouping
    chart_type:      Literal["bar", "line", "scatter", "pie", "heatmap",
                             "kpi_card", "data_table", "area", "funnel"]
    insight_summary: str               # One-sentence business interpretation
    priority:        int               # 1=primary KPI, 2=supporting, 3=detail

class QueryResult(TypedDict):
    query_id:        str
    result_storage_path: str           # Supabase Storage path to result.parquet
    row_count:       int
    column_names:    List[str]
    sample_rows:     List[Dict]        # EXACTLY 5 rows in state — full data in storage
    execution_ms:    int
    error:           Optional[str]

class ChartConfig(TypedDict):
    query_id:        str
    echarts_option:  Dict              # Full ECharts option object — ready to render
    layout:          Dict              # {x: int, y: int, w: int, h: int} in 12-col grid
    title:           str
    subtitle:        str
    responsive:      bool

class QAReport(TypedDict):
    data_quality_score:   float        # 0.0–1.0
    completeness_score:   float
    query_validity:       Dict[str, bool]   # {query_id: is_valid}
    chart_relevance:      Dict[str, float]  # {query_id: 0.0–1.0 relevance score}
    anomalies:            List[str]
    suggestions:          List[str]
    overall_confidence:   float
    approval_status:      Literal["approved", "needs_review", "rejected"]
    reviewer_notes:       Optional[str]

# ─── Master State ──────────────────────────────────────────────────────────────

class AgentSwarmState(TypedDict):
    # ── Pipeline Meta ────────────────────────────────
    session_id:           str
    user_id:              str
    pipeline_status:      Literal[
        "initiated", "routing", "ingesting", "cleaning",
        "featuring", "querying", "layouting", "verifying", "complete", "failed"
    ]
    created_at:           str
    updated_at:           str
    current_agent:        str

    # ── User Intent (set once by Router Node) ────────
    raw_query:            str
    intent_class:         Literal["trend_analysis", "root_cause", "comparison",
                                  "distribution", "correlation", "ranking", "forecasting"]
    business_domain:      Literal["finance", "sales", "operations", "marketing",
                                  "hr", "ecommerce", "iot", "unknown"]
    key_entities:         List[str]     # ["revenue", "Q3", "sales rep"]
    time_dimension:       Optional[str] # "date", "month", "year", or null

    # ── Ingestion Agent Namespace ────────────────────
    raw_file_path:        str           # Supabase Storage path
    file_type:            Literal["csv", "xlsx", "json", "parquet", "unknown"]
    raw_row_count:        int
    raw_col_count:        int
    schema_fingerprint:   str           # SHA-256(sorted column names + dtypes)
    schema_embedding_id:  Optional[str] # pgvector row ID for similarity lookup
    column_metadata:      List[ColumnMeta]
    similar_schemas_found: bool         # If true, reused a cached pipeline

    # ── Cleaning Agent Namespace ─────────────────────
    cleaning_operations:  List[CleaningOperation]
    cleaned_parquet_path: str           # Supabase Storage path
    data_quality_score:   float
    rows_before:          int
    rows_after:           int
    columns_dropped:      List[str]

    # ── Feature Architect Namespace ──────────────────
    feature_definitions:  List[FeatureDefinition]
    enriched_parquet_path: str
    feature_rationale:    str           # Overall justification for chosen features

    # ── Analytics Engine Namespace ───────────────────
    generated_queries:    List[QueryDefinition]
    query_results:        List[QueryResult]    # Only summaries — not full data
    queries_failed:       List[str]            # IDs of failed queries

    # ── Layout Agent Namespace ───────────────────────
    dashboard_config:     List[ChartConfig]
    dashboard_title:      str
    dashboard_theme:      Literal["light", "dark", "brand"]
    layout_rationale:     str

    # ── QA Agent Namespace ───────────────────────────
    qa_report:            QAReport

    # ── Error & Retry Handling ───────────────────────
    errors:               List[Dict[str, str]]  # [{agent, error, timestamp, recoverable}]
    retry_count:          int
    token_usage:          Dict[str, int]         # {provider: total_tokens_used}
```

## 2.3 Context Window Bloat Prevention — 4-Strategy Framework

This is the most dangerous failure mode in multi-agent systems. Implement all four strategies from Day 1.

### Strategy 1: Agent Context Slicing

Each agent function receives only its required state slice, constructed by a `ContextSlicer` utility. The full `AgentSwarmState` is never passed to an LLM prompt.

```python
# backend/core/context_slicer.py

AGENT_CONTEXT_FIELDS = {
    "router":    ["raw_query", "file_type", "raw_row_count", "raw_col_count"],
    "ingestion": ["raw_query", "raw_file_path", "file_type", "intent_class", "business_domain"],
    "cleaning":  ["column_metadata", "raw_row_count", "intent_class", "key_entities"],
    "feature":   ["column_metadata", "cleaning_operations", "intent_class",
                  "business_domain", "key_entities", "time_dimension"],
    "analytics": ["column_metadata", "feature_definitions", "raw_query",
                  "intent_class", "business_domain", "key_entities", "time_dimension"],
    "layout":    ["generated_queries", "query_results", "raw_query",
                  "business_domain", "intent_class"],
    "qa":        ["*"],  # QA agent is the only one that sees full state
}

def slice_context(state: AgentSwarmState, agent_name: str) -> Dict:
    fields = AGENT_CONTEXT_FIELDS.get(agent_name, [])
    if fields == ["*"]:
        return dict(state)
    return {k: state[k] for k in fields if k in state}
```

### Strategy 2: Schema Compression — Never Pass Raw Data to LLMs

```python
# backend/core/schema_compressor.py

def compress_column_meta_for_prompt(columns: List[ColumnMeta]) -> str:
    """
    Converts full ColumnMeta list into a compact prompt-friendly table.
    Reduces ~8,000 tokens to ~600 tokens.
    
    Output format:
    | column_name | dtype | semantic_type | null_pct | sample_values |
    |-------------|-------|---------------|----------|---------------|
    | revenue     | DOUBLE| currency      | 0.02     | 1200, 3400    |
    """
    lines = ["| Column | Type | Semantic | Null% | Sample |"]
    lines.append("|--------|------|----------|-------|--------|")
    for col in columns:
        samples = ", ".join(str(v) for v in col["sample_values"][:2])
        lines.append(
            f"| {col['name'][:20]} | {col['dtype']} | "
            f"{col['semantic_type']} | {col['null_pct']:.0%} | {samples} |"
        )
    return "\n".join(lines)
```

### Strategy 3: Result Indirection — Query Results Never Live in State

DuckDB executes queries and writes results directly to Supabase Storage as Parquet. The `AgentSwarmState` stores only a `QueryResult` summary (5 rows, column names, metadata). The Next.js frontend fetches results directly from Supabase Storage via signed URLs. This keeps state at ~2KB regardless of result set size.

```
DuckDB executes → writes result.parquet → Supabase Storage
                                              ↓
            State stores: {result_storage_path, row_count, sample_rows[5]}
                                              ↓
                    Frontend: signed URL fetch → ECharts hydration
```

### Strategy 4: State Checkpointing with Rolling Compression

LangGraph's built-in PostgreSQL checkpointer persists state after every node. If a node fails, the pipeline resumes from the last checkpoint — no reprocessing.

```python
# backend/core/graph_builder.py

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def build_pipeline_graph(db_url: str):
    checkpointer = await AsyncPostgresSaver.from_conn_string(db_url)
    await checkpointer.setup()
    
    graph = StateGraph(AgentSwarmState)
    graph.add_node("router",    router_node)
    graph.add_node("ingestion", ingestion_node)
    graph.add_node("cleaning",  cleaning_node)
    graph.add_node("feature",   feature_node)
    graph.add_node("analytics", analytics_node)
    graph.add_node("layout",    layout_node)
    graph.add_node("qa",        qa_node)
    
    graph.set_entry_point("router")
    graph.add_edge("router",    "ingestion")
    graph.add_edge("ingestion", "cleaning")
    graph.add_edge("cleaning",  "feature")
    graph.add_edge("feature",   "analytics")
    graph.add_edge("analytics", "layout")
    graph.add_edge("layout",    "qa")
    graph.add_conditional_edges("qa", qa_router)  # → END or → analytics (retry)
    
    return graph.compile(checkpointer=checkpointer)
```

## 2.4 The 5 Agent Prompt Contracts

### Agent 1: Ingestion & Schema Agent

```
SYSTEM:
You are a Senior Data Architect specializing in business data schema analysis.
Your task: analyze a data schema and return structured metadata.

RULES:
1. Return ONLY valid JSON. No markdown. No preamble. No explanation.
2. semantic_type MUST be one of: identifier, metric, dimension, date, currency,
   percentage, boolean, text_description, geographic, unknown
3. business_label must be human-readable PascalCase ("Monthly Revenue", not "rev_mth")
4. is_candidate_kpi: true only if this column directly answers the user's question
5. sample_values: include at most 3 representative values

USER MESSAGE:
Business Question: {raw_query}
Domain: {business_domain}

Schema Profile:
{compressed_schema_table}

Return a JSON array of ColumnMeta objects for ALL {col_count} columns.
```

### Agent 2: Cleaning & Imputation Agent

```
SYSTEM:
You are a Data Quality Engineer. Generate a cleaning strategy for a dataset.

RULES:
1. Return ONLY valid JSON array of CleaningOperation objects.
2. For null_pct < 0.02: strategy "drop_row" is acceptable
3. For null_pct 0.02–0.15: use "median" for numerics, "mode" for categoricals
4. For null_pct > 0.15: flag column as "low_quality", recommend "drop_column"
5. For outliers: use "iqr" for normally distributed, "z_score" for skewed
6. NEVER impute target metric columns (is_candidate_kpi: true) — drop those rows
7. polars_code must be a valid single Polars expression (will be executed directly)

USER MESSAGE:
Dataset Quality Profile:
{duckdb_summarize_output}

Column Metadata:
{compressed_schema}

Business Intent: {intent_class} in {business_domain} domain

For each column requiring action, return a CleaningOperation.
Only include columns that need changes. Skip clean columns.
```

### Agent 3: Feature Architect Agent

```
SYSTEM:
You are a Senior Analytics Engineer specializing in feature engineering for BI.
Generate derived features that will produce meaningful business insights.

RULES:
1. Return ONLY valid JSON array of FeatureDefinition objects.
2. Generate 3–7 features. More is not better.
3. Only generate features that DIRECTLY help answer: "{raw_query}"
4. polars_expr must use only available columns listed below
5. sql_expr must be valid DuckDB SQL alias expression
6. expected_insight must predict a specific, falsifiable pattern
7. Do NOT generate features that duplicate existing columns

USER MESSAGE:
Business Question: {raw_query}
Intent: {intent_class} | Domain: {business_domain}
Time Dimension: {time_dimension}

Available Columns After Cleaning:
{compressed_schema}

Previously Applied Cleaning:
{cleaning_summary}  ← (agent generates this: just list column names + operations)
```

### Agent 4: Layout & UI/UX Agent — ECharts Config Generation

```
SYSTEM:
You are a Data Visualization Expert specializing in ECharts configurations.
Convert analytical query results into production-ready ECharts option objects.

RULES:
1. Return ONLY valid JSON. The JSON must be a parseable ECharts 'option' object.
2. Chart types: bar (comparisons), line (trends), scatter (correlations),
   pie (composition <8 segments ONLY), heatmap (matrix data), area (cumulative trends)
3. All axis labels must be human-readable. No raw column names.
4. Color palette: ["#5B8FF9", "#5AD8A6", "#5D7092", "#F6BD16", "#E86452"]
5. Include: title.text, legend, tooltip (formatter), series, grid (with padding)
6. For time-series: xAxis.type must be "time", data format must be ISO 8601
7. responsive: true — grid left/right as percentages, not pixels

USER MESSAGE:
Query: {query_definition}
Result Sample (5 rows): {sample_rows}
Columns: {column_names}
Row Count: {row_count}

Generate a complete ECharts option object for chart_type: {chart_type}
```

### Agent 5: QA & Verification Agent

```
SYSTEM:
You are a Senior QA Engineer for a BI platform. Verify the complete pipeline output.

RULES:
1. Return ONLY valid JSON conforming to the QAReport schema.
2. approval_status: "approved" if overall_confidence >= 0.75, else "needs_review"
3. "rejected" ONLY if: SQL injection detected, results contradict the business question,
   or data_quality_score < 0.40
4. query_validity: execute a dry-run of each SQL — flag syntax errors
5. chart_relevance: score 0.0–1.0 how well each chart answers the user's question
6. anomalies: report unexpected values, impossible ranges, date ordering issues
7. suggestions: actionable improvements for the user

USER MESSAGE:
Original Question: {raw_query}
Data Quality Score: {data_quality_score}
Generated Queries: {query_definitions_json}
Query Results Summary: {query_results_summary}
Dashboard Config: {dashboard_titles_and_chart_types}

Verify and return QAReport.
```

---

# SECTION 3: THE 90-DAY VIBE CODING EXECUTION PLAN

## Sprint Overview

```
Sprint 1  (Days  1–14):  "The Skeleton"     → Dev env, DB schema, file upload, DuckDB engine
Sprint 2  (Days 15–28):  "The Brain Stem"   → LangGraph setup, Ingestion + Cleaning agents
Sprint 3  (Days 29–42):  "The Cortex"       → Feature Architect + Analytics Engine agents
Sprint 4  (Days 43–56):  "The Face"         → Layout agent, frontend dashboard, real-time UI
Sprint 5  (Days 57–70):  "Stress Testing"   → QA Agent, 50-dataset edge case suite, tuning
Sprint 6  (Days 71–84):  "Hardening"        → Auth, multi-tenancy, monitoring, launch prep
Buffer    (Days 85–90):  "Launch Week"      → Deploy, beta users, live bug fixes
```

**Daily Rhythm (8 hours):**
- **Hours 1–2:** Architecture review, prompt design, system design sessions
- **Hours 3–5:** Vibe coding (Cursor/Copilot generation, integration, wiring)
- **Hours 6–7:** Testing, edge case evaluation, output quality review
- **Hour 8:** Documentation, state contract updates, tomorrow's prompt prep

---

## SPRINT 1: "THE SKELETON" (Days 1–14)
**Goal:** Working mono-repo. File upload → DuckDB load → Schema display in UI. No agents yet.
**Deliverable:** User uploads CSV, sees inferred column schema within 25 seconds.

---

### Day 1 — Environment Bootstrap

**AI Engineer:**
- Create Python 3.12 venv. Install: `langgraph langchain-core langchain-groq langchain-google-genai duckdb polars fastapi uvicorn httpx python-dotenv pydantic[email] redis supabase python-multipart openpyxl pyarrow`
- Configure `.env`: `GROQ_API_KEY`, `GOOGLE_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `UPSTASH_REDIS_URL`, `DATABASE_URL`
- Validate all API connections. Run: `python -c "from groq import Groq; print(Groq().chat.completions.create(model='llama-3.1-8b-instant', messages=[{'role':'user','content':'ping'}]).choices[0].message.content)"`
- Install Ollama. Pull models:
  ```bash
  ollama pull qwen2.5-coder:7b
  ollama pull llama3.2:latest
  ollama pull nomic-embed-text:latest
  ```
- Set up `backend/` directory structure: `core/`, `agents/`, `api/`, `tests/`

**Full-Stack Dev:**
- `npx create-next-app@latest frontend --typescript --tailwind --app`
- `cd frontend && npx shadcn@latest init` → select defaults
- Install: `npm i echarts echarts-for-react @tremor/react zustand axios react-dropzone react-hot-toast`
- Create Supabase project. Note all credentials.
- Set up Vercel deployment linked to GitHub repo main branch.
- Create `.env.local`: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`

**End-of-Day Test:** `curl localhost:8000/health` returns `{"status": "ok"}`. Frontend loads on `localhost:3000`.

---

### Day 2 — State Contract & Database Schema

**AI Engineer:**
- Implement the complete `AgentSwarmState` TypedDict as defined in Section 2.2 exactly.
- Implement all sub-types: `ColumnMeta`, `CleaningOperation`, `FeatureDefinition`, `QueryDefinition`, `QueryResult`, `ChartConfig`, `QAReport`
- Implement `ContextSlicer` class (Section 2.3, Strategy 1)
- Implement `SchemaCompressor.compress_column_meta_for_prompt()` (Section 2.3, Strategy 2)
- **Vibe coding prompt for Cursor:**
  > "Generate a complete Pydantic v2 model that mirrors this TypedDict exactly: [paste AgentSwarmState]. Add validators: session_id must be UUID format, pipeline_status must match the Literal values, null_pct and unique_pct must be between 0.0 and 1.0, sample_values list length must not exceed 5. Add a model_validator that ensures rows_after <= rows_before in CleaningOperation."

**Full-Stack Dev:**
- Apply Supabase migrations using SQL editor. Tables:
  ```sql
  -- sessions: one row per user pipeline run
  CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id),
    status TEXT NOT NULL DEFAULT 'initiated',
    raw_file_path TEXT,
    file_type TEXT,
    raw_query TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
  );

  -- pipeline_states: checkpoint per agent completion
  CREATE TABLE pipeline_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    state_json JSONB NOT NULL,
    tokens_used INT DEFAULT 0,
    checkpoint_at TIMESTAMPTZ DEFAULT NOW()
  );

  -- dashboards: final output stored for sharing/re-loading
  CREATE TABLE dashboards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID UNIQUE REFERENCES sessions(id),
    config_json JSONB NOT NULL,
    title TEXT,
    published BOOL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
  
  -- Enable pgvector
  CREATE EXTENSION IF NOT EXISTS vector;
  CREATE TABLE schema_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint TEXT UNIQUE NOT NULL,
    embedding vector(768),
    column_metadata JSONB,
    domain TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
  );
  
  -- Row-Level Security
  ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
  CREATE POLICY "Users see own sessions" ON sessions FOR ALL USING (auth.uid() = user_id);
  ALTER TABLE dashboards ENABLE ROW LEVEL SECURITY;
  CREATE POLICY "Users see own dashboards" ON dashboards FOR ALL USING (
    EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id AND s.user_id = auth.uid())
  );
  ```
- Create Supabase Storage buckets: `raw-uploads` (private), `cleaned-data` (private), `query-results` (private)

---

### Day 3 — File Upload Infrastructure

**Full-Stack Dev:**
- **Vibe coding prompt for Cursor:**
  > "Generate a FastAPI router for file upload. Endpoint: POST /api/upload. Accepts: multipart/form-data with 'file' and 'user_id' fields. Supported types: csv, xlsx, json, parquet. Max size: 100MB. Stream file to Supabase Storage at path: '{user_id}/{session_id}/{filename}'. Create a session row in PostgreSQL. Return: {session_id, file_path, file_type, file_size_mb, row_count_estimate}. Use async supabase-py client. Validate file type by magic bytes, not extension. Include error handling for all failure modes."
- Build Next.js upload page at `app/upload/page.tsx`:
  > "Generate a Next.js 14 page with a full-screen drag-and-drop file uploader. Use react-dropzone. Show accepted formats: CSV, Excel, JSON, Parquet. During upload show a progress bar using axios onUploadProgress. On success, redirect to /session/[session_id]/configure. On error show a shadcn/ui Alert. Style: dark theme, monospace font, engineering aesthetic."

**AI Engineer:**
- Build the `DuckDBEngine` class:
  ```python
  # backend/core/duckdb_engine.py
  
  class DuckDBEngine:
      def __init__(self):
          self.conn = duckdb.connect(":memory:")
          self.conn.execute("INSTALL httpfs; LOAD httpfs;")
          self.conn.execute("INSTALL parquet; LOAD parquet;")
          self.current_table: Optional[str] = None
      
      def load_from_supabase(self, signed_url: str, file_type: str) -> Dict:
          """Load file from Supabase Storage signed URL into DuckDB."""
          ...
      
      def get_schema_profile(self) -> str:
          """Returns DESCRIBE + sample for prompt construction."""
          ...
      
      def get_statistical_summary(self) -> str:
          """Returns SUMMARIZE output formatted for Cleaning Agent prompt."""
          ...
      
      def execute_validated(self, sql: str) -> Dict:
          """Execute SQL with error catching. Returns {success, data, error, row_count, ms}."""
          ...
      
      def write_to_parquet(self, output_path: str) -> str:
          """Write current table to Parquet, return path."""
          ...
  ```

---

### Day 4 — Intent Router Node

**AI Engineer:**
- Implement the Router Node as a LangGraph node function:
  ```python
  # backend/agents/router_node.py
  
  async def router_node(state: AgentSwarmState) -> AgentSwarmState:
      ctx = slice_context(state, "router")
      response = await llm_router.route(
          task_type=TaskType.INTENT_ROUTING,
          messages=[
              {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
              {"role": "user",   "content": format_router_user_msg(ctx)}
          ],
          max_tokens=256
      )
      parsed = parse_json_response(response["content"])
      return {
          **state,
          "intent_class":     parsed["intent"],
          "business_domain":  parsed["domain"],
          "key_entities":     parsed["key_entities"],
          "time_dimension":   parsed.get("time_dimension"),
          "pipeline_status":  "routing",
          "current_agent":    "router",
      }
  ```
- Write the `ROUTER_SYSTEM_PROMPT` (from Agent 4 contract above)
- **Evaluation protocol:** Create `tests/test_router.py` with 25 test cases:
  - 5 finance queries (root_cause, trend, comparison)
  - 5 sales queries
  - 5 HR/operations queries
  - 5 ecommerce queries
  - 5 ambiguous/edge case queries
  - Measure: accuracy rate, latency (target <800ms), token usage
  - Minimum passing bar: 88% correct classification

**Full-Stack Dev:**
- Build `/api/pipeline/start` FastAPI endpoint
- Build the `POST /api/pipeline/{session_id}/start` that: creates session, loads file to DuckDB, kicks off LangGraph execution asynchronously (Celery task or asyncio background task)
- Build SSE endpoint `GET /api/pipeline/{session_id}/stream` that polls Redis for status updates and streams them to frontend

---

### Day 5 — DuckDB Integration & Schema Profile

**AI Engineer:**
- Complete `DuckDBEngine` implementation from Day 3
- Build `SchemaProfiler` utility that transforms DuckDB `DESCRIBE` output into the prompt-ready table format
- Write comprehensive tests with 6 file types: clean CSV, messy CSV (mixed types), Excel (.xlsx with merged cells), JSON (nested), Parquet (clean), JSON Lines
- Benchmark: target < 5 seconds to load and profile a 50MB CSV

**Full-Stack Dev:**
- Build `/session/[session_id]/configure` page in Next.js
- This page shows: file name, row/col count, detected file type, and a live-updating status indicator for the pipeline
- **Vibe coding prompt:**
  > "Generate a Next.js 14 page at /session/[sessionId]/configure. It should connect to a Server-Sent Events endpoint at /api/pipeline/{sessionId}/stream. Display: a status stepper component showing 6 steps (Upload, Schema Analysis, Cleaning, Features, Queries, Dashboard). The active step animates. Show a live text log of agent activities. When status becomes 'complete', show a 'View Dashboard' button. Use Tailwind, shadcn/ui Stepper, and Zustand for state."

---

### Days 6–7 — Integration Test: Upload to Schema Display

**Both:**
- End-to-end integration test: Upload 3 different CSV files → DuckDB load → Router node runs → Schema profile generated → Displayed in UI
- Fix: file type detection bugs, Supabase Storage auth errors, DuckDB memory limits, SSE connection drops
- Performance target: under 20 seconds from upload click to schema visible on screen
- Document all bugs found and fixes applied

---

### Days 8–9 — LLMRouter & Token Budget System

**AI Engineer:**
- Complete full `LLMRouter` class with all three provider callers (Groq, Gemini, Ollama)
- Implement `DailyUsageTracker` backed by Upstash Redis
- Implement circuit breaker: if a provider hits 95% daily limit → mark exhausted in Redis for remainder of day
- Implement response parser: `parse_json_response()` that handles:
  - Clean JSON: parse directly
  - JSON wrapped in markdown fences: strip and parse
  - Partial JSON: attempt repair with `json-repair` library
  - Complete failure: return structured error dict
- Unit test `LLMRouter` with mocked provider calls
- **Token Budget Dashboard:** Simple terminal script that prints daily usage per provider, available remaining budget, estimated remaining sessions

**Full-Stack Dev:**
- Build PostgreSQL async layer using SQLAlchemy 2.0 async:
  > "Generate a complete async SQLAlchemy 2.0 database layer for these tables: sessions, pipeline_states, dashboards, schema_embeddings. Include: get_session(id), create_session(user_id, file_path, file_type), update_session_status(id, status), save_pipeline_checkpoint(session_id, agent_name, state_json, tokens_used), get_latest_checkpoint(session_id), save_dashboard(session_id, config_json, title). Use asyncpg as the driver. All functions must be async. Include a database connection pool manager."

---

### Days 10–12 — API Layer & Real-Time Streaming

**Full-Stack Dev:**
- Complete FastAPI router structure:
  - `POST /api/upload` → file upload
  - `POST /api/pipeline/{session_id}/start` → kick off pipeline
  - `GET /api/pipeline/{session_id}/stream` → SSE
  - `GET /api/pipeline/{session_id}/state` → current state snapshot
  - `GET /api/dashboards/{session_id}` → final dashboard config
  - `GET /api/dashboards/{session_id}/query/{query_id}/data` → fetch result from Supabase Storage
- Implement Upstash Redis pub/sub: each agent node publishes a status event after completion; SSE endpoint subscribes and forwards to client
- CORS configuration for Next.js ↔ FastAPI

**AI Engineer:**
- Build `EmbeddingEngine` class using Ollama's `nomic-embed-text`:
  ```python
  class EmbeddingEngine:
      async def embed_schema(self, column_metadata: List[ColumnMeta]) -> List[float]:
          """Generate 768-dim embedding from schema fingerprint"""
          schema_text = " ".join([f"{c['name']}:{c['semantic_type']}" for c in column_metadata])
          # POST to ollama /api/embeddings with nomic-embed-text
          ...
      
      async def find_similar_schema(self, embedding: List[float], threshold: float = 0.92) -> Optional[Dict]:
          """Query pgvector for similar schemas. If found, reuse their cleaned pipeline."""
          ...
  ```
- This is the **schema cache** — if a user uploads data with the same schema shape as a previous session (>92% cosine similarity), skip cleaning/feature steps and reuse cached results.

---

### Days 13–14 — Sprint 1 Review & Refinement

**Both:**
- End-to-end test with 10 different real-world CSV datasets
- Must pass: upload → schema display in under 25 seconds for all 10
- Code review: ensure state contract is being respected (no agent writing to another's namespace)
- Write `README.md` for the repository covering environment setup
- Commit working sprint state to git with tag `sprint-1-complete`

---

## SPRINT 2: "THE BRAIN STEM" (Days 15–28)
**Goal:** Ingestion Agent + Cleaning Agent fully operational within LangGraph.
**Deliverable:** Upload a messy CSV → auto-cleaned Parquet stored → quality score displayed.

---

### Days 15–16 — Ingestion Agent — Full Implementation

**AI Engineer:**
- Implement complete `ingestion_node()` LangGraph function
- The node must:
  1. Load file into DuckDB via `DuckDBEngine.load_from_supabase()`
  2. Run `get_schema_profile()` → get compressed schema table
  3. Check pgvector for similar schema via `EmbeddingEngine.find_similar_schema()`
  4. If similar schema found (>92% similarity): set `similar_schemas_found=True`, copy that schema's `column_metadata` — skip LLM call
  5. If not found: call `LLMRouter.route(TaskType.SCHEMA_INFERENCE, ...)` with the system prompt from Section 2.4
  6. Parse response → validate each `ColumnMeta` object via Pydantic
  7. Store schema embedding in pgvector
  8. Write updated state back
- **Evaluation:** Test against 15 CSV files spanning 5 domains. Score accuracy of `semantic_type` classification.
  - Target: 90%+ semantic type accuracy, 85%+ business label quality (human judgment)
  - Failure mode to test: single-column CSV, 200-column CSV, all-null column, date columns in 6 different formats, currency columns (USD, EUR, INR)

**Full-Stack Dev:**
- Build the schema display UI component for `/session/[sessionId]/configure`:
  > "Generate a React component called SchemaViewer. It displays a list of ColumnMeta objects as an interactive table. Each row shows: column name, detected type badge (color-coded: green=metric, blue=dimension, orange=identifier, purple=date), business label (editable inline via shadcn/ui Input on click), null percentage as a thin progress bar, semantic type as a dropdown (user can override). Selecting a column shows sample values in a side panel. Changes to semantic_type or business_label update a local edited state and enable a 'Confirm Schema & Continue' button."

---

### Days 17–18 — Cleaning Agent — Strategy Generation

**AI Engineer:**
- Implement the strategy generation half of `cleaning_node()`:
  1. Run `DuckDBEngine.get_statistical_summary()` → compact SUMMARIZE output
  2. Build Cleaning Agent prompt using `compress_column_meta_for_prompt()`
  3. Route to `LLMRouter.route(TaskType.CLEANING_STRATEGY, ...)`
  4. Parse response → validate all `CleaningOperation` objects
  5. Log each operation to state
- **Critical evaluation:** Test with datasets specifically designed to break cleaning:
  - 30% null rate in a key column
  - Date column with 5 different formats in same column
  - Numeric column stored as string ("$1,234.00", "1.2K", "N/A")
  - Duplicate rows (partial and exact)
  - Mixed encodings (UTF-8 / Latin-1 in same file)

**Full-Stack Dev:**
- Build cleaning progress UI: shows each cleaning operation as it completes
  - Operation type icon (wrench, scissors, wand)
  - Column name, strategy applied, rows affected
  - Real-time via SSE

---

### Days 19–21 — Cleaning Agent — Code Generation & Execution

**AI Engineer:**
- Implement the code generation + execution half of `cleaning_node()`:
  1. For each `CleaningOperation` from strategy step, call `LLMRouter.route(TaskType.CODE_GENERATION, ...)` with Ollama Qwen2.5-Coder as primary
  2. Prompt: "Generate a Polars expression to apply this CleaningOperation to a Polars LazyFrame called `lf`. Operation: {operation}. Column: {column}. Strategy: {strategy}. Return ONLY the Python expression using pl.col() notation. The expression will be used as: `lf = lf.with_columns([YOUR_EXPRESSION])`"
  3. Execute generated Polars code in a sandboxed subprocess (use `RestrictedPython` or simple `exec()` with allowlisted imports: `polars`, `re`, `datetime` only)
  4. Validate: `rows_before` and `rows_after` counts match expected `rows_affected`
  5. If validation fails: retry with a repair prompt ("That expression failed with error: {error}. Fix it.")
  6. After all operations: write cleaned DataFrame as Parquet to Supabase Storage
  7. Calculate `data_quality_score`: weighted average of completeness, uniqueness, type consistency

**Full-Stack Dev:**
- Build data quality score UI: a circular gauge component showing 0–100% quality score
- Show breakdown: completeness, uniqueness, type consistency sub-scores
- Show before/after row counts

---

### Days 22–23 — End-to-End: Ingestion + Cleaning Pipeline Test

**Both:**
- Batch test: 20 real-world "messy" datasets (find on Kaggle: sales, HR, financial, IoT datasets with known issues)
- For each dataset: track quality score before/after, operations applied, total LLM tokens used, total wall-clock time
- Target: average quality score improvement of +25 points, total pipeline time < 45 seconds for <50MB files
- Fix top 3 failure modes found

---

### Days 24–25 — LangGraph Checkpoint Testing

**AI Engineer:**
- Test LangGraph checkpoint/resume behavior:
  1. Start a pipeline run
  2. Kill the FastAPI process mid-cleaning (simulate server crash)
  3. Restart and call `pipeline.resume(session_id)` — must continue from last checkpoint
  4. Verify: no re-running of completed nodes, correct state restored
- Implement exponential backoff retry for failed LLM calls (max 3 retries, 1s/2s/4s delays)
- Implement the `error_recovery_node`: if any agent fails 3 times, set `pipeline_status="failed"` and send user a `errors` summary with `recoverable: true/false` flags

**Full-Stack Dev:**
- Build error recovery UI: if pipeline fails, show: which agent failed, what was attempted, "Retry" button, "Start Over" button
- "Retry" calls `POST /api/pipeline/{session_id}/resume` which resumes from last checkpoint

---

### Days 26–28 — Sprint 2 Polish & Integration

**Both:**
- Performance profiling: profile the full ingestion+cleaning pipeline with `py-spy`
- Identify bottlenecks (suspect: Polars code execution in subprocess, Supabase upload)
- Token usage audit: calculate average tokens per session so far, project daily capacity
- Commit with tag `sprint-2-complete`

---

## SPRINT 3: "THE CORTEX" (Days 29–42)
**Goal:** Feature Architect + Analytics Engine agents operational. DuckDB executing LLM-generated SQL.
**Deliverable:** Given a business question + cleaned data, pipeline generates and executes 5+ relevant SQL queries.

---

### Days 29–30 — Feature Architect Agent

**AI Engineer:**
- Implement `feature_node()` LangGraph function:
  1. Build prompt: slice context (columns, cleaning ops, intent, domain, time dimension)
  2. Route to Gemini 2.0 Flash for feature ideation (`TaskType.FEATURE_IDEATION`)
  3. Parse `FeatureDefinition[]` from response
  4. For each feature: route to Ollama Qwen2.5-Coder for Polars expression generation (`TaskType.CODE_GENERATION`)
  5. Validate each Polars expression compiles without executing (use `polars.Expr` introspection)
  6. Execute all valid features, write enriched Parquet to Supabase Storage
- **Specific features to verify the agent generates correctly for a sales dataset:**
  - `revenue_per_unit` (metric / count)
  - `revenue_growth_pct` (period-over-period % change)
  - `days_to_close` (date difference)
  - `is_high_value_customer` (threshold boolean)
  - `rolling_30d_revenue` (window function)

**Full-Stack Dev:**
- Build feature list UI: shows each derived feature, its formula, and expected insight
- User can toggle individual features on/off before proceeding
- "Run with selected features" button

---

### Days 31–33 — Analytics Engine Agent — SQL Generation

**AI Engineer:**
- Implement `analytics_node()` LangGraph function (SQL generation phase):
  1. Build comprehensive Analytics Agent prompt using:
     - Compressed column metadata (original + derived features)
     - Raw user question
     - Intent classification
     - Time dimension if present
  2. Route to Groq llama-3.3-70b for SQL generation (`TaskType.QUERY_DESIGN`)
  3. The agent should generate 5–8 SQL queries targeting different analytical angles:
     - 1 primary KPI answer (directly answers the user question)
     - 2 supporting trend queries
     - 2 breakdown/segmentation queries
     - 1 anomaly detection query
     - 1 comparative benchmark query
  4. Parse `QueryDefinition[]` from response
  5. Validate each SQL: `DuckDB.execute_validated()` with `EXPLAIN` prefix (no data scan, just parse/plan validation)
  6. If SQL fails validation: route to `TaskType.QUERY_REPAIR` with error message for self-correction

**Full-Stack Dev:**
- Build SQL preview UI: shows each generated query with syntax highlighting (use `react-syntax-highlighter`)
- User can edit SQL inline before execution
- "Execute Queries" button

---

### Days 34–36 — Analytics Engine Agent — DuckDB Execution

**AI Engineer:**
- Implement execution phase of `analytics_node()`:
  1. Execute each validated SQL against DuckDB (loaded from enriched Parquet)
  2. For each result: write to Supabase Storage as `result_{query_id}.parquet`
  3. Store in state: `QueryResult` (summary only — 5 rows, column names, row count, execution time)
  4. Handle: query timeout (10s max), memory limit (512MB), empty result set
  5. If query fails execution (not just validation): attempt 1 auto-repair via LLM, then flag as failed
- **SQL patterns to test the agent generates correctly:**
  - GROUP BY with aggregate (SUM, AVG, COUNT)
  - Window functions (LAG, LEAD, RANK, ROW_NUMBER)
  - DATE_TRUNC for time series
  - CASE WHEN for conditional buckets
  - Multiple JOINs (test with multi-table uploads — future sprint)
  - Subqueries and CTEs

**Full-Stack Dev:**
- Build query results preview: table component showing first 20 rows of each query result
- Execution time badge per query
- Error state for failed queries with the error message

---

### Days 37–39 — Query Self-Correction Loop

**AI Engineer:**
- Implement the retry loop in `analytics_node()`:
  1. After first SQL generation pass, collect all failed queries
  2. If any failed: build repair prompt: "The following DuckDB SQL query failed with this error. The table is called 'data' with these columns. Fix the SQL: {error} | {original_sql} | Available columns: {columns}"
  3. Route repair to Gemini Flash (better at error analysis)
  4. Attempt repaired SQL — maximum 2 repair cycles
  5. After 2 cycles: mark query as `permanently_failed`, continue with remaining queries
  6. User sees: 5 successful queries + 1 failed query with error explanation
- **Edge cases to test:**
  - Column name with spaces (must be quoted in DuckDB: `"column name"`)
  - Date arithmetic in DuckDB (INTERVAL syntax differs from standard SQL)
  - Numeric overflow (SUM on large integers)
  - Division by zero (must add NULLIF)

**Full-Stack Dev:**
- Build "insights cards" UI: below each query result table, show the `insight_summary` from `QueryDefinition`
- Real-time execution progress: show queries executing one by one with a loading state

---

### Days 40–42 — Sprint 3 Integration Test

**Both:**
- Full pipeline test: Upload → Ingest → Clean → Feature → Analytics with 15 diverse datasets
- Benchmark: average number of successful queries per session, average execution time
- Token usage report: calculate exact cost per session if using paid APIs (for future pricing)
- Target: ≥4 successful queries per session, ≤5% SQL failure rate after repair
- Commit with tag `sprint-3-complete`

---

## SPRINT 4: "THE FACE" (Days 43–56)
**Goal:** Layout Agent generates ECharts configs. Next.js renders interactive BI dashboard. Full E2E pipeline.
**Deliverable:** Single-prompt → fully interactive BI dashboard in the browser.

---

### Days 43–44 — Layout Agent — Chart Type Classification

**AI Engineer:**
- Implement `layout_node()` — Phase 1: chart type selection per query
  - Route to Groq llama-3.1-8b-instant (`TaskType.CHART_SELECTION`)
  - Prompt: "Given query title, x/y axis types, row count, and group_by column, classify the optimal chart type. Return ONLY one of: bar, line, scatter, area, pie, heatmap, kpi_card, data_table"
  - Rules in prompt:
    - `kpi_card` if the query returns a single aggregate value (1 row, 1 column)
    - `line` if x_axis is temporal
    - `pie` only if there are ≤7 distinct groups and the data is compositional
    - `scatter` if two numeric columns, no grouping
    - `heatmap` if both x and y are categorical
    - `bar` is the safe default
  - Test: 15 different query shapes to validate chart type accuracy

**Full-Stack Dev:**
- Set up ECharts in Next.js:
  ```bash
  npm i echarts echarts-for-react
  ```
- Build `DynamicChart` component:
  > "Generate a React component called DynamicChart. It accepts an `echartsOption` prop (the full ECharts option object) and a `chartType` prop. It renders an echarts-for-react instance with automatic height based on chartType (kpi_card: 120px, data_table: 400px, all others: 300px). Attach an onChartReady callback that stores the chart instance. Include a download-as-PNG button in the top-right corner that calls chart.getDataURL(). On error, show a fallback placeholder."

---

### Days 45–47 — Layout Agent — ECharts Config Generation

**AI Engineer:**
- Implement `layout_node()` — Phase 2: full ECharts `option` object generation
  - Route to Gemini 2.0 Flash (`TaskType.ECHARTS_CONFIG`)
  - Use the ECharts prompt from Section 2.4 Agent 4
  - For each query: pass `QueryDefinition` + `QueryResult.sample_rows` (5 rows) + determined `chart_type`
  - Parse returned JSON as `ChartConfig.echarts_option`
  - Validate: must contain `title`, `series` array, `tooltip`; for time-series must have `xAxis.type: "time"`
  - Grid layout assignment: prioritize primary KPI (priority=1) → full-width at top; supporting queries → 2-column grid; detail queries → below fold
- **Evaluation test set — 8 chart types, each must render correctly:**
  - Line chart with 3 series and time x-axis
  - Stacked bar chart with 5 groups
  - KPI card with large number formatting (1.2M not 1200000)
  - Scatter plot with tooltip showing 2 dimensions
  - Horizontal bar chart (sorted descending)
  - Pie chart (max 7 slices, "Others" aggregate)
  - Heatmap with categorical x and y
  - Data table with sortable columns

**Full-Stack Dev:**
- Build the dashboard grid layout system:
  > "Generate a React component called DashboardGrid. It accepts an array of ChartConfig objects. Each ChartConfig has a layout property {x, y, w, h} in a 12-column grid. Use react-grid-layout (or CSS Grid if simpler) to render charts in their assigned positions. Charts should be draggable and resizable by the user. Save layout changes to local state. Show a 'Save Layout' button that calls PUT /api/dashboards/{sessionId}/layout. Title and subtitle render above each chart."

---

### Days 48–50 — Full Dashboard Page

**Full-Stack Dev:**
- Build `/dashboard/[sessionId]` page:
  - Fetch `dashboard_config` from `GET /api/dashboards/{sessionId}`
  - For each `ChartConfig`: fetch actual data from `GET /api/dashboards/{sessionId}/query/{queryId}/data`
  - Pass `{echarts_option, data}` to `DynamicChart`
  - Dashboard header: title, subtitle, "Share" button, "Export PDF" button, "Ask Another Question" input
  - Responsive: stacks to single column on mobile
- **Vibe coding prompt for the dashboard page:**
  > "Generate a complete Next.js 14 dashboard page at /dashboard/[sessionId]. It should: 1) fetch dashboard config from an API on mount, 2) show a skeleton loading state for each chart while data loads, 3) render charts in a responsive 12-column CSS grid based on layout positions, 4) include a sticky top header with: dashboard title, a search-style input pre-filled with the original question, a 'Refine' button that opens a sidebar for a follow-up question, and export buttons, 5) add a bottom panel showing data quality score and pipeline summary stats. Dark theme."

**AI Engineer:**
- Test ECharts config quality across all 8 chart types generated by the Layout Agent
- Specific issue to test: ECharts configs generated by LLMs often have wrong `series.data` format — validate the config before passing to frontend
- Build `EChartsValidator` utility that checks:
  - `series` is an array (LLMs sometimes return a single object)
  - Time-series `data` is in `[timestamp, value]` format not `{name, value}`
  - Pie chart `data` is `[{name, value}]` not column arrays
  - No undefined or NaN values in data arrays

---

### Days 51–52 — "Follow-Up Question" Feature

**AI Engineer:**
- Implement the follow-up question flow: user types a new question while on the dashboard
- This creates a new pipeline run but with the SAME cleaned/enriched Parquet from the original session — skip Ingestion and Cleaning nodes, jump directly to Feature Architect and Analytics Engine
- This is the "Incremental Query" flow:
  ```python
  async def start_incremental_query(session_id: str, new_question: str) -> str:
      existing_state = await get_latest_state(session_id)
      new_session = await create_derived_session(parent_session_id=session_id, query=new_question)
      # Start LangGraph from "feature" node, not "router"
      await pipeline.ainvoke(
          {**existing_state, "session_id": new_session.id, "raw_query": new_question},
          config={"configurable": {"thread_id": new_session.id, "start_node": "feature"}}
      )
      return new_session.id
  ```

**Full-Stack Dev:**
- Build the follow-up question sidebar in the dashboard
- On submit: POST to `/api/pipeline/{sessionId}/follow-up` with `{question}`
- Open a mini-pipeline progress panel showing only analytics + layout steps
- When complete: new charts slide into the dashboard above existing ones

---

### Days 53–56 — Full E2E System Test & Sprint Review

**Both:**
- Complete end-to-end test with 5 real business scenarios:
  1. Sales data (CSV, 50K rows): "Why did revenue drop in Q3?"
  2. HR data (Excel, 5K rows): "What is our employee churn risk by department?"
  3. E-commerce (JSON, 200K rows): "Which product category has the best margin?"
  4. Finance (Parquet, 100K rows): "Show me monthly cash flow trends with anomalies"
  5. IoT sensors (CSV, 500K rows): "Which machines show abnormal readings?"
- For each: measure total pipeline time, quality score, number of charts, user satisfaction (subjective review of chart relevance)
- Target: <90 seconds E2E for <100MB files, ≥4 relevant charts per dashboard
- Commit with tag `sprint-4-complete`

---

## SPRINT 5: "STRESS TESTING" (Days 57–70)
**Goal:** QA Agent + comprehensive edge case coverage. System handles chaos gracefully.
**Deliverable:** Pipeline handles 50 diverse/messy datasets with <5% catastrophic failure rate.

---

### Days 57–58 — QA & Verification Agent Implementation

**AI Engineer:**
- Implement `qa_node()`:
  1. Slice full state for QA context
  2. Validate SQL for injection patterns (regex blocklist: `DROP`, `DELETE`, `INSERT`, `UPDATE`, `CREATE`, `EXEC`, `xp_`)
  3. Cross-check: do the chart titles and insights relate to `raw_query`? (semantic similarity via embeddings)
  4. Route to Groq 70B for fast validation checks (`TaskType.QA_VALIDATION`)
  5. Route to Gemini Flash for comprehensive QA report (`TaskType.QA_REPORT`)
  6. Determine `approval_status`: approved / needs_review / rejected
  7. If `needs_review`: flag in state but still return dashboard (with a "Review" badge)
  8. If `rejected`: block dashboard, return error with specific reason
- Implement `qa_router()` conditional edge function: approved → END, needs_review → END, rejected → analytics (1 retry) → END

**Full-Stack Dev:**
- Build QA badge in dashboard header: green "Verified", yellow "Needs Review", red "Failed"
- "View QA Report" button → expandable panel showing scores, anomalies, suggestions
- If `needs_review`: show a yellow banner "Some insights may need validation. Check the QA report."

---

### Days 59–63 — The 50-Dataset Edge Case Gauntlet

**AI Engineer (leads this phase):**
Build a test automation harness (`tests/gauntlet/`) that runs 50 datasets through the full pipeline and records:
- Pipeline completion status (success / partial / failed)
- Data quality score
- Number of queries generated (success/fail)
- Number of charts rendered
- QA approval status
- Total tokens used per provider
- Wall-clock time

**The 50 datasets must include:**
- 10 "perfect" datasets: clean, well-labeled, single domain
- 10 "messy basics": nulls, duplicates, wrong types, inconsistent dates
- 5 "encoding nightmares": mixed UTF-8/Latin-1, BOM markers, Windows line endings
- 5 "schema ambiguity": column names like "col1", "data", "value", "x1"
- 5 "extreme sizes": 1-row, 3-column; 1M-row, 2-column; 50-column, 100-row
- 5 "domain mixtures": sales + HR combined, financial + operational
- 5 "temporal complexity": multiple date columns, fiscal years, quarterly labels
- 5 "numeric extremes": all zeros, extreme outliers (1e15), negative values where impossible

**Fix the top 10 failures found. This is the most valuable sprint.**

**Full-Stack Dev:**
- Build admin page at `/admin/gauntlet` showing the gauntlet results dashboard
- Session list with color-coded status, quality scores, token usage sparklines
- Click into any session to see its full pipeline state for debugging

---

### Days 64–66 — Performance Optimization

**AI Engineer:**
- Profile LLM call latency per agent:
  - If Ingestion Agent > 8s: add schema compression caching layer
  - If Cleaning Agent > 15s: parallelize CleaningOperation generation (batch LLM calls)
  - If Analytics Agent > 20s: parallelize independent SQL queries in asyncio.gather()
- Implement async parallel execution for independent operations:
  ```python
  # Run SQL generation for all queries in parallel
  query_tasks = [generate_single_query(query_def) for query_def in query_definitions]
  results = await asyncio.gather(*query_tasks, return_exceptions=True)
  ```
- Implement result caching: if the same schema + same question were asked before, return cached dashboard (TTL: 24 hours, Redis-backed)

**Full-Stack Dev:**
- Implement frontend performance optimizations:
  - Chart lazy loading: only render charts in the viewport
  - Result data streaming: for large result sets, use chunked loading
  - Service Worker for offline dashboard caching
  - Dashboard load time target: < 3 seconds after pipeline completes

---

### Days 67–70 — Sprint 5 Polish & Hardening

**Both:**
- Gauntlet re-run: all 50 datasets again after fixes
- New target: <3% catastrophic failure rate, >80% QA approval rate
- Security review: all user-uploaded files are scanned with `python-magic` for MIME type validation; no file is executed directly
- Commit with tag `sprint-5-complete`

---

## SPRINT 6: "PRODUCTION HARDENING" (Days 71–84)
**Goal:** Multi-tenancy, authentication, monitoring, usage limits, deployment.
**Deliverable:** Production SaaS MVP on custom domain with Supabase Auth gating access.

---

### Days 71–73 — Authentication & Multi-Tenancy

**Full-Stack Dev:**
- Implement Supabase Auth: email/password + Google OAuth
  > "Generate a Next.js 14 auth flow using @supabase/ssr. Pages: /login (email + Google OAuth), /register, /verify-email. After login, redirect to /dashboard. Protect all /dashboard/* and /session/* routes with middleware.ts. On the server, use createServerClient to validate JWT on every API request. Include a logout button in the nav. Style: minimal, dark."
- All FastAPI endpoints: add `Depends(verify_supabase_jwt)` middleware
- Row-Level Security is already set up from Day 2 — validate it works with multiple test users

**AI Engineer:**
- Implement per-user rate limiting:
  - Free tier: 3 pipeline runs per day, max file size 25MB, max 3 charts per dashboard
  - Limits stored in Redis: `rate_limit:{user_id}:{date}` → integer
  - When limit hit: return HTTP 429 with `{limit_type, resets_at}` in body
- Implement the limit-check as a LangGraph entry guard node before Router

---

### Days 74–76 — Monitoring & Observability

**AI Engineer:**
- Implement structured logging using `loguru`:
  - Every LLM call: log `{provider, model, task_type, tokens_in, tokens_out, latency_ms, success}`
  - Every agent completion: log `{session_id, agent, wall_time_ms, status}`
  - Every pipeline completion: log full session summary
- Build a Prometheus metrics endpoint at `/metrics`:
  - `pipeline_runs_total{status}` counter
  - `llm_tokens_used_total{provider, model}` counter
  - `pipeline_duration_seconds{agent}` histogram
  - `data_quality_score_avg` gauge
- Build admin dashboard at `/admin/metrics` (protected, admin-only):
  - Daily pipeline runs chart
  - Token budget remaining per provider
  - Average pipeline time trend
  - Top failure reasons

**Full-Stack Dev:**
- Implement error boundary in Next.js: all dashboard pages wrapped in React Error Boundary
- Implement Sentry (free tier): capture all unhandled frontend + backend errors
- Build the user's personal usage dashboard at `/account/usage`:
  - Runs used today / daily limit
  - Total dashboards created
  - List of recent sessions with status and time-to-complete

---

### Days 77–79 — Dashboard Sharing & Export

**Full-Stack Dev:**
- Implement dashboard sharing:
  - `PUT /api/dashboards/{sessionId}/publish` → sets `published=true`, generates `share_token`
  - Public URL: `/share/{shareToken}` → read-only dashboard view, no auth required
  - Share dialog: copy link, preview image (screenshot via Puppeteer — use `@sparticuz/chromium` for serverless)
- Implement PDF export:
  > "Generate a Next.js API route at /api/export/pdf that accepts a sessionId, uses @sparticuz/chromium + puppeteer-core to screenshot the dashboard page (set viewport to 1440x900), and returns the PDF as a binary response. The dashboard page must have a ?print=true query param mode that hides the navbar and shows all charts expanded."

**AI Engineer:**
- Implement the dashboard "Explanation Panel":
  - Route to Gemini Flash: "Given this dashboard with {N} charts answering '{raw_query}', write a 3-paragraph executive summary of the key insights. Be specific about numbers. Write for a non-technical business audience."
  - Rendered in a collapsible panel below the dashboard
  - This is a key differentiator from standard BI tools

---

### Days 80–82 — Database Connection Feature (MVP)

**AI Engineer:**
- Implement PostgreSQL direct connection:
  - User provides: `host`, `port`, `database`, `user`, `password`
  - FastAPI endpoint: `POST /api/connections/test` → validates connection, returns schema list
  - On success: generate DuckDB view over PostgreSQL using DuckDB's postgres scanner extension:
    ```python
    self.conn.execute(f"""
      ATTACH 'dbname={db} host={host} user={user} password={pw}' AS pg_source (TYPE postgres);
    """)
    ```
  - This allows the same DuckDB analytics engine to work on live databases
  - Store connection credentials encrypted in Supabase (use `cryptography.fernet` symmetric encryption)

**Full-Stack Dev:**
- Build "Connect Database" wizard UI: 4-step form (connection type → credentials → table selection → confirm)
- Connection health indicator: green dot if connected, red if unreachable
- Show live query execution count when using database connection

---

### Days 83–84 — Launch Prep & Final Hardening

**Both:**
- Load test: simulate 10 concurrent users running pipelines simultaneously
  - Use `locust` for load testing
  - Target: no crashes, graceful degradation when rate limits hit
- Security audit checklist:
  - SQL injection blocked by QA Agent + regex blocklist
  - File upload: MIME validation, size limit, path traversal prevention
  - API: JWT validation on all endpoints
  - CORS: restricted to your domain only
  - Environment variables: never logged, never returned in API responses
- Configure Railway production deployment: set all env vars, set up health check, configure auto-restart
- Configure Vercel production: set env vars, enable Edge Config for feature flags
- Set up domain: point custom domain to Vercel
- Write landing page copy and deploy at root domain
- Commit with tag `sprint-6-complete`

---

## LAUNCH WEEK (Days 85–90)

**Day 85:** Deploy to production. Smoke test all critical paths.

**Day 86:** Invite 5–10 beta users from personal network. Provide onboarding doc with 3 sample CSV files and 3 pre-written business questions to test.

**Days 87–88:** Monitor logs. Fix top 5 bugs found by beta users. Each bug: <4 hour fix cycle.

**Day 89:** Interview 3 beta users. Document: what confused them, what delighted them, what they tried that broke it.

**Day 90:** Review all metrics: pipeline success rate, avg quality score, avg time-to-dashboard, token budget utilization. Write 30-day roadmap for Month 2 based on beta feedback.

---

# SECTION 4: FUTURE-PROOFING — THE PLUGIN ARCHITECTURE

## 4.1 The BaseAgent Contract — How to Add Any Agent in <1 Day

Every agent in your system — including future agents like PowerBI Integration or Predictive Forecasting — must implement this abstract base class. This is the contract that makes the system modular.

```python
# backend/core/base_agent.py

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseAgent(ABC):
    """
    All agents in the swarm implement this contract.
    Adding a new agent = subclassing BaseAgent + registering in AgentRegistry.
    No other changes required.
    """
    
    # ── Identity ──────────────────────────────────────────────────────────────
    name: str            # Unique agent identifier (e.g., "powerbi_integration")
    version: str         # Semantic version (e.g., "1.0.0")
    requires_state_fields: List[str]   # Fields this agent reads from state
    writes_state_fields: List[str]     # Fields this agent writes to state
    
    # ── Configuration ─────────────────────────────────────────────────────────
    primary_task_type: TaskType       # Primary LLM routing task
    max_retries: int = 3
    timeout_seconds: int = 120
    
    @abstractmethod
    async def execute(self, state: AgentSwarmState) -> AgentSwarmState:
        """
        Main execution method. Must:
        1. Call slice_context(state, self.name) before building any LLM prompt
        2. Only write to fields listed in writes_state_fields
        3. Call self._checkpoint(state) after completion
        4. Handle errors and update state["errors"] if any
        """
        raise NotImplementedError
    
    @abstractmethod
    def validate_input(self, state: AgentSwarmState) -> tuple[bool, str]:
        """
        Validate that required state fields are populated before execution.
        Return (is_valid, error_message).
        """
        raise NotImplementedError
    
    async def _checkpoint(self, state: AgentSwarmState) -> None:
        """Persist current state to PostgreSQL. Called automatically by execute()."""
        await save_pipeline_checkpoint(
            session_id=state["session_id"],
            agent_name=self.name,
            state_json=state,
            tokens_used=state["token_usage"].get("total_session", 0)
        )
    
    async def _emit_progress(self, session_id: str, message: str) -> None:
        """Publish SSE progress event to Redis pub/sub channel."""
        await redis.publish(f"pipeline:{session_id}", json.dumps({
            "agent": self.name, "message": message, "timestamp": utcnow()
        }))


class AgentRegistry:
    """
    Central registry for all agents. Adding a new agent requires only:
    1. Subclass BaseAgent
    2. Call AgentRegistry.register(MyNewAgent())
    3. Add the LangGraph edge in graph_builder.py
    """
    _registry: Dict[str, BaseAgent] = {}
    
    @classmethod
    def register(cls, agent: BaseAgent) -> None:
        cls._registry[agent.name] = agent
    
    @classmethod
    def get(cls, name: str) -> BaseAgent:
        if name not in cls._registry:
            raise AgentNotFoundError(f"Agent '{name}' not registered. Available: {list(cls._registry.keys())}")
        return cls._registry[name]
    
    @classmethod
    def list_agents(cls) -> List[str]:
        return list(cls._registry.keys())
```

## 4.2 How to Add the PowerBI Integration Agent (Month 4)

**New State Fields to Add:**

```python
# Add to AgentSwarmState
powerbi_workspace_id:  Optional[str]
powerbi_dataset_id:    Optional[str]
powerbi_measures:      Optional[List[Dict]]  # DAX measures from semantic model
powerbi_dimensions:    Optional[List[Dict]]  # Dimension tables
powerbi_push_status:   Optional[str]         # "pending", "pushed", "failed"
```

**New Agent Implementation:**

```python
# backend/agents/powerbi_agent.py

class PowerBIIntegrationAgent(BaseAgent):
    name = "powerbi_integration"
    version = "1.0.0"
    requires_state_fields = ["dashboard_config", "query_results", "session_id"]
    writes_state_fields = ["powerbi_workspace_id", "powerbi_dataset_id", "powerbi_push_status"]
    primary_task_type = TaskType.CODE_GENERATION
    
    async def execute(self, state: AgentSwarmState) -> AgentSwarmState:
        ctx = slice_context(state, self.name)
        
        # Step 1: Convert query results to PowerBI Push Dataset schema
        pb_dataset = self._build_push_dataset(ctx["query_results"])
        
        # Step 2: Route to LLM to generate DAX measures equivalent to our SQL
        dax_response = await llm_router.route(
            TaskType.CODE_GENERATION,
            messages=[{
                "role": "system",
                "content": "Convert this DuckDB SQL to DAX measure syntax. Return only the DAX expression."
            }, {
                "role": "user",
                "content": str(ctx["generated_queries"])
            }]
        )
        
        # Step 3: Push to PowerBI via REST API using MSAL OAuth
        push_result = await self._push_to_powerbi(pb_dataset, dax_response["content"])
        
        return {
            **state,
            "powerbi_workspace_id": push_result["workspace_id"],
            "powerbi_dataset_id":   push_result["dataset_id"],
            "powerbi_push_status":  "pushed",
        }
    
    def validate_input(self, state):
        if not state.get("dashboard_config"):
            return False, "dashboard_config required before PowerBI push"
        return True, ""
```

**To wire into the pipeline — only 3 lines change:**

```python
# backend/core/graph_builder.py

# Line 1: Import and register
AgentRegistry.register(PowerBIIntegrationAgent())

# Line 2: Add node
graph.add_node("powerbi", AgentRegistry.get("powerbi_integration").execute)

# Line 3: Add conditional edge (only if user has PowerBI connected)
graph.add_conditional_edges(
    "qa",
    lambda state: "powerbi" if state.get("powerbi_workspace_id") else END
)
```

## 4.3 How to Add the Predictive Forecasting Agent (Month 4)

**New State Fields:**

```python
forecast_horizon_days:   Optional[int]
forecast_model_type:     Optional[Literal["prophet", "arima", "linear"]]
forecast_results:        Optional[List[Dict]]  # {date, predicted, lower_ci, upper_ci}
forecast_accuracy_metrics: Optional[Dict]     # {mae, rmse, mape}
```

**New Agent Implementation:**

```python
# backend/agents/forecasting_agent.py

class PredictiveForecastingAgent(BaseAgent):
    name = "forecasting"
    version = "1.0.0"
    requires_state_fields = ["enriched_parquet_path", "time_dimension", "column_metadata", "intent_class"]
    writes_state_fields = ["forecast_results", "forecast_accuracy_metrics", "forecast_horizon_days"]
    primary_task_type = TaskType.CODE_GENERATION
    
    async def execute(self, state: AgentSwarmState) -> AgentSwarmState:
        # Only runs if intent_class == "forecasting" or user explicitly requested it
        if state["intent_class"] != "forecasting":
            return state  # No-op pass-through
        
        ctx = slice_context(state, self.name)
        
        # Step 1: Route to Ollama to generate Prophet/ARIMA Python code
        forecast_code = await llm_router.route(
            TaskType.CODE_GENERATION,
            messages=[{
                "role": "system",
                "content": "Generate Python code using the Prophet library to forecast the target metric. The data is loaded as a Polars DataFrame from this path: {path}. The time column is '{time_col}' and the target column is '{target_col}'. Return a list of {horizon} future predictions as [{'ds': date, 'yhat': value, 'yhat_lower': lb, 'yhat_upper': ub}]."
            }]
        )
        
        # Step 2: Execute in sandboxed subprocess with prophet installed
        forecast_results = await self._execute_forecast_code(forecast_code["content"])
        
        # Step 3: Store results, update state
        return {**state, "forecast_results": forecast_results, ...}
```

## 4.4 Data Source Adapter Pattern — Plugging in New Sources

This is the pattern for adding new data sources (S3, BigQuery, Snowflake, REST APIs) without touching core pipeline logic:

```python
# backend/core/data_source_adapter.py

class DataSourceAdapter(ABC):
    """All data sources implement this. The DuckDB engine only calls this interface."""
    
    @abstractmethod
    async def to_duckdb(self, conn: duckdb.DuckDBPyConnection, table_name: str = "data") -> Dict:
        """Load data into DuckDB as a table. Return {row_count, col_count, schema}."""
        raise NotImplementedError
    
    @abstractmethod
    async def validate_connection(self) -> bool:
        raise NotImplementedError

# ── Built-in adapters ─────────────────────────────────────────────────────────

class SupabaseStorageAdapter(DataSourceAdapter):
    """For uploaded files (CSV, Excel, JSON, Parquet) stored in Supabase Storage."""
    async def to_duckdb(self, conn, table_name="data"):
        signed_url = await supabase.storage.from_("raw-uploads").create_signed_url(self.path, 3600)
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet('{signed_url}')")

class PostgreSQLAdapter(DataSourceAdapter):
    """For direct database connections."""
    async def to_duckdb(self, conn, table_name="data"):
        conn.execute(f"ATTACH '{self.connection_string}' AS pg_source (TYPE postgres)")
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM pg_source.{self.table_name}")

# ── Future adapters — just implement the interface ─────────────────────────────

class S3ParquetAdapter(DataSourceAdapter):         # Month 4
    ...

class BigQueryAdapter(DataSourceAdapter):           # Month 5
    ...

class RESTAPIAdapter(DataSourceAdapter):            # Month 5: pull from REST endpoints
    ...

class PowerBISemanticModelAdapter(DataSourceAdapter):  # Month 4
    ...
```

## 4.5 The LangGraph Node Registration Pattern — Month 4 Expansion

When you're ready to add Month 4 agents, the LangGraph graph is built dynamically from the `AgentRegistry`:

```python
# backend/core/graph_builder.py — Month 4 version

PIPELINE_SEQUENCE = [
    "router",
    "ingestion",
    "cleaning",
    "feature",
    "analytics",
    "layout",
    "qa",
    # ── Month 4 optional agents (conditionally inserted) ──
    "forecasting",        # Only if intent_class == "forecasting"
    "powerbi_integration", # Only if user has PowerBI connected
    "pdf_report",         # Only if user requests report export
]

def build_dynamic_graph(enabled_agents: List[str]) -> CompiledGraph:
    """Build the pipeline graph from the active agent list."""
    graph = StateGraph(AgentSwarmState)
    
    for agent_name in PIPELINE_SEQUENCE:
        if agent_name in enabled_agents or agent_name in CORE_AGENTS:
            agent = AgentRegistry.get(agent_name)
            graph.add_node(agent_name, agent.execute)
    
    # Wire edges based on active sequence
    active = [a for a in PIPELINE_SEQUENCE if a in enabled_agents or a in CORE_AGENTS]
    for i in range(len(active) - 1):
        graph.add_edge(active[i], active[i+1])
    
    graph.set_entry_point(active[0])
    return graph.compile(checkpointer=checkpointer)
```

---

# APPENDIX A: MASTER FILE STRUCTURE

```
multiagent-bi-engine/
├── backend/
│   ├── core/
│   │   ├── state.py               # AgentSwarmState + all TypedDicts
│   │   ├── base_agent.py          # BaseAgent ABC + AgentRegistry
│   │   ├── llm_router.py          # LLMRouter + token budget tracking
│   │   ├── context_slicer.py      # ContextSlicer utility
│   │   ├── schema_compressor.py   # Schema → prompt string compression
│   │   ├── duckdb_engine.py       # DuckDBEngine class
│   │   ├── embedding_engine.py    # nomic-embed-text + pgvector
│   │   ├── graph_builder.py       # LangGraph assembly
│   │   └── data_source_adapter.py # Adapter pattern for data sources
│   ├── agents/
│   │   ├── router_node.py
│   │   ├── ingestion_agent.py
│   │   ├── cleaning_agent.py
│   │   ├── feature_agent.py
│   │   ├── analytics_agent.py
│   │   ├── layout_agent.py
│   │   └── qa_agent.py
│   ├── api/
│   │   ├── upload.py              # File upload endpoint
│   │   ├── pipeline.py            # Pipeline start/resume/stream
│   │   ├── dashboards.py          # Dashboard CRUD + sharing
│   │   ├── connections.py         # DB connection management
│   │   └── admin.py               # Metrics + gauntlet (protected)
│   ├── prompts/
│   │   ├── router.txt
│   │   ├── ingestion.txt
│   │   ├── cleaning.txt
│   │   ├── feature.txt
│   │   ├── analytics.txt
│   │   ├── layout.txt
│   │   └── qa.txt
│   ├── tests/
│   │   ├── unit/                  # Per-agent unit tests
│   │   ├── integration/           # Full pipeline integration tests
│   │   └── gauntlet/              # 50-dataset stress test suite
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py                    # FastAPI app entry point
├── frontend/
│   ├── app/
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   └── register/page.tsx
│   │   ├── upload/page.tsx
│   │   ├── session/[sessionId]/
│   │   │   ├── configure/page.tsx
│   │   │   └── processing/page.tsx
│   │   ├── dashboard/[sessionId]/page.tsx
│   │   ├── share/[token]/page.tsx
│   │   ├── account/usage/page.tsx
│   │   └── admin/
│   │       ├── metrics/page.tsx
│   │       └── gauntlet/page.tsx
│   ├── components/
│   │   ├── charts/DynamicChart.tsx
│   │   ├── charts/DashboardGrid.tsx
│   │   ├── pipeline/StatusStepper.tsx
│   │   ├── pipeline/AgentProgressLog.tsx
│   │   ├── schema/SchemaViewer.tsx
│   │   ├── qa/QAReportPanel.tsx
│   │   └── ui/                    # shadcn/ui generated components
│   ├── lib/
│   │   ├── api.ts                 # Axios API client
│   │   ├── supabase.ts            # Supabase client
│   │   └── store.ts               # Zustand global store
│   └── middleware.ts               # Auth guard
├── docker-compose.yml
├── .env.example
└── README.md
```

---

# APPENDIX B: CRITICAL VIBE CODING RULES

1. **Never write boilerplate from scratch.** Every file starts with a Cursor/Copilot prompt. Only edit the output.

2. **Prompt-then-validate.** After every vibe-coded file: run the tests. Don't accept output that doesn't pass.

3. **The State Contract is sacred.** When any agent prompt or node function tries to access a state field not in its `requires_state_fields` list, that is a bug. Enforce it.

4. **One Ollama call saves ~2,000 Groq tokens.** Always check: can this be done locally?

5. **The gauntlet is your regression suite.** Every new feature: re-run the 50 datasets. No new feature ships that drops the pass rate below the previous sprint's baseline.

6. **LLM output is untrusted input.** Every agent output goes through Pydantic validation before touching the state. No exceptions.

7. **Checkpoint aggressively.** Every node writes to PostgreSQL before returning. A crashed server should never lose more than one agent's work.
```

"""
Day 4 Evaluation Protocol: Intent Router Node Tests

25 test cases across 5 categories:
- 5 finance queries (root_cause, trend, comparison, ranking, forecasting)
- 5 sales queries
- 5 HR/operations queries
- 5 ecommerce queries
- 5 ambiguous/edge case queries

Measures: accuracy rate (target ≥88%), latency (target <800ms), token usage
"""
import time
import pytest
import uuid
from datetime import datetime, timezone
from backend.core.state import AgentSwarmState, QAReport
from backend.agents.router_node import router_node


def _make_state(raw_query: str) -> AgentSwarmState:
    """Helper to create a valid baseline AgentSwarmState for a given query."""
    return AgentSwarmState(
        session_id=uuid.uuid4(),
        user_id="test_user",
        pipeline_status="initiated",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        current_agent="system",
        raw_query=raw_query,
        intent_class="trend_analysis",
        business_domain="unknown",
        key_entities=[],
        time_dimension=None,
        raw_file_path="mock.csv",
        file_type="csv",
        raw_row_count=100,
        raw_col_count=10,
        schema_fingerprint="abc123",
        schema_embedding_id=None,
        column_metadata=[],
        similar_schemas_found=False,
        cleaning_operations=[],
        cleaned_parquet_path="",
        data_quality_score=1.0,
        rows_before=100,
        rows_after=100,
        columns_dropped=[],
        feature_definitions=[],
        enriched_parquet_path="",
        feature_rationale="",
        generated_queries=[],
        query_results=[],
        queries_failed=[],
        dashboard_config=[],
        dashboard_title="",
        dashboard_theme="light",
        layout_rationale="",
        qa_report=QAReport(
            data_quality_score=1.0,
            completeness_score=1.0,
            query_validity={},
            chart_relevance={},
            anomalies=[],
            suggestions=[],
            overall_confidence=1.0,
            approval_status="approved",
            reviewer_notes=None,
        ),
        errors=[],
        retry_count=0,
        token_usage={},
    )


# ============================================================================
# Finance Queries (5)
# ============================================================================

FINANCE_CASES = [
    ("Why did our revenue drop in Q3?", "root_cause", "finance"),
    ("Compare profit margins between EMEA and NA for 2024", "comparison", "finance"),
    ("What is the trend of operating expenses over the last quarter?", "trend_analysis", "finance"),
    ("Show me the top 10 most profitable products.", "ranking", "finance"),
    ("Forecast cash flow for next year.", "forecasting", "finance"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent,expected_domain", FINANCE_CASES, ids=[
    "finance_root_cause", "finance_comparison", "finance_trend", "finance_ranking", "finance_forecasting"
])
async def test_finance_queries(query: str, expected_intent: str, expected_domain: str):
    state = _make_state(query)
    start = time.perf_counter()
    result = await router_node(state)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Structural assertions (must always pass)
    assert result.pipeline_status == "routing"
    assert result.current_agent == "router"

    # Classification assertions
    assert result.intent_class == expected_intent, (
        f"Intent mismatch for '{query}': expected={expected_intent}, got={result.intent_class}"
    )
    assert result.business_domain == expected_domain, (
        f"Domain mismatch for '{query}': expected={expected_domain}, got={result.business_domain}"
    )

    # Latency assertion (target <800ms)
    assert elapsed_ms < 800, f"Latency {elapsed_ms:.0f}ms exceeds 800ms target for '{query}'"


# ============================================================================
# Sales Queries (5)
# ============================================================================

SALES_CASES = [
    ("Show me total sales for last quarter", "trend_analysis", "sales"),
    ("Why did we lose so many deals in October?", "root_cause", "sales"),
    ("Compare win rates across different sales reps.", "comparison", "sales"),
    ("What is the distribution of deal sizes?", "distribution", "sales"),
    ("Rank the top 5 sales teams by quota attainment.", "ranking", "sales"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent,expected_domain", SALES_CASES, ids=[
    "sales_trend", "sales_root_cause", "sales_comparison", "sales_distribution", "sales_ranking"
])
async def test_sales_queries(query: str, expected_intent: str, expected_domain: str):
    state = _make_state(query)
    start = time.perf_counter()
    result = await router_node(state)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.pipeline_status == "routing"
    assert result.current_agent == "router"
    assert result.intent_class == expected_intent, (
        f"Intent mismatch for '{query}': expected={expected_intent}, got={result.intent_class}"
    )
    assert result.business_domain == expected_domain, (
        f"Domain mismatch for '{query}': expected={expected_domain}, got={result.business_domain}"
    )
    assert elapsed_ms < 800, f"Latency {elapsed_ms:.0f}ms exceeds 800ms target"


# ============================================================================
# HR / Operations Queries (5)
# ============================================================================

HR_CASES = [
    ("What is our employee retention rate?", "trend_analysis", "hr"),
    ("Why did attrition spike last month?", "root_cause", "hr"),
    ("Compare compensation between engineering and sales.", "comparison", "hr"),
    ("Show demographics distribution of new hires.", "distribution", "hr"),
    ("Is there a correlation between training hours and performance?", "correlation", "hr"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent,expected_domain", HR_CASES, ids=[
    "hr_trend", "hr_root_cause", "hr_comparison", "hr_distribution", "hr_correlation"
])
async def test_hr_queries(query: str, expected_intent: str, expected_domain: str):
    state = _make_state(query)
    start = time.perf_counter()
    result = await router_node(state)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.pipeline_status == "routing"
    assert result.current_agent == "router"
    assert result.intent_class == expected_intent, (
        f"Intent mismatch for '{query}': expected={expected_intent}, got={result.intent_class}"
    )
    assert result.business_domain == expected_domain, (
        f"Domain mismatch for '{query}': expected={expected_domain}, got={result.business_domain}"
    )
    assert elapsed_ms < 800, f"Latency {elapsed_ms:.0f}ms exceeds 800ms target"


# ============================================================================
# Ecommerce Queries (5)
# ============================================================================

ECOMMERCE_CASES = [
    ("How many orders did we process yesterday?", "trend_analysis", "ecommerce"),
    ("Why is cart abandonment so high on mobile?", "root_cause", "ecommerce"),
    ("Compare AOV for returning vs new customers.", "comparison", "ecommerce"),
    ("What's the relationship between shipping time and reviews?", "correlation", "ecommerce"),
    ("Forecast order volume for Black Friday.", "forecasting", "ecommerce"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent,expected_domain", ECOMMERCE_CASES, ids=[
    "ecom_trend", "ecom_root_cause", "ecom_comparison", "ecom_correlation", "ecom_forecasting"
])
async def test_ecommerce_queries(query: str, expected_intent: str, expected_domain: str):
    state = _make_state(query)
    start = time.perf_counter()
    result = await router_node(state)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.pipeline_status == "routing"
    assert result.current_agent == "router"
    assert result.intent_class == expected_intent, (
        f"Intent mismatch for '{query}': expected={expected_intent}, got={result.intent_class}"
    )
    assert result.business_domain == expected_domain, (
        f"Domain mismatch for '{query}': expected={expected_domain}, got={result.business_domain}"
    )
    assert elapsed_ms < 800, f"Latency {elapsed_ms:.0f}ms exceeds 800ms target"


# ============================================================================
# Ambiguous / Edge Case Queries (5)
# ============================================================================

EDGE_CASES = [
    ("just show me the data", "trend_analysis", "unknown"),
    ("what is this dataset about?", "trend_analysis", "unknown"),
    ("make a chart", "trend_analysis", "unknown"),
    ("why?", "root_cause", "unknown"),
    ("give me everything", "trend_analysis", "unknown"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent,expected_domain", EDGE_CASES, ids=[
    "edge_show_data", "edge_about_dataset", "edge_make_chart", "edge_why", "edge_give_everything"
])
async def test_edge_case_queries(query: str, expected_intent: str, expected_domain: str):
    state = _make_state(query)
    start = time.perf_counter()
    result = await router_node(state)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result.pipeline_status == "routing"
    assert result.current_agent == "router"
    assert result.intent_class == expected_intent, (
        f"Intent mismatch for '{query}': expected={expected_intent}, got={result.intent_class}"
    )
    assert result.business_domain == expected_domain, (
        f"Domain mismatch for '{query}': expected={expected_domain}, got={result.business_domain}"
    )
    assert elapsed_ms < 800, f"Latency {elapsed_ms:.0f}ms exceeds 800ms target"


# ============================================================================
# Accuracy Summary Test
# ============================================================================

ALL_CASES = FINANCE_CASES + SALES_CASES + HR_CASES + ECOMMERCE_CASES + EDGE_CASES


@pytest.mark.asyncio
async def test_overall_accuracy_meets_88_percent():
    """
    Aggregate accuracy test: at least 88% of the 25 queries must be
    correctly classified for both intent AND domain.
    """
    correct = 0
    total = len(ALL_CASES)
    failures = []

    for query, expected_intent, expected_domain in ALL_CASES:
        state = _make_state(query)
        result = await router_node(state)
        intent_ok = result.intent_class == expected_intent
        domain_ok = result.business_domain == expected_domain
        if intent_ok and domain_ok:
            correct += 1
        else:
            failures.append(
                f"  '{query}' → intent={result.intent_class}(exp={expected_intent}), "
                f"domain={result.business_domain}(exp={expected_domain})"
            )

    accuracy = correct / total
    failure_report = "\n".join(failures) if failures else "None"
    assert accuracy >= 0.88, (
        f"Accuracy {accuracy:.0%} ({correct}/{total}) is below the 88% minimum bar.\n"
        f"Failures:\n{failure_report}"
    )

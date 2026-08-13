import json
import re
from enum import Enum
from typing import List, Dict, Optional, Any


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


class LLMRouter:
    """
    Mock implementation of LLMRouter for Day 4 testing.
    Uses keyword-based classification that mirrors what a real LLM would return.
    The full multi-provider implementation (Groq, Gemini, Ollama) will be built
    in Days 8-9.
    """
    def __init__(self, redis_url: Optional[str] = None):
        self._total_tokens_used = 0

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens_used

    async def route(
        self,
        task_type: TaskType,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        max_tokens: int = 2048,
    ) -> Dict[str, Any]:
        """
        Route to the optimal available provider. Returns {content, provider, model, tokens_used}.
        Currently mocked for Day 4 testing — only INTENT_ROUTING is implemented.
        """
        if task_type != TaskType.INTENT_ROUTING:
            raise NotImplementedError(
                f"Only INTENT_ROUTING is implemented in the Day 4 mock. Got: {task_type}"
            )

        user_message = ""
        for msg in messages:
            if msg["role"] == "user":
                user_message = msg["content"].lower()

        intent = self._classify_intent(user_message)
        domain = self._classify_domain(user_message)
        key_entities = self._extract_entities(user_message)
        time_dimension = self._extract_time(user_message)

        response_content = {
            "intent": intent,
            "domain": domain,
            "key_entities": key_entities,
            "time_dimension": time_dimension,
        }

        tokens_used = 150
        self._total_tokens_used += tokens_used

        return {
            "content": json.dumps(response_content),
            "provider": "mock",
            "model": "mock-router",
            "tokens_used": tokens_used,
            "routed_via": "mock/mock-router",
        }

    @staticmethod
    def _classify_intent(text: str) -> str:
        """Classify intent using keyword priority rules."""
        # Order matters: check more specific patterns first
        if any(kw in text for kw in ["forecast", "predict", "future", "project"]):
            return "forecasting"
        if any(kw in text for kw in ["why", "cause", "reason", "driver", "spike", "drop"]):
            return "root_cause"
        if any(kw in text for kw in ["distribution", "spread", "demographics", "histogram", "percentile"]):
            return "distribution"
        # Check correlation BEFORE comparison — "between" appears in both contexts
        if any(kw in text for kw in ["correlat", "relationship", "relate"]):
            return "correlation"
        if any(kw in text for kw in ["compare", " vs ", "versus", "between", "against"]):
            return "comparison"
        if any(kw in text for kw in ["top", "bottom", "rank", "best", "worst", "leader", "highest", "lowest"]):
            return "ranking"
        # Default
        return "trend_analysis"

    @staticmethod
    def _classify_domain(text: str) -> str:
        """Classify business domain using keyword matching.
        
        Priority order is critical — more specific domains are checked first
        to avoid false matches from generic keywords.
        """
        # Check ecommerce first ("order", "cart" are specific)
        if any(kw in text for kw in ["ecommerce", "cart", "aov", "shipping", "order"]):
            return "ecommerce"
        # Check customer_success before sales ("customer" could appear in sales contexts)
        if any(kw in text for kw in ["churn", "customer", "nps", "product usage",
                                       "onboarding", "customer success"]):
            return "customer_success"
        # Check healthcare (distinct medical vocabulary)
        if any(kw in text for kw in ["patient", "treatment", "hospital", "medical",
                                       "clinical", "diagnosis", "admission"]):
            return "healthcare"
        # Check education (distinct academic vocabulary)
        if any(kw in text for kw in ["student", "academic", "enrollment", "marks",
                                       "semester", "subjects", "attendance",
                                       "faculty", "curriculum", "grade"]):
            return "education"
        # Check HR before sales — "compensation", "training" are HR-specific
        if any(kw in text for kw in ["employee", " hr ", "retention", "attrition", "compensation",
                                       "headcount", "hire", "training", "performance review"]):
            return "hr"
        # Check sales
        if any(kw in text for kw in ["sales", "deal", "win rate", "quota", "pipeline"]):
            return "sales"
        # Check finance
        if any(kw in text for kw in ["revenue", "profit", "expense", "margin", "cash flow", "financial"]):
            return "finance"
        # Check supply_chain before operations ("inventory" moved here)
        if any(kw in text for kw in ["inventory", "supply chain", "stockout",
                                       "demand"]):
            return "supply_chain"
        # Check logistics
        if any(kw in text for kw in ["delivery", "logistics", "freight"]):
            return "logistics"
        # Check operations (reduced to non-overlapping keywords)
        if any(kw in text for kw in ["manufacturing", "efficiency", "process",
                                       "supply", "throughput"]):
            return "operations"
        # Check marketing
        if any(kw in text for kw in ["marketing", "campaign", "conversion", "engagement",
                                       "cac", "ltv", "acquisition", "advertising",
                                       "lead", "roi"]):
            return "marketing"
        # Check IoT
        if any(kw in text for kw in ["iot", "sensor", "telemetry", "uptime"]):
            return "iot"
        # Default
        return "unknown"

    @staticmethod
    def _extract_entities(text: str) -> List[str]:
        """Extract naive key entities from the query."""
        entities = []
        entity_keywords = [
            "revenue", "profit", "sales", "orders", "deals", "customers",
            "employees", "products", "cash flow", "expenses", "attrition",
            "retention", "aov", "cart", "shipping", "quota",
        ]
        for kw in entity_keywords:
            if kw in text:
                entities.append(kw)
        return entities

    @staticmethod
    def _extract_time(text: str) -> Optional[str]:
        """Extract time dimensions from the query."""
        time_patterns = [
            (r"last quarter", "last quarter"),
            (r"last month", "last month"),
            (r"last year", "last year"),
            (r"yesterday", "yesterday"),
            (r"q[1-4]\s*\d{4}", None),  # Q3 2023
            (r"\b20\d{2}\b", None),      # 2024
        ]
        for pattern, fixed_value in time_patterns:
            match = re.search(pattern, text)
            if match:
                return fixed_value if fixed_value else match.group(0)
        return None

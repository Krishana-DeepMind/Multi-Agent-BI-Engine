## Final Metrics
**Total Columns in Dataset Sample:** 5
- **Columns dropped (rate limit / file failure)**: 5
- **Columns dropped (no response from LLM)**: 0
- **Columns evaluated (real completion)**: 0
**Semantic Type Accuracy:** 0.0%
**Business Label Quality (LLM Judge):** 0.0%
**is_candidate_kpi Metrics:** Precision: 0.0%, Recall: 0.0%

### KPI Precision & Recall by Domain

# Ingestion Agent Evaluation Report
## Setup
- 2 files
- Mocked Embedding Cache to force LLM generation
- Semantic types checked via exact match
- Business labels judged by LLM

## Results per File
### ecommerce_0.csv ❌ FAILED
Errors: [{'agent': 'ingestion', 'error': 'All providers exhausted for task: schema_inference. Last error: 429 You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.7-flash\nPlease retry in 5.541412917s. [links {\n  description: "Learn more about Gemini API quotas"\n  url: "https://ai.google.dev/gemini-api/docs/rate-limits"\n}\n, violations {\n  quota_metric: "generativelanguage.googleapis.com/generate_content_free_tier_requests"\n  quota_id: "GenerateRequestsPerDayPerProjectPerModel-FreeTier"\n  quota_dimensions {\n    key: "model"\n    value: "gemini-3.7-flash"\n  }\n  quota_dimensions {\n    key: "location"\n    value: "global"\n  }\n  quota_value: 20\n}\n, retry_delay {\n  seconds: 5\n}\n]', 'recoverable': True}]
### finance_0.csv ❌ FAILED
Errors: [{'agent': 'ingestion', 'error': "All providers exhausted for task: schema_inference. Last error: Error code: 413 - {'error': {'message': 'Request too large for model `qwen/qwen3.6-27b` in organization `org_01m02dhwmve1pa2b0c7108wbg4` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Requested 8617, please reduce your message size and try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}", 'recoverable': True}]

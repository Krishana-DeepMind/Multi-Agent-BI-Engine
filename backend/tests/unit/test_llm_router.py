import pytest
from backend.core.llm_router import parse_json_response, LLMRouter, TaskType

def test_parse_json_response_clean():
    clean_json = '{"intent": "sales", "domain": "ecommerce"}'
    parsed = parse_json_response(clean_json)
    assert parsed["intent"] == "sales"
    assert parsed["domain"] == "ecommerce"

def test_parse_json_response_markdown():
    markdown_json = '''```json
    {"intent": "hr", "domain": "operations"}
    ```'''
    parsed = parse_json_response(markdown_json)
    assert parsed["intent"] == "hr"
    assert parsed["domain"] == "operations"

def test_parse_json_response_partial():
    # If json-repair is available, it might repair this.
    # We just ensure it doesn't crash.
    partial_json = '{"intent": "sales", "domain": "ecommerce"'
    parsed = parse_json_response(partial_json)
    assert isinstance(parsed, dict)

def test_parse_json_response_invalid():
    invalid = "I cannot fulfill this request."
    parsed = parse_json_response(invalid)
    assert "error" in parsed

@pytest.mark.asyncio
async def test_circuit_breaker():
    router = LLMRouter(redis_url=None) # No redis
    
    # Just verify that without redis, usage is 0
    usage = await router._get_daily_usage("groq")
    assert usage == 0

import asyncio
from backend.core.llm_router import LLMRouter, parse_json_response
from backend.scripts.evaluate_ingestion import llm_judge_business_label

async def main():
    router = LLMRouter()
    
    # Test 1: Using the function directly
    res1 = await llm_judge_business_label(router, "ecommerce_id", "Ecommerce ID")
    print(f"Result for 'Ecommerce ID': {res1}")
    
    # Test 2: Using the function for 'Revenue'
    res2 = await llm_judge_business_label(router, "revenue_usd", "Revenue")
    print(f"Result for 'Revenue': {res2}")

if __name__ == "__main__":
    asyncio.run(main())

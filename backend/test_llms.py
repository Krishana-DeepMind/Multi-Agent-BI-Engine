import os
import asyncio
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

async def test_llms():
    from backend.core.llm_router import LLMRouter
    router = LLMRouter()
    
    # Test Groq
    try:
        res = await router._groq_caller("llama-3.3-70b-versatile", [{"role": "user", "content": "hi"}], None, 100)
        print("Groq success:", res)
    except Exception as e:
        print("Groq error type:", type(e))
        print("Groq error repr:", repr(e))
        
    # Test Gemini
    try:
        res = await router._gemini_caller("gemini-2.0-flash-exp", [{"role": "user", "content": "hi"}], None, 100)
        print("Gemini success:", res)
    except Exception as e:
        print("Gemini error type:", type(e))
        print("Gemini error repr:", repr(e))

asyncio.run(test_llms())

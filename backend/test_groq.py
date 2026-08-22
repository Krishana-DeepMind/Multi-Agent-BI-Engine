import os
import asyncio
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

async def test_groq():
    from backend.core.llm_router import LLMRouter, TaskType
    router = LLMRouter()
    try:
        res = await router._groq_caller("llama3-8b-8192", [{"role": "user", "content": "hi"}], None, 100)
        print("Groq success:", res)
    except Exception as e:
        print("Groq error type:", type(e))
        print("Groq error repr:", repr(e))

asyncio.run(test_groq())

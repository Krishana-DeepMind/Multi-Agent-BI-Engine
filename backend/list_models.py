import os
import asyncio
from dotenv import load_dotenv
import httpx

env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

async def check_models():
    # Groq
    try:
        groq_key = os.environ.get("GROQ_API_KEY")
        async with httpx.AsyncClient() as client:
            res = await client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {groq_key}"})
            data = res.json()
            models = [m["id"] for m in data.get("data", [])]
            print("Groq models:", models)
    except Exception as e:
        print("Groq models error:", e)

    # Gemini
    try:
        from google import genai
        client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
        models = [m.name for m in client.models.list() if "generateContent" in (getattr(m, "supported_actions", []) or [])]
        print("Gemini models:", models[:10], f"(Total: {len(models)})")
    except Exception as e:
        print("Gemini models error:", e)

asyncio.run(check_models())

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
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        print("Gemini models:", models)
    except Exception as e:
        print("Gemini models error:", e)

asyncio.run(check_models())

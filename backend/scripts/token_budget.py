import os
import asyncio
from datetime import date
import redis.asyncio as aioredis
from dotenv import load_dotenv
import sys

# Add backend to path so we can import from core if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.llm_router import ROUTING_TABLE

# Pricing / Token approximations (for estimation)
# Assume average pipeline session uses about:
# - 3000 tokens for routing (Groq)
# - 15000 tokens for schema/cleaning (Groq)
# - 10000 tokens for feature/layout (Gemini)
# - Gemini offers 1M tokens/day
AVG_GROQ_TOKENS_PER_SESSION = 18000
AVG_GEMINI_TOKENS_PER_SESSION = 10000

async def main():
    load_dotenv()
    redis_url = os.environ.get("UPSTASH_REDIS_URL")
    if not redis_url or redis_url == "your_upstash_redis_url_here":
        print("Error: UPSTASH_REDIS_URL not configured in .env")
        print("Cannot check remote token budgets.")
        return

    try:
        r = aioredis.from_url(redis_url)
        # Test connection
        await r.ping()
    except Exception as e:
        print(f"Failed to connect to Redis: {e}")
        return

    today = date.today().isoformat()
    providers = ["groq", "gemini"]
    
    print(f"--- Token Budget Dashboard for {today} ---")
    print("-" * 50)
    
    # Provider limits based on our master routing table logic
    limits = {
        "groq": 500_000,
        "gemini": 1_000_000
    }
    
    usage_data = {}
    
    for provider in providers:
        key = f"token_usage:{provider}:{today}"
        val = await r.get(key)
        usage = int(val) if val else 0
        usage_data[provider] = usage
        
        limit = limits[provider]
        limit_str = str(limit) if limit != float('inf') else "Unlimited"
        
        if limit != float('inf'):
            pct = (usage / limit) * 100
            print(f"{provider.capitalize():<8}: {usage:>8} / {limit_str:<8} tokens ({pct:.1f}% used)")
        else:
            print(f"{provider.capitalize():<8}: {usage:>8} / {limit_str:<8} tokens")

    print("-" * 50)
    print("Estimates for remaining sessions today:")
    
    groq_rem = limits["groq"] - usage_data["groq"]
    gemini_rem = limits["gemini"] - usage_data["gemini"]
    
    est_groq = max(0, groq_rem // AVG_GROQ_TOKENS_PER_SESSION)
    est_gemini = max(0, gemini_rem // AVG_GEMINI_TOKENS_PER_SESSION)
    
    est_sessions = min(est_groq, est_gemini)
    
    print(f"Groq capacity   : ~{est_groq} sessions")
    print(f"Gemini capacity : ~{est_gemini} sessions")
    print(f"System capacity : ~{est_sessions} total pipeline runs remaining")
    
    await r.aclose()

if __name__ == "__main__":
    asyncio.run(main())

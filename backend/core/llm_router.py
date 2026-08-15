import os
import json
import re
import asyncio
import time
import logging
from datetime import date
from enum import Enum
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv

try:
    from json_repair import repair_json
except ImportError:
    def repair_json(json_str: str, return_objects=False):
        # Fallback if json-repair isn't installed
        return json_str

# Load environment variables
load_dotenv()

logger = logging.getLogger("llm_router")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

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

@dataclass
class ProviderConfig:
    provider: str
    model: str
    daily_limit: int     # tokens
    rpm_limit: int       # requests per minute
    priority: int        # lower = higher priority

ROUTING_TABLE: Dict[TaskType, List[ProviderConfig]] = {
    TaskType.INTENT_ROUTING: [
        ProviderConfig("groq",   "llama-3.1-8b-instant",      500_000, 14_400, 1),
        ProviderConfig("ollama", "llama3.2:latest",            999_999, 9999,   2),
    ],
    TaskType.CODE_GENERATION: [
        ProviderConfig("ollama", "qwen2.5-coder:7b",          999_999, 9999,   1),  # Always local first
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.SCHEMA_INFERENCE: [
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 1),
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 2),
        ProviderConfig("ollama", "llama3.2:latest",            999_999,  9999,  3),
    ],
    TaskType.FEATURE_IDEATION: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.ECHARTS_CONFIG: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.CLEANING_STRATEGY: [
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 1),
        ProviderConfig("ollama", "llama3.2:latest",            999_999, 9999,   2),
    ],
    TaskType.QUERY_DESIGN: [
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 1),
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 2),
    ],
    TaskType.QUERY_REPAIR: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.CHART_SELECTION: [
        ProviderConfig("groq",   "llama-3.1-8b-instant",      500_000, 14_400, 1),
        ProviderConfig("ollama", "llama3.2:latest",            999_999, 9999,   2),
    ],
    TaskType.QA_VALIDATION: [
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 1),
        ProviderConfig("ollama", "llama3.2:latest",            999_999, 9999,   2),
    ],
    TaskType.QA_REPORT: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ],
    TaskType.BUSINESS_LABELING: [
        ProviderConfig("gemini", "gemini-2.0-flash-exp",    1_000_000,  1_500, 1),
        ProviderConfig("groq",   "llama-3.3-70b-versatile",   500_000, 14_400, 2),
    ]
}

def parse_json_response(content: str) -> Dict[str, Any]:
    """Parse JSON with fallbacks for markdown fences and partial JSON."""
    content = content.strip()
    
    # 1. Try stripping markdown code blocks
    if content.startswith("```"):
        # Match ```json ... ``` or just ``` ... ```
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
        if match:
            content = match.group(1).strip()
    
    # 2. Try direct JSON parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
        
    # 3. Use json-repair as fallback
    try:
        repaired = repair_json(content, return_objects=True)
        if repaired and isinstance(repaired, (dict, list)):
             return repaired # type: ignore
    except Exception:
        pass
        
    # 4. Fail gracefully
    return {"error": "Failed to parse JSON response", "raw_content": content}


class LLMRouter:
    def __init__(self, redis_url: Optional[str] = None):
        redis_url = redis_url or os.environ.get("UPSTASH_REDIS_URL")
        # Handle connection gracefully if redis_url is placeholder or None
        self.redis = None
        if redis_url and redis_url != "your_upstash_redis_url_here":
            try:
                self.redis = aioredis.from_url(redis_url)
            except Exception as e:
                print(f"Warning: Redis connection failed, token tracking disabled. {e}")
        
        self._providers = self._init_providers()

    def _init_providers(self) -> Dict[str, Any]:
        return {
            "groq":   self._groq_caller,
            "gemini": self._gemini_caller,
            "ollama": self._ollama_caller,
        }

    async def _get_daily_usage(self, provider: str) -> int:
        """Fetch today's token count from Redis. Resets at midnight UTC."""
        if not self.redis:
            return 0
        key = f"token_usage:{provider}:{self._today()}"
        try:
            val = await self.redis.get(key)
            return int(val) if val else 0
        except Exception:
            return 0

    async def _increment_usage(self, provider: str, tokens: int):
        if not self.redis:
            return
        key = f"token_usage:{provider}:{self._today()}"
        try:
            await self.redis.incrby(key, tokens)
            await self.redis.expire(key, 86400)  # 24h TTL
        except Exception:
            pass

    async def route(
        self,
        task_type: TaskType,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        max_tokens: int = 2048,
    ) -> Dict[str, Any]:
        """Route to the optimal available provider. Returns {content, provider, model, tokens_used}"""
        configs = ROUTING_TABLE.get(task_type, [])
        if not configs:
            raise ValueError(f"No routing configuration for task: {task_type}")

        last_error = None
        for config in sorted(configs, key=lambda c: c.priority):
            usage = await self._get_daily_usage(config.provider)
            if usage >= config.daily_limit * 0.95:  # 5% buffer circuit breaker
                continue
            
            try:
                caller = self._providers[config.provider]
                t0 = time.perf_counter()
                result = await caller(config.model, messages, response_format, max_tokens)
                elapsed = time.perf_counter() - t0
                
                await self._increment_usage(config.provider, result["tokens_used"])
                result["routed_via"] = f"{config.provider}/{config.model}"
                
                logger.info(f"[Agent: {task_type.value}] [Provider: {config.provider}] Latency: {elapsed:.2f}s, Tokens: {result['tokens_used']}")
                
                return result
            except Exception as e:
                last_error = e
                # Fallback to next provider on error (e.g., rate limit, Ollama down)
                continue
                
        raise RuntimeError(f"All providers exhausted for task: {task_type}. Last error: {last_error}")

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    # --- Provider-specific callers ---
    
    async def _groq_caller(self, model: str, messages: List[Dict[str, str]], response_format: Optional[Dict], max_tokens: int) -> Dict:
        import openai
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key or api_key == "your_groq_api_key_here":
            raise ValueError("GROQ_API_KEY not configured")
            
        client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        kwargs = {}
        if response_format:
            kwargs["response_format"] = response_format
            
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            **kwargs
        )
        return {
            "content": response.choices[0].message.content,
            "provider": "groq",
            "model": model,
            "tokens_used": response.usage.total_tokens if response.usage else 0,
        }

    async def _gemini_caller(self, model: str, messages: List[Dict[str, str]], response_format: Optional[Dict], max_tokens: int) -> Dict:
        import google.generativeai as genai
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key or api_key == "your_google_api_key_here":
            raise ValueError("GOOGLE_API_KEY not configured")
            
        genai.configure(api_key=api_key)
        
        # Determine actual model name. Sometimes flash is "gemini-2.0-flash-exp"
        model_name = "gemini-2.0-flash-exp" if "flash-exp" in model else model
        # Just use gemini-1.5-flash as fallback if 2.0 isn't available in SDK yet, but we'll try what's requested
        
        gemini_model = genai.GenerativeModel(model_name)
        
        # Convert messages from OpenAI format to Gemini format
        # System prompt usually goes to system_instruction in GenerativeModel
        system_instruction = None
        gemini_history = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                gemini_history.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] == "assistant":
                gemini_history.append({"role": "model", "parts": [msg["content"]]})
                
        if system_instruction:
            gemini_model = genai.GenerativeModel(model_name, system_instruction=system_instruction)
            
        # Optional JSON mode config
        generation_config = genai.types.GenerationConfig(max_output_tokens=max_tokens)
        if response_format and response_format.get("type") == "json_object":
            generation_config.response_mime_type = "application/json"
            
        chat = gemini_model.start_chat(history=gemini_history[:-1])
        last_user_msg = gemini_history[-1]["parts"][0]
        
        response = await chat.send_message_async(
            last_user_msg, 
            generation_config=generation_config
        )
        
        # Gemini does not return exact token usage in the simple API as consistently, but we can estimate or check metadata
        usage = response.usage_metadata
        tokens_used = usage.total_token_count if usage else 0
        
        return {
            "content": response.text,
            "provider": "gemini",
            "model": model,
            "tokens_used": tokens_used,
        }

    async def _ollama_caller(self, model: str, messages: List[Dict[str, str]], response_format: Optional[Dict], max_tokens: int) -> Dict:
        # Uses httpx to hit localhost:11434/api/chat
        url = "http://localhost:11434/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens
            }
        }
        if response_format and response_format.get("type") == "json_object":
            payload["format"] = "json"
            
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            
            # approximate tokens based on eval count and prompt eval count
            tokens_used = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
            
            return {
                "content": data.get("message", {}).get("content", ""),
                "provider": "ollama",
                "model": model,
                "tokens_used": tokens_used,
            }

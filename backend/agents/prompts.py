import os

_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_prompt(filename: str) -> str:
    """Load a prompt from the backend/prompts/ directory."""
    filepath = os.path.join(_PROMPTS_DIR, "..", "prompts", filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

ROUTER_SYSTEM_PROMPT = _load_prompt("router.txt")
INGESTION_SYSTEM_PROMPT = _load_prompt("ingestion.txt")

import os

_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_prompt(filename: str) -> str:
    """Load a prompt from the backend/prompts/ directory."""
    filepath = os.path.join(_PROMPTS_DIR, "..", "prompts", filename)
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

ROUTER_SYSTEM_PROMPT = _load_prompt("router.txt")
INGESTION_SYSTEM_PROMPT = _load_prompt("ingestion.txt")
CLEANING_SYSTEM_PROMPT = _load_prompt("cleaning.txt")
FEATURE_SYSTEM_PROMPT = _load_prompt("feature.txt")
ANALYTICS_SYSTEM_PROMPT = _load_prompt("analytics.txt")
LAYOUT_CHART_SELECTION_PROMPT = _load_prompt("layout_chart_selection.txt")
LAYOUT_ECHARTS_CONFIG_PROMPT = _load_prompt("layout_echarts_config.txt")

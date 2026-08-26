# Ingestion Agent LLM Testing Report

During the evaluation of the `agent_ingestion` node using the live LLM pipeline, we encountered several problems that caused the testing to fail. We have investigated the errors, applied necessary fixes, and are now running a full end-to-end evaluation.

Here is the full report of the problems identified:

## 1. Groq TPM Rate Limit (Error 413)
- **Problem**: The ingestion node was passing `max_tokens=8192` to the LLM Router for the `schema_inference` task. Groq's `qwen3.6-27b` model on the On-Demand tier has a limit of 8,000 Tokens Per Minute (TPM). Groq calculates the token request size by adding the input prompt tokens to `max_tokens`. Thus, a request with ~400 prompt tokens + 8192 max tokens = ~8592 tokens, which instantly exceeds the 8000 TPM limit and returns a 413 Error.
- **Impact**: Groq instantly rejects the `schema_inference` requests, forcing the LLMRouter to failover to the secondary provider (Gemini).
- **Fix Applied**: Modified `backend/agents/ingestion_node.py` to use `max_tokens=4000`. The compressed schema payload is small (~600 tokens), and generating JSON for the columns takes well under 4000 tokens. This keeps the total requested tokens below 8000, allowing Groq to process the requests natively.

## 2. Gemini Deprecated Package Error
- **Problem**: When the router falls back to Gemini, we get a `FutureWarning` and failure stating that all support for `google.generativeai` has ended. Furthermore, Gemini sometimes returns empty responses due to safety/max_token stops (`finish_reason: 2`).
- **Impact**: Prevents Gemini from serving as a reliable fallback provider. When Groq fails, the entire pipeline crashes because Gemini cannot process the prompt either.
- **Next Steps**: The codebase should be updated to migrate from the deprecated `google.generativeai` SDK to the new `google.genai` SDK.

## 3. Sanity Check Crashing the Pipeline
- **Problem**: The `api_sanity_check` in the evaluation script was hard-failing with `sys.exit(1)` when Gemini's check failed.
- **Impact**: It prevented the actual ingestion evaluation from running even if the primary provider (Groq) was perfectly healthy.
- **Fix Applied**: Removed the `sys.exit(1)` for the sanity check, allowing the script to continue with the providers that are online.

## 4. Uncaught Evaluation Script Bottlenecks
- **Problem**: The evaluation script was initially hardcoded to only run on 2 sample files.
- **Impact**: Prevented us from getting a full report across all domains.
- **Fix Applied**: Removed the 2-file sampling limit. The script is now evaluating the full dataset. 

---
> [!NOTE] 
> The evaluation script is currently running with the Groq fix applied. We should expect successful schema inference using Groq without rate-limit crashes. Once the background task finishes, you can check the generated `evaluation_report.md` for semantic accuracy scores!

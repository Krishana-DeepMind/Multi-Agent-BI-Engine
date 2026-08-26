import os
import json
import asyncio
import sys
from unittest.mock import patch, AsyncMock
from pathlib import Path
from tqdm import tqdm
from dotenv import load_dotenv

from backend.agents.ingestion_node import ingestion_node
from backend.core.llm_router import LLMRouter, TaskType

# Load .env file
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(env_path)

async def llm_judge_business_label(router: LLMRouter, original_col: str, generated_label: str) -> bool:
    """Uses the LLM router to judge if a generated business label is high quality."""
    if not generated_label:
        return False
        
    prompt = f"""
    You are evaluating the quality of an auto-generated business label for a column in a BI dashboard.
    Original column name: '{original_col}'
    Generated label: '{generated_label}'
    
    A good label should:
    1. Be human-readable (no underscores, no weird abbreviations). EXPLICITLY ALLOW common business abbreviations such as: ID, KPI, YTD, ARR, MRR, ROI, and similar standard acronyms. These are perfectly good business labels.
    2. Be in PascalCase or Title Case (e.g. 'Monthly Revenue')
    3. Make sense to a business user
    
    Return ONLY a JSON object: {{"score": 1}} if good, {{"score": 0}} if bad. No markdown.
    """
    try:
        # Using intent routing task type since it allows simple JSON output
        response = await router.route(
            task_type=TaskType.INTENT_ROUTING,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000
        )
        content = response["content"].strip()
        if content.startswith("```json"): content = content[7:]
        if content.startswith("```"): content = content[3:]
        if content.endswith("```"): content = content[:-3]
        
        from backend.core.llm_router import parse_json_response
        result = parse_json_response(content)
        return result.get("score", 0) == 1
    except Exception as e:
        print(f"LLM Judge failed for {original_col}: {e}")
        # fallback heuristic
        return generated_label.replace(" ", "").isalpha()

async def api_sanity_check(router: LLMRouter):
    """Fires a tiny prompt to verify providers are up and have quota."""
    print("Running API Sanity Check...")
    
    # 1. Test Groq (using a Groq-prioritized task type)
    try:
        await router.route(
            task_type=TaskType.CODE_GENERATION, # Groq is #1 here
            messages=[{"role": "user", "content": "Say 'ok'."}],
            max_tokens=5
        )
        print("[PASS] Groq check passed.")
    except Exception as e:
        print(f"[FAIL] Groq sanity check failed: {e}")
        sys.exit(1)
        
    # 2. Test Gemini (using a Gemini-prioritized task type)
    try:
        await router.route(
            task_type=TaskType.FEATURE_IDEATION, # Gemini is #1 here
            messages=[{"role": "user", "content": "Say 'ok'."}],
            max_tokens=5
        )
        print("[PASS] Gemini check passed.")
    except Exception as e:
        print(f"[FAIL] Gemini sanity check failed: {e}")
        sys.exit(1)
    
    print("Sanity check complete. All APIs operational.\n")

async def evaluate():
    print("Starting Ingestion Agent Evaluation (Live LLM)...")
    router = LLMRouter()
    
    with open("test_dataset/ground_truth.json", "r") as f:
        ground_truth = json.load(f)

    # Run sanity check before full execution
    await api_sanity_check(router)
        
    files = [f for f in os.listdir("test_dataset") if f.endswith(".csv")]
    
    total_cols_in_dataset = 0
    cols_failed_file = 0
    cols_dropped_by_llm = 0
    cols_evaluated = 0
    
    correct_semantics = 0
    total_labels_judged = 0
    good_labels = 0
    
    # metrics for candidate_kpi
    kpi_tp = 0
    kpi_fp = 0
    kpi_fn = 0
    
    files = [f for f in os.listdir("test_dataset") if f.endswith(".csv")]
    
    # Run against just 2 files to confirm functionality without spending quota
    sampled_files = []
    seen_domains = set()
    for f in sorted(files):
        domain = f.split("_")[0]
        if domain not in seen_domains:
            sampled_files.append(f)
            seen_domains.add(domain)
        if len(sampled_files) >= 2:
            break
    files = sampled_files

    report_lines = ["# Ingestion Agent Evaluation Report\n"]
    report_lines.append(f"## Setup\n- {len(files)} files\n- Mocked Embedding Cache to force LLM generation\n- Semantic types checked via exact match\n- Business labels judged by LLM\n\n")
    # Initialize these below the sampling block
    domain_kpi_stats = {}
    misclassifications = []

    report_lines.append("## Results per File\n")
    
    # Mocking EmbeddingEngine to always return None (cache miss)
    with patch("backend.agents.ingestion_node.EmbeddingEngine") as MockEmbedding:
        mock_embed = MockEmbedding.return_value
        mock_embed.embed_schema = AsyncMock(return_value=[0.1])
        mock_embed.find_similar_schema = AsyncMock(return_value=None)
        
        for filename in tqdm(files):
            domain = filename.split("_")[0]
            file_path = os.path.abspath(os.path.join("test_dataset", filename))
            
            state = {
                "session_id": f"eval-{filename}",
                "business_domain": domain,
                "raw_file_path": file_path,
                "file_type": "csv",
                "errors": []
            }
            
            try:
                # The node uses DuckDBEngine.load_from_supabase which works with local absolute paths!
                new_state = await ingestion_node(state, router)
                
                gt_meta = ground_truth.get(filename, [])
                gt_map = {item["name"]: item for item in gt_meta}
                
                file_correct_sem = 0
                file_total_cols = len(gt_meta)
                total_cols_in_dataset += file_total_cols
                
                if new_state.get("pipeline_status") == "failed":
                    print(f"Pipeline failed for {filename}. Errors:", new_state.get('errors'))
                    report_lines.append(f"### {filename} ❌ FAILED\nErrors: {new_state.get('errors')}\n")
                    cols_failed_file += file_total_cols
                    continue
                    
                output_metadata = new_state.get("column_metadata", [])
                
                report_lines.append(f"### {filename}\n")
                report_lines.append("| Column | Expected Sem | Output Sem | Expected KPI | Output KPI | Generated Label | Label Score |\n")
                report_lines.append("|---|---|---|---|---|---|---|\n")
                
                if domain not in domain_kpi_stats:
                    domain_kpi_stats[domain] = {"tp": 0, "fp": 0, "fn": 0}
                
                for col in output_metadata:
                    col_name = col["name"]
                    sem_type = col["semantic_type"]
                    label = col["business_label"]
                    out_kpi = col.get("is_candidate_kpi", False)
                    
                    gt_item = gt_map.get(col_name)
                    expected_sem = gt_item["semantic_type"] if gt_item else "unknown"
                    expected_kpi = gt_item.get("is_candidate_kpi", False) if gt_item else False
                    
                    if sem_type == "unknown" and label == "" and not out_kpi:
                        cols_dropped_by_llm += 1
                        report_lines.append(f"| {col_name} | {expected_sem} | ⚠️ NO RESPONSE | {expected_kpi} | ⚠️ | ⚠️ | ⚠️ |\n")
                        continue
                        
                    cols_evaluated += 1
                    
                    if sem_type == expected_sem:
                        file_correct_sem += 1
                        correct_semantics += 1
                        sem_icon = "✅"
                    else:
                        sem_icon = "❌"
                        misclassifications.append(f"- **{filename}** | Col: `{col_name}` | Expected: `{expected_sem}` | Got: `{sem_type}`")
                        
                    if expected_kpi and out_kpi:
                        kpi_tp += 1
                        domain_kpi_stats[domain]["tp"] += 1
                        kpi_icon = "✅ (TP)"
                    elif expected_kpi and not out_kpi:
                        kpi_fn += 1
                        domain_kpi_stats[domain]["fn"] += 1
                        kpi_icon = "❌ (FN)"
                    elif not expected_kpi and out_kpi:
                        kpi_fp += 1
                        domain_kpi_stats[domain]["fp"] += 1
                        kpi_icon = "❌ (FP)"
                    else:
                        kpi_icon = "✅ (TN)"
                        
                    is_good_label = await llm_judge_business_label(router, col_name, label)
                    label_icon = "✅" if is_good_label else "❌"
                    if is_good_label: good_labels += 1
                    total_labels_judged += 1
                    
                    report_lines.append(f"| {col_name} | {expected_sem} | {sem_type} {sem_icon} | {expected_kpi} | {out_kpi} {kpi_icon} | {label} | {label_icon} |\n")
                    
                    # Sleep to avoid Groq 30 RPM free tier limits
                    await asyncio.sleep(2.1)
                
                report_lines.append(f"\n**File Accuracy**: {file_correct_sem}/{file_total_cols} ({file_correct_sem/file_total_cols:.0%})\n\n")

            except Exception as e:
                report_lines.append(f"### {filename} ❌ EXCEPTION: {str(e)}\n")
                
    # Final Metrics
    # Final Metrics
    sem_accuracy = correct_semantics / cols_evaluated if cols_evaluated > 0 else 0
    label_quality = good_labels / total_labels_judged if total_labels_judged > 0 else 0
    
    kpi_precision = kpi_tp / (kpi_tp + kpi_fp) if (kpi_tp + kpi_fp) > 0 else 0
    kpi_recall = kpi_tp / (kpi_tp + kpi_fn) if (kpi_tp + kpi_fn) > 0 else 0
    
    domain_kpi_text = "### KPI Precision & Recall by Domain\n"
    for d, stats in domain_kpi_stats.items():
        dtp = stats["tp"]
        dfp = stats["fp"]
        dfn = stats["fn"]
        if (dtp + dfp + dfn) > 0: # Only report if domain was evaluated
            dpre = dtp / (dtp + dfp) if (dtp + dfp) > 0 else 0
            drec = dtp / (dtp + dfn) if (dtp + dfn) > 0 else 0
            domain_kpi_text += f"- **{d}**: Precision: {dpre:.1%}, Recall: {drec:.1%}\n"
    
    if misclassifications and sem_accuracy < 0.5:
        report_lines.insert(0, "### Concrete Misclassification Examples (Sample)\n" + "\n".join(misclassifications[:10]) + "\n\n")

    report_lines.insert(0, domain_kpi_text + "\n")
    report_lines.insert(0, f"**is_candidate_kpi Metrics:** Precision: {kpi_precision:.1%}, Recall: {kpi_recall:.1%}\n\n")
    report_lines.insert(0, f"**Business Label Quality (LLM Judge):** {label_quality:.1%}\n")
    report_lines.insert(0, f"**Semantic Type Accuracy:** {sem_accuracy:.1%}\n")
    report_lines.insert(0, f"- **Columns evaluated (real completion)**: {cols_evaluated}\n")
    report_lines.insert(0, f"- **Columns dropped (no response from LLM)**: {cols_dropped_by_llm}\n")
    report_lines.insert(0, f"- **Columns dropped (rate limit / file failure)**: {cols_failed_file}\n")
    report_lines.insert(0, f"**Total Columns in Dataset Sample:** {total_cols_in_dataset}\n")
    report_lines.insert(0, f"## Final Metrics\n")
    
    with open("evaluation_report.md", "w", encoding="utf-8") as f:
        f.writelines(report_lines)
        
    print(f"Evaluation complete. Semantic Acc: {sem_accuracy:.1%}, Label Quality: {label_quality:.1%}")

if __name__ == "__main__":
    asyncio.run(evaluate())

import logging
import hashlib
from typing import Dict, Any

from backend.core.state import AgentSwarmState, ColumnMeta
from backend.core.context_slicer import slice_context
from backend.core.duckdb_engine import DuckDBEngine
from backend.core.schema_profiler import SchemaProfiler
from backend.core.embedding_engine import EmbeddingEngine
from backend.core.llm_router import LLMRouter, TaskType, parse_json_response
from backend.core.schema_compressor import compress_column_meta_for_prompt
from backend.agents.prompts import INGESTION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

async def ingestion_node(state: AgentSwarmState, llm_router: LLMRouter) -> AgentSwarmState:
    """
    Ingestion & Schema Agent node.
    - Loads file into DuckDB
    - Profiles schema
    - Checks pgvector for similar schemas
    - Uses LLM to infer semantic types and business labels if no cache hit
    """
    state_dict = state.model_dump() if hasattr(state, "model_dump") else dict(state)
    logger.info(f"Starting ingestion_node for session {state_dict.get('session_id')}")
    
    # Context slicing for prompt
    ctx = slice_context(state, "ingestion")
    raw_query = ctx.get("raw_query", "")
    business_domain = ctx.get("business_domain", "unknown")
    raw_file_path = ctx.get("raw_file_path", "")
    file_type = ctx.get("file_type", "csv")
    
    # Initialize engines
    db_engine = DuckDBEngine()
    schema_profiler = SchemaProfiler(db_engine)
    embedding_engine = EmbeddingEngine()
    
    def return_state():
        return AgentSwarmState(**state_dict) if hasattr(state, "model_dump") else state_dict

    # 1. Load file into DuckDB
    try:
        load_result = db_engine.load_from_supabase(raw_file_path, file_type)
        raw_row_count = load_result.get("row_count", 0)
        raw_col_count = load_result.get("column_count", 0)
    except Exception as e:
        logger.error(f"Failed to load dataset: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "ingestion", "error": str(e), "recoverable": False}]
        return return_state()
        
    # 2. Run schema profiler to get raw columns
    try:
        column_metadata = schema_profiler.profile_table()
    except Exception as e:
        logger.error(f"Failed to profile schema: {e}")
        state_dict["pipeline_status"] = "failed"
        state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "ingestion", "error": str(e), "recoverable": False}]
        return return_state()
        
    # Generate schema fingerprint
    fingerprint_input = sorted([f"{c.name}:{c.dtype}" for c in column_metadata])
    schema_fingerprint = hashlib.sha256(",".join(fingerprint_input).encode()).hexdigest()
    
    # 3. Check for similar schema (cache hit)
    try:
        col_meta_dicts = [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in column_metadata]
        embedding = await embedding_engine.embed_schema(col_meta_dicts)
        similar_schema = await embedding_engine.find_similar_schema(embedding, threshold=0.92)
    except Exception as e:
        logger.error(f"Embedding engine error: {e}")
        similar_schema = None
        embedding = []
        
    similar_schemas_found = False
    
    if similar_schema and similar_schema.get("column_metadata"):
        logger.info("Similar schema found. Reusing cached metadata.")
        similar_schemas_found = True
        column_metadata_raw = similar_schema["column_metadata"]
        state_dict["column_metadata"] = column_metadata_raw
        state_dict["schema_embedding_id"] = similar_schema.get("id")
    else:
        logger.info("No similar schema found. Invoking LLM for inference.")
        
        # Build prompt using compressed schema
        compressed_schema = compress_column_meta_for_prompt(column_metadata)
        user_msg = f"Domain: {business_domain}\n\nSchema Profile:\n{compressed_schema}\n\nReturn a JSON object with a single key 'columns' containing the array of ColumnMeta objects for ALL {raw_col_count} columns."
        
        try:
            response = await llm_router.route(
                task_type=TaskType.SCHEMA_INFERENCE,
                messages=[
                    {"role": "system", "content": INGESTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg}
                ],
                max_tokens=4000
            )
            parsed_json = parse_json_response(response["content"])
            
            inferred_cols = []
            if isinstance(parsed_json, list):
                inferred_cols = parsed_json
            elif isinstance(parsed_json, dict):
                inferred_cols = next((v for v in parsed_json.values() if isinstance(v, list)), [])
            
            inferred_map = {(col.get("name") or col.get("column_name")): col for col in inferred_cols if isinstance(col, dict)}
            
            final_metadata = []
            for col in column_metadata:
                col_dict = col.model_dump() if hasattr(col, "model_dump") else dict(col)
                inferred = inferred_map.get(col_dict["name"], {})
                
                col_dict["semantic_type"] = inferred.get("semantic_type", "unknown")
                col_dict["business_label"] = inferred.get("business_label", "")
                col_dict["is_candidate_kpi"] = inferred.get("is_candidate_kpi", False)
                
                final_metadata.append(col_dict)
                
            state_dict["column_metadata"] = final_metadata
            
        except Exception as e:
            logger.error(f"LLM Schema Inference failed: {e}")
            state_dict["pipeline_status"] = "failed"
            state_dict["errors"] = state_dict.get("errors", []) + [{"agent": "ingestion", "error": str(e), "recoverable": True}]
            return return_state()

    # 4. Update state
    state_dict["raw_row_count"] = raw_row_count
    state_dict["raw_col_count"] = raw_col_count
    state_dict["schema_fingerprint"] = schema_fingerprint
    state_dict["similar_schemas_found"] = similar_schemas_found
    state_dict["pipeline_status"] = "ingesting"
    state_dict["current_agent"] = "ingestion"
    
    return return_state()

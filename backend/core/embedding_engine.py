import os
import json
import logging
import google.generativeai as genai
from typing import List, Dict, Optional, Any

from sqlalchemy import text
from backend.core.database import get_db_session

logger = logging.getLogger(__name__)

class EmbeddingEngine:
    def __init__(self):
        self.model = "models/text-embedding-004"
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key and api_key != "your_google_api_key_here":
            genai.configure(api_key=api_key)
        else:
            logger.warning("GOOGLE_API_KEY not configured. Embeddings will fallback to zeros.")

    async def embed_schema(self, column_metadata: List[Dict[str, Any]]) -> List[float]:
        """Generate 768-dim embedding from schema fingerprint using Gemini API"""
        schema_text = " ".join([f"{c.get('name')}:{c.get('semantic_type')}" for c in column_metadata])
        
        try:
            result = genai.embed_content(
                model=self.model,
                content=schema_text,
                task_type="semantic_similarity"
            )
            embedding = result.get('embedding', [])
            if not embedding:
                raise ValueError("Empty embedding returned")
            return embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding from Gemini: {e}")
            # Fallback to zeros if Gemini is unreachable or quota exceeded
            return [0.0] * 768

    async def find_similar_schema(self, embedding: List[float], threshold: float = 0.92) -> Optional[Dict[str, Any]]:
        """Query pgvector for similar schemas. If found, reuse their cleaned pipeline."""
        if not embedding or embedding == [0.0] * 768:
            return None

        # Convert embedding list to pgvector string format: '[0.1, 0.2, ...]'
        embedding_str = "[" + ",".join(map(str, embedding)) + "]"
        
        query = text("""
            SELECT id, fingerprint, column_metadata, domain,
                   1 - (embedding <=> :target_embedding::vector) as similarity
            FROM schema_embeddings
            WHERE 1 - (embedding <=> :target_embedding::vector) >= :threshold
            ORDER BY similarity DESC
            LIMIT 1
        """)

        try:
            async with get_db_session() as session:
                result = await session.execute(query, {
                    "target_embedding": embedding_str,
                    "threshold": threshold
                })
                row = result.fetchone()
                
                if row:
                    return {
                        "id": str(row.id),
                        "fingerprint": row.fingerprint,
                        "column_metadata": row.column_metadata,
                        "domain": row.domain,
                        "similarity": row.similarity
                    }
        except Exception as e:
            logger.error(f"Error querying similar schemas: {e}")
        
        return None

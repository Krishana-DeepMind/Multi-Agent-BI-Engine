import os
import json
import logging
import httpx
from typing import List, Dict, Optional, Any

from sqlalchemy import text
from backend.core.database import get_db_session

logger = logging.getLogger(__name__)

class EmbeddingEngine:
    def __init__(self):
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.model = "nomic-embed-text"

    async def embed_schema(self, column_metadata: List[Dict[str, Any]]) -> List[float]:
        """Generate 768-dim embedding from schema fingerprint"""
        schema_text = " ".join([f"{c.get('name')}:{c.get('semantic_type')}" for c in column_metadata])
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.ollama_url}/api/embeddings",
                    json={
                        "model": self.model,
                        "prompt": schema_text
                    },
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()
                return data.get("embedding", [])
            except Exception as e:
                logger.error(f"Failed to generate embedding from Ollama: {e}")
                # Fallback to zeros if Ollama is unreachable
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

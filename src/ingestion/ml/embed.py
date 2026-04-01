"""
Embedding utility with:
- batching
- error handling
- clean X, y generation
"""

import aiohttp
import asyncio
from typing import List, Optional

from src.config.constants import OLLAMA_URL, OLLAMA_MODEL, ML_EMBEDDING_BATCH_SIZE

MODEL = OLLAMA_MODEL
BATCH_SIZE = ML_EMBEDDING_BATCH_SIZE


async def fetch_embedding(session: aiohttp.ClientSession, text: str) -> Optional[List[float]]:
    try:
        async with session.post(
            OLLAMA_URL,
            json={"model": MODEL, "prompt": text}
        ) as resp:
            if resp.status != 200:
                return None

            data = await resp.json()
            return data.get("embedding")

    except Exception:
        return None


async def get_embeddings_async(texts: List[str]) -> List[Optional[List[float]]]:
    results = []

    async with aiohttp.ClientSession() as session:
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            tasks = [fetch_embedding(session, t) for t in batch]
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)

    return results


def get_embeddings(texts: List[str]) -> List[Optional[List[float]]]:
    return asyncio.run(get_embeddings_async(texts))

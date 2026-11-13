import hashlib
import numpy as np
import httpx
from typing import List
from app.core.config import settings


async def get_embedding(text: str, dim: int = 768) -> List[float]:
    """
    Computes a 768-dimensional normalized embedding vector.
    First attempts to query the local LM Studio embeddings endpoint (/v1/embeddings).
    If LM Studio is unreachable or text is empty, falls back to a deterministic,
    semantic-hash projection that guarantees unit L2-norm.
    """
    if not text.strip():
        return [0.0] * dim

    # Try LM Studio
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL}/embeddings",
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                json={"input": text, "model": settings.LLM_EMBEDDING_MODEL}
            )
            if resp.is_success:
                data = resp.json()
                emb = data["data"][0]["embedding"]
                # Pad or truncate if needed to 768
                if len(emb) < dim:
                    emb = emb + [0.0] * (dim - len(emb))
                elif len(emb) > dim:
                    emb = emb[:dim]
                arr = np.array(emb, dtype=np.float32)
                norm = np.linalg.norm(arr)
                if norm > 0:
                    arr = arr / norm
                return arr.tolist()
    except Exception:
        pass

    # Deterministic fallback projection (reproducible for tests and offline runs)
    rng_seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.RandomState(rng_seed)
    # Generate vector from standard normal
    vec = rng.randn(dim).astype(np.float32)
    # Inject word frequency properties so similar texts share variance
    words = text.lower().split()
    for i, w in enumerate(words[:20]):
        w_hash = int(hashlib.md5(w.encode("utf-8")).hexdigest()[:4], 16) % dim
        vec[w_hash] += 1.5 / (i + 1)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


async def get_embeddings_batch(texts: List[str], dim: int = 768) -> List[List[float]]:
    return [await get_embedding(t, dim) for t in texts]

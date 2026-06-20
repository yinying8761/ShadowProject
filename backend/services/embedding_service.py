"""
Embedding service abstraction.

Supports three tiers:
  1. API-based (via OpenAI-compatible /v1/embeddings, reuses the LLM API key)
  2. Local ONNX model via fastembed (offline, ~100 MB on first download)
  3. None (FTS5-only mode when neither is available)

Call embed() with a list of texts and get back a list of float vectors,
or embed_single() for one text → one vector. Both return None when
embeddings are unavailable.
"""

from config import settings


# Lazy-loaded fastembed reference
_fastembed_model = None


def _get_fastembed_model():
    """Lazy-load the fastembed model. Returns None if fastembed is unavailable."""
    global _fastembed_model
    if _fastembed_model is not None:
        return _fastembed_model or None
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None
    model_name = settings.get_embedding_model() or "BAAI/bge-small-zh-v1.5"
    try:
        _fastembed_model = TextEmbedding(model_name=model_name)
        print(f"[Embedding] loaded local model: {model_name}", flush=True)
    except Exception as e:
        print(f"[Embedding] fastembed init failed: {e}", flush=True)
        _fastembed_model = False  # sentinel: tried and failed
        return None
    return _fastembed_model


async def _embed_api(texts: list[str]) -> list[list[float]] | None:
    """Call an OpenAI-compatible /v1/embeddings endpoint."""
    api_key = settings.get_embedding_api_key()
    base_url = settings.get_embedding_base_url()
    model = settings.get_embedding_model()

    if not api_key or not model:
        return None

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        response = await client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in response.data]
    except Exception as e:
        print(f"[Embedding] API call failed: {e}", flush=True)
        return None


async def embed(texts: list[str]) -> list[list[float]] | None:
    """Return embedding vectors for a batch of texts, or None if unavailable."""
    if not texts:
        return None

    # Tier 1: local fastembed (if configured)
    if settings.embedding_local:
        model = _get_fastembed_model()
        if model:
            try:
                result = list(model.embed(texts))
                return [r.tolist() for r in result]
            except Exception as e:
                print(f"[Embedding] local failed: {e}, falling back", flush=True)

    # Tier 2: API
    return await _embed_api(texts)


async def embed_single(text: str) -> list[float] | None:
    """Return a single embedding vector, or None."""
    result = await embed([text])
    return result[0] if result else None

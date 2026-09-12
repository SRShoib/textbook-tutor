"""
What: the one place that loads bge-m3 and turns text into dense + sparse
      vectors. Both ingest.py (whole-book batches, at upload time) and
      retrieve.py (single query strings, at question time) import
      embed_texts() from here, so the model is loaded into GPU memory once
      per process no matter which module touches it first.
Why here and not inline in ingest.py: CLAUDE.md's hardware section says not
      to load two large models at once — that budget is per process, so
      ingest.py and retrieve.py must share one singleton rather than each
      loading its own copy of bge-m3. Splitting it out also means retrieve.py
      does not have to import the PDF-parsing module just to reach the
      embedder.
"""

from __future__ import annotations

_embedding_model = None


def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from FlagEmbedding import BGEM3FlagModel

        from app.core.config import get_settings

        settings = get_settings()
        _embedding_model = BGEM3FlagModel(settings.embedding_model, device=settings.device, use_fp16=True)
    return _embedding_model


def embed_texts(texts: list[str]) -> tuple[list[list[float]], list[dict[str, float]]]:
    """One batched bge-m3 call: dense vectors + sparse (lexical weight) dicts.
    Sparse keys are stringified for JSONB storage and for the dict-based dot
    product retrieve.py does at query time."""
    model = get_embedding_model()
    result = model.encode(texts, return_dense=True, return_sparse=True, return_colbert_vecs=False)
    dense = [vec.tolist() for vec in result["dense_vecs"]]
    sparse = [{str(k): float(v) for k, v in weights.items()} for weights in result["lexical_weights"]]
    return dense, sparse

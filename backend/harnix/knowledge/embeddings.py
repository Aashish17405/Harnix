"""Embedding providers. `openai` is the real one (key from OPENAI_API_KEY); `hash` is a deterministic MOCK for tests
and offline use: a hashed bag of words, so similar wording scores high but it has no semantic understanding."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_WORD = re.compile(r"[A-Za-z0-9_]+")
HASH_DIM = 256
_SPLIT = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def _words(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text):
        out.append(w.lower())
        if "_" in w or not w.islower():
            out += [p.lower() for p in _SPLIT.findall(w.replace("_", " ")) if p.lower() != w.lower()]
    return out


class Embedder(Protocol):
    name: str  # recorded with every stored vector: vectors of different models are never compared

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashEmbedder:
    name = "mock-hash-256"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * HASH_DIM
            for w in _words(t):
                h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "big")
                v[h % HASH_DIM] += 1.0 if (h >> 63) & 1 else -1.0
            out.append(_norm(v))
        return out


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, timeout: float = 30.0, batch: int = 64):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self.model, self.batch = model, batch
        self.name = f"openai:{model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            r = self.client.embeddings.create(
                model=self.model, input=[t[:8000] for t in texts[i : i + self.batch]]
            )
            out += [_norm(list(d.embedding)) for d in r.data]
        return out


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))  # both vectors are unit length


def make_embedder(settings) -> Embedder:
    if settings.resolved_embedding_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("FORGE_EMBEDDING_PROVIDER=openai needs OPENAI_API_KEY")
        return OpenAIEmbedder(settings.openai_api_key, settings.embedding_model)
    return HashEmbedder()


# How a document chunk is turned into text before it is embedded. "ctx1": the chunk is prefixed with its source's kind,
# id and title, and its section heading (contextual chunk embedding), so a BRD's "2.2 Objective" section is not
# interchangeable with another BRD's. Stored vectors carry the tag; vectors written another way are re-embedded by
# `Ingestor.rebuild` and are not compared with the current ones in the meantime.
CHUNK_FORMAT = "ctx1"


def doc_model(embedder: Embedder) -> str:
    """The `embedding_model` value stored on document chunks: the model plus how the chunk text was written."""
    return f"{embedder.name}|{CHUNK_FORMAT}"

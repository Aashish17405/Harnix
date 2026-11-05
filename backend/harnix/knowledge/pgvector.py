"""Optional vector search inside PostgreSQL with the pgvector extension (FORGE_KNOWLEDGE_VECTOR=pgvector).

The JSON `embedding` column stays the source of truth; `embedding_vec` is a copy the database can rank with `<=>`
(cosine distance). Copies are made lazily before a search, so ingestion code does not change. Without this the
service uses PythonIndex (same results, slower on a large corpus).
"""

from __future__ import annotations

from sqlalchemy import text

from harnix.knowledge.repo import KnowledgeRepo


class PgVectorUnavailable(Exception):
    pass


def ensure(repo: KnowledgeRepo) -> None:
    """Create the extension and the column (idempotent). Needs a PostgreSQL image that ships pgvector."""
    eng = repo.store.engine
    if eng.dialect.name != "postgresql":
        raise PgVectorUnavailable("FORGE_KNOWLEDGE_VECTOR=pgvector needs PostgreSQL (FORGE_DATABASE_URL)")
    try:
        with eng.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            c.execute(text("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector"))
    except Exception as e:  # noqa: BLE001
        raise PgVectorUnavailable(
            f"cannot enable pgvector ({type(e).__name__}): use a PostgreSQL image with the extension, e.g. pgvector/pgvector:pg16"
        ) from None


class PgVectorIndex:
    def __init__(self, repo: KnowledgeRepo):
        self.repo = repo
        self._ready = False

    def search(self, project: str, qvec: list[float], model: str, kinds: list[str] | None, limit: int) -> list[tuple[str, float]]:  # fmt: skip
        if not self._ready:
            ensure(self.repo)
            self._ready = True
        q = "[" + ",".join(f"{x:.7f}" for x in qvec) + "]"
        with self.repo.store.engine.begin() as c:
            c.execute(text("UPDATE knowledge_chunks SET embedding_vec = CAST(CAST(embedding AS text) AS vector) WHERE embedding_vec IS NULL AND embedding IS NOT NULL"))  # fmt: skip
            sql = (
                "SELECT k.id, 1 - (k.embedding_vec <=> CAST(:q AS vector)) FROM knowledge_chunks k "
                "JOIN knowledge_sources s ON s.id = k.source_id "
                "WHERE k.project = :p AND k.embedding_model = :m AND k.embedding_vec IS NOT NULL"
            )
            params: dict[str, object] = {"q": q, "p": project, "m": model, "n": limit}
            if kinds:
                sql += " AND s.kind = ANY(:kinds)"
                params["kinds"] = list(kinds)
            rows = c.execute(
                text(sql + " ORDER BY k.embedding_vec <=> CAST(:q AS vector) LIMIT :n"), params
            ).all()
        return [(r[0], float(r[1])) for r in rows]

"""Neo4j access for the knowledge graph. The only module that talks to the graph database.

Graph model (every node carries `repo_id`; a repository is a namespace, so repositories never mix):

    (:Repo {uid})                      one per repository: version, completeness, counts
    (:File {uid, repo_id, path, ...})  one per indexed file
    (:Symbol {uid, repo_id, ...})      class / function / method (names, signature, line range, first doc line)
    (:External {uid, repo_id, name})   a top-level module outside the repository
    (File)-[:DEFINES]->(Symbol)   (Symbol)-[:CONTAINS]->(Symbol)   (File)-[:IMPORTS]->(File)
    (File)-[:IMPORTS_EXTERNAL]->(External)   (File)-[:TESTS]->(File)
    (Symbol)-[:CALLS]->(Symbol)   (Symbol)-[:INHERITS]->(Symbol)

Rules that keep this safe at scale:
* every statement is a constant string with parameters (no Cypher is ever built from user, model or repository
  text; labels and relationship types are constants);
* writes are idempotent (MERGE / delete-and-recreate per file) and batched with UNWIND;
* every read has a server-side transaction timeout and a LIMIT;
* the graph holds no source text: names, signatures, line ranges and one docstring line only.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from neo4j import Driver, GraphDatabase, ManagedTransaction, unit_of_work
from neo4j.exceptions import AuthError, ClientError, DriverError, Neo4jError, ServiceUnavailable

from harnix.graph.extract import FileFacts, SymbolFacts

T = TypeVar("T")
BATCH = 1000
WRITE_TIMEOUT = (
    55.0  # just under the server-side ceiling set in docker-compose.yml (db.transaction.timeout=60s)
)

SCHEMA = [
    "CREATE CONSTRAINT forge_repo_uid IF NOT EXISTS FOR (n:Repo) REQUIRE n.uid IS UNIQUE",
    "CREATE CONSTRAINT forge_file_uid IF NOT EXISTS FOR (n:File) REQUIRE n.uid IS UNIQUE",
    "CREATE CONSTRAINT forge_symbol_uid IF NOT EXISTS FOR (n:Symbol) REQUIRE n.uid IS UNIQUE",
    "CREATE CONSTRAINT forge_external_uid IF NOT EXISTS FOR (n:External) REQUIRE n.uid IS UNIQUE",
    "CREATE INDEX forge_file_repo IF NOT EXISTS FOR (n:File) ON (n.repo_id)",
    "CREATE INDEX forge_file_repo_path IF NOT EXISTS FOR (n:File) ON (n.repo_id, n.path)",
    "CREATE INDEX forge_file_repo_name IF NOT EXISTS FOR (n:File) ON (n.repo_id, n.name)",
    "CREATE INDEX forge_symbol_repo IF NOT EXISTS FOR (n:Symbol) ON (n.repo_id)",
    "CREATE INDEX forge_symbol_repo_name IF NOT EXISTS FOR (n:Symbol) ON (n.repo_id, n.name)",
    "CREATE INDEX forge_symbol_repo_path_name IF NOT EXISTS FOR (n:Symbol) ON (n.repo_id, n.path, n.name)",
    "CREATE TEXT INDEX forge_symbol_lname IF NOT EXISTS FOR (n:Symbol) ON (n.lname)",
]


class GraphUnavailable(Exception):
    """The graph database cannot be used right now (down, bad credentials, timeout...). Never carries secrets."""


def file_uid(repo_id: str, path: str) -> str:
    return f"{repo_id}|{path}"


def symbol_uid(repo_id: str, path: str, qualname: str, start: int) -> str:
    return f"{repo_id}|{path}|{qualname}|{start}"


def _chunks(items: list[T], n: int = BATCH) -> Iterator[list[T]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _describe(e: Exception) -> str:
    if isinstance(e, AuthError):
        return "Neo4j rejected the credentials (check NEO4J_PASSWORD and FORGE_NEO4J_USER)"
    if isinstance(e, ServiceUnavailable):
        return f"cannot reach Neo4j ({str(e)[:160] or 'service unavailable'}). Start it with: docker compose up -d neo4j"
    if isinstance(e, ClientError) and "timed out" in str(e).lower():
        return "Neo4j query timed out"
    return f"{type(e).__name__}: {str(e)[:200]}"


class GraphStore:
    def __init__(
        self, uri: str, user: str, password: str, database: str = "neo4j", *, query_timeout: float = 10.0,
        driver: Driver | None = None,
    ):  # fmt: skip
        self.uri, self.database, self.query_timeout = uri, database, query_timeout
        self._driver = driver or GraphDatabase.driver(
            uri, auth=(user, password), max_connection_pool_size=20, connection_acquisition_timeout=10,
            connection_timeout=3, max_transaction_retry_time=10, keep_alive=True,
            # "IF NOT EXISTS" no-ops are INFORMATION; unknown property keys in a fresh database are UNRECOGNIZED:
            # both are expected noise. Performance warnings (e.g. a cartesian product) still come through.
            notifications_min_severity="WARNING", notifications_disabled_classifications=["UNRECOGNIZED"],
        )  # fmt: skip
        self._schema_ready = False
        self._lock = threading.Lock()

    def close(self) -> None:
        self._driver.close()

    # ---- plumbing ---------------------------------------------------------------------------------
    def _read(self, fn: Callable[[ManagedTransaction], T], timeout: float | None = None) -> T:
        wrapped = unit_of_work(timeout=timeout or self.query_timeout)(lambda tx: fn(tx))
        try:
            with self._driver.session(database=self.database) as s:
                return s.execute_read(wrapped)
        except (Neo4jError, DriverError) as e:
            raise GraphUnavailable(_describe(e)) from e

    def _write(self, fn: Callable[[ManagedTransaction], T], timeout: float = WRITE_TIMEOUT) -> T:
        wrapped = unit_of_work(timeout=timeout)(lambda tx: fn(tx))
        try:
            with self._driver.session(database=self.database) as s:
                return s.execute_write(wrapped)
        except (Neo4jError, DriverError) as e:
            raise GraphUnavailable(_describe(e)) from e

    def health(self) -> tuple[bool, str]:
        try:
            self._driver.verify_connectivity()
            self._read(lambda tx: tx.run("RETURN 1").consume(), timeout=3)
            return True, f"connected to {self.uri}"
        except GraphUnavailable as e:
            return False, str(e)
        except (Neo4jError, DriverError) as e:
            return False, _describe(e)

    def ensure_schema(self) -> None:
        with self._lock:
            if self._schema_ready:
                return

            def run(tx: ManagedTransaction) -> None:
                for stmt in SCHEMA:
                    tx.run(stmt).consume()  # type: ignore[arg-type]

            self._write(run)
            self._schema_ready = True

    # ---- repository state -------------------------------------------------------------------------
    def repo_state(self, repo_id: str) -> dict[str, Any] | None:
        def run(tx: ManagedTransaction) -> dict[str, Any] | None:
            rec = tx.run("MATCH (r:Repo {uid: $r}) RETURN properties(r) AS p", r=repo_id).single()
            return dict(rec["p"]) if rec else None

        return self._read(run)

    def file_records(self, repo_id: str) -> dict[str, tuple[int, int, str | None]]:
        """path -> (size, mtime_ns, sha256) of everything indexed: the stat cache for incremental runs."""

        def run(tx: ManagedTransaction) -> dict[str, tuple[int, int, str | None]]:
            res = tx.run(
                "MATCH (f:File {repo_id: $r}) RETURN f.path AS p, f.size AS s, f.mtime_ns AS m, f.sha256 AS h",
                r=repo_id,
            )
            return {x["p"]: (x["s"], x["m"], x["h"]) for x in res}

        return self._read(run, timeout=50)

    def python_paths(self, repo_id: str) -> list[str]:
        def run(tx: ManagedTransaction) -> list[str]:
            res = tx.run("MATCH (f:File {repo_id: $r, language: 'python'}) RETURN f.path AS p", r=repo_id)
            return [x["p"] for x in res]

        return self._read(run, timeout=50)

    def importers_of(self, repo_id: str, paths: list[str], limit: int = 100_000) -> list[dict[str, Any]]:
        """Files that directly import any of `paths` (one BFS level)."""

        def run(tx: ManagedTransaction) -> list[dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for chunk in _chunks(paths, 500):
                res = tx.run(
                    "MATCH (d:File {repo_id: $r})-[:IMPORTS]->(f:File {repo_id: $r}) WHERE f.path IN $paths "
                    "RETURN DISTINCT d.path AS path, d.is_test AS is_test LIMIT $limit",
                    r=repo_id, paths=chunk, limit=limit,
                )  # fmt: skip
                for x in res:
                    out[x["path"]] = {"path": x["path"], "is_test": bool(x["is_test"])}
            return list(out.values())[:limit]

        return self._read(run, timeout=50)

    def files_importing_modules(self, repo_id: str, names: list[str]) -> list[str]:
        """Files whose raw imports mention any dotted module name in `names` (they may resolve differently now)."""
        if not names:
            return []

        def run(tx: ManagedTransaction) -> list[str]:
            res = tx.run(
                "MATCH (f:File {repo_id: $r, language: 'python'}) WHERE any(m IN f.imports_mods WHERE m IN $names) "
                "RETURN f.path AS p",
                r=repo_id, names=names,
            )  # fmt: skip
            return [x["p"] for x in res]

        return self._read(run, timeout=50)

    # ---- writes -----------------------------------------------------------------------------------
    def upsert_files(self, repo_id: str, files: list[FileFacts]) -> None:
        """Replace everything stored for these files (symbols, outgoing resolved edges) with the new facts."""
        if not files:
            return
        file_rows, sym_rows, contain_rows = [], [], []
        for f in files:
            fuid = file_uid(repo_id, f.path)
            name = f.path.rsplit("/", 1)[-1]
            mods: list[str] = []
            for row in f.imports:
                if row[0] == "i":
                    mods.append(row[1])
                else:
                    if row[1]:
                        mods.append(row[1])
                    mods += [f"{row[1]}.{n}" if row[1] else n for n, _ in row[3]]
            file_rows.append({"uid": fuid, "props": {
                "repo_id": repo_id, "path": f.path, "name": name, "dir": f.path.rpartition("/")[0],
                "language": f.language, "size": f.size, "lines": f.lines, "sha256": f.sha256, "mtime_ns": f.mtime_ns,
                "is_test": f.is_test, "parsed": f.parsed, "error": f.error, "in_degree": 0,
                "imports_raw": json.dumps(f.imports, separators=(",", ":")), "imports_mods": sorted(set(mods))[:500],
            }})  # fmt: skip
            uids: dict[str, str] = {}
            for s in f.symbols:
                uid = symbol_uid(repo_id, f.path, s.qualname, s.start)
                uids.setdefault(s.qualname, uid)
                sym_rows.append(_symbol_row(repo_id, fuid, uid, f, s))
            for s in f.symbols:
                if s.parent and s.parent in uids:
                    contain_rows.append(
                        {"parent": uids[s.parent], "child": symbol_uid(repo_id, f.path, s.qualname, s.start)}
                    )
        uids_all = [r["uid"] for r in file_rows]

        def run(tx: ManagedTransaction) -> None:
            tx.run(
                "MATCH (f:File) WHERE f.uid IN $uids OPTIONAL MATCH (f)-[:DEFINES]->(s:Symbol) DETACH DELETE s",
                uids=uids_all,
            ).consume()
            tx.run(
                "MATCH (f:File)-[e:IMPORTS|IMPORTS_EXTERNAL|TESTS]->() WHERE f.uid IN $uids DELETE e",
                uids=uids_all,
            ).consume()
            tx.run("UNWIND $rows AS r MERGE (f:File {uid: r.uid}) SET f += r.props", rows=file_rows).consume()
            for chunk in _chunks(sym_rows, 2000):
                tx.run(
                    "UNWIND $rows AS r MATCH (f:File {uid: r.file}) "
                    "CREATE (s:Symbol {uid: r.uid}) SET s += r.props CREATE (f)-[:DEFINES]->(s)",
                    rows=chunk,
                ).consume()
            for chunk in _chunks(contain_rows, 2000):
                tx.run(
                    "UNWIND $rows AS r MATCH (a:Symbol {uid: r.parent}) MATCH (b:Symbol {uid: r.child}) "
                    "CREATE (a)-[:CONTAINS]->(b)",
                    rows=chunk,
                ).consume()

        self._write(run)

    def delete_files(self, repo_id: str, paths: list[str]) -> None:
        def run(tx: ManagedTransaction) -> None:
            for chunk in _chunks(paths, 500):
                tx.run(
                    "MATCH (f:File {repo_id: $r}) WHERE f.path IN $paths "
                    "OPTIONAL MATCH (f)-[:DEFINES]->(s:Symbol) DETACH DELETE s, f",
                    r=repo_id, paths=chunk,
                ).consume()  # fmt: skip

        if paths:
            self._write(run)

    def clear_edges(self, repo_id: str, paths: list[str]) -> None:
        """Remove the resolved outgoing edges of these files and of their symbols (they are recomputed)."""

        def run(tx: ManagedTransaction) -> None:
            for chunk in _chunks(paths, 500):
                tx.run(
                    "MATCH (f:File {repo_id: $r})-[e:IMPORTS|IMPORTS_EXTERNAL|TESTS]->() WHERE f.path IN $paths DELETE e",
                    r=repo_id, paths=chunk,
                ).consume()  # fmt: skip
                tx.run(
                    "MATCH (f:File {repo_id: $r})-[:DEFINES]->(s:Symbol)-[e:CALLS|INHERITS]->() "
                    "WHERE f.path IN $paths DELETE e",
                    r=repo_id, paths=chunk,
                ).consume()  # fmt: skip

        if paths:
            self._write(run)

    def load_raw(self, repo_id: str, paths: list[str]) -> list[dict[str, Any]]:
        """Stored raw facts (imports, symbols with their raw calls/bases) for re-resolution."""

        def run(tx: ManagedTransaction) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for chunk in _chunks(paths, 200):
                res = tx.run(
                    "MATCH (f:File {repo_id: $r}) WHERE f.path IN $paths OPTIONAL MATCH (f)-[:DEFINES]->(s:Symbol) "
                    "RETURN f.path AS path, f.is_test AS is_test, f.imports_raw AS imports, "
                    "collect(CASE WHEN s IS NULL THEN null ELSE {uid: s.uid, name: s.name, qualname: s.qualname, "
                    "top: s.top, kind: s.kind, calls: s.calls_raw, bases: s.bases_raw} END) AS symbols",
                    r=repo_id, paths=chunk,
                )  # fmt: skip
                out += [dict(x) for x in res]
            return out

        return self._read(run, timeout=50)

    def write_edges(
        self, repo_id: str, *, imports: list[tuple[str, str]], externals: list[tuple[str, str]],
        tests: list[tuple[str, str]], calls: list[dict[str, Any]], inherits: list[dict[str, Any]],
    ) -> None:  # fmt: skip
        """Edge writers take file paths / symbol uids; targets that do not exist are silently skipped (MATCH)."""

        def run(tx: ManagedTransaction) -> None:
            file_pair = "UNWIND $rows AS r MATCH (a:File {uid: r.src}) MATCH (b:File {uid: r.dst}) MERGE (a)-[:%s]->(b)"
            for rel, pairs in (("IMPORTS", imports), ("TESTS", tests)):
                rows = [{"src": file_uid(repo_id, a), "dst": file_uid(repo_id, b)} for a, b in pairs]
                for chunk in _chunks(rows, 2000):
                    tx.run(file_pair % rel, rows=chunk).consume()  # type: ignore[arg-type]
            ext_rows = [
                {"src": file_uid(repo_id, a), "name": n, "uid": f"{repo_id}|ext|{n}"} for a, n in externals
            ]
            for chunk in _chunks(ext_rows, 2000):
                tx.run(
                    "UNWIND $rows AS r MATCH (a:File {uid: r.src}) MERGE (x:External {uid: r.uid}) "
                    "SET x.repo_id = $repo, x.name = r.name MERGE (a)-[:IMPORTS_EXTERNAL]->(x)",
                    rows=chunk, repo=repo_id,
                ).consume()  # fmt: skip
            sym_pair = (
                "UNWIND $rows AS r MATCH (a:Symbol {uid: r.src}) "
                "MATCH (b:Symbol {repo_id: $repo, path: r.path, name: r.name}) "
                "WHERE a <> b AND ((r.qual IS NULL AND b.top) OR b.qualname = r.qual) MERGE (a)-[:%s]->(b)"
            )
            for rel, rows in (("CALLS", calls), ("INHERITS", inherits)):
                for chunk in _chunks(rows, 2000):
                    tx.run(sym_pair % rel, rows=chunk, repo=repo_id).consume()  # type: ignore[arg-type]

        self._write(run)

    def finish_index(
        self, repo_id: str, name: str, *, complete: bool, changed: bool, notes: list[str]
    ) -> dict[str, Any]:
        """Bump the version, recompute importance and counts (only when something changed), return the state."""

        def run(tx: ManagedTransaction) -> dict[str, Any]:
            tx.run(
                "MERGE (r:Repo {uid: $r}) ON CREATE SET r.version = 0, r.repo_id = $r "
                "SET r.name = $name, r.complete = $complete, r.notes = $notes, r.indexed_at = timestamp(), "
                "r.version = r.version + $inc",
                r=repo_id, name=name, complete=complete, notes=notes[:10], inc=1 if changed else 0,
            ).consume()  # fmt: skip
            if changed:
                tx.run(
                    "MATCH (f:File {repo_id: $r}) OPTIONAL MATCH (f)<-[e:IMPORTS]-() "
                    "WITH f, count(e) AS d SET f.in_degree = d",
                    r=repo_id,
                ).consume()
                counts = tx.run(
                    "MATCH (f:File {repo_id: $r}) WITH count(f) AS files "
                    "OPTIONAL MATCH (s:Symbol {repo_id: $r}) WITH files, count(s) AS symbols "
                    "OPTIONAL MATCH (:File {repo_id: $r})-[i:IMPORTS]->() WITH files, symbols, count(i) AS imports "
                    "OPTIONAL MATCH (:Symbol {repo_id: $r})-[c:CALLS]->() "
                    "RETURN files, symbols, imports, count(c) AS calls",
                    r=repo_id,
                ).single()
                if counts is not None:
                    tx.run(
                        "MATCH (r:Repo {uid: $r}) SET r.files = $files, r.symbols = $symbols, "
                        "r.imports = $imports, r.calls = $calls",
                        r=repo_id, **dict(counts),
                    ).consume()  # fmt: skip
            rec = tx.run("MATCH (r:Repo {uid: $r}) RETURN properties(r) AS p", r=repo_id).single()
            return dict(rec["p"]) if rec else {}

        return self._write(run)

    def drop_repo(self, repo_id: str) -> int:
        """Delete everything stored for one repository (derived data; it can be rebuilt)."""
        total = 0

        def run(tx: ManagedTransaction) -> int:
            rec = tx.run(
                "MATCH (n {repo_id: $r}) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c", r=repo_id
            ).single()
            return int(rec["c"]) if rec else 0

        while True:
            n = self._write(run)
            total += n
            if n == 0:
                return total

    # ---- queries (all bounded) --------------------------------------------------------------------
    def overview(self, repo_id: str, top_n: int) -> dict[str, Any]:
        def run(tx: ManagedTransaction) -> dict[str, Any]:
            langs = tx.run(
                "MATCH (f:File {repo_id: $r}) RETURN f.language AS l, count(*) AS c ORDER BY c DESC LIMIT 12",
                r=repo_id,
            )
            dirs = tx.run(
                "MATCH (f:File {repo_id: $r}) RETURN split(f.dir, '/')[0] AS d, count(*) AS c ORDER BY c DESC LIMIT 12",
                r=repo_id,
            )
            central = tx.run(
                "MATCH (f:File {repo_id: $r}) WHERE f.in_degree > 0 AND NOT f.is_test "
                "RETURN f.path AS path, f.in_degree AS dependents ORDER BY f.in_degree DESC, f.path LIMIT $n",
                r=repo_id, n=top_n,
            )  # fmt: skip
            return {
                "languages": [(x["l"], x["c"]) for x in langs],
                "top_dirs": [(x["d"] or "(root)", x["c"]) for x in dirs],
                "central": [dict(x) for x in central],
            }

        return self._read(run)

    def file_profile(self, repo_id: str, path: str, n: int) -> dict[str, Any] | None:
        uid = file_uid(repo_id, path)

        def run(tx: ManagedTransaction) -> dict[str, Any] | None:
            head = tx.run(
                "MATCH (f:File {uid: $u}) RETURN f.language AS language, f.lines AS lines, f.is_test AS is_test, "
                "f.in_degree AS dependents, f.parsed AS parsed, f.error AS error",
                u=uid,
            ).single()
            if head is None:
                return None
            syms = tx.run(
                "MATCH (:File {uid: $u})-[:DEFINES]->(s:Symbol) RETURN s.kind AS kind, s.qualname AS qualname, "
                "s.start AS start, s.end AS end, s.signature AS signature ORDER BY s.start LIMIT $n",
                u=uid, n=n,
            )  # fmt: skip
            imports = tx.run(
                "MATCH (:File {uid: $u})-[:IMPORTS]->(t:File) RETURN t.path AS path ORDER BY t.in_degree DESC, t.path LIMIT $n",
                u=uid, n=n,
            )  # fmt: skip
            importers = tx.run(
                "MATCH (d:File)-[:IMPORTS]->(:File {uid: $u}) RETURN d.path AS path, d.is_test AS is_test "
                "ORDER BY d.is_test, d.path LIMIT $n",
                u=uid, n=n,
            )  # fmt: skip
            ext = tx.run(
                "MATCH (:File {uid: $u})-[:IMPORTS_EXTERNAL]->(x:External) RETURN x.name AS name ORDER BY name LIMIT $n",
                u=uid, n=n,
            )  # fmt: skip
            return {
                **dict(head), "symbols": [dict(x) for x in syms], "imports": [x["path"] for x in imports],
                "imported_by": [dict(x) for x in importers], "external": [x["name"] for x in ext],
            }  # fmt: skip

        return self._read(run)

    def symbols_named(self, repo_id: str, name: str, n: int) -> list[dict[str, Any]]:
        def run(tx: ManagedTransaction) -> list[dict[str, Any]]:
            res = tx.run(
                "MATCH (s:Symbol {repo_id: $r, name: $name}) RETURN s.kind AS kind, s.qualname AS qualname, "
                "s.path AS path, s.start AS start, s.end AS end, s.signature AS signature, s.doc AS doc "
                "ORDER BY s.top DESC, s.path LIMIT $n",
                r=repo_id, name=name, n=n,
            )  # fmt: skip
            return [dict(x) for x in res]

        return self._read(run)

    def search_symbols(self, repo_id: str, fragment: str, n: int) -> list[dict[str, Any]]:
        def run(tx: ManagedTransaction) -> list[dict[str, Any]]:
            res = tx.run(
                "MATCH (s:Symbol) WHERE s.lname CONTAINS $q AND s.repo_id = $r "
                "RETURN s.kind AS kind, s.qualname AS qualname, s.path AS path, s.start AS start, "
                "s.signature AS signature ORDER BY CASE WHEN s.lname = $q THEN 0 WHEN s.lname STARTS WITH $q THEN 1 "
                "ELSE 2 END, size(s.name), s.path LIMIT $n",
                q=fragment.lower(), r=repo_id, n=n,
            )  # fmt: skip
            return [dict(x) for x in res]

        return self._read(run)

    def callers(self, repo_id: str, name: str, n: int) -> list[dict[str, Any]]:
        def run(tx: ManagedTransaction) -> list[dict[str, Any]]:
            res = tx.run(
                "MATCH (t:Symbol {repo_id: $r, name: $name}) WITH t LIMIT 20 MATCH (c:Symbol)-[:CALLS]->(t) "
                "RETURN t.qualname AS target, t.path AS target_path, c.qualname AS caller, c.path AS caller_path, "
                "c.start AS line ORDER BY c.path, c.start LIMIT $n",
                r=repo_id, name=name, n=n,
            )  # fmt: skip
            return [dict(x) for x in res]

        return self._read(run)

    def callees(self, repo_id: str, name: str, n: int) -> list[dict[str, Any]]:
        def run(tx: ManagedTransaction) -> list[dict[str, Any]]:
            res = tx.run(
                "MATCH (c:Symbol {repo_id: $r, name: $name}) WITH c LIMIT 20 MATCH (c)-[:CALLS]->(t:Symbol) "
                "RETURN c.qualname AS caller, c.path AS caller_path, t.qualname AS target, t.path AS target_path, "
                "t.start AS line ORDER BY t.path, t.start LIMIT $n",
                r=repo_id, name=name, n=n,
            )  # fmt: skip
            return [dict(x) for x in res]

        return self._read(run)

    def hierarchy(self, repo_id: str, name: str, n: int) -> dict[str, list[dict[str, Any]]]:
        def run(tx: ManagedTransaction) -> dict[str, list[dict[str, Any]]]:
            up = tx.run(
                "MATCH (c:Symbol {repo_id: $r, name: $name, kind: 'class'}) WITH c LIMIT 20 "
                "MATCH (c)-[:INHERITS]->(b:Symbol) RETURN c.qualname AS cls, b.qualname AS base, b.path AS path LIMIT $n",
                r=repo_id, name=name, n=n,
            )  # fmt: skip
            down = tx.run(
                "MATCH (c:Symbol {repo_id: $r, name: $name, kind: 'class'}) WITH c LIMIT 20 "
                "MATCH (s:Symbol)-[:INHERITS]->(c) RETURN c.qualname AS cls, s.qualname AS sub, s.path AS path LIMIT $n",
                r=repo_id, name=name, n=n,
            )  # fmt: skip
            return {"bases": [dict(x) for x in up], "subclasses": [dict(x) for x in down]}

        return self._read(run)

    def existing_paths(self, repo_id: str, paths: list[str]) -> set[str]:
        if not paths:
            return set()

        def run(tx: ManagedTransaction) -> set[str]:
            res = tx.run(
                "MATCH (f:File {repo_id: $r}) WHERE f.path IN $p RETURN f.path AS p", r=repo_id, p=paths[:200]
            )
            return {x["p"] for x in res}

        return self._read(run)

    def paths_named(self, repo_id: str, name: str, n: int = 3) -> list[str]:
        def run(tx: ManagedTransaction) -> list[str]:
            res = tx.run(
                "MATCH (f:File {repo_id: $r, name: $n}) RETURN f.path AS p LIMIT $lim",
                r=repo_id,
                n=name,
                lim=n,
            )
            return [x["p"] for x in res]

        return self._read(run)

    def symbol_name_counts(self, repo_id: str, names: list[str]) -> dict[str, int]:
        if not names:
            return {}

        def run(tx: ManagedTransaction) -> dict[str, int]:
            res = tx.run(
                "MATCH (s:Symbol {repo_id: $r}) WHERE s.name IN $names RETURN s.name AS n, count(*) AS c",
                r=repo_id, names=names[:100],
            )  # fmt: skip
            return {x["n"]: x["c"] for x in res}

        return self._read(run)


def _symbol_row(repo_id: str, file_uid_: str, uid: str, f: FileFacts, s: SymbolFacts) -> dict[str, Any]:
    return {"uid": uid, "file": file_uid_, "props": {
        "repo_id": repo_id, "kind": s.kind, "name": s.name, "lname": s.name.lower(), "qualname": s.qualname,
        "path": f.path, "start": s.start, "end": s.end, "signature": s.signature, "doc": s.doc, "top": s.top,
        "is_test": f.is_test, "calls_raw": json.dumps(s.calls, separators=(",", ":")),
        "bases_raw": json.dumps(s.bases, separators=(",", ":")),
    }}  # fmt: skip


__all__: Iterable[str] = ["GraphStore", "GraphUnavailable", "file_uid", "symbol_uid"]

"""Knowledge graph against a real Neo4j (skipped when it is not reachable: `docker compose up -d neo4j`)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from harnix.graph import indexer as indexer_mod
from harnix.graph.indexer import RepoIndexer
from harnix.graph.query import RepoGraph
from harnix.graph.store import GraphStore, GraphUnavailable

FILES = {
    "pkg/__init__.py": "",
    "pkg/util.py": "class Base:\n    def base_method(self):\n        return 1\n\n\ndef helper(x):\n    return x\n\n\ndef other():\n    return 2\n",
    "pkg/core.py": (
        "import os\nimport pkg.util as u\nfrom pkg.util import Base, helper\n\n\n"
        "class Service(Base):\n    def run(self):\n        return self.step() + helper(1) + u.other()\n\n"
        "    def step(self):\n        return os.getpid()\n\n\ndef top():\n    return Service()\n"
    ),
    "app.py": "from pkg import core\nfrom pkg import missing_mod\n\n\ndef main():\n    return core.top()\n",
    "tests/test_core.py": "from pkg.core import top\n\n\ndef test_top():\n    assert top()\n",
}


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return write(tmp_path / "proj", FILES)


def indexer(store: GraphStore, **kw: Any) -> RepoIndexer:
    return RepoIndexer(store, max_files=kw.pop("max_files", 1000), max_parse_bytes=1_000_000)


def view(store: GraphStore, rid: str, **kw: Any) -> RepoGraph:
    return RepoGraph(store, rid, version=1, complete=True, max_results=25, **kw)


def names(rows: list[dict[str, Any]], key: str) -> list[str]:
    return sorted(r[key] for r in rows)


# ---- graph content --------------------------------------------------------------------------------------


def test_index_builds_files_symbols_and_resolved_edges(neo: GraphStore, project: Path):
    rid = neo.new_repo_id()
    rep = indexer(neo).index(project, rid, "proj", time_budget=60)
    assert rep.complete and rep.files_parsed == 5 and rep.files_unchanged == 0 and not rep.truncated
    state = neo.repo_state(rid)
    assert state and (state["files"], state["symbols"], state["imports"], state["calls"]) == (5, 10, 5, 6)
    assert state["version"] == 1 and state["complete"] is True

    g = view(neo, rid)
    assert names(g.op("callers", "helper")["results"], "caller") == ["Service.run"]
    assert names(g.op("callers", "Service.step")["results"], "caller") == ["Service.run"]  # self.step()
    assert names(g.op("callers", "other")["results"], "caller") == [
        "Service.run"
    ]  # through the alias `u.other()`
    assert names(g.op("callers", "top")["results"], "caller") == [
        "main",
        "test_top",
    ]  # module alias + imported name
    assert names(g.op("callees", "Service.run")["results"], "calls") == ["Service.step", "helper", "other"]
    assert [r["calls"] for r in g.op("callees", "top")["results"]] == ["Service"]  # constructor call -> class
    h = g.op("hierarchy", "Base")
    assert [r["subclass"] for r in h["subclasses"]] == ["Service"]
    assert [r["base"] for r in g.op("hierarchy", "Service")["bases"]] == ["Base"]

    info = g.op("file_info", "pkg/core.py")
    assert info["found"] and set(info["imports"]) == {"pkg/util.py", "pkg/__init__.py"}
    assert info["external"] == ["os"] and info["language"] == "python" and not info["is_test"]
    assert {(d["path"], d["is_test"]) for d in info["imported_by"]} == {
        ("app.py", False),
        ("tests/test_core.py", True),
    }
    assert [s["qualname"] for s in info["symbols"]] == ["Service", "Service.run", "Service.step", "top"]

    deps = {r["path"]: r["distance"] for r in g.op("dependents", "pkg/util.py", depth=3)["results"]}
    assert deps == {"pkg/core.py": 1, "app.py": 2, "tests/test_core.py": 2}
    assert {r["path"]: r["distance"] for r in g.op("dependents", "pkg/util.py", depth=1)["results"]} == {
        "pkg/core.py": 1
    }
    assert [r["path"] for r in g.op("tests_for", "pkg/util.py")["results"]] == ["tests/test_core.py"]
    assert [r["name"] for r in g.op("search_symbols", "HELP")["results"]] == ["helper"]  # case-insensitive
    assert g.op("find_symbol", "helper")["results"][0]["signature"] == "helper(x)"
    missing = g.op("file_info", "pkg/nope.py")
    assert missing["found"] is False and missing["similar_paths"] == [] and "snapshot" in missing
    assert g.op("file_info", "core.py")["similar_paths"] == [
        "pkg/core.py"
    ]  # a near miss suggests the right path


def test_overview_ranks_by_dependents_and_counts_languages(neo: GraphStore, project: Path):
    rid = neo.new_repo_id()
    indexer(neo).index(project, rid, "proj", time_budget=60)
    ov = neo.overview(rid, 5)
    assert ov["languages"] == [("python", 5)]
    assert [(c["path"], c["dependents"]) for c in ov["central"]] == [
        ("pkg/__init__.py", 2),
        ("pkg/core.py", 2),
        ("pkg/util.py", 1),
    ]
    assert dict(ov["top_dirs"]) == {"pkg": 3, "tests": 1, "(root)": 1}


# ---- incremental updates ---------------------------------------------------------------------------------


def test_second_run_touches_nothing_and_does_not_bump_the_version(neo: GraphStore, project: Path):
    rid = neo.new_repo_id()
    ix = indexer(neo)
    ix.index(project, rid, "proj", time_budget=60)
    again = ix.index(project, rid, "proj", time_budget=60)
    assert (again.files_parsed, again.files_unchanged, again.files_resolved, again.changed) == (
        0,
        5,
        0,
        False,
    )
    assert again.version == 1


def test_changes_reparse_only_what_changed_and_reresolve_importers(neo: GraphStore, project: Path):
    rid = neo.new_repo_id()
    ix = indexer(neo)
    ix.index(project, rid, "proj", time_budget=60)

    # rename helper -> helper2: only util.py is re-parsed, but core.py (an importer) must be re-resolved
    (project / "pkg/util.py").write_text(FILES["pkg/util.py"].replace("def helper", "def helper2"))
    rep = ix.index(project, rid, "proj", time_budget=60)
    assert rep.files_parsed == 1 and rep.files_resolved == 2 and rep.version == 2
    g = view(neo, rid)
    assert g.op("callers", "helper")["results"] == [] and g.op("find_symbol", "helper")["results"] == []
    assert names(g.op("callees", "Service.run")["results"], "calls") == ["Service.step", "other"]

    # remove util.py: its importer loses the edges, the inheritance edge disappears with its target
    (project / "pkg/util.py").unlink()
    rep = ix.index(project, rid, "proj", time_budget=60)
    assert rep.files_removed == 1 and rep.version == 3
    assert "pkg/util.py" not in g.op("file_info", "pkg/core.py")["imports"]
    assert g.op("hierarchy", "Service")["bases"] == []
    assert g.op("callees", "Service.run")["results"][0]["calls"] == "Service.step"
    assert neo.repo_state(rid)["files"] == 4  # type: ignore[index]

    # a module that satisfies an import which could not resolve before: the importer is re-resolved without being edited
    assert "pkg/missing_mod.py" not in g.op("file_info", "app.py")["imports"]
    write(project, {"pkg/missing_mod.py": "def new_fn():\n    return 1\n"})
    rep = ix.index(project, rid, "proj", time_budget=60)
    assert rep.files_parsed == 1 and rep.files_resolved >= 1
    assert "pkg/missing_mod.py" in g.op("file_info", "app.py")["imports"]


def test_interrupted_index_resumes_and_converges_to_the_same_graph(
    neo: GraphStore, project: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(indexer_mod, "READ_BATCH", 1)
    rid = neo.new_repo_id()
    cancel = threading.Event()
    first = indexer(neo).index(
        project, rid, "proj", time_budget=60, cancel=cancel, progress=lambda *_: cancel.set()
    )
    assert not first.complete and first.files_parsed == 1
    assert neo.repo_state(rid)["complete"] is False  # type: ignore[index]  # so the next run re-resolves everything

    second = indexer(neo).index(project, rid, "proj", time_budget=60)
    assert second.complete and second.files_parsed == 4  # continues where it stopped
    reference = neo.new_repo_id()
    indexer(neo).index(project, reference, "proj", time_budget=60)
    keys = ("files", "symbols", "imports", "calls")
    assert (
        [neo.repo_state(rid)[k] for k in keys]
        == [neo.repo_state(reference)[k] for k in keys]
        == [5, 10, 5, 6]
    )  # type: ignore[index]


def test_time_budget_is_respected_and_reported(neo: GraphStore, project: Path):
    rid = neo.new_repo_id()
    rep = indexer(neo).index(project, rid, "proj", time_budget=0)
    assert not rep.complete and rep.files_parsed == 0
    assert any("time budget" in n for n in rep.notes)
    assert indexer(neo).index(project, rid, "proj", time_budget=60).complete


def test_file_cap_truncates_without_deleting_what_was_already_indexed(neo: GraphStore, project: Path):
    rid = neo.new_repo_id()
    indexer(neo).index(project, rid, "proj", time_budget=60)
    capped = indexer(neo, max_files=3).index(project, rid, "proj", time_budget=60)
    assert capped.truncated and capped.files_removed == 0
    assert neo.repo_state(rid)["files"] == 5  # type: ignore[index]  # files beyond the cap were not seen, not deleted


def test_unparseable_files_are_kept_as_files_and_do_not_break_the_run(neo: GraphStore, project: Path):
    write(project, {"broken.py": "def oops(:\n"})
    rid = neo.new_repo_id()
    rep = indexer(neo).index(project, rid, "proj", time_budget=60)
    assert rep.complete
    info = view(neo, rid).op("file_info", "broken.py")
    assert (
        info["found"]
        and info["parsed"] is False
        and info["error"].startswith("SyntaxError")
        and info["symbols"] == []
    )


# ---- isolation and safety --------------------------------------------------------------------------------


def test_repositories_are_separate_namespaces(neo: GraphStore, project: Path):
    a, b = neo.new_repo_id(), neo.new_repo_id()
    indexer(neo).index(project, a, "one", time_budget=60)
    indexer(neo).index(project, b, "two", time_budget=60)
    assert neo.repo_state(a)["name"] == "one" and neo.repo_state(b)["name"] == "two"  # type: ignore[index]
    deleted = neo.drop_repo(a)
    assert (
        deleted == 17 and neo.repo_state(a) is None
    )  # 1 repo + 5 files + 10 symbols + 1 external module (os)
    assert neo.repo_state(b)["files"] == 5  # type: ignore[index]
    assert (
        view(neo, a).op("find_symbol", "helper")["results"] == []
    )  # nothing of `a` is visible, even by name
    assert len(view(neo, b).op("find_symbol", "helper")["results"]) == 1


def test_values_are_always_parameters_never_query_text(neo: GraphStore, project: Path):
    write(project, {"we'ird name.py": "def f():\n    return 1\n"})
    rid = neo.new_repo_id()
    indexer(neo).index(project, rid, "proj", time_budget=60)
    before = neo.repo_state(rid)
    g = view(neo, rid)
    attacks = [
        "x'}) DETACH DELETE n //",
        "x` OR 1=1 //",
        '" RETURN 1 //',
        "a\\'; MATCH (n) DETACH DELETE n; //",
        "%00",
        "{$ne: null}",
    ]
    for op in ("find_symbol", "search_symbols", "callers", "callees", "hierarchy"):
        for evil in attacks:
            r = g.op(op, evil)
            assert (
                not r.get("results") and not r.get("bases") and not r.get("subclasses")
            )  # literal strings match nothing
    for evil in attacks:
        assert g.op("file_info", evil)["found"] is False
    assert g.op("file_info", "we'ird name.py")["found"]  # a legitimately odd file name still works
    assert neo.repo_state(rid) == before  # nothing was deleted or altered


def test_the_graph_stores_no_source_text(neo: GraphStore, tmp_path: Path):
    secret = "hunter2-do-not-store-this-literal"
    root = write(
        tmp_path / "p",
        {
            "a.py": f'API = "{secret}"\n\ndef connect(password="{secret}"):\n    """Connect."""\n    return "{secret}"\n'
        },
    )
    rid = neo.new_repo_id()
    indexer(neo).index(root, rid, "p", time_budget=60)

    def dump(tx: Any) -> list[Any]:
        return [dict(r["p"]) for r in tx.run("MATCH (n {repo_id: $r}) RETURN properties(n) AS p", r=rid)]

    nodes = neo._read(dump)
    assert len(nodes) == 3  # the repo, the file and the one function: nothing else is stored
    assert secret not in repr(
        nodes
    )  # not in any property of any node: names, signatures and line ranges only
    sig = next(n["signature"] for n in nodes if n.get("name") == "connect")
    assert sig == "connect(password=...)"


# ---- failure modes ---------------------------------------------------------------------------------------


def test_unreachable_database_is_reported_quickly_not_hung(project: Path):
    store = GraphStore("bolt://127.0.0.1:1", "neo4j", "x", query_timeout=2)
    try:
        t = time.monotonic()
        ok, detail = store.health()
        assert not ok and "cannot reach Neo4j" in detail and "docker compose up -d neo4j" in detail
        with pytest.raises(GraphUnavailable):
            RepoIndexer(store, max_files=10, max_parse_bytes=1000).index(project, "r", "p", time_budget=5)
        assert time.monotonic() - t < 20
    finally:
        store.close()


def test_wrong_password_is_reported_without_echoing_it(neo4j_settings):
    s = neo4j_settings
    bad = "definitely-the-wrong-password-123"
    store = GraphStore(s.neo4j_uri, s.neo4j_user, bad, s.neo4j_database)
    try:
        ok, detail = store.health()
        assert not ok and "credentials" in detail and bad not in detail
    finally:
        store.close()


def test_schema_is_idempotent_and_has_the_constraints(neo: GraphStore):
    neo.ensure_schema()
    neo.ensure_schema()
    rows = neo._read(
        lambda tx: [dict(r) for r in tx.run("SHOW CONSTRAINTS YIELD name, type RETURN name, type")]
    )
    got = {r["name"]: r["type"] for r in rows}
    for n in ("forge_repo_uid", "forge_file_uid", "forge_symbol_uid", "forge_external_uid"):
        assert got.get(n) == "UNIQUENESS", n
    idx = {
        r["name"]
        for r in neo._read(lambda tx: [dict(r) for r in tx.run("SHOW INDEXES YIELD name RETURN name")])
    }
    assert {"forge_symbol_repo_name", "forge_file_repo_path", "forge_symbol_lname"} <= idx

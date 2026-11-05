"""Turn source files into graph facts (no database access here).

Python is parsed with the standard library `ast` (exact for syntactically valid code). Every other recognised
text file becomes a File fact only. New languages plug in by adding an extractor to `EXTRACTORS`; a tree-sitter
based one is the natural next step. Nothing in a source file is stored except names, signatures, line ranges
and the first docstring line: the graph never holds source text.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

LANGUAGES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".cs": "csharp", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".sh": "shell", ".sql": "sql", ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".txt": "text", ".html": "html", ".css": "css",
}  # fmt: skip
MAX_IMPORTS = 500
MAX_CALLS = 60
MAX_BASES = 10
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def language_of(path: str) -> str | None:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower())


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name.lower()
    return (
        name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js"))
        or name == "conftest.py" or any(part.lower() in ("tests", "test", "__tests__") for part in p.parts[:-1])
    )  # fmt: skip


def clean(text: str, limit: int) -> str:
    """One line, no control characters, bounded: names and docstrings come from untrusted repositories."""
    return _CTRL.sub(" ", " ".join(text.split()))[:limit].strip()


@dataclass
class SymbolFacts:
    kind: str  # class | function | method
    name: str
    qualname: str
    start: int
    end: int
    signature: str
    doc: str
    parent: str | None  # qualname of the enclosing class
    top: bool  # module-level, so other files can refer to it by name
    calls: list[list[str]] = field(default_factory=list)  # [kind, text]; kind n = name, s = self.x, a = a.b
    bases: list[str] = field(default_factory=list)


@dataclass
class FileFacts:
    path: str
    language: str
    size: int
    mtime_ns: int
    lines: int = 0
    sha256: str | None = None
    is_test: bool = False
    parsed: bool = False
    error: str | None = None
    imports: list[list] = field(
        default_factory=list
    )  # ["i", module, asname] | ["f", module, level, [[name, asname], ...]]
    symbols: list[SymbolFacts] = field(default_factory=list)


# ---- Python -----------------------------------------------------------------------------------------


def _unparse(node: ast.AST | None, limit: int = 200) -> str:
    if node is None:
        return ""
    try:
        return clean(ast.unparse(node), limit)
    except (RecursionError, ValueError, TypeError):
        return ""


def _params(a: ast.arguments) -> str:
    """Parameter names and annotations only. Default VALUES are never stored: they are source text and can be secrets."""

    def one(arg: ast.arg, has_default: bool = False) -> str:
        s = arg.arg + (f": {_unparse(arg.annotation, 60)}" if arg.annotation else "")
        return s + ("=..." if has_default else "")

    out: list[str] = []
    pos = [*a.posonlyargs, *a.args]
    defaults: list[ast.expr | None] = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for i, (arg, d) in enumerate(zip(pos, defaults, strict=True)):
        out.append(one(arg, d is not None))
        if a.posonlyargs and i == len(a.posonlyargs) - 1:
            out.append("/")
    if a.vararg:
        out.append("*" + one(a.vararg))
    elif a.kwonlyargs:
        out.append("*")
    out += [one(arg, d is not None) for arg, d in zip(a.kwonlyargs, a.kw_defaults, strict=True)]
    if a.kwarg:
        out.append("**" + one(a.kwarg))
    return ", ".join(out)


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _calls(node: ast.AST) -> list[list[str]]:
    seen: dict[tuple[str, str], None] = {}
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Name):
            seen[("n", f.id)] = None
        elif isinstance(f, ast.Attribute):
            d = _dotted(f)
            if d is None:
                continue
            base, _, rest = d.partition(".")
            if base in ("self", "cls") and "." not in rest:
                seen[("s", rest)] = None
            elif "." not in rest:  # a.b only; deeper chains are attribute access on a value we cannot resolve
                seen[("a", d)] = None
        if len(seen) >= MAX_CALLS:
            break
    return [[k, v] for k, v in seen]


def _blocks(node: ast.AST) -> list[list[ast.stmt]]:
    out = [
        getattr(node, f) for f in ("body", "orelse", "finalbody") if isinstance(getattr(node, f, None), list)
    ]
    out += [h.body for h in getattr(node, "handlers", [])]
    return out


def _symbols(body: list[ast.stmt], scope: list[str], cls: str | None, out: list[SymbolFacts]) -> None:
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            qual = ".".join([*scope, node.name])
            sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}({_params(node.args)})"
            if node.returns is not None:
                sig += f" -> {_unparse(node.returns, 60)}"
            out.append(SymbolFacts(
                "method" if cls else "function", node.name, qual, node.lineno, node.end_lineno or node.lineno,
                clean(sig, 200), clean((ast.get_docstring(node) or "").split("\n", 1)[0], 160), cls, not scope,
                _calls(node),
            ))  # fmt: skip
        elif isinstance(node, ast.ClassDef):
            qual = ".".join([*scope, node.name])
            bases = [b for b in (_dotted(x) for x in node.bases) if b][:MAX_BASES]
            out.append(SymbolFacts(
                "class", node.name, qual, node.lineno, node.end_lineno or node.lineno,
                clean(f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}", 200),
                clean((ast.get_docstring(node) or "").split("\n", 1)[0], 160), cls, not scope, [], bases,
            ))  # fmt: skip
            _symbols(node.body, [*scope, node.name], qual, out)
        else:
            for block in _blocks(node):  # if TYPE_CHECKING / try-except-import / with: same scope
                _symbols(block, scope, cls, out)


def extract_python(source: str) -> tuple[list[list], list[SymbolFacts]]:
    tree = ast.parse(source)
    imports: list[list] = []
    for n in ast.walk(tree):
        if len(imports) >= MAX_IMPORTS:
            break
        if isinstance(n, ast.Import):
            imports += [["i", a.name, a.asname or ""] for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            imports.append(["f", n.module or "", n.level, [[a.name, a.asname or ""] for a in n.names]])
    symbols: list[SymbolFacts] = []
    _symbols(tree.body, [], None, symbols)
    return imports, symbols


def facts_for(path: str, data: bytes, size: int, mtime_ns: int, max_parse_bytes: int) -> FileFacts:
    """Facts for one file's bytes. Never raises: a file that cannot be parsed is still recorded, with the reason."""
    lang = language_of(path) or "text"
    f = FileFacts(path, lang, size, mtime_ns, is_test=is_test_path(path))
    if size > max_parse_bytes:
        f.error = f"larger than {max_parse_bytes} bytes: recorded, not parsed"
        return f
    f.sha256 = hashlib.sha256(data).hexdigest()
    f.lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    if lang != "python":
        return f
    try:
        f.imports, f.symbols = extract_python(data.decode("utf-8", "replace"))
        f.parsed = True
    except (SyntaxError, ValueError, RecursionError, MemoryError, TypeError) as e:
        f.error = clean(f"{type(e).__name__}: {e}", 160)
    return f

"""Static, name-based resolution of imports, calls and base classes to files and symbols (pure functions).

This is an approximation, and the graph says so: Python is dynamic, so an edge exists only when a name can be
tied to exactly one definition (an import that maps to one file; a call to a module-level function/class that
is defined or imported by name; `self.method()` inside the same class; `module.func()` through an import alias).
Anything ambiguous or dynamic gets no edge rather than a guessed one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

PRIMARY, SUFFIX = 0, 1


class ModuleMap:
    """dotted module name -> file path, for the Python files of one repository."""

    def __init__(self, py_paths: Iterable[str]):
        self.paths = set(py_paths)
        self._cands: dict[str, list[tuple[int, str]]] = {}
        self._names: dict[str, list[str]] = {}
        for p in sorted(self.paths):
            primary = self._primary(p)
            if primary:
                self._add(primary, PRIMARY, p)
            parts = list(PurePosixPath(p).with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            for i in range(len(parts) - 1):  # suffixes with at least two components (src/, backend/ layouts)
                self._add(".".join(parts[i:]), SUFFIX, p)

    def _add(self, name: str, rank: int, path: str) -> None:
        self._cands.setdefault(name, []).append((rank, path))
        self._names.setdefault(path, []).append(name)

    def _primary(self, path: str) -> str | None:
        """The name Python itself would use: climb while the directory is a package (has __init__.py)."""
        p = PurePosixPath(path)
        parts = [] if p.name == "__init__.py" else [p.stem]
        cur = p.parent
        while str(cur) not in ("", ".") and f"{cur.as_posix()}/__init__.py" in self.paths:
            parts.append(cur.name)
            cur = cur.parent
        return ".".join(reversed(parts)) or None

    def lookup(self, dotted: str) -> str | None:
        c = self._cands.get(dotted)
        if not c:
            return None
        for rank in (
            PRIMARY,
            SUFFIX,
        ):  # a real package path beats a path-suffix guess; two of a kind is ambiguous
            same = [p for r, p in c if r == rank]
            if len(same) == 1:
                return same[0]
            if len(same) > 1:
                return None
        return None

    def relative(self, from_path: str, level: int, module: str) -> str | None:
        d = PurePosixPath(from_path).parent
        for _ in range(level - 1):
            if str(d) in ("", "."):
                return None
            d = d.parent
        target = d.joinpath(*module.split(".")) if module else d
        options = ([f"{target.as_posix()}.py"] if module else []) + [f"{target.as_posix()}/__init__.py"]
        for o in options:
            o = o.removeprefix("./")
            if o in self.paths:
                return o
        return None

    def module_names(self, path: str) -> list[str]:
        """Every dotted name another file might use to import `path` (used to find importers of a new file)."""
        return sorted(set(self._names.get(path, [])))


@dataclass
class FileResolution:
    imports: set[str] = field(default_factory=set)  # internal target files
    externals: set[str] = field(default_factory=set)  # top-level names of modules outside the repository
    modules: dict[str, str] = field(default_factory=dict)  # local alias -> file (an imported module)
    symbols: dict[str, tuple[str, str]] = field(default_factory=dict)  # local alias -> (file, original name)


def resolve_imports(path: str, imports: list[list], mm: ModuleMap) -> FileResolution:
    r = FileResolution()
    for row in imports:
        if row[0] == "i":
            _, mod, asname = row
            parts = mod.split(".")
            hit = False
            for i in range(1, len(parts) + 1):  # importing a.b.c also imports a and a.b
                t = mm.lookup(".".join(parts[:i]))
                if t:
                    r.imports.add(t)
                    hit = True
            if not hit:
                r.externals.add(parts[0])
            full = mm.lookup(mod) if asname else mm.lookup(parts[0])
            if full:
                r.modules[asname or parts[0]] = full
            continue
        _, module, level, names = row
        base = mm.relative(path, level, module) if level else (mm.lookup(module) if module else None)
        if base:
            r.imports.add(base)
        resolved_any = bool(base)
        for name, asname in names:
            if name == "*":
                continue
            local = asname or name
            if level:
                sub = mm.relative(path, level, f"{module}.{name}" if module else name)
            else:
                sub = mm.lookup(f"{module}.{name}") if module else mm.lookup(name)
            if sub and sub != base:
                r.imports.add(sub)
                r.modules[local] = sub
                resolved_any = True
            elif base:
                r.symbols[local] = (base, name)
        if not resolved_any and not level and module:
            r.externals.add(module.split(".")[0])
    r.imports.discard(path)
    return r


# (file path, symbol name, qualified name or None). With no qualified name only module-level symbols match.
Target = tuple[str, str, str | None]


def resolve_ref(
    kind: str, text: str, path: str, qualname: str, local_top: set[str], res: FileResolution
) -> Target | None:
    """Resolve one raw reference found inside the symbol `qualname` of file `path`."""
    if kind == "n":
        if text in local_top:
            return (path, text, None)
        if text in res.symbols:
            tp, orig = res.symbols[text]
            return (tp, orig, None)
        return None
    if kind == "s":
        cls = qualname.rpartition(".")[0]
        return (path, text, f"{cls}.{text}") if cls else None
    base, _, attr = text.partition(".")
    if base in res.modules:
        return (res.modules[base], attr, None)
    if base in local_top:  # LocalClass.method(...)
        return (path, attr, f"{base}.{attr}")
    if base in res.symbols:  # ImportedClass.method(...)
        tp, orig = res.symbols[base]
        return (tp, attr, f"{orig}.{attr}")
    return None

"""Skills: packaged instructions the agent loads on demand (modelled on opencode's skill mechanism).

A skill is a directory with a SKILL.md (YAML-style frontmatter + Markdown body) and optional resource files:

    <skills dir>/<id>/SKILL.md          ---\\nname: ...\\ndescription: ...\\n---\\nInstructions...
    <skills dir>/<id>/reference/*.md    files the instructions may point to

Discovery is from operator-configured directories (FORGE_SKILLS_DIRS). Skills shipped inside the target repository
(.claude/skills, .opencode/skills) are loaded only with FORGE_REPO_SKILLS=true and are labelled untrusted, because a
skill is instructions and repository content is not trusted. Only each skill's id, name and description go into the
first message (progressive disclosure); the body is loaded with the `load_skill` tool, or preloaded when the task
names the skill. A skill can never change policy: every action it suggests still goes through validate -> policy ->
approval -> executor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from harnix.policies.paths import PathViolation, WorkspacePaths

MAX_SKILL_BYTES = 64_000
MAX_FILE_BYTES = 100_000
MAX_SKILLS = 200
MAX_LISTED_FILES = 10
MAX_DEPTH = 4
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REPO_SKILL_DIRS = (".claude/skills", ".opencode/skills")
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """`---` delimited header with `key: value`, quoted values, `>`/`|` blocks and `[a, b]` / `- item` lists.

    Deliberately small (no YAML dependency): anything it does not understand is ignored, never executed.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    data: dict[str, object] = {}
    i = 1
    while i < end:
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_.\-/]+)\s*:\s*(.*)$", line)
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in (">", "|", ">-", "|-", ">+", "|+"):
            block: list[str] = []
            while i < end and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            data[key] = (" " if value.startswith(">") else "\n").join(x for x in block if x).strip()
        elif value == "":
            items: list[str] = []
            while i < end and re.match(r"^\s+-\s+", lines[i]):
                items.append(_unquote(re.sub(r"^\s+-\s+", "", lines[i]).strip()))
                i += 1
            data[key] = items if items else ""
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [_unquote(x.strip()) for x in value[1:-1].split(",") if x.strip()]
        else:
            data[key] = _unquote(value)
    return data, "\n".join(lines[end + 1 :]).strip()


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _truthy(v: object) -> bool:
    return str(v).strip().lower() in ("true", "yes", "on", "1")


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    path: Path  # the SKILL.md
    content: str
    source: str  # operator | repository
    autoinvoke: bool = True  # listed for the model; False = only when the task names it

    @property
    def directory(self) -> Path:
        return self.path.parent


@dataclass
class SkillRegistry:
    skills: dict[str, Skill] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # skipped skills, with the reason (shown to operators)

    @classmethod
    def discover(cls, operator_dirs: list[Path], repo_root: Path | None = None) -> SkillRegistry:
        reg = cls()
        for d in operator_dirs:
            reg._scan(d, "operator")
        if repo_root is not None:
            for rel in REPO_SKILL_DIRS:
                reg._scan(repo_root / rel, "repository")
        return reg

    def _scan(self, root: Path, source: str) -> None:
        if not root.is_dir() or root.is_symlink():
            return
        queue: list[tuple[Path, int]] = [(root, 0)]
        while queue:
            d, depth = queue.pop(0)
            try:
                entries = sorted(d.iterdir())
            except OSError:
                continue
            skill_md = d / "SKILL.md"
            if d != root and skill_md.is_file() and not skill_md.is_symlink():
                self._add(skill_md, d.name, source)
                continue  # a skill's own subdirectories are its resources, not more skills
            for e in entries:
                if e.is_symlink() or e.name.startswith(".") or e.name in _SKIP:
                    continue
                if e.is_dir() and depth < MAX_DEPTH:
                    queue.append((e, depth + 1))
                elif d == root and e.is_file() and e.suffix == ".md":
                    self._add(e, e.stem, source)  # single-file skills: <dir>/<id>.md

    def _add(self, path: Path, default_id: str, source: str) -> None:
        if len(self.skills) >= MAX_SKILLS:
            self.problems.append(f"{path}: more than {MAX_SKILLS} skills; the rest are ignored")
            return
        try:
            raw = path.read_bytes()
        except OSError as e:
            self.problems.append(f"{path}: unreadable ({e.strerror})")
            return
        if len(raw) > MAX_SKILL_BYTES:
            self.problems.append(f"{path}: larger than {MAX_SKILL_BYTES} bytes")
            return
        meta, body = parse_frontmatter(raw.decode("utf-8", "replace"))
        sid = str(meta.get("id") or default_id)
        if not ID_RE.match(sid):
            self.problems.append(f"{path}: invalid skill id {sid!r}")
            return
        if sid in self.skills:
            self.problems.append(
                f"{path}: duplicate skill id {sid!r} (the {self.skills[sid].source} one is kept)"
            )
            return
        if not body.strip():
            self.problems.append(f"{path}: empty skill")
            return
        description = " ".join(str(meta.get("description") or "").split())[:500]
        autoinvoke = bool(description) and not _truthy(meta.get("disable-model-invocation", False))
        self.skills[sid] = Skill(
            sid,
            " ".join(str(meta.get("name") or sid).split())[:80],
            description,
            path,
            body,
            source,
            autoinvoke,
        )

    # ---- what the model sees ------------------------------------------------------------------------
    def listed(self) -> list[Skill]:
        return sorted((s for s in self.skills.values() if s.autoinvoke), key=lambda s: s.id)

    def render_list(self) -> str:
        """The <available_skills> block: ids, names and descriptions only."""
        lines = [
            "Skills provide specialized instructions and workflows for specific tasks.",
            "Use the load_skill tool to load a skill when the task matches its description.",
            "<available_skills>",
        ]
        for s in self.listed():
            trust = "" if s.source == "operator" else ' source="repository" trust="untrusted"'
            lines.append(
                f'  <skill id="{s.id}"{trust}><name>{s.name}</name><description>{s.description}</description></skill>'
            )
        lines.append("</available_skills>")
        return "\n".join(lines)

    def files(self, skill: Skill) -> list[str]:
        base = skill.directory
        out: list[str] = []
        if skill.path.name != "SKILL.md":
            return out
        for p in sorted(base.rglob("*")):
            if len(out) >= MAX_LISTED_FILES:
                break
            if p.is_symlink() or not p.is_file() or p.name == "SKILL.md":
                continue
            rel = p.relative_to(base).as_posix()
            if any(part.startswith(".") or part in _SKIP for part in rel.split("/")):
                continue
            out.append(rel)
        return out

    def render(self, skill: Skill) -> str:
        """The <skill_content> block returned by load_skill (and used for preloaded skills)."""
        from harnix.harness.context import fence  # local: context imports this module

        trust = "operator" if skill.source == "operator" else "untrusted"
        files = self.files(skill)
        parts = [
            f'<skill_content id="{skill.id}" name="{skill.name}" source="{skill.source}" trust="{trust}">',
            fence(skill.content.strip(), "skill_content"),
        ]
        if files:
            parts.append(
                "Resource files of this skill (load one with load_skill(id, file=...)):\n"
                + "\n".join(f"- {f}" for f in files)
            )
        parts.append("</skill_content>")
        return "\n".join(parts)

    def read_file(self, skill: Skill, rel: str) -> str:
        """A resource file of the skill: contained in its directory, no links, no secrets, text only, bounded."""
        paths = WorkspacePaths(skill.directory)
        p = paths.resolve(rel)  # raises PathViolation on traversal or links out of the skill directory
        if paths.is_secret(p):
            raise PathViolation("secret-looking files are not readable")
        if not p.is_file():
            raise FileNotFoundError(rel)
        data = p.read_bytes()[: MAX_FILE_BYTES + 1]
        if b"\x00" in data[:4096]:
            raise ValueError("binary file")
        text = data[:MAX_FILE_BYTES].decode("utf-8", "replace")
        note = "\n[file truncated]" if len(data) > MAX_FILE_BYTES else ""
        from harnix.harness.context import fence

        return f'<skill_file skill="{skill.id}" path="{paths.rel(p)}">\n{fence(text, "skill_file")}{note}\n</skill_file>'

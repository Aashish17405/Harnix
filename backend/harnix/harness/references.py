"""Files the user pointed at (`@path`, `@path#10-20`). The harness, not the client, reads them.

They are read from the task's isolated workspace copy (so they match what the agent sees), through the same
containment rules as every tool, and they are handed to the model as clearly labelled untrusted data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harnix.policies.paths import PathViolation, WorkspacePaths

MAX_REFS = 10
DEFAULT_LINES = 400  # when no range is given, only the top of the file is included
MAX_CHARS_PER_REF = 20_000
MAX_CHARS_TOTAL = 60_000
MAX_READ_BYTES = 500_000


@dataclass
class RefBlock:
    path: str
    start: int
    end: int
    total_lines: int
    text: str
    truncated: bool


@dataclass
class RefReport:
    blocks: list[RefBlock] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    def event_data(self) -> dict[str, Any]:
        return {
            "included": [
                {"path": b.path, "start": b.start, "end": b.end, "truncated": b.truncated}
                for b in self.blocks
            ],
            "skipped": [{"path": p, "reason": r} for p, r in self.skipped],
        }


def load_references(paths: WorkspacePaths, refs: list[dict[str, Any]]) -> RefReport:
    report = RefReport()
    budget = MAX_CHARS_TOTAL
    seen: set[tuple[str, int | None, int | None]] = set()
    for ref in refs[:MAX_REFS]:
        raw = str(ref.get("path", ""))
        key = (raw, ref.get("start_line"), ref.get("end_line"))
        if key in seen:
            continue
        seen.add(key)
        try:
            p = paths.resolve(raw)
        except PathViolation:
            report.skipped.append((raw, "outside the workspace"))
            continue
        if paths.is_secret(p) or paths.is_protected(p):
            report.skipped.append((raw, "looks like a secret or protected file"))
            continue
        if not p.is_file():
            report.skipped.append((raw, "not found in the isolated copy of the repository"))
            continue
        data = p.read_bytes()[:MAX_READ_BYTES]
        if b"\x00" in data[:4096]:
            report.skipped.append((raw, "binary file"))
            continue
        lines = data.decode("utf-8", "replace").splitlines()
        start = max(1, int(ref.get("start_line") or 1))
        end = int(ref.get("end_line") or (start + DEFAULT_LINES - 1))
        end = min(max(end, start), len(lines))
        if start > max(len(lines), 1):
            report.skipped.append((raw, f"file has only {len(lines)} lines"))
            continue
        text = "\n".join(lines[start - 1 : end])
        cap = min(MAX_CHARS_PER_REF, budget)
        truncated = len(text) > cap or (end < len(lines) and not ref.get("end_line"))
        if len(text) > cap:
            if cap <= 0:
                report.skipped.append((raw, "reference size limit reached"))
                continue
            text = text[:cap]
        budget -= len(text)
        report.blocks.append(RefBlock(paths.rel(p), start, end, len(lines), text, bool(truncated)))
    for extra in refs[MAX_REFS:]:
        report.skipped.append((str(extra.get("path", "")), f"more than {MAX_REFS} references"))
    return report

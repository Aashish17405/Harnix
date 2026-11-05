"""Secret redaction for logs, events, tool output and API responses (best effort, not a guarantee)."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
    re.compile(r"(?i)\b(authorization:\s*bearer)\s+[A-Za-z0-9._\-]{8,}"),
]
# NAME=value / "name": "value" where the name looks secret
_KV = re.compile(
    r"""(?ix)
    (\b[A-Za-z0-9_.\-]*(?:api[_-]?key|secret|token|passw(?:or)?d|credential|private[_-]?key)[A-Za-z0-9_.\-]*\b
       ["']?\s*[:=]\s*["']?)
    ([^\s"',;]{4,})
    """
)


def _redact_kv(m: re.Match[str]) -> str:
    """Only redact secret-shaped values. Plain code such as `max_tokens=16000` or `token = request.token`
    must survive, otherwise the agent reads corrupted source."""
    value = m.group(2)
    if len(value) >= 8 and any(c.isdigit() for c in value) and any(c.isalpha() for c in value):
        return m.group(1) + REDACTED
    return m.group(0)


class Redactor:
    def __init__(self, secrets: list[str] | None = None):
        self._secrets = sorted({s for s in (secrets or []) if s and len(s) >= 6}, key=len, reverse=True)

    def with_secrets(self, extra: list[str]) -> Redactor:
        return Redactor([*self._secrets, *extra])

    def redact(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, REDACTED)
        for p in _PATTERNS:
            text = p.sub(lambda m: (m.group(1) + " " if m.lastindex else "") + REDACTED, text)
        return _KV.sub(_redact_kv, text)

    def redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, list | tuple):
            return [self.redact_obj(v) for v in obj]
        return obj

"""Harnix compatibility shim for legacy forge imports.

All core modules have transitioned to the `harnix` package.
This module dynamically maps `forge.*` imports to `harnix.*` to maintain
backward compatibility.
"""

from __future__ import annotations

import sys
import harnix

sys.modules["forge"] = harnix

_submodules = [
    "api",
    "config",
    "client",
    "cli",
    "evaluations",
    "execution",
    "graph",
    "harness",
    "knowledge",
    "mcp",
    "models",
    "observability",
    "persistence",
    "policies",
    "tools",
    "verification",
]

for _sub in _submodules:
    try:
        _mod = __import__(f"harnix.{_sub}", fromlist=[_sub])
        sys.modules[f"forge.{_sub}"] = _mod
    except Exception:
        pass


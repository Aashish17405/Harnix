"""Execution limits and usage accounting. Time spent waiting for a human is excluded via pause().

Two different things live here:
* limits that stop a runaway loop (iterations, wall-clock time, tool calls): these raise BudgetExceeded;
* usage that is only *measured* (tokens and estimated dollars): counted for reports and the UI, never a reason to stop.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"Budget exceeded ({kind}): {detail}")
        self.kind = kind


@dataclass
class Budget:
    max_iterations: int
    max_duration_seconds: float
    max_tool_calls: int
    clock: Callable[[], float] = time.monotonic
    iterations: int = 0
    tool_calls: int = 0
    tokens: int = 0
    # estimated dollars need prices (USD per 1M tokens); this is accounting only, there is no spending cap
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    cost_usd: float = 0.0
    _start: float = field(default=0.0, init=False)
    _paused: float = field(default=0.0, init=False)
    # background sub-agents charge this budget from other threads: counters change only under the lock
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._start = self.clock()

    def elapsed(self) -> float:
        return self.clock() - self._start - self._paused

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._paused += max(0.0, seconds)

    def check_time(self) -> None:
        if self.elapsed() > self.max_duration_seconds:
            raise BudgetExceeded("duration", f"{self.max_duration_seconds}s limit reached")

    def next_iteration(self) -> int:
        self.check_time()
        with self._lock:
            if self.iterations >= self.max_iterations:
                raise BudgetExceeded("iterations", f"{self.max_iterations} iterations used")
            self.iterations += 1
            return self.iterations

    def add_tool_call(self) -> None:
        with self._lock:
            if self.tool_calls >= self.max_tool_calls:
                raise BudgetExceeded("tool_calls", f"{self.max_tool_calls} tool calls used")
            self.tool_calls += 1

    def add_tokens(self, n: int) -> None:
        with self._lock:
            self.tokens += n

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.tokens += input_tokens + output_tokens
            if self.price_in_per_mtok is not None and self.price_out_per_mtok is not None:
                self.cost_usd += (
                    input_tokens / 1e6 * self.price_in_per_mtok
                    + output_tokens / 1e6 * self.price_out_per_mtok
                )

    def remaining_seconds(self) -> float:
        return max(0.0, self.max_duration_seconds - self.elapsed())

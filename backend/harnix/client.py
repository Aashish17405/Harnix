"""Small HTTP client for the Harnix API (standard library only). Used by the `harnix` CLI and the tests."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

# localhost traffic must never go through a system/corporate proxy
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HarnixError(Exception):
    """API or connection failure. `status` is 0 when the server could not be reached."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    @property
    def unreachable(self) -> bool:
        return self.status == 0


ForgeError = HarnixError


def parse_sse(lines: Iterator[bytes]) -> Iterator[tuple[str, dict[str, Any] | None]]:
    """Yield (event_name, json_data) from raw SSE lines. Comments/keep-alives are skipped."""
    name, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                text = "\n".join(data)
                try:
                    yield name, json.loads(text)
                except json.JSONDecodeError:
                    yield name, None
            name, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


class HarnixClient:
    def __init__(
        self, base_url: str = "http://127.0.0.1:8000", token: str | None = None, timeout: float = 30
    ):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ---- plumbing -------------------------------------------------------------------------------
    def _req(self, method: str, path: str, body: Any = None, *, timeout: float | None = None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base}/api{path}", data=data, method=method, headers=headers)
        try:
            return _OPENER.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode("utf-8", "replace"))["error"]
                raise ForgeError(e.code, err.get("code", "error"), err.get("message", str(e))) from None
            except (ValueError, KeyError, TypeError):
                raise ForgeError(e.code, "error", f"HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ForgeError(
                0, "unreachable", f"Cannot reach the Forge backend at {self.base} ({e})"
            ) from None

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        with self._req(method, path, body) as r:
            return json.loads(r.read().decode("utf-8"))

    # ---- API ------------------------------------------------------------------------------------
    def health(self) -> dict:
        return self._json("GET", "/health")

    def config(self) -> dict:
        return self._json("GET", "/config")

    def create_task(self, **fields: Any) -> dict:
        return self._json("POST", "/tasks", {k: v for k, v in fields.items() if v not in (None, [])})

    def get_task(self, task_id: str) -> dict:
        return self._json("GET", f"/tasks/{task_id}")

    def list_tasks(self, limit: int = 10) -> dict:
        return self._json("GET", f"/tasks?limit={limit}")

    def run(self, task_id: str) -> dict:
        return self._json("POST", f"/tasks/{task_id}/run")

    def cancel(self, task_id: str) -> dict:
        return self._json("POST", f"/tasks/{task_id}/cancel")

    def decide(self, approval_id: str, decision: str, note: str | None = None) -> dict:
        return self._json("POST", f"/approvals/{approval_id}/decision", {"decision": decision, "note": note})

    def skills(self) -> dict:
        return self._json("GET", "/skills")

    def agents(self) -> dict:
        return self._json("GET", "/agents")

    def memory(self, repo_path: str) -> list[dict]:
        return self._json("GET", f"/memory?repo_path={urllib.parse.quote(repo_path, safe='')}")

    def add_memory(self, repo_path: str, content: str) -> dict:
        return self._json("POST", "/memory", {"repo_path": repo_path, "content": content})

    def delete_memory(self, repo_path: str, memory_id: str) -> dict:
        q = urllib.parse.quote(repo_path, safe="")
        return self._json("DELETE", f"/memory/{urllib.parse.quote(memory_id, safe='')}?repo_path={q}")

    def knowledge(self, method: str, path: str, body: Any = None) -> Any:
        """Ingestion embeds text through a model API, which can take minutes: a long timeout, unlike other calls."""
        with self._req(method, "/knowledge" + path, body, timeout=900) as r:
            return json.loads(r.read().decode("utf-8"))

    def processes(self) -> dict:
        return self._json("GET", "/processes")

    def stop_process(self, process_id: str) -> dict:
        return self._json("POST", f"/processes/{urllib.parse.quote(process_id, safe='')}/stop")

    def graph_status(self, repo_path: str) -> dict:
        return self._json("GET", f"/graph/status?repo_path={urllib.parse.quote(repo_path, safe='')}")

    def graph_index(self, repo_path: str) -> dict:
        return self._json("POST", "/graph/index", {"repo_path": repo_path})

    def graph_drop(self, repo_path: str) -> dict:
        return self._json("DELETE", f"/graph?repo_path={urllib.parse.quote(repo_path, safe='')}")

    def diff(self, task_id: str) -> dict:
        return self._json("GET", f"/tasks/{task_id}/diff")

    def verifications(self, task_id: str) -> list[dict]:
        return self._json("GET", f"/tasks/{task_id}/verifications")

    def apply(self, task_id: str, *, confirm: bool = False, dry_run: bool = False) -> dict:
        return self._json("POST", f"/tasks/{task_id}/apply", {"confirm": confirm, "dry_run": dry_run})

    def stream_events(
        self, task_id: str, after: int = 0, *, read_timeout: float = 90, retries: int = 5
    ) -> Iterator[dict]:
        """Yield events until the server says the task is over. Reconnects (resuming after the last id) on drops."""
        last, failures = after, 0
        while True:
            try:
                with self._req(
                    "GET", f"/tasks/{task_id}/events/stream?after={last}", timeout=read_timeout
                ) as r:
                    for name, data in parse_sse(iter(r.readline, b"")):
                        if name == "end":
                            return
                        if data:
                            last = max(last, data["id"])
                            failures = 0
                            yield data
                # stream closed without "end": reconnect
            except (TimeoutError, ConnectionError, urllib.error.URLError, OSError):
                pass
            failures += 1
            if failures > retries:
                raise ForgeError(
                    0, "stream", "Lost the connection to the Forge backend while watching the task"
                )
            if failures > 1:
                import time

                time.sleep(min(failures, 3))


ForgeClient = HarnixClient

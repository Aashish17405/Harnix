"""A small synchronous MCP client (JSON-RPC 2.0) over stdio and streamable HTTP.

Only what an agent needs: initialize, tools/list, tools/call. Requests the server sends back (ping is answered;
roots, sampling, elicitation are refused) never give a server more than a tool result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import Any

import httpx

from harnix.mcp.config import ServerConfig

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "forge", "version": "0.1"}
# The child process gets this and the server's own `env`, never the backend's environment (it holds API keys).
_SAFE_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "LANG", "LC_ALL", "USER", "USERNAME", "SHELL", "TERM",
)  # fmt: skip


class McpError(Exception):
    """A failure talking to a server (transport, protocol or a JSON-RPC error object)."""


class Client:
    """Common request logic; subclasses move messages."""

    def __init__(self, cfg: ServerConfig, timeout: float):
        self.cfg, self.timeout = cfg, timeout
        self._ids = 0
        self._id_lock = threading.Lock()
        self.server_info: dict[str, Any] = {}

    def _next_id(self) -> int:
        with self._id_lock:
            self._ids += 1
            return self._ids

    def _request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        raise NotImplementedError

    def _notify(self, method: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def initialize(self) -> None:
        r = self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
            self.timeout,
        )
        if not isinstance(r, dict):
            raise McpError("the server's initialize answer was not an object")
        info = r.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        self._notify("notifications/initialized")

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(50):  # a server cannot keep us paging forever
            r = self._request("tools/list", {"cursor": cursor} if cursor else {}, self.timeout)
            if not isinstance(r, dict) or not isinstance(r.get("tools"), list):
                raise McpError("tools/list did not return a tools array")
            tools += [t for t in r["tools"] if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = r.get("nextCursor") if isinstance(r.get("nextCursor"), str) else None
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
        r = self._request("tools/call", {"name": name, "arguments": arguments}, timeout)
        if not isinstance(r, dict):
            raise McpError("tools/call did not return an object")
        return r


def _unwrap(msg: dict[str, Any]) -> Any:
    if "error" in msg:
        e = msg["error"] if isinstance(msg["error"], dict) else {}
        raise McpError(f"{e.get('message', 'error')} (code {e.get('code', '?')})")
    return msg.get("result")


class StdioClient(Client):
    def __init__(self, cfg: ServerConfig, timeout: float, cwd: str | None = None):
        super().__init__(cfg, timeout)
        exe = (
            shutil.which(cfg.command) or cfg.command
        )  # CreateProcess does not resolve npx -> npx.cmd by itself
        env = {k: v for k, v in os.environ.items() if k.upper() in _SAFE_ENV}
        env.update(cfg.env)
        try:
            self.proc = subprocess.Popen(  # noqa: S603 - argv list, no shell; the operator wrote this command
                [exe, *cfg.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=cfg.cwd or cwd, env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
            )  # fmt: skip
        except OSError as e:
            raise McpError(f"cannot start {cfg.command!r}: {e}") from None
        self._pending: dict[int, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._plock = threading.Lock()
        self._wlock = threading.Lock()
        self.stderr_tail = ""
        threading.Thread(target=self._read, daemon=True, name=f"mcp-{cfg.name}-out").start()
        threading.Thread(target=self._drain_err, daemon=True, name=f"mcp-{cfg.name}-err").start()

    def _send(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        with self._wlock:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                raise McpError(f"the server exited{self._why()}") from None

    def _why(self) -> str:
        tail = self.stderr_tail.strip()[-300:]
        return f": {tail}" if tail else ""

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue  # a server that prints a banner on stdout must not break the protocol
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg:  # a request or notification from the server
                if "id" in msg:
                    if msg["method"] == "ping":
                        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                    else:
                        self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported by this client"}})  # fmt: skip
                continue
            with self._plock:
                mid = msg.get("id")
                slot = self._pending.get(mid) if isinstance(mid, int) else None
            if slot:
                slot[1].append(msg)
                slot[0].set()
        with self._plock:  # EOF: wake everybody
            for ev, _ in self._pending.values():
                ev.set()

    def _drain_err(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_tail = (self.stderr_tail + line)[-2000:]

    def _request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        rid = self._next_id()
        ev, box = threading.Event(), []
        with self._plock:
            self._pending[rid] = (ev, box)
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "method": method,
                    **({"params": params} if params is not None else {}),
                }
            )
            if not ev.wait(timeout):
                self._send_quiet({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": rid, "reason": "timeout"}})  # fmt: skip
                raise McpError(f"no answer to {method} within {timeout:g}s{self._why()}")
            if not box:
                raise McpError(f"the server exited{self._why()}")
            return _unwrap(box[0])
        finally:
            with self._plock:
                self._pending.pop(rid, None)

    def _send_quiet(self, obj: dict[str, Any]) -> None:
        try:
            self._send(obj)
        except McpError:
            pass

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)


class HttpClient(Client):
    """Streamable HTTP: every message is a POST; the answer is JSON or an SSE stream that ends with our answer."""

    def __init__(self, cfg: ServerConfig, timeout: float, transport: httpx.BaseTransport | None = None):
        super().__init__(cfg, timeout)
        self.http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0), transport=transport, follow_redirects=False
        )
        self.session_id: str | None = None
        self.protocol = PROTOCOL_VERSION

    def _headers(self) -> dict[str, str]:
        h = {**self.cfg.headers, "Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": self.protocol}  # fmt: skip
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, body: dict[str, Any], timeout: float | None = None) -> httpx.Response | None:
        try:
            req = self.http.build_request(
                "POST", self.cfg.url, json=body, headers=self._headers(), timeout=timeout or self.timeout
            )
            r = self.http.send(req, stream=True)
        except httpx.HTTPError as e:
            raise McpError(f"{type(e).__name__}: {e}") from None
        if r.status_code >= 400:
            r.read()
            r.close()
            raise McpError(f"HTTP {r.status_code} from the server")
        return r

    def _request(self, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
        rid = self._next_id()
        r = self._post(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "method": method,
                **({"params": params} if params is not None else {}),
            },
            timeout,
        )
        assert r is not None
        try:
            if method == "initialize" and r.headers.get("mcp-session-id"):
                self.session_id = r.headers["mcp-session-id"]
            ctype = r.headers.get("content-type", "")
            if ctype.startswith("text/event-stream"):
                data: list[str] = []
                for line in [*r.iter_lines(), ""]:
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif line == "" and data:
                        try:
                            msg = json.loads("\n".join(data))
                        except ValueError:
                            msg = None
                        data = []
                        if isinstance(msg, dict) and msg.get("id") == rid and "method" not in msg:
                            return _unwrap(msg)
                raise McpError("the response stream ended without an answer")
            try:
                msg = json.loads(r.read().decode("utf-8", "replace"))
            except ValueError:
                raise McpError("the server's answer was not JSON") from None
            if isinstance(msg, list):  # a batch: pick ours
                msg = next((m for m in msg if isinstance(m, dict) and m.get("id") == rid), None)
            if not isinstance(msg, dict):
                raise McpError("unexpected answer shape")
            return _unwrap(msg)
        except httpx.HTTPError as e:
            raise McpError(f"{type(e).__name__}: {e}") from None
        finally:
            r.close()

    def _notify(self, method: str) -> None:
        r = self._post({"jsonrpc": "2.0", "method": method})
        if r is not None:
            r.read()
            r.close()

    def close(self) -> None:
        try:
            if self.session_id:
                self.http.delete(self.cfg.url, headers=self._headers())
        except httpx.HTTPError:
            pass
        self.http.close()


def connect(cfg: ServerConfig, timeout: float, cwd: str | None = None) -> Client:
    c: Client = StdioClient(cfg, timeout, cwd) if cfg.transport == "stdio" else HttpClient(cfg, timeout)
    try:
        c.initialize()
    except Exception:
        c.close()
        raise
    return c

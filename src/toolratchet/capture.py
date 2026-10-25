"""Capture a live tool surface from an MCP server over stdio or Streamable HTTP.

Only ``initialize`` and ``tools/list`` are ever sent. No tool is called.
"""

from __future__ import annotations

import collections
import itertools
import json
import os
import queue
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from . import __version__
from .ingest import parse_surface
from .model import Surface

PROTOCOL_VERSION = "2025-06-18"


class CaptureError(Exception):
    pass


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------


class StdioTransport:
    def __init__(self, argv: list, env: Optional[dict] = None, cwd: Optional[str] = None) -> None:
        full_env = dict(os.environ)
        full_env.update(env or {})
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                cwd=cwd,
            )
        except OSError as exc:
            raise CaptureError(f"cannot start {argv[0]!r}: {exc}") from exc
        self._q: queue.Queue = queue.Queue()
        self.noise: list = []
        self.stderr_tail: collections.deque = collections.deque(maxlen=20)
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                self.noise.append(line[:200])
        self._q.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CaptureError(f"server closed its input: {self.hint()}") from exc

    def recv(self, timeout: float) -> Any:
        try:
            msg = self._q.get(timeout=max(timeout, 0.01))
        except queue.Empty as exc:
            raise TimeoutError from exc
        if msg is None:
            raise CaptureError(f"server exited before replying: {self.hint()}")
        return msg

    def hint(self) -> str:
        tail = " | ".join(list(self.stderr_tail)[-3:])
        return f"stderr: {tail}" if tail else "no stderr output"

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        for t in self._threads:
            t.join(timeout=1)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


class HttpTransport:
    def __init__(self, url: str, headers: Optional[dict] = None, timeout: float = 30.0) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self.protocol_version: Optional[str] = None
        self._q: collections.deque = collections.deque()

    def send(self, msg: dict) -> None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"toolratchet/{__version__}",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version and msg.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol_version
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode("utf-8"), headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            extra = ""
            if exc.code == 401:
                extra = f" (WWW-Authenticate: {exc.headers.get('WWW-Authenticate', 'absent')}); pass --header 'Authorization: Bearer ...'"
            exc.close()
            raise CaptureError(f"HTTP {exc.code} from {self.url}{extra}") from exc
        except urllib.error.URLError as exc:
            raise CaptureError(f"cannot reach {self.url}: {exc.reason}") from exc
        except OSError as exc:
            raise CaptureError(f"cannot reach {self.url}: {exc}") from exc
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if resp.status == 202:
                return
            ctype = resp.headers.get("Content-Type", "")
            want = msg.get("id")
            if "text/event-stream" in ctype:
                for payload in _iter_sse(resp):
                    try:
                        doc = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    self._q.append(doc)
                    if want is not None and isinstance(doc, dict) and doc.get("id") == want and ("result" in doc or "error" in doc):
                        break
            else:
                body = resp.read()
                if not body.strip():
                    return
                try:
                    doc = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise CaptureError(f"{self.url} returned non-JSON content ({ctype or 'no content-type'})") from exc
                if isinstance(doc, list):
                    self._q.extend(doc)
                else:
                    self._q.append(doc)

    def recv(self, timeout: float) -> Any:
        if self._q:
            return self._q.popleft()
        raise TimeoutError

    def close(self) -> None:
        if not self.session_id:
            return
        req = urllib.request.Request(
            self.url,
            headers={"Mcp-Session-Id": self.session_id, **self.headers},
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:
            pass


def _iter_sse(resp: Any):
    data: list = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            chunk = line[5:]
            data.append(chunk[1:] if chunk.startswith(" ") else chunk)
    if data:
        yield "\n".join(data)


# --------------------------------------------------------------------------
# minimal MCP client
# --------------------------------------------------------------------------


class MCPClient:
    def __init__(self, transport: Any, protocol_version: str = PROTOCOL_VERSION) -> None:
        self.t = transport
        self.protocol_version = protocol_version
        self._ids = itertools.count(1)

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self.t.send(msg)

    def request(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> Any:
        rid = next(self._ids)
        self.t.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            msg = self.t.recv(remaining)
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    err = msg["error"] if isinstance(msg["error"], dict) else {}
                    raise CaptureError(f"{method} failed: {err.get('message', msg['error'])}")
                return msg.get("result")
            if "method" in msg and "id" in msg:  # server-initiated request
                if msg["method"] == "ping":
                    self.t.send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                else:
                    self.t.send(
                        {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}}
                    )

    def initialize(self, timeout: float = 30.0) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "toolratchet", "version": __version__},
            },
            timeout,
        )
        if not isinstance(result, dict):
            raise CaptureError("initialize returned an unexpected result")
        if hasattr(self.t, "protocol_version"):
            self.t.protocol_version = result.get("protocolVersion", self.protocol_version)
        self.notify("notifications/initialized")
        return result

    def list_tools(self, timeout: float = 30.0) -> list:
        tools: list = []
        cursor: Optional[str] = None
        for _ in range(100):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout)
            if not isinstance(result, dict):
                raise CaptureError("tools/list returned an unexpected result")
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
        raise CaptureError("tools/list pagination did not terminate")


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------


def _finish(info: dict, tools: list, source: str) -> Surface:
    surface = parse_surface({"tools": tools}, source=source)
    si = info.get("serverInfo") if isinstance(info.get("serverInfo"), dict) else {}
    surface.server = {k: si[k] for k in ("name", "version") if k in si}
    return surface


def capture_stdio(
    argv: list,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    timeout: float = 30.0,
    protocol_version: str = PROTOCOL_VERSION,
) -> Surface:
    if not argv:
        raise CaptureError("empty server command")
    transport = StdioTransport(argv, env, cwd)
    try:
        client = MCPClient(transport, protocol_version)
        info = client.initialize(timeout)
        tools = client.list_tools(timeout)
    except TimeoutError as exc:
        raise CaptureError(f"timed out after {timeout:g}s waiting for the server ({transport.hint()})") from exc
    finally:
        transport.close()
    return _finish(info, tools, "cmd:" + " ".join(shlex.quote(a) for a in argv))


def capture_http(
    url: str,
    headers: Optional[dict] = None,
    timeout: float = 30.0,
    protocol_version: str = PROTOCOL_VERSION,
) -> Surface:
    transport = HttpTransport(url, headers, timeout)
    try:
        client = MCPClient(transport, protocol_version)
        info = client.initialize(timeout)
        tools = client.list_tools(timeout)
    except TimeoutError as exc:
        raise CaptureError(f"{url} did not return a response to the request") from exc
    finally:
        transport.close()
    return _finish(info, tools, url)

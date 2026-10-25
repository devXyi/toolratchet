import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helpers
from helpers import MINI_SERVER, tool
from toolratchet.capture import CaptureError, capture_http, capture_stdio

TOOLS = [
    tool("alpha", "First tool", {"a": {"type": "string"}}),
    tool("beta", "Second tool", {"b": {"type": "integer"}}),
    tool("gamma", "Third tool"),
]


def write_tools(tmp, tools=TOOLS):
    path = os.path.join(tmp, "tools.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(tools, fh)
    return path


class TestStdio(unittest.TestCase):
    def run_server(self, *extra, tools=TOOLS):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_tools(tmp, tools)
            return capture_stdio([sys.executable, MINI_SERVER, "--tools", path, *extra], timeout=15)

    def test_capture(self):
        s = self.run_server("--name", "demo", "--version", "3.1.4")
        self.assertEqual([t.name for t in s.tools], ["alpha", "beta", "gamma"])
        self.assertEqual((s.server["name"], s.version), ("demo", "3.1.4"))
        self.assertTrue(s.source.startswith("cmd:"))

    def test_pagination(self):
        s = self.run_server("--page-size", "1")
        self.assertEqual(len(s.tools), 3)

    def test_stdout_noise_is_ignored(self):
        s = self.run_server("--noise")
        self.assertEqual(len(s.tools), 3)

    def test_missing_binary(self):
        with self.assertRaises(CaptureError):
            capture_stdio(["definitely-not-a-real-binary-xyz"], timeout=5)

    def test_server_that_exits(self):
        with self.assertRaises(CaptureError):
            capture_stdio([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"], timeout=10)

    def test_timeout(self):
        with self.assertRaises(CaptureError) as ctx:
            capture_stdio([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertIn("timed out", str(ctx.exception))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        srv = self.server
        if srv.token and self.headers.get("Authorization") != f"Bearer {srv.token}":
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer resource_metadata="https://example.test/.well-known/oauth-protected-resource"')
            self.end_headers()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mid, method = body.get("id"), body.get("method")
        if mid is None:
            self.send_response(202)
            self.end_headers()
            return
        if method == "initialize":
            result = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-demo", "version": "0.9.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        else:
            result = {}
        payload = json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}).encode()
        self.send_response(200)
        if method == "initialize":
            self.send_header("Mcp-Session-Id", "sess-123")
        if srv.sse:
            data = b": keepalive\n\nevent: message\ndata: " + payload + b"\n\n"
            self.send_header("Content-Type", "text/event-stream")
        else:
            data = payload
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TestHttp(unittest.TestCase):
    def serve(self, sse=False, token=None):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        srv.sse, srv.token = sse, token
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/mcp"

    def test_json_response(self):
        s = capture_http(self.serve(), timeout=10)
        self.assertEqual([t.name for t in s.tools], ["alpha", "beta", "gamma"])
        self.assertEqual((s.server["name"], s.version), ("http-demo", "0.9.0"))

    def test_sse_response(self):
        s = capture_http(self.serve(sse=True), timeout=10)
        self.assertEqual(len(s.tools), 3)

    def test_auth_required(self):
        url = self.serve(token="secret")
        with self.assertRaises(CaptureError) as ctx:
            capture_http(url, timeout=10)
        self.assertIn("401", str(ctx.exception))
        self.assertIn("resource_metadata", str(ctx.exception))
        s = capture_http(url, headers={"Authorization": "Bearer secret"}, timeout=10)
        self.assertEqual(len(s.tools), 3)

    def test_unreachable(self):
        with self.assertRaises(CaptureError):
            capture_http("http://127.0.0.1:9/mcp", timeout=3)


if __name__ == "__main__":
    unittest.main()

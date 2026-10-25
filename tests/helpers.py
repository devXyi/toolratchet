"""Shared test helpers (also puts ../src on sys.path so no install is needed)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
MINI_SERVER = os.path.join(FIXTURES, "mini_server.py")

from toolratchet.ingest import parse_surface  # noqa: E402


def tool(name, desc="", props=None, required=None, **extra):
    schema = {"type": "object", "properties": props or {}}
    if required:
        schema["required"] = required
    d = {"name": name, "description": desc, "inputSchema": schema}
    d.update(extra)
    return d


def surface(*tools, **server):
    data = {"tools": list(tools)}
    if server:
        data["server"] = server
    return parse_surface(data)

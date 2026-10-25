#!/usr/bin/env python3
"""A tiny stdlib-only MCP stdio server used by the test-suite.

It serves whatever tool list is in the JSON file given with --tools.
"""

import argparse
import json
import sys


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", required=True)
    ap.add_argument("--name", default="mini")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--page-size", type=int, default=0)
    ap.add_argument("--noise", action="store_true", help="print a non-JSON line on stdout")
    args = ap.parse_args()
    with open(args.tools, encoding="utf-8") as fh:
        tools = json.load(fh)
    if args.noise:
        send_raw = sys.stdout.write("server booting...\n")
        sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": msg["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": args.name, "version": args.version},
                    },
                }
            )
        elif method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            start = int(cursor) if cursor else 0
            if args.page_size:
                result = {"tools": tools[start : start + args.page_size]}
                if start + args.page_size < len(tools):
                    result["nextCursor"] = str(start + args.page_size)
            else:
                result = {"tools": tools}
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})


if __name__ == "__main__":
    main()

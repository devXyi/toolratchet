"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from . import budget as B
from . import integrity as I
from .capture import PROTOCOL_VERSION, CaptureError
from .diff import _SEC_MSG, compare
from .ingest import IngestError
from .model import Surface
from .render import (
    render_github,
    render_inspect,
    render_json,
    render_markdown,
    render_text,
)
from .report import Policy, evaluate
from .sources import SourceError, dumps_lock, load_surface
from .tokens import get_tokenizer, tool_tokens

DEFAULT_LOCK = "toolratchet.lock.json"


class CLIError(Exception):
    pass


def _live_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("live server options")
    g.add_argument("-H", "--header", action="append", default=[], metavar="'K: V'", help="HTTP header (repeatable)")
    g.add_argument("--env", action="append", default=[], metavar="K=V", help="environment variable for a stdio server (repeatable)")
    g.add_argument("--cwd", help="working directory for a stdio server")
    g.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for the server (default 30)")
    g.add_argument("--protocol-version", default=PROTOCOL_VERSION, help=argparse.SUPPRESS)
    return p


def _gate_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("gate options")
    g.add_argument("--fail-on", choices=["breaking", "review", "never"], default="breaking", help="what fails the gate (default: breaking)")
    g.add_argument("--allow-security", action="store_true", help="do not fail on high-severity integrity flags")
    g.add_argument("--max-tokens", type=int, metavar="N", help="fail if total tool tokens exceed N")
    g.add_argument("--max-increase", type=float, metavar="PCT", help="fail if total tool tokens grow by more than PCT percent")
    g.add_argument("--max-tool-tokens", type=int, metavar="N", help="fail if any single tool exceeds N tokens")
    g.add_argument("--ratchet", action="store_true", help="token cost may never increase (same as --max-increase 0)")
    g.add_argument("--check-version", action="store_true", help="fail if the declared semver bump is lower than the changes require")
    g.add_argument("--old-version", help="override the old version (default: from the lock / server)")
    g.add_argument("--new-version", help="override the new version (default: from the live server)")
    g.add_argument("--rename-threshold", type=float, default=0.75, help="similarity needed to treat remove+add as a rename")
    g.add_argument("--tokenizer", choices=["estimate", "tiktoken"], default="estimate")
    o = p.add_argument_group("output options")
    o.add_argument("--format", choices=["text", "markdown", "json", "github"], default="text")
    o.add_argument("--summary-file", metavar="FILE", help="also append a markdown report to FILE (e.g. $GITHUB_STEP_SUMMARY)")
    return p


def build_parser() -> argparse.ArgumentParser:
    live, gate = _live_parent(), _gate_parent()
    p = argparse.ArgumentParser(
        prog="toolratchet",
        description="Compatibility, token-budget and integrity gate for LLM tool definitions.",
        epilog="Sources: a JSON file, an http(s):// MCP URL, 'cmd:<command>', or '-- <command...>' for a stdio MCP server.",
    )
    p.add_argument("--version", action="version", version=f"toolratchet {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("lock", parents=[live], help="record the current tool surface in a lock file")
    s.add_argument("source", nargs="?")
    s.add_argument("-o", "--output", default=DEFAULT_LOCK, help=f"lock file (default {DEFAULT_LOCK})")
    s.set_defaults(func=cmd_lock)

    s = sub.add_parser("check", parents=[live, gate], help="compare a live server against the lock file")
    s.add_argument("source", nargs="?")
    s.add_argument("-l", "--lock", default=DEFAULT_LOCK, help=f"lock file (default {DEFAULT_LOCK})")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("diff", parents=[live, gate], help="compare two tool surfaces")
    s.add_argument("old")
    s.add_argument("new", nargs="?")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("inspect", parents=[live], help="token cost and integrity flags for one surface")
    s.add_argument("source", nargs="?")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--tokenizer", choices=["estimate", "tiktoken"], default="estimate")
    s.set_defaults(func=cmd_inspect)
    return p


# --------------------------------------------------------------------------


def _kv(items: list, sep: str, what: str) -> dict:
    out: dict = {}
    for item in items:
        if sep not in item:
            raise CLIError(f"bad {what} {item!r} (expected 'K{sep} V')")
        k, v = item.split(sep, 1)
        out[k.strip()] = v.strip() if sep == ":" else v
    return out


def _load(spec, args) -> Surface:
    return load_surface(
        spec,
        headers=_kv(args.header, ":", "header"),
        env=_kv(args.env, "=", "env var"),
        cwd=args.cwd,
        timeout=args.timeout,
        protocol_version=args.protocol_version,
    )


def _source(spec: Optional[str], tail: Optional[list], what: str):
    if tail:
        return list(tail)
    if spec:
        return spec
    raise CLIError(f"no {what}: give a source (file / URL / cmd:...) or '-- <server command>'")


def cmd_lock(args, tail) -> int:
    surface = _load(_source(args.source, tail, "server to lock"), args)
    Path(args.output).write_text(dumps_lock(surface), encoding="utf-8")
    total = sum(tool_tokens(t) for t in surface.tools)
    ver = f" v{surface.version}" if surface.version else ""
    print(f"locked {len(surface.tools)} tools (~{total:,} tokens){ver} -> {args.output}")
    return 0


def _gate(old: Surface, new: Surface, args) -> int:
    count = get_tokenizer(args.tokenizer)
    changes = compare(old, new, rename_threshold=args.rename_threshold)
    budget = B.compute(old, new, count)
    pct = args.max_increase if args.max_increase is not None else (0.0 if args.ratchet else None)
    policy = Policy(
        fail_on=args.fail_on,
        allow_security=args.allow_security,
        max_tokens=args.max_tokens,
        max_increase_pct=pct,
        max_tool_tokens=args.max_tool_tokens,
        check_version=args.check_version,
        old_version=args.old_version,
        new_version=args.new_version,
    )
    result = evaluate(old, new, changes, budget, policy)
    renderers = {"text": render_text, "markdown": render_markdown, "json": render_json, "github": render_github}
    sys.stdout.write(renderers[args.format](result))
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(result))
    return 0 if result.passed else 1


def cmd_check(args, tail) -> int:
    lock = Path(args.lock)
    if not lock.is_file():
        raise CLIError(f"lock file {args.lock!r} not found; create it with: toolratchet lock -- <server command>")
    old = load_surface(str(lock))
    new = _load(_source(args.source, tail, "server to check"), args)
    return _gate(old, new, args)


def cmd_diff(args, tail) -> int:
    old = _load(args.old, args)
    new = _load(_source(args.new, tail, "new surface"), args)
    return _gate(old, new, args)


def cmd_inspect(args, tail) -> int:
    surface = _load(_source(args.source, tail, "surface to inspect"), args)
    count = get_tokenizer(args.tokenizer)
    costs = [(t.name, tool_tokens(t, count)) for t in surface.tools]
    sys.stdout.write(render_inspect(surface, costs, args.top))
    flagged = []
    for t in surface.tools:
        texts = {"description": t.description, **I.collect_texts(t.input_schema)}
        for where, text in texts.items():
            for f in I.analyze(text):
                if f.severity == "high":
                    flagged.append(f"[SECURITY] {t.name}  {where}\n    {_SEC_MSG.get(f.kind, f.kind)}: {f.evidence}")
    if flagged:
        sys.stdout.write("\n" + "\n".join(flagged) + "\n")
    return 0


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tail: Optional[list] = None
    if "--" in argv:
        i = argv.index("--")
        argv, tail = argv[:i], argv[i + 1 :]
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, tail)
    except (CLIError, SourceError, CaptureError, IngestError, ValueError, RuntimeError) as exc:
        print(f"toolratchet: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

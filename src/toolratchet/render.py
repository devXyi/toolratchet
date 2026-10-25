"""Renderers: text, markdown (PR comments), JSON and GitHub annotations."""

from __future__ import annotations

import json

from .model import Change, Level
from .report import Result

MARKER = "<!-- toolratchet-report -->"


def _delta(old: int, new: int) -> str:
    d = new - old
    pct = f", {100.0 * d / old:+.1f}%" if old else ""
    return f"{d:+,}{pct}"


def _where(c: Change) -> str:
    return f"{c.tool}  {c.path}" if c.path else c.tool


def _largest_increases(r: Result, n: int = 3) -> list:
    grown = sorted((t for t in r.budget.tools if t.delta > 0), key=lambda t: -t.delta)
    return [f"{t.name} {t.delta:+,}" for t in grown[:n]]


def render_text(r: Result, max_detail: int = 8) -> str:
    b = r.budget
    out = [
        f"toolratchet  {r.old.source or 'old'} -> {r.new.source or 'new'}",
        f"tools   {len(r.old.tools)} -> {len(r.new.tools)}",
        f"tokens  {b.old_total:,} -> {b.new_total:,} ({_delta(b.old_total, b.new_total)})",
    ]
    inc = _largest_increases(r)
    if inc and b.delta > 0:
        out.append("largest increases: " + ", ".join(inc))
    out.append("")
    if not r.changes:
        out.append("No changes.")
    for lvl in (Level.BREAKING, Level.REVIEW, Level.INFO):
        group = [c for c in r.changes if c.level == lvl]
        if not group:
            continue
        for c in group:
            tag = "info" if lvl == Level.INFO else lvl.name
            sec = " [SECURITY]" if c.security == "high" else ""
            out.append(f"[{tag}]{sec} {_where(c)}")
            out.append(f"    {c.message}")
            if c.detail:
                lines = c.detail.splitlines()
                for ln in lines[:max_detail]:
                    out.append(f"      {ln}")
                if len(lines) > max_detail:
                    out.append(f"      ... ({len(lines) - max_detail} more)")
        out.append("")
    out.append(f"required semver bump: {r.required_bump}" + (f"  (declared: {r.declared_bump})" if r.declared_bump else ""))
    out.extend(f"note: {n}" for n in r.notes)
    if r.passed:
        out.append("RESULT: PASS")
    else:
        out.append("RESULT: FAIL")
        out.extend(f"  - {f}" for f in r.failures)
    return "\n".join(out) + "\n"


def render_markdown(r: Result) -> str:
    b = r.budget
    c = r.counts()
    icon = "✅" if r.passed else "❌"
    out = [
        MARKER,
        f"## {icon} toolratchet: {'passed' if r.passed else 'failed'}",
        "",
        f"**{c['breaking']} breaking · {c['review']} to review · {c['info']} compatible**"
        + (f" · {c['security']} security flag(s)" if c["security"] else ""),
        "",
        f"- Tools: {len(r.old.tools)} → {len(r.new.tools)}",
        f"- Tool tokens: {b.old_total:,} → {b.new_total:,} ({_delta(b.old_total, b.new_total)})",
        f"- Required semver bump: **{r.required_bump}**" + (f" (declared: {r.declared_bump})" if r.declared_bump else ""),
    ]
    out.extend(f"- Note: {n}" for n in r.notes)
    if r.failures:
        out.append("")
        out.append("**Why it failed**")
        out.extend(f"- {f}" for f in r.failures)
    if r.changes:
        out.extend(["", "| | Tool | Change |", "|---|---|---|"])
        icons = {Level.BREAKING: "🔴", Level.REVIEW: "🟡", Level.INFO: "🟢"}
        for ch in r.changes:
            lock = " 🔒" if ch.security == "high" else ""
            loc = f"`{ch.tool}`" + (f" › `{ch.path}`" if ch.path else "")
            out.append(f"| {icons[ch.level]} | {loc} | {ch.message.replace('|', '/')}{lock} |")
        details = [ch for ch in r.changes if ch.detail]
        if details:
            out.extend(["", "<details><summary>Details</summary>", ""])
            for ch in details:
                out.append(f"**{ch.tool}** › `{ch.path or ch.kind}`")
                out.extend(["", "```diff", ch.detail or "", "```", ""])
            out.append("</details>")
    return "\n".join(out) + "\n"


def render_json(r: Result) -> str:
    b = r.budget
    doc = {
        "passed": r.passed,
        "failures": r.failures,
        "counts": r.counts(),
        "requiredBump": r.required_bump,
        "declaredBump": r.declared_bump,
        "tokens": {"old": b.old_total, "new": b.new_total, "delta": b.delta, "pct": b.pct},
        "tools": {"old": len(r.old.tools), "new": len(r.new.tools)},
        "changes": [c.to_dict() for c in r.changes],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _esc(s: str, prop: bool = False) -> str:
    s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        s = s.replace(":", "%3A").replace(",", "%2C")
    return s


def render_github(r: Result) -> str:
    out = []
    for c in r.changes:
        cmd = {Level.BREAKING: "error", Level.REVIEW: "warning", Level.INFO: "notice"}[c.level]
        title = _esc(f"toolratchet {c.kind}", prop=True)
        out.append(f"::{cmd} title={title}::{_esc(_where(c) + ': ' + c.message)}")
    for f in r.failures:
        out.append(f"::error title=toolratchet gate::{_esc(f)}")
    return "\n".join(out) + ("\n" if out else "")


def render_inspect(surface, costs: list, top: int = 20) -> str:
    total = sum(c for _n, c in costs)
    out = [f"toolratchet inspect  {surface.source or 'surface'}", f"{len(costs)} tools, ~{total:,} tokens per request", ""]
    for name, tok in sorted(costs, key=lambda x: -x[1])[:top]:
        pct = 100.0 * tok / total if total else 0.0
        out.append(f"{tok:>7,}  {pct:4.1f}%  {name}")
    if len(costs) > top:
        out.append(f"... {len(costs) - top} more")
    return "\n".join(out) + "\n"

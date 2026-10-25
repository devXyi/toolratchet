"""Compare two tool surfaces and classify every change."""

from __future__ import annotations

import difflib
import json
from typing import Any, Optional

from . import integrity as I
from . import schema as S
from .model import Change, Level, Surface, ToolDef

_MISSING = object()

_SEC_MSG = {
    "hidden-unicode": "Invisible Unicode characters were added",
    "instruction-override": "Text tries to override the model's instructions",
    "concealment": "Text asks the model to hide something from the user",
    "covert-action": "Text asks for covert behaviour",
    "exfiltration": "Text appears to direct data to an external destination",
    "sensitive-path": "Text references credential or config files",
    "system-tag": "Text contains pseudo-system tags",
    "imperative-before-use": "Text adds instructions to follow before using the tool",
    "url": "A new external URL was added",
    "encoded-blob": "A long encoded blob was added",
}

# (safe value, risky value) for each MCP tool annotation hint
_HINTS = {
    "readOnlyHint": (True, False, False),
    "destructiveHint": (False, True, True),
    "idempotentHint": (True, False, False),
    "openWorldHint": (False, True, True),
}


def _v(x: Any, n: int = 70) -> str:
    if x is None:
        return "unset"
    s = json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def _list(xs: Any) -> str:
    return "/".join(xs) if xs else "nothing"


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


# --------------------------------------------------------------------------
# issue wording
# --------------------------------------------------------------------------


def _phrase(i: S.Issue, direction: str) -> str:
    d = i.data
    inp = direction == "input"
    k = i.kind
    if k == "type":
        extra, sup = d.get("extra", []), d.get("sup")
        if inp:
            if extra == ["any"]:
                return f"Now restricted to type {_list(sup)} (previously any type)"
            return f"Type {_list(extra)} is no longer accepted (now {_list(sup)})"
        if extra == ["any"]:
            return f"Output may now be any type (previously {_list(sup)})"
        return f"Output type widened: may now be {_list(extra)} (previously {_list(sup)})"
    if k == "required":
        p = d["prop"]
        return f"'{p}' is now required" if inp else f"'{p}' is no longer guaranteed in the output"
    if k == "closed":
        p = d["prop"]
        if p == "*":
            return "Arbitrary keys are no longer accepted" if inp else "Output may now contain undeclared keys"
        return f"'{p}' is no longer accepted (schema is closed)" if inp else f"Output may now contain undeclared property '{p}'"
    if k == "enum":
        if d.get("restricted"):
            if inp:
                return f"Now restricted to {_v(d['values'])} (previously unrestricted)"
            return f"Output is no longer restricted to {_v(d['values'])}"
        if inp:
            return f"Value(s) {_v(d['values'])} are no longer accepted"
        return f"Output may now be {_v(d['values'])}, which was not allowed before"
    if k == "bound":
        name, sub, sup = d["name"], d["sub"], d["sup"]
        if inp:
            return f"{name} tightened: {_v(sub)} -> {_v(sup)}"
        return f"Output {name} loosened: {_v(sup)} -> {_v(sub)}"
    if k == "pattern":
        sub, sup = d["sub"], d["sup"]
        if sub is None:
            return f"pattern {_v(sup)} added" if inp else f"Output no longer guaranteed to match {_v(sup)}"
        if inp:
            return f"pattern changed {_v(sub)} -> {_v(sup)} (regex inclusion cannot be proven)"
        return f"Output pattern changed {_v(sup)} -> {_v(sub)} (regex inclusion cannot be proven)"
    if k == "format":
        sub, sup = d["sub"], d["sup"]
        if inp:
            return f"format changed {_v(sub)} -> {_v(sup)} (validators may enforce it)"
        return f"Output format changed {_v(sup)} -> {_v(sub)}"
    if k == "unique":
        return "uniqueItems is now required" if inp else "Output no longer guarantees unique items"
    if k == "multiple":
        return f"multipleOf changed {_v(d['sub'])} -> {_v(d['sup'])}" if inp else "Output multipleOf guarantee changed"
    return f"Compatibility could not be proven ({d.get('what', 'unsupported schema feature')})"


def _issue_path(i: S.Issue) -> str:
    if i.kind in ("required", "closed") and i.data.get("prop") not in (None, "*"):
        return _join(i.path, i.data["prop"])
    return i.path


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _text_diff(old: str, new: str, limit: int = 40) -> str:
    lines = difflib.unified_diff(I.sentences(old), I.sentences(new), "old", "new", lineterm="", n=0)
    keep = [ln for ln in lines if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return "\n".join(keep[:limit])


def _integrity(tool: str, where: str, text: str, baseline: str) -> list:
    out = []
    for f in I.analyze(text, baseline):
        out.append(
            Change(
                tool,
                f"security.{f.kind}",
                Level.REVIEW,
                _SEC_MSG.get(f.kind, f"Integrity flag: {f.kind}"),
                path=where,
                security=f.severity,
                detail=f.evidence,
            )
        )
    return out


def _walk(schema: Any, root: Any = None, path: str = "", out: Optional[dict] = None, depth: int = 0) -> dict:
    out = {} if out is None else out
    root = schema if root is None else root
    if depth > 12:
        return out
    s = S.flatten_schema(schema, root)
    if not isinstance(s, dict):
        return out
    props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
    req = set(s.get("required", [])) if isinstance(s.get("required"), list) else set()
    for name, sub in props.items():
        p = _join(path, name)
        rs = S.flatten_schema(sub, root)
        out[p] = (rs if isinstance(rs, dict) else {}, name in req)
        _walk(sub, root, p, out, depth + 1)
    items = s.get("items")
    if isinstance(items, dict):
        _walk(items, root, path + "[]", out, depth + 1)
    return out


# --------------------------------------------------------------------------
# per-tool comparison
# --------------------------------------------------------------------------


def _compare_input(tool: str, o: ToolDef, n: ToolDef) -> list:
    out: list = []
    issues = S.check_subschema(o.input_schema, n.input_schema)
    closed: set = set()
    for i in issues:
        level = Level.BREAKING if i.definite else Level.REVIEW
        out.append(Change(tool, f"input.{i.kind}", level, _phrase(i, "input"), path=_issue_path(i)))
        if i.kind == "closed":
            closed.add(_issue_path(i))

    op, np_ = _walk(o.input_schema), _walk(n.input_schema)
    for p, (_s, req) in np_.items():
        if p not in op and not req:
            out.append(Change(tool, "input.param-added", Level.INFO, "Optional parameter added", path=p))
    for p in op:
        if p not in np_ and p not in closed:
            out.append(
                Change(
                    tool,
                    "input.param-removed",
                    Level.REVIEW,
                    "Parameter is no longer declared (an open schema still accepts it, but it may be ignored)",
                    path=p,
                )
            )
    for p in [x for x in op if x in np_]:
        so, sn = op[p][0], np_[p][0]
        do, dn = so.get("default", _MISSING), sn.get("default", _MISSING)
        if do is not _MISSING or dn is not _MISSING:
            if do is _MISSING or dn is _MISSING or not S.json_eq(do, dn):
                out.append(
                    Change(
                        tool,
                        "input.default-changed",
                        Level.REVIEW,
                        f"Default changed: {_v(None if do is _MISSING else do)} -> {_v(None if dn is _MISSING else dn)}",
                        path=p,
                    )
                )
        if (so.get("description") or "") != (sn.get("description") or ""):
            out.append(
                Change(
                    tool,
                    "input.param-description-changed",
                    Level.REVIEW,
                    "Parameter description changed",
                    path=p,
                    detail=_text_diff(so.get("description") or "", sn.get("description") or ""),
                )
            )
        if not so.get("deprecated") and sn.get("deprecated"):
            out.append(Change(tool, "input.param-deprecated", Level.INFO, "Parameter marked deprecated", path=p))

    old_t, new_t = I.collect_texts(o.input_schema), I.collect_texts(n.input_schema)
    for where, text in new_t.items():
        base = old_t.get(where, "")
        if text != base:
            out.extend(_integrity(tool, where, text, base))
    return out


def _compare_output(tool: str, o: ToolDef, n: ToolDef) -> list:
    if o.output_schema is None and n.output_schema is None:
        return []
    if o.output_schema is None:
        return [Change(tool, "output.schema-added", Level.INFO, "Output schema added", path="output")]
    if n.output_schema is None:
        return [
            Change(
                tool,
                "output.schema-removed",
                Level.BREAKING,
                "Output schema removed (the structured-output guarantee is gone)",
                path="output",
            )
        ]
    out = []
    for i in S.check_subschema(n.output_schema, o.output_schema):
        p = _issue_path(i)
        out.append(
            Change(
                tool,
                f"output.{i.kind}",
                Level.BREAKING if i.definite else Level.REVIEW,
                _phrase(i, "output"),
                path="output" + (f".{p}" if p else ""),
            )
        )
    return out


def _compare_annotations(tool: str, o: ToolDef, n: ToolDef) -> list:
    out = []
    for hint, (safe, risky, default) in _HINTS.items():
        eo = o.annotations.get(hint, default)
        en = n.annotations.get(hint, default)
        if not isinstance(eo, bool) or not isinstance(en, bool) or eo == en:
            continue
        if eo == safe and en == risky:
            out.append(
                Change(
                    tool,
                    "annotations.escalated",
                    Level.REVIEW,
                    f"{hint} changed {str(eo).lower()} -> {str(en).lower()}: the tool is now less restricted",
                    path=hint,
                    security="high",
                )
            )
        else:
            out.append(
                Change(
                    tool,
                    "annotations.relaxed",
                    Level.INFO,
                    f"{hint} changed {str(eo).lower()} -> {str(en).lower()}",
                    path=hint,
                )
            )
    return out


def compare_tool(o: ToolDef, n: ToolDef, label: Optional[str] = None) -> list:
    tool = label or n.name
    out: list = []
    if (o.title or "") != (n.title or ""):
        out.append(Change(tool, "title.changed", Level.INFO, "Title changed", path="title"))
    if o.description != n.description:
        out.append(
            Change(
                tool,
                "description.changed",
                Level.REVIEW,
                "Description changed (models choose and call tools from this text)",
                path="description",
                detail=_text_diff(o.description, n.description),
            )
        )
        out.extend(_integrity(tool, "description", n.description, o.description))
    out.extend(_compare_input(tool, o, n))
    out.extend(_compare_output(tool, o, n))
    out.extend(_compare_annotations(tool, o, n))
    return out


# --------------------------------------------------------------------------
# surface comparison (adds / removes / renames)
# --------------------------------------------------------------------------


def _prop_names(t: ToolDef) -> set:
    s = S.flatten_schema(t.input_schema)
    props = s.get("properties") if isinstance(s, dict) else None
    return set(props) if isinstance(props, dict) else set()


def _similarity(o: ToolDef, n: ToolDef) -> tuple:
    po, pn = _prop_names(o), _prop_names(n)
    if o.input_schema == n.input_schema:
        s_schema = 1.0
    elif po or pn:
        s_schema = 0.9 * len(po & pn) / len(po | pn)
    else:
        s_schema = 0.5
    if not o.description and not n.description:
        s_desc = 0.5
    else:
        s_desc = difflib.SequenceMatcher(None, o.description.lower(), n.description.lower()).ratio()
    s_name = difflib.SequenceMatcher(None, o.name.lower(), n.name.lower()).ratio()
    return 0.5 * s_schema + 0.3 * s_desc + 0.2 * s_name, s_desc, s_name


def _match_renames(removed: list, added: list, threshold: float) -> list:
    cands = []
    for o in removed:
        for n in added:
            score, s_desc, s_name = _similarity(o, n)
            if score >= threshold and (s_desc >= 0.5 or s_name >= 0.6):
                cands.append((score, o, n))
    cands.sort(key=lambda c: -c[0])
    used_o: set = set()
    used_n: set = set()
    pairs = []
    for _score, o, n in cands:
        if o.name in used_o or n.name in used_n:
            continue
        used_o.add(o.name)
        used_n.add(n.name)
        pairs.append((o, n))
    return pairs


def compare(old: Surface, new: Surface, rename_threshold: float = 0.75) -> list:
    o = {t.name: t for t in old.tools}
    n = {t.name: t for t in new.tools}
    removed = [x for x in o if x not in n]
    added = [x for x in n if x not in o]
    pairs = _match_renames([o[x] for x in removed], [n[x] for x in added], rename_threshold)
    renamed_old = {a.name for a, _ in pairs}
    renamed_new = {b.name for _, b in pairs}

    changes: list = []
    for name in o:
        if name in n:
            changes.extend(compare_tool(o[name], n[name]))
    for a, b in pairs:
        changes.append(
            Change(
                f"{a.name} -> {b.name}",
                "tool.renamed",
                Level.BREAKING,
                f"Tool renamed to '{b.name}'; callers still using '{a.name}' will fail",
            )
        )
        changes.extend(compare_tool(a, b, label=f"{a.name} -> {b.name}"))
    for name in removed:
        if name not in renamed_old:
            changes.append(Change(name, "tool.removed", Level.BREAKING, "Tool removed"))
    for name in added:
        if name in renamed_new:
            continue
        t = n[name]
        changes.append(Change(name, "tool.added", Level.INFO, "Tool added"))
        changes.extend(_integrity(name, "description", t.description, ""))
        for where, text in I.collect_texts(t.input_schema).items():
            changes.extend(_integrity(name, where, text, ""))
    changes.sort(key=lambda c: (-int(c.level), c.tool, c.path, c.kind))
    return changes

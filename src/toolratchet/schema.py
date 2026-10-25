"""Conservative JSON Schema compatibility analysis.

``check_subschema(sub, sup)`` answers one question: is every value accepted by
``sub`` also accepted by ``sup``?  It returns a list of :class:`Issue`:

* empty list      -> *proven*: ``sub`` is a subschema of ``sup``
* definite issue  -> proven (or, for a few constraints, assumed) incompatible
* other issue     -> could not be proven either way ("unknown")

The design rule is soundness: **never report "compatible" unless it was
proven**. Anything the engine cannot reason about (regex inclusion, ``not``,
``if/then/else`` ...) surfaces as *unknown* so a human reviews it instead of the
change slipping through silently.

For a tool's *input* schema, callers send what the old schema allowed, so the
check is ``old ⊆ new``. For an *output* schema, consumers expect what the old
schema promised, so the check is ``new ⊆ old``.

Practical assumption: callers only send properties the schema declares. Adding
an optional, constrained property to an open schema is therefore compatible.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Optional

YES, NO, UNKNOWN = "yes", "no", "unknown"

ALL_TYPES = frozenset({"null", "boolean", "integer", "number", "string", "array", "object"})

_ANNOTATIONS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "$schema",
        "$id",
        "$comment",
        "$anchor",
        "deprecated",
        "readOnly",
        "writeOnly",
        "$defs",
        "definitions",
    }
)
_UNSUPPORTED = frozenset(
    {
        "not",
        "if",
        "then",
        "else",
        "contains",
        "minContains",
        "maxContains",
        "patternProperties",
        "propertyNames",
        "dependentRequired",
        "dependentSchemas",
        "dependencies",
        "unevaluatedProperties",
        "unevaluatedItems",
        "additionalItems",
        "$dynamicRef",
        "$dynamicAnchor",
        "$recursiveRef",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
    }
)
_COMBINATORS = frozenset({"allOf", "anyOf", "oneOf"})
_HANDLED = frozenset(
    {
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minProperties",
        "maxProperties",
        "nullable",
        "$ref",
    }
) | _COMBINATORS


@dataclass(frozen=True)
class Issue:
    kind: str
    path: str
    definite: bool
    data: dict = field(default_factory=dict, compare=False, hash=False)


class _Unresolvable(Exception):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _frac(x: Any) -> Fraction:
    return Fraction(str(x))


def _core(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in _ANNOTATIONS and not k.startswith("x-")}


def _lookup(ref: str, root: Any) -> Any:
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        raise _Unresolvable(ref)
    node = root
    for raw in ref[2:].split("/"):
        key = urllib.parse.unquote(raw).replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            raise _Unresolvable(ref)
    return node


def resolve(schema: Any, root: Any) -> Any:
    """Follow local ``$ref`` chains. Raises ``_Unresolvable`` on failure."""
    hops = 0
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        target = _lookup(schema["$ref"], root)
        siblings = {k: v for k, v in schema.items() if k != "$ref" and k not in _ANNOTATIONS}
        schema = {"allOf": [target, siblings]} if siblings else target
        hops += 1
        if hops > 32:
            raise _Unresolvable(schema)
    return schema


def _tset(t: Any) -> Optional[set]:
    if t is None:
        return None
    return {t} if isinstance(t, str) else set(t)


def _types(s: dict) -> Optional[set]:
    return _tset(s.get("type"))


def _norm(s: dict) -> dict:
    """Light normalisation: ``nullable`` and ``anyOf`` of bare types."""
    s = dict(s)
    if s.get("nullable") is True and "type" in s:
        ts = _tset(s["type"]) or set()
        s["type"] = sorted(ts | {"null"})
    s.pop("nullable", None)
    br = s.get("anyOf")
    if isinstance(br, list) and br and "type" not in s:
        merged: set = set()
        ok = True
        for b in br:
            if isinstance(b, dict) and set(_core(b)) == {"type"}:
                merged |= _tset(b["type"]) or set()
            else:
                ok = False
                break
        if ok:
            s = {k: v for k, v in s.items() if k != "anyOf"}
            s["type"] = sorted(merged)
    return s


def _type_intersect(a: set, b: set) -> set:
    """Intersection of two JSON type sets, honouring integer ⊂ number."""
    out = a & b
    if ("integer" in a and "number" in b) or ("number" in a and "integer" in b):
        out = out | {"integer"}
    return out


def _merge2(x: dict, y: Any) -> tuple:
    """Merge ``y`` into ``x`` as a conjunction. Returns (merged, residual list)."""
    if y is True:
        return x, []
    if y is False or not isinstance(y, dict) or "$ref" in y:
        return x, [y]
    out = dict(x)
    residual: list = []
    for k, v in y.items():
        if k in _ANNOTATIONS or k.startswith("x-"):
            out.setdefault(k, v)
            continue
        if k not in out:
            out[k] = v
            continue
        cur = out[k]
        if cur == v:
            continue
        if k == "type":
            out[k] = sorted(_type_intersect(_tset(cur) or set(), _tset(v) or set()))
        elif k == "required" and isinstance(cur, list) and isinstance(v, list):
            out[k] = list(dict.fromkeys([*cur, *v]))
        elif k == "properties" and isinstance(cur, dict) and isinstance(v, dict):
            props = dict(cur)
            for pk, pv in v.items():
                props[pk] = {"allOf": [props[pk], pv]} if pk in props and props[pk] != pv else pv
            out[k] = props
        elif k in ("minimum", "minLength", "minItems", "minProperties") and _is_num(cur) and _is_num(v):
            out[k] = max(cur, v)
        elif k in ("maximum", "maxLength", "maxItems", "maxProperties") and _is_num(cur) and _is_num(v):
            out[k] = min(cur, v)
        elif k == "enum" and isinstance(cur, list) and isinstance(v, list):
            out[k] = [e for e in cur if any(_json_eq(e, o) for o in v)]
        elif k == "uniqueItems" and isinstance(cur, bool) and isinstance(v, bool):
            out[k] = cur or v
        elif k == "items" and isinstance(cur, dict) and isinstance(v, dict):
            out[k] = {"allOf": [cur, v]}
        elif k == "additionalProperties":
            if cur is False or v is False:
                out[k] = False
            elif isinstance(cur, dict) and isinstance(v, dict):
                out[k] = {"allOf": [cur, v]}
            elif cur is True:
                out[k] = v
        else:
            residual.append({k: v})
    return out, residual


def _flatten(s: dict) -> dict:
    """Merge ``allOf`` members into one schema where that is safely possible."""
    members = s.get("allOf")
    if not isinstance(members, list):
        return s
    merged = {k: v for k, v in s.items() if k != "allOf"}
    residual: list = []
    for m in members:
        merged, res = _merge2(merged, m)
        residual.extend(res)
    if residual:
        merged = {**merged, "allOf": residual}
    return merged


def flatten_schema(schema: Any, root: Any = None) -> Any:
    """Public helper: resolve refs, normalise and merge ``allOf``."""
    root = schema if root is None else root
    try:
        s = resolve(schema, root)
    except _Unresolvable:
        return {}
    if not isinstance(s, dict):
        return s
    return _flatten(_norm(s))


def _json_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b))
    return a == b


def _matches(v: Any, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return _is_num(v)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return False


json_eq = _json_eq


def _and(results: list) -> str:
    if NO in results:
        return NO
    if UNKNOWN in results:
        return UNKNOWN
    return YES


# --------------------------------------------------------------------------
# instance validation (used for enum/const reasoning and self-tests)
# --------------------------------------------------------------------------


def validate(inst: Any, schema: Any, root: Any = None, depth: int = 0) -> str:
    """Tri-state validation: ``yes`` valid, ``no`` invalid, ``unknown``."""
    root = schema if root is None else root
    if depth > 40:
        return UNKNOWN
    if schema is True:
        return YES
    if schema is False:
        return NO
    if not isinstance(schema, dict):
        return UNKNOWN
    try:
        schema = resolve(schema, root)
    except _Unresolvable:
        return UNKNOWN
    if schema is True or schema is False or not isinstance(schema, dict):
        return validate(inst, schema, root, depth + 1)
    s = _norm(schema)
    res: list = []

    t = _types(s)
    if t is not None:
        res.append(YES if any(_matches(inst, x) for x in t) else NO)
    if "enum" in s and isinstance(s["enum"], list):
        res.append(YES if any(_json_eq(inst, e) for e in s["enum"]) else NO)
    if "const" in s:
        res.append(YES if _json_eq(inst, s["const"]) else NO)

    if _is_num(inst):
        x = _frac(inst)
        mn, mx = s.get("minimum"), s.get("maximum")
        exmn, exmx = s.get("exclusiveMinimum"), s.get("exclusiveMaximum")
        if _is_num(mn):
            res.append(YES if (x > _frac(mn) if exmn is True else x >= _frac(mn)) else NO)
        if _is_num(mx):
            res.append(YES if (x < _frac(mx) if exmx is True else x <= _frac(mx)) else NO)
        if _is_num(exmn):
            res.append(YES if x > _frac(exmn) else NO)
        if _is_num(exmx):
            res.append(YES if x < _frac(exmx) else NO)
        m = s.get("multipleOf")
        if _is_num(m) and m != 0:
            res.append(YES if (x / _frac(m)).denominator == 1 else NO)

    if isinstance(inst, str):
        if _is_num(s.get("minLength")):
            res.append(YES if len(inst) >= s["minLength"] else NO)
        if _is_num(s.get("maxLength")):
            res.append(YES if len(inst) <= s["maxLength"] else NO)
        if isinstance(s.get("pattern"), str):
            try:
                res.append(YES if re.search(s["pattern"], inst) else NO)
            except re.error:
                res.append(UNKNOWN)
        if "format" in s:
            res.append(UNKNOWN)

    if isinstance(inst, list):
        if _is_num(s.get("minItems")):
            res.append(YES if len(inst) >= s["minItems"] else NO)
        if _is_num(s.get("maxItems")):
            res.append(YES if len(inst) <= s["maxItems"] else NO)
        if s.get("uniqueItems") is True:
            dup = any(_json_eq(inst[i], inst[j]) for i in range(len(inst)) for j in range(i + 1, len(inst)))
            res.append(NO if dup else YES)
        if "prefixItems" in s or isinstance(s.get("items"), list):
            res.append(UNKNOWN)
        elif "items" in s:
            res.extend(validate(el, s["items"], root, depth + 1) for el in inst)

    if isinstance(inst, dict):
        req = s.get("required")
        if isinstance(req, list):
            res.append(YES if all(k in inst for k in req if isinstance(k, str)) else NO)
        props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
        for k, v in inst.items():
            if k in props:
                res.append(validate(v, props[k], root, depth + 1))
        extra = [k for k in inst if k not in props]
        ap = s.get("additionalProperties")
        if extra and ap is not None:
            if "patternProperties" in s:
                res.append(UNKNOWN)
            elif ap is False:
                res.append(NO)
            elif isinstance(ap, dict):
                res.extend(validate(inst[k], ap, root, depth + 1) for k in extra)
        if _is_num(s.get("minProperties")):
            res.append(YES if len(inst) >= s["minProperties"] else NO)
        if _is_num(s.get("maxProperties")):
            res.append(YES if len(inst) <= s["maxProperties"] else NO)

    if isinstance(s.get("allOf"), list):
        res.extend(validate(inst, m, root, depth + 1) for m in s["allOf"])
    if isinstance(s.get("anyOf"), list):
        rs = [validate(inst, m, root, depth + 1) for m in s["anyOf"]]
        res.append(YES if YES in rs else (NO if all(r == NO for r in rs) else UNKNOWN))
    if isinstance(s.get("oneOf"), list):
        rs = [validate(inst, m, root, depth + 1) for m in s["oneOf"]]
        yes = rs.count(YES)
        if yes > 1:
            res.append(NO)
        elif yes == 1:
            res.append(YES if UNKNOWN not in rs else UNKNOWN)
        else:
            res.append(NO if all(r == NO for r in rs) else UNKNOWN)
    if "not" in s:
        r = validate(inst, s["not"], root, depth + 1)
        res.append({YES: NO, NO: YES}.get(r, UNKNOWN))
    for k in ("if", "then", "else", "contains", "propertyNames", "dependentRequired", "dependentSchemas", "dependencies"):
        if k in s:
            res.append(UNKNOWN)
    return _and(res)


# --------------------------------------------------------------------------
# subschema check
# --------------------------------------------------------------------------


class _Ctx:
    def __init__(self, sub_root: Any, sup_root: Any) -> None:
        self.sub_root = sub_root
        self.sup_root = sup_root
        self.stack: set = set()


def check_subschema(sub: Any, sup: Any, *, sub_root: Any = None, sup_root: Any = None) -> list:
    """Return the issues preventing ``sub`` from being proven a subschema of ``sup``."""
    ctx = _Ctx(sub if sub_root is None else sub_root, sup if sup_root is None else sup_root)
    out: list = []
    _sub(ctx, sub, sup, "", out, 0)
    seen: set = set()
    uniq: list = []
    for i in out:
        key = (i.kind, i.path, i.definite, json.dumps(i.data, sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            uniq.append(i)
    return uniq


def _unknown(out: list, path: str, what: str) -> None:
    out.append(Issue("unsupported", path, False, {"what": what}))


def _sub(ctx: _Ctx, a: Any, b: Any, path: str, out: list, depth: int) -> None:
    if depth > 40:
        _unknown(out, path, "schema nesting too deep")
        return
    try:
        a = resolve(a, ctx.sub_root)
        b = resolve(b, ctx.sup_root)
    except _Unresolvable as exc:
        _unknown(out, path, f"unresolvable $ref {exc}")
        return
    if a is b or a == b:
        return
    if b is True or (isinstance(b, dict) and not _core(b)):
        return
    if a is False:
        return
    if b is False:
        out.append(Issue("type", path, True, {"extra": ["any"], "sup": []}))
        return
    if a is True:
        a = {}
    if not isinstance(a, dict) or not isinstance(b, dict):
        _unknown(out, path, "non-object schema")
        return
    key = (id(a), id(b))
    if key in ctx.stack:
        return  # coinductive: assume the recursive case holds
    ctx.stack.add(key)
    try:
        _sub_dicts(ctx, _flatten(_norm(a)), _flatten(_norm(b)), path, out, depth)
    finally:
        ctx.stack.discard(key)


def _sub_dicts(ctx: _Ctx, a: dict, b: dict, path: str, out: list, depth: int) -> None:
    ca, cb = _core(a), _core(b)

    # a: unions -> every branch must fit inside b
    for ck in ("anyOf", "oneOf"):
        if isinstance(ca.get(ck), list):
            own = {k: v for k, v in a.items() if k != ck}
            if _core(own):
                tmp: list = []
                _sub(ctx, own, b, path, tmp, depth + 1)
                if not tmp:
                    return
            for br in ca[ck]:
                _sub(ctx, {"allOf": [own, br]} if _core(own) else br, b, path, out, depth + 1)
            return

    # a: residual conjunction -> sufficient if any member fits b
    if isinstance(ca.get("allOf"), list):
        members = [{k: v for k, v in a.items() if k != "allOf"}, *ca["allOf"]]
        for m in members:
            tmp = []
            _sub(ctx, m, b, path, tmp, depth + 1)
            if not tmp:
                return
        _unknown(out, path, "could not prove subset of a conjunction")
        return

    # b: conjunction -> all members must hold
    if isinstance(cb.get("allOf"), list):
        own = {k: v for k, v in b.items() if k != "allOf"}
        if _core(own):
            _sub(ctx, a, own, path, out, depth + 1)
        for m in cb["allOf"]:
            _sub(ctx, a, m, path, out, depth + 1)
        return

    # b: unions -> a must fit one branch
    for ck in ("anyOf", "oneOf"):
        if isinstance(cb.get(ck), list):
            own = {k: v for k, v in b.items() if k != ck}
            if _core(own):
                _sub(ctx, a, own, path, out, depth + 1)
            _sub_union(ctx, a, cb[ck], path, out, depth)
            return

    for k in _UNSUPPORTED:
        if k in cb and ca.get(k) != cb[k]:
            _unknown(out, path, k)
    for k in cb:
        if k not in _HANDLED and k not in _UNSUPPORTED and ca.get(k) != cb[k]:
            _unknown(out, path, k)

    _leaf(ctx, a, b, path, out, depth)


def _sub_union(ctx: _Ctx, a: dict, branches: list, path: str, out: list, depth: int) -> None:
    results = []
    for br in branches:
        tmp: list = []
        _sub(ctx, a, br, path, tmp, depth + 1)
        if not tmp:
            return
        results.append(tmp)
    vals = _finite(a)
    if vals is not None:
        _check_values(ctx, vals, {"anyOf": branches}, path, out)
        return
    if all(any(i.definite and i.kind == "type" for i in r) for r in results):
        sup: set = set()
        for br in branches:
            if isinstance(br, dict):
                sup |= _types(br) or set()
        out.append(Issue("type", path, True, {"extra": sorted(_types(a) or {"any"}), "sup": sorted(sup)}))
        return
    _unknown(out, path, "could not prove subset of a union")


def _finite(s: dict) -> Optional[list]:
    if "const" in s:
        return [s["const"]]
    if isinstance(s.get("enum"), list):
        return list(s["enum"])
    t = _types(s)
    if t == {"boolean"}:
        return [True, False]
    if t == {"null"}:
        return [None]
    return None


def _check_values(ctx: _Ctx, vals: list, b: Any, path: str, out: list) -> None:
    bad: list = []
    unk = False
    for v in vals:
        r = validate(v, b, ctx.sup_root)
        if r == NO:
            bad.append(v)
        elif r == UNKNOWN:
            unk = True
    if bad:
        out.append(Issue("enum", path, True, {"values": bad, "restricted": False}))
    elif unk:
        _unknown(out, path, "enum values could not be validated")


def _leaf(ctx: _Ctx, a: dict, b: dict, path: str, out: list, depth: int) -> None:
    va = _finite(a)
    if va is not None:
        _check_values(ctx, va, b, path, out)
        return
    vb = _finite(b)
    if vb is not None:
        out.append(Issue("enum", path, True, {"restricted": True, "values": vb}))
        return

    ta, tb = _types(a), _types(b)
    if tb is not None:
        if ta is None:
            out.append(Issue("type", path, True, {"extra": ["any"], "sup": sorted(tb)}))
            return
        extra = sorted(t for t in ta if t not in tb and not (t == "integer" and "number" in tb))
        if extra:
            out.append(Issue("type", path, True, {"extra": extra, "sup": sorted(tb)}))
    active = set(ta) if ta is not None else set(ALL_TYPES)
    if active & {"number", "integer"}:
        _numeric(a, b, path, out, ta)
    if "string" in active:
        _string(a, b, path, out)
    if "array" in active:
        _array(ctx, a, b, path, out, depth)
    if "object" in active:
        _object(ctx, a, b, path, out, depth)


def _lower(s: dict, integer: bool) -> Optional[tuple]:
    cands = []
    mn, ex = s.get("minimum"), s.get("exclusiveMinimum")
    if _is_num(mn):
        cands.append((_frac(mn), ex is True))
    if _is_num(ex):
        cands.append((_frac(ex), True))
    if not cands:
        return None
    v, e = max(cands, key=lambda c: (c[0], c[1]))
    if integer:
        v = Fraction(math.floor(v) + 1 if e else math.ceil(v))
        e = False
    return (v, e)


def _upper(s: dict, integer: bool) -> Optional[tuple]:
    cands = []
    mx, ex = s.get("maximum"), s.get("exclusiveMaximum")
    if _is_num(mx):
        cands.append((_frac(mx), ex is True))
    if _is_num(ex):
        cands.append((_frac(ex), True))
    if not cands:
        return None
    v, e = min(cands, key=lambda c: (c[0], -int(c[1])))
    if integer:
        v = Fraction(math.ceil(v) - 1 if e else math.floor(v))
        e = False
    return (v, e)


def _bound(out: list, path: str, name: str, sub: Any, sup: Any) -> None:
    out.append(Issue("bound", path, True, {"name": name, "sub": sub, "sup": sup}))


def _num_out(f: Fraction) -> Any:
    return int(f) if f.denominator == 1 else float(f)


def _numeric(a: dict, b: dict, path: str, out: list, ta: Optional[set]) -> None:
    integer = ta is not None and ta <= {"integer"}
    la, lb = _lower(a, integer), _lower(b, integer)
    if lb is not None and (la is None or (la[0], la[1]) < (lb[0], lb[1])):
        _bound(out, path, "minimum", None if la is None else _num_out(la[0]), _num_out(lb[0]))
    ua, ub = _upper(a, integer), _upper(b, integer)
    if ub is not None and (ua is None or (ua[0], -int(ua[1])) > (ub[0], -int(ub[1]))):
        _bound(out, path, "maximum", None if ua is None else _num_out(ua[0]), _num_out(ub[0]))
    mb = b.get("multipleOf")
    if _is_num(mb) and mb != 0:
        ma = a.get("multipleOf")
        if _is_num(ma) and ma != 0:
            if (_frac(ma) / _frac(mb)).denominator != 1:
                out.append(Issue("multiple", path, True, {"sub": ma, "sup": mb}))
        elif not (integer and _frac(mb) == 1):
            out.append(Issue("multiple", path, True, {"sub": None, "sup": mb}))


def _string(a: dict, b: dict, path: str, out: list) -> None:
    mla, mlb = a.get("minLength", 0), b.get("minLength", 0)
    if _is_num(mlb) and _is_num(mla) and mlb > mla:
        _bound(out, path, "minLength", mla, mlb)
    xa, xb = a.get("maxLength"), b.get("maxLength")
    if _is_num(xb) and (not _is_num(xa) or xa > xb):
        _bound(out, path, "maxLength", xa, xb)
    pa, pb = a.get("pattern"), b.get("pattern")
    if pb is not None and pb != pa:
        if pa is None:
            out.append(Issue("pattern", path, True, {"sub": None, "sup": pb}))
        else:
            out.append(Issue("pattern", path, False, {"sub": pa, "sup": pb}))
    fa, fb = a.get("format"), b.get("format")
    if fb is not None and fb != fa:
        out.append(Issue("format", path, False, {"sub": fa, "sup": fb}))


def _items_sig(s: dict) -> Any:
    return (s.get("prefixItems"), s.get("items") if isinstance(s.get("items"), list) else None)


def _array(ctx: _Ctx, a: dict, b: dict, path: str, out: list, depth: int) -> None:
    mia, mib = a.get("minItems", 0), b.get("minItems", 0)
    if _is_num(mib) and _is_num(mia) and mib > mia:
        _bound(out, path, "minItems", mia, mib)
    xa, xb = a.get("maxItems"), b.get("maxItems")
    if _is_num(xb) and (not _is_num(xa) or xa > xb):
        _bound(out, path, "maxItems", xa, xb)
    if b.get("uniqueItems") is True and a.get("uniqueItems") is not True:
        out.append(Issue("unique", path, True, {}))
    tuple_b = "prefixItems" in b or isinstance(b.get("items"), list)
    tuple_a = "prefixItems" in a or isinstance(a.get("items"), list)
    if tuple_b or tuple_a:
        if _items_sig(a) != _items_sig(b):
            _unknown(out, path, "tuple-form items differ")
        return
    ib = b.get("items")
    if ib is not None and ib is not True:
        _sub(ctx, a.get("items", True), ib, path + "[]", out, depth + 1)


def _object(ctx: _Ctx, a: dict, b: dict, path: str, out: list, depth: int) -> None:
    pa = a.get("properties") if isinstance(a.get("properties"), dict) else {}
    pb = b.get("properties") if isinstance(b.get("properties"), dict) else {}
    ra = set(x for x in a.get("required", []) if isinstance(x, str)) if isinstance(a.get("required"), list) else set()
    rb = set(x for x in b.get("required", []) if isinstance(x, str)) if isinstance(b.get("required"), list) else set()
    for p in sorted(rb - ra):
        out.append(Issue("required", path, True, {"prop": p}))
    apb = b.get("additionalProperties")
    apa = a.get("additionalProperties")
    for p, sa in pa.items():
        if p in pb:
            _sub(ctx, sa, pb[p], _join(path, p), out, depth + 1)
        elif apb is False and "patternProperties" not in b:
            out.append(Issue("closed", path, True, {"prop": p}))
        elif isinstance(apb, dict):
            _sub(ctx, sa, apb, _join(path, p), out, depth + 1)
    if isinstance(apa, dict):
        for p, sb in pb.items():
            if p not in pa:
                _sub(ctx, apa, sb, _join(path, p), out, depth + 1)
    if apb is False and not pa and apa not in (None, False) and "patternProperties" not in b:
        out.append(Issue("closed", path, True, {"prop": "*"}))
    mpa, mpb = a.get("minProperties", 0), b.get("minProperties", 0)
    if _is_num(mpb) and _is_num(mpa) and mpb > mpa:
        _bound(out, path, "minProperties", mpa, mpb)
    xpa, xpb = a.get("maxProperties"), b.get("maxProperties")
    if _is_num(xpb) and (not _is_num(xpa) or xpa > xpb):
        _bound(out, path, "maxProperties", xpa, xpb)

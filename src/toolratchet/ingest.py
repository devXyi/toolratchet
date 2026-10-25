"""Parse tool definitions from MCP, OpenAI, Anthropic and Gemini formats."""

from __future__ import annotations

from typing import Any

from .model import Surface, ToolDef


class IngestError(ValueError):
    pass


_SKIP = {"default", "const", "enum", "examples"}
_MAPS = {"properties", "patternProperties", "$defs", "definitions"}


def _lower_type(v: Any) -> Any:
    if isinstance(v, str):
        return v.lower()
    if isinstance(v, list):
        return [x.lower() if isinstance(x, str) else x for x in v]
    return v


def _norm_types(node: Any) -> Any:
    """Lower-case ``type`` keywords (Gemini uses 'STRING', 'OBJECT')."""
    if isinstance(node, dict):
        out: dict = {}
        for k, v in node.items():
            if k in _SKIP:
                out[k] = v
            elif k == "type":
                out[k] = _lower_type(v)
            elif k in _MAPS and isinstance(v, dict):
                out[k] = {pk: _norm_types(pv) for pk, pv in v.items()}
            else:
                out[k] = _norm_types(v)
        return out
    if isinstance(node, list):
        return [_norm_types(x) for x in node]
    return node


def _tool_from(item: dict) -> ToolDef:
    fn = item.get("function") if item.get("type") == "function" and isinstance(item.get("function"), dict) else item
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        raise IngestError(f"tool without a name: {str(item)[:80]}")
    schema: Any = None
    for key in ("inputSchema", "input_schema", "parameters", "parametersJsonSchema"):
        if key in fn:
            schema = fn[key]
            break
    if not isinstance(schema, (dict, bool)):
        schema = {"type": "object"}
    out = None
    for key in ("outputSchema", "output_schema"):
        if isinstance(fn.get(key), dict):
            out = _norm_types(fn[key])
            break
    ann = fn.get("annotations") if isinstance(fn.get("annotations"), dict) else {}
    title = fn.get("title") if isinstance(fn.get("title"), str) else ann.get("title")
    ann = {k: v for k, v in ann.items() if k != "title"}
    desc = fn.get("description")
    return ToolDef(
        name=name,
        description=desc if isinstance(desc, str) else "",
        input_schema=_norm_types(schema),
        output_schema=out,
        annotations=ann,
        title=title if isinstance(title, str) else None,
    )


def _expand(items: list) -> list:
    out: list = []
    for it in items:
        if not isinstance(it, dict):
            raise IngestError("tool entries must be JSON objects")
        if isinstance(it.get("functionDeclarations"), list):
            out.extend(_expand(it["functionDeclarations"]))
        else:
            out.append(_tool_from(it))
    return out


def parse_surface(data: Any, source: str = "") -> Surface:
    """Accept an MCP ``tools/list`` result, a JSON-RPC response, a lock file,
    or a bare list of OpenAI / Anthropic / Gemini tool definitions."""
    server: dict = {}
    if isinstance(data, dict):
        if isinstance(data.get("result"), dict) and "tools" in data["result"]:
            data = data["result"]
        for key in ("server", "serverInfo"):
            if isinstance(data.get(key), dict):
                server = {k: data[key][k] for k in ("name", "version") if k in data[key]}
        if isinstance(data.get("tools"), list):
            items = data["tools"]
        elif isinstance(data.get("functionDeclarations"), list):
            items = [data]
        else:
            raise IngestError("JSON object has no 'tools' array")
    elif isinstance(data, list):
        items = data
    else:
        raise IngestError("expected a JSON object or array of tool definitions")
    tools = _expand(items)
    seen: set = set()
    for t in tools:
        if t.name in seen:
            raise IngestError(f"duplicate tool name {t.name!r}")
        seen.add(t.name)
    return Surface(tools=tools, server=server, source=source)

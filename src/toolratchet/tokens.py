"""Token estimation for tool definitions.

The default estimator is a deterministic heuristic (no dependencies). It is
meant for *relative* comparisons (old vs new), which is what a CI gate needs.
Install the ``tiktoken`` extra and pass ``--tokenizer tiktoken`` for exact
counts against an OpenAI encoding.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from .model import ToolDef

_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z\d]{1,2}")


def estimate_tokens(text: str) -> int:
    n = 0
    for m in _TOKEN_RE.finditer(text):
        s = m.group(0)
        if s[0].isalpha():
            n += 1 + len(s) // 9
        elif s[0].isdigit():
            n += 1 + (len(s) - 1) // 3
        else:
            n += 1
    return n


def get_tokenizer(name: str = "estimate") -> Callable[[str], int]:
    if name == "estimate":
        return estimate_tokens
    if name == "tiktoken":
        try:
            import tiktoken  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("tiktoken is not installed (pip install 'toolratchet[tiktoken]')") from exc
        enc = tiktoken.get_encoding("cl100k_base")
        return lambda text: len(enc.encode(text))
    raise ValueError(f"unknown tokenizer {name!r}")


def tool_payload(tool: ToolDef) -> str:
    """The text a client typically sends to the model for this tool."""
    payload = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def tool_tokens(tool: ToolDef, count: Callable[[str], int] = estimate_tokens) -> int:
    return count(tool_payload(tool))

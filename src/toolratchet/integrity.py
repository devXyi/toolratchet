"""Integrity heuristics for tool text (descriptions, parameter docs, enums).

These catch the usual shapes of tool poisoning and "rug pull" updates: hidden
characters, instruction-override phrases, concealment, exfiltration and
references to credential files. They only inspect text that is *new* in a
change, so an unchanged legacy description never raises a flag.

They are heuristics, not a verdict: a flag means "a human should look".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_HIDDEN_RANGES = (
    (0x200B, 0x200F),  # zero-width + directional marks
    (0x202A, 0x202E),  # bidi embeddings / overrides
    (0x2060, 0x2064),  # word joiner, invisible operators
    (0x2066, 0x2069),  # bidi isolates
    (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
    (0xE0000, 0xE007F),  # Unicode "tag" characters (ASCII smuggling)
)

# "Any character except a newline or a sentence end (period + space)": lets paths
# such as ~/.ssh/id_rsa sit inside one match while still stopping at sentence ends.
_G = r"(?:(?!\.\s)[^\n])"


def _gap(n: int) -> str:
    return _G + "{0,%d}" % n


_PATTERNS = [
    (
        "instruction-override",
        "high",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b"
            + _gap(40)
            + r"\b(previous|prior|above|earlier|all|any)\b"
            + _gap(30)
            + r"\b(instructions?|prompts?|rules?|messages?|guidelines?)\b",
            re.I,
        ),
    ),
    (
        "concealment",
        "high",
        re.compile(
            r"\b(do not|don't|never|without)\b"
            + _gap(30)
            + r"\b(tell|inform|notify|alert|mention|reveal|show|disclose)\b"
            + _gap(20)
            + r"\b(the )?(user|human|operator)\b",
            re.I,
        ),
    ),
    ("covert-action", "high", re.compile(r"\b(secretly|silently|covertly|stealthily)\b", re.I)),
    (
        "exfiltration",
        "high",
        re.compile(
            r"\b(send|post|upload|forward|transmit|exfiltrate|leak|email)\b"
            + _gap(80)
            + r"\b(to|at|via)\b"
            + _gap(40)
            + r"(https?://|\S+@\S+|webhook|attacker|remote server)",
            re.I,
        ),
    ),
    (
        "sensitive-path",
        "high",
        re.compile(
            r"(~/\.ssh|\bid_rsa\b|\.aws/credentials|/etc/passwd|/etc/shadow|\.npmrc|\.netrc|"
            r"\.git-credentials|\bmcp\.json\b|claude_desktop_config|\.env\b)",
            re.I,
        ),
    ),
    ("system-tag", "high", re.compile(r"<\s*/?\s*(important|system|instructions?|admin|override)\s*>", re.I)),
    (
        "imperative-before-use",
        "low",
        re.compile(
            r"\b(before|prior to)\b"
            + _gap(30)
            + r"\b(using|calling|invoking|running)\b"
            + _gap(30)
            + r"\b(this|the|any) (tool|function)\b",
            re.I,
        ),
    ),
    ("url", "low", re.compile(r"https?://[^\s)>\]'\"]+")),
    ("encoded-blob", "low", re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/])")),
]


@dataclass(frozen=True)
class Flag:
    kind: str
    severity: str  # "high" | "low"
    evidence: str


def _hidden_chars(text: str) -> list:
    return [ch for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _HIDDEN_RANGES)]


def _decode_tags(chars: list) -> str:
    out = []
    for ch in chars:
        cp = ord(ch)
        if 0xE0020 <= cp <= 0xE007E:
            out.append(chr(cp - 0xE0000))
    return "".join(out)


def analyze(text: str, baseline: str = "") -> list:
    """Flags in ``text`` that are not already present in ``baseline``."""
    flags: list = []
    hidden = _hidden_chars(text)
    if len(hidden) > len(_hidden_chars(baseline)):
        codes: dict = {}
        for ch in hidden:
            key = f"U+{ord(ch):04X}"
            codes[key] = codes.get(key, 0) + 1
        evidence = ", ".join(f"{c} x{n}" for c, n in sorted(codes.items()))
        decoded = _decode_tags(hidden)
        if decoded:
            evidence += f"; hidden text: {decoded[:80]!r}"
        flags.append(Flag("hidden-unicode", "high", evidence))
    fresh = added_text(baseline, text) if baseline else text
    for kind, sev, rx in _PATTERNS:
        m = rx.search(fresh)
        if m:
            flags.append(Flag(kind, sev, m.group(0)[:100]))
    return flags


def _sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


sentences = _sentences


def added_text(old: str, new: str) -> str:
    """Sentences present in ``new`` but not in ``old``."""
    seen = set(_sentences(old))
    return " ".join(s for s in _sentences(new) if s not in seen)


def collect_texts(schema: Any, path: str = "", out: Any = None, depth: int = 0) -> dict:
    """Every human-readable string a model will read inside a schema."""
    out = {} if out is None else out
    if depth > 20:
        return out
    if isinstance(schema, dict):
        for key, val in schema.items():
            where = path or "(input)"
            if key in ("description", "title") and isinstance(val, str):
                out[f"{where}#{key}"] = val
            elif key == "enum" and isinstance(val, list):
                strs = [v for v in val if isinstance(v, str)]
                if strs:
                    out[f"{where}#enum"] = " | ".join(strs)
            elif key == "default" and isinstance(val, str):
                out[f"{where}#default"] = val
            elif key == "properties" and isinstance(val, dict):
                for name, sub in val.items():
                    collect_texts(sub, f"{path}.{name}" if path else name, out, depth + 1)
            elif key == "items":
                collect_texts(val, path + "[]", out, depth + 1)
            elif key in ("anyOf", "oneOf", "allOf") and isinstance(val, list):
                for sub in val:
                    collect_texts(sub, path, out, depth + 1)
            elif key in ("$defs", "definitions") and isinstance(val, dict):
                for name, sub in val.items():
                    collect_texts(sub, f"$defs.{name}", out, depth + 1)
            elif key == "additionalProperties" and isinstance(val, dict):
                collect_texts(val, path + ".*", out, depth + 1)
    return out

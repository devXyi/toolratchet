"""Semantic-version helpers for the version-bump check."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .model import Level

_RX = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+].*)?$")
_RANK = {"none": 0, "patch": 1, "minor": 2, "major": 3}

ADDITIVE_KINDS = {"tool.added", "input.param-added", "output.schema-added"}


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int


def parse(text: Optional[str]) -> Optional[Version]:
    if not text:
        return None
    m = _RX.match(text.strip())
    if not m:
        return None
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def bump(old: Version, new: Version) -> str:
    """The kind of bump going from ``old`` to ``new`` ('downgrade' if it went backwards)."""
    if (new.major, new.minor, new.patch) < (old.major, old.minor, old.patch):
        return "downgrade"
    if new.major != old.major:
        return "major"
    if new.minor != old.minor:
        return "minor"
    if new.patch != old.patch:
        return "patch"
    return "none"


def required_bump(changes: list) -> str:
    if any(c.level == Level.BREAKING for c in changes):
        return "major"
    if any(c.kind in ADDITIVE_KINDS for c in changes):
        return "minor"
    return "patch" if changes else "none"


def satisfies(declared: str, required: str, old: Version) -> bool:
    """Does the declared bump cover the required one? (0.x: shift one level down.)"""
    if declared == "downgrade":
        return False
    eff = declared
    if old.major == 0:
        eff = {"major": "major", "minor": "major", "patch": "minor"}.get(declared, declared)
    return _RANK[eff] >= _RANK[required]

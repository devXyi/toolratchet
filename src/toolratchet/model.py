"""Core data types shared by every module."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional


class Level(IntEnum):
    """How much a change matters to existing callers."""

    INFO = 0  # compatible (e.g. a tool or optional parameter was added)
    REVIEW = 1  # cannot be proven safe (behavioural / semantic / unknown)
    BREAKING = 2  # proven (or assumed) to break existing callers


@dataclass
class ToolDef:
    """A normalised tool definition, independent of vendor format."""

    name: str
    description: str = ""
    input_schema: Any = field(default_factory=lambda: {"type": "object"})
    output_schema: Any = None
    annotations: dict = field(default_factory=dict)
    title: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.title:
            d["title"] = self.title
        if self.output_schema is not None:
            d["outputSchema"] = self.output_schema
        if self.annotations:
            d["annotations"] = self.annotations
        return d


@dataclass
class Surface:
    """A captured set of tools plus where it came from."""

    tools: list
    server: dict = field(default_factory=dict)
    source: str = ""

    @property
    def version(self) -> Optional[str]:
        v = self.server.get("version")
        return str(v) if v is not None else None


@dataclass
class Change:
    """One difference between two tool surfaces."""

    tool: str
    kind: str
    level: Level
    message: str
    path: str = ""
    security: Optional[str] = None  # None | "low" | "high"
    detail: Optional[str] = None  # unified diff or evidence

    def to_dict(self) -> dict:
        d = {
            "tool": self.tool,
            "kind": self.kind,
            "level": self.level.name.lower(),
            "message": self.message,
        }
        if self.path:
            d["path"] = self.path
        if self.security:
            d["security"] = self.security
        if self.detail:
            d["detail"] = self.detail
        return d

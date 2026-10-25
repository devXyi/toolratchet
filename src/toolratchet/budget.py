"""Token budget accounting for tool surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .model import Surface
from .tokens import estimate_tokens, tool_tokens


@dataclass
class ToolCost:
    name: str
    old: Optional[int]
    new: Optional[int]

    @property
    def delta(self) -> int:
        return (self.new or 0) - (self.old or 0)


@dataclass
class Budget:
    old_total: int
    new_total: int
    tools: list = field(default_factory=list)

    @property
    def delta(self) -> int:
        return self.new_total - self.old_total

    @property
    def pct(self) -> Optional[float]:
        if self.old_total == 0:
            return None
        return 100.0 * self.delta / self.old_total


def compute(old: Surface, new: Surface, count: Callable[[str], int] = estimate_tokens) -> Budget:
    o = {t.name: tool_tokens(t, count) for t in old.tools}
    n = {t.name: tool_tokens(t, count) for t in new.tools}
    names = list(dict.fromkeys([*o.keys(), *n.keys()]))
    tools = [ToolCost(x, o.get(x), n.get(x)) for x in names]
    return Budget(sum(o.values()), sum(n.values()), tools)


def violations(
    b: Budget,
    max_tokens: Optional[int] = None,
    max_increase_pct: Optional[float] = None,
    max_tool_tokens: Optional[int] = None,
) -> list:
    out: list = []
    if max_tokens is not None and b.new_total > max_tokens:
        out.append(f"total tool tokens {b.new_total:,} exceed the limit of {max_tokens:,}")
    if max_increase_pct is not None and b.old_total > 0:
        pct = b.pct or 0.0
        if pct > max_increase_pct + 1e-9:
            out.append(f"token cost grew {pct:+.1f}% (limit {max_increase_pct:+.1f}%)")
    elif max_increase_pct is not None and b.old_total == 0 and b.new_total > 0 and max_increase_pct <= 0:
        out.append(f"token cost grew from 0 to {b.new_total:,}")
    if max_tool_tokens is not None:
        for t in b.tools:
            if t.new is not None and t.new > max_tool_tokens:
                out.append(f"tool '{t.name}' costs {t.new:,} tokens (limit {max_tool_tokens:,})")
    return out

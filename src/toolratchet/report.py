"""Turn a list of changes into a pass/fail result according to a policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import budget as B
from . import semver
from .model import Level, Surface


@dataclass
class Policy:
    fail_on: str = "breaking"  # breaking | review | never
    allow_security: bool = False
    max_tokens: Optional[int] = None
    max_increase_pct: Optional[float] = None
    max_tool_tokens: Optional[int] = None
    check_version: bool = False
    old_version: Optional[str] = None
    new_version: Optional[str] = None


@dataclass
class Result:
    old: Surface
    new: Surface
    changes: list
    budget: B.Budget
    required_bump: str
    declared_bump: Optional[str] = None
    failures: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def counts(self) -> dict:
        return {
            "breaking": sum(1 for c in self.changes if c.level == Level.BREAKING),
            "review": sum(1 for c in self.changes if c.level == Level.REVIEW),
            "info": sum(1 for c in self.changes if c.level == Level.INFO),
            "security": sum(1 for c in self.changes if c.security == "high"),
        }


def evaluate(old: Surface, new: Surface, changes: list, budget: B.Budget, policy: Policy) -> Result:
    failures: list = []
    notes: list = []
    counts = {lvl: sum(1 for c in changes if c.level == lvl) for lvl in Level}

    required = semver.required_bump(changes)
    declared: Optional[str] = None
    acknowledged = False
    if policy.check_version:
        ov = semver.parse(policy.old_version or old.version)
        nv = semver.parse(policy.new_version or new.version)
        if ov is None or nv is None:
            failures.append("version check requested but old/new versions are missing or not semver")
        else:
            declared = semver.bump(ov, nv)
            if semver.satisfies(declared, required, ov):
                acknowledged = required == "major"
            else:
                failures.append(f"version bump '{declared}' is lower than the required '{required}'")

    if policy.fail_on in ("breaking", "review") and counts[Level.BREAKING]:
        if acknowledged:
            notes.append(f"{counts[Level.BREAKING]} breaking change(s) are covered by the declared '{declared}' version bump")
        else:
            failures.append(f"{counts[Level.BREAKING]} breaking change(s)")
    if policy.fail_on == "review" and counts[Level.REVIEW]:
        failures.append(f"{counts[Level.REVIEW]} change(s) need review")
    if not policy.allow_security:
        sec = [c for c in changes if c.security == "high"]
        if sec:
            failures.append(f"{len(sec)} high-severity integrity flag(s)")
    failures.extend(B.violations(budget, policy.max_tokens, policy.max_increase_pct, policy.max_tool_tokens))
    return Result(old, new, changes, budget, required, declared, failures, notes)

"""Customer-configurable policy: floors that the registry defaults cannot erode.

A floor pins a tool, or a whole class of consequences, to a *minimum* tier.
Floors only ever tighten — this is the mechanism by which an enterprise sets
bounds that model improvement does not loosen. The resolver takes the strictest
of (what the consequence heuristic decided) and (what policy floors demand),
so a floor can raise a tier but nothing can lower it below the floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .consequence import Consequence, Tier, rank

DEFAULT_CONFIDENCE_THRESHOLD = 0.85


@dataclass(frozen=True)
class ClassFloor:
    """A floor keyed on a partial match against consequence properties.

    `match` names any subset of {reversibility, blast_radius, absorbed_by,
    detection_latency}; a consequence matches if it agrees on every named key.
    """

    name: str
    match: dict[str, str]
    min_tier: Tier

    def matches(self, c: Consequence) -> bool:
        for key, value in self.match.items():
            if getattr(c, key) != value:
                return False
        return True


@dataclass(frozen=True)
class Policy:
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    tool_floors: dict[str, Tier] = field(default_factory=dict)
    class_floors: tuple[ClassFloor, ...] = ()

    def floor_for(self, tool: str, c: Consequence) -> tuple[Tier | None, str | None]:
        """The strictest floor that applies to this (tool, consequence), and the
        name of the rule that produced it. Returns (None, None) if nothing pins
        this call."""
        best_tier: Tier | None = None
        best_name: str | None = None

        candidates: list[tuple[Tier, str]] = []
        if tool in self.tool_floors:
            candidates.append((self.tool_floors[tool], f"tool:{tool}"))
        for cf in self.class_floors:
            if cf.matches(c):
                candidates.append((cf.min_tier, cf.name))

        for tier, name in candidates:
            if best_tier is None or rank(tier) > rank(best_tier):
                best_tier, best_name = tier, name
        return best_tier, best_name

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Policy":
        floors = data.get("floors", {}) or {}
        tool_floors = {
            name: Tier(value) for name, value in (floors.get("tools", {}) or {}).items()
        }
        class_floors = tuple(
            ClassFloor(
                name=entry["name"],
                match=dict(entry["match"]),
                min_tier=Tier(entry["min_tier"]),
            )
            for entry in (floors.get("classes", []) or [])
        )
        return Policy(
            confidence_threshold=float(
                data.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
            ),
            tool_floors=tool_floors,
            class_floors=class_floors,
        )

    @staticmethod
    def load(path: str | Path) -> "Policy":
        with open(path) as fh:
            return Policy.from_dict(yaml.safe_load(fh) or {})


def as_policy(policy: "Policy | str | Path | None") -> Policy:
    """Coerce whatever the caller passed for `policy=` into a Policy."""
    if policy is None:
        return Policy()
    if isinstance(policy, Policy):
        return policy
    return Policy.load(policy)

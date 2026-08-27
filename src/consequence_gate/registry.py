"""Tool registration and the `@consequence` decorator.

The registry is the gate's inventory: for every tool the agent might call, it
holds the callable and the consequence metadata that describes what calling it
risks. The metadata is *declared* here (see the `source` field on Consequence);
deriving it from observed behavior instead is a deliberate non-goal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .consequence import (
    AbsorbedBy,
    BlastRadius,
    Consequence,
    DetectionLatency,
    Reversibility,
    Source,
)

CONSEQUENCE_ATTR = "__consequence__"


def consequence(
    *,
    reversibility: Reversibility,
    blast_radius: BlastRadius,
    absorbed_by: AbsorbedBy,
    detection_latency: DetectionLatency,
    notes: str = "",
    source: Source = "declared",
) -> Callable:
    """Decorator that annotates a tool function with its consequence metadata.

    The function is left otherwise untouched — the gate reads the annotation off
    it at registration time. This is half of the two-line public surface:

        @consequence(reversibility="irreversible", blast_radius="external",
                     absorbed_by="customer", detection_latency="immediate")
        def email_customer(to: str, body: str) -> None: ...
    """

    meta = Consequence(
        reversibility=reversibility,
        blast_radius=blast_radius,
        absorbed_by=absorbed_by,
        detection_latency=detection_latency,
        notes=notes,
        source=source,
    )

    def decorate(fn: Callable) -> Callable:
        setattr(fn, CONSEQUENCE_ATTR, meta)
        return fn

    return decorate


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable
    consequence: Consequence
    cost: float = 0.0  # nominal cost per call, used by metrics


class Registry:
    """Name -> (callable, consequence) for every tool the gate governs."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        fn: Callable,
        *,
        consequence: Consequence | None = None,
        name: str | None = None,
        cost: float = 0.0,
    ) -> ToolSpec:
        meta = consequence or getattr(fn, CONSEQUENCE_ATTR, None)
        if meta is None:
            raise ValueError(
                f"{getattr(fn, '__name__', fn)!r} has no consequence metadata: "
                f"decorate it with @consequence or pass one to register()."
            )
        spec = ToolSpec(name=name or fn.__name__, fn=fn, consequence=meta, cost=cost)
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"tool {name!r} is not registered")
        return self._tools[name]

    def consequence_of(self, name: str) -> Consequence:
        return self.get(name).consequence

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

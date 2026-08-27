"""Core value types: the consequence metadata attached to a tool, and the
four autonomy tiers a call can resolve to.

Nothing in this module makes a decision. It only defines *what can be said*
about a tool's consequences and *what outcomes are possible*. The decision
logic lives in `resolver.py` and is a pure function over these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Reversibility = Literal["reversible", "costly", "irreversible"]
BlastRadius = Literal["task", "record", "system", "external"]
AbsorbedBy = Literal["agent", "operator", "customer", "regulator"]
DetectionLatency = Literal["immediate", "hours", "days", "unbounded"]
Source = Literal["declared", "inferred", "policy"]


@dataclass(frozen=True)
class Consequence:
    """What being wrong about this tool costs, who absorbs it, and how long
    before anyone would notice.

    The four properties are deliberately orthogonal. Reversibility is about the
    action; blast_radius is about scope; absorbed_by is about *who is not in the
    room* when it goes wrong; detection_latency is about how long the damage
    compounds unseen.
    """

    reversibility: Reversibility
    blast_radius: BlastRadius
    absorbed_by: AbsorbedBy
    detection_latency: DetectionLatency
    notes: str = ""  # why it was classified this way
    source: Source = "declared"


class Tier(StrEnum):
    """The four possible verdicts, ordered here from most to least autonomy."""

    EXECUTE = "execute"  # proceed, log only
    EXECUTE_NOTIFY = "execute_notify"  # proceed, surface for review
    PROPOSE = "propose"  # prepare, require a human to commit
    REFUSE = "refuse"  # out of scope regardless of confidence


# A total order over tiers. Higher rank == stricter == less autonomy.
# Every "one tier stricter" / "the strictest of these wins" operation in the
# resolver and the policy layer routes through this single ordering.
_RANK: dict[Tier, int] = {
    Tier.EXECUTE: 0,
    Tier.EXECUTE_NOTIFY: 1,
    Tier.PROPOSE: 2,
    Tier.REFUSE: 3,
}
_BY_RANK: dict[int, Tier] = {rank: tier for tier, rank in _RANK.items()}

# The auto-modifiers in the resolver (confidence, latency, blame) may raise a
# tier this far and no further. Crossing into REFUSE is a policy statement about
# scope, not something the severity heuristic gets to decide on its own.
AUTO_CEILING = Tier.PROPOSE


def rank(tier: Tier) -> int:
    return _RANK[tier]


def stricter(tier: Tier, steps: int = 1) -> Tier:
    """Move `steps` tiers toward less autonomy, clamped at REFUSE."""
    return _BY_RANK[min(_RANK[tier] + steps, _RANK[Tier.REFUSE])]


def strictest(*tiers: Tier) -> Tier:
    """The least-autonomy tier among the arguments."""
    return _BY_RANK[max(_RANK[t] for t in tiers)]


def bump_capped(tier: Tier, ceiling: Tier = AUTO_CEILING) -> Tier:
    """One tier stricter, but never past `ceiling`. Used by auto-modifiers so
    that confidence and consequence can escalate an action all the way to
    'a human must commit' but never to outright 'refuse'.

    A tier already at or beyond the ceiling is returned unchanged rather than
    pulled back — auto-modifiers only tighten, never loosen.
    """
    if _RANK[tier] >= _RANK[ceiling]:
        return tier
    return _BY_RANK[min(_RANK[stricter(tier)], _RANK[ceiling])]

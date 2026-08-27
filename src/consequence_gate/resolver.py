"""The tier resolver: a pure function from (metadata, confidence, policy,
context) to (tier, human-readable reason).

Pure means: no I/O, no clock, no agent. Given the same inputs it always
produces the same tier and the same reason string. That is what makes every
gate decision testable, replayable, and explainable without re-running the
agent that proposed the call.

Resolution proceeds in a fixed, documented order:

  1. Base severity        -- look the consequence class up in one table.
  2. Auto-modifiers        -- detection latency, who absorbs the cost, and
                              confidence each tighten by one tier, capped at
                              PROPOSE. They never loosen and never refuse.
  3. Taint                 -- if this call shares context with one a human just
                              rejected, floor it at PROPOSE (held for review).
  4. Policy floor          -- the strictest floor for this tool/class wins, and
                              is the *only* thing that can produce REFUSE.

The design claim, made mechanical: confidence appears only in step 2, and only
ever adds a tightening bump. There is no branch anywhere that loosens a tier as
confidence rises. Confidence can make the gate stricter, never looser.
"""

from __future__ import annotations

from dataclasses import dataclass

from .consequence import (
    Consequence,
    Reversibility,
    Tier,
    bump_capped,
    rank,
    strictest,
)
from .policy import Policy

# --- Step 1: the severity table -------------------------------------------
#
# The whole base policy, in one place, so a reader can see it at a glance
# instead of reconstructing it from scattered conditionals.
#
# Rows collapse blast_radius into three severity bands: task and record are
# both "local" (contained to the unit of work), then "system", then "external".
#
#            | reversible      | costly          | irreversible
#   local    | EXECUTE         | EXECUTE_NOTIFY  | PROPOSE
#   system   | EXECUTE_NOTIFY  | PROPOSE         | PROPOSE
#   external | PROPOSE         | PROPOSE         | PROPOSE
#
# Deviation from a naive table: the external/irreversible cell is PROPOSE, not
# REFUSE. REFUSE means "out of scope regardless of confidence" -- a statement
# about what the agent is *permitted* to do at all, which is a policy judgment,
# not something derivable from consequence properties. So the base table tops
# out at PROPOSE (a human commits) and REFUSE is expressed exclusively through
# policy floors. This also gives the policy layer a real job instead of a
# table that has already decided everything.

_ROW: dict[str, str] = {
    "task": "local",
    "record": "local",
    "system": "system",
    "external": "external",
}

SEVERITY: dict[tuple[str, Reversibility], Tier] = {
    ("local", "reversible"): Tier.EXECUTE,
    ("local", "costly"): Tier.EXECUTE_NOTIFY,
    ("local", "irreversible"): Tier.PROPOSE,
    ("system", "reversible"): Tier.EXECUTE_NOTIFY,
    ("system", "costly"): Tier.PROPOSE,
    ("system", "irreversible"): Tier.PROPOSE,
    ("external", "reversible"): Tier.PROPOSE,
    ("external", "costly"): Tier.PROPOSE,
    ("external", "irreversible"): Tier.PROPOSE,
}


def base_severity(c: Consequence) -> Tier:
    return SEVERITY[(_ROW[c.blast_radius], c.reversibility)]


@dataclass(frozen=True)
class Resolution:
    tier: Tier
    reason: str
    policy_applied: str | None


# --- phrasing helpers for the reason string -------------------------------

_REV_PHRASE = {
    "reversible": "Reversible",
    "costly": "Costly to reverse",
    "irreversible": "Irreversible",
}
_BLAST_PHRASE = {
    "task": "task-level",
    "record": "record-level",
    "system": "system-level",
    "external": "external",
}
_ABSORB_PHRASE = {
    "agent": "the agent",
    "operator": "an operator",
    "customer": "the customer",
    "regulator": "a regulator",
}
_LATENCY_PHRASE = {
    "immediate": "immediate detection",
    "hours": "detection within hours",
    "days": "detection within days",
    "unbounded": "unbounded detection latency",
}
_OUTCOME_PHRASE = {
    Tier.EXECUTE: "Proceeds automatically; logged only.",
    Tier.EXECUTE_NOTIFY: "Proceeds automatically and is surfaced for review.",
    Tier.PROPOSE: "Prepared but held; a human must commit before it executes.",
    Tier.REFUSE: "Out of scope; will not execute regardless of confidence.",
}


def resolve(
    tool: str,
    consequence: Consequence,
    confidence: float,
    policy: Policy,
    context: dict | None = None,
) -> Resolution:
    ctx = context or {}
    threshold = policy.confidence_threshold
    c = consequence

    # 1. base severity
    base = base_severity(c)
    tier = base

    # 2. auto-modifiers (each tightens one tier, capped at PROPOSE).
    # `capped` records whether any requested bump was absorbed by the ceiling,
    # so the reason string can say so honestly.
    bumps: list[str] = []
    capped = False

    def apply_bump(label: str) -> None:
        nonlocal tier, capped
        after = bump_capped(tier)
        if after == tier:  # a bump was requested but the ceiling absorbed it
            capped = True
        tier = after
        bumps.append(label)

    if c.detection_latency == "unbounded":
        apply_bump("unbounded detection latency")
    if c.absorbed_by in ("customer", "regulator"):
        apply_bump(f"{_ABSORB_PHRASE[c.absorbed_by]} absorbs the cost")
    low_confidence = confidence < threshold
    if low_confidence:
        apply_bump(f"confidence {confidence:.2f} is below the {threshold:.2f} threshold")

    # 3. taint from a sibling rejection (see override.py)
    taint = ctx.get("tainted_by")
    if taint:
        tier = strictest(tier, Tier.PROPOSE)

    # 4. policy floor -- the only path to REFUSE
    floor, floor_name = policy.floor_for(tool, c)
    policy_applied: str | None = None
    if floor is not None and rank(floor) > rank(tier):
        tier = floor
        policy_applied = floor_name

    reason = _build_reason(
        c=c,
        base=base,
        bumps=bumps,
        capped=capped,
        low_confidence=low_confidence,
        confidence=confidence,
        threshold=threshold,
        taint=taint,
        floor=floor,
        floor_name=floor_name if policy_applied else None,
        final=tier,
    )
    return Resolution(tier=tier, reason=reason, policy_applied=policy_applied)


def _build_reason(
    *,
    c: Consequence,
    base: Tier,
    bumps: list[str],
    capped: bool,
    low_confidence: bool,
    confidence: float,
    threshold: float,
    taint: dict | None,
    floor: Tier | None,
    floor_name: str | None,
    final: Tier,
) -> str:
    parts: list[str] = []

    parts.append(
        f"{_REV_PHRASE[c.reversibility]}, {_BLAST_PHRASE[c.blast_radius]} blast "
        f"radius, absorbed by {_ABSORB_PHRASE[c.absorbed_by]}, "
        f"{_LATENCY_PHRASE[c.detection_latency]}."
    )
    parts.append(f"Base severity for this class is '{base.value}'.")

    if bumps:
        joined = _join(bumps)
        cap_note = (
            " (capped at 'propose' — the resolver never refuses on its own)"
            if capped
            else ""
        )
        parts.append(f"Raised one tier for {joined}{cap_note}.")

    if not low_confidence:
        parts.append(
            f"Confidence {confidence:.2f} is at or above the {threshold:.2f} "
            f"threshold, so it adds no caution."
        )

    if taint:
        shared = taint.get("shared", "context")
        rej = taint.get("call_id", "an earlier call")
        code = taint.get("reason_code", "rejected")
        parts.append(
            f"Shares {shared} with call {rej}, which a human rejected as "
            f"'{code}'; the context it was built on is now suspect, so it is "
            f"held for review."
        )

    if floor_name is not None and floor is not None:
        parts.append(
            f"Policy floor '{floor_name}' pins this to '{floor.value}'; "
            f"confidence cannot buy past a policy floor."
        )

    parts.append(_OUTCOME_PHRASE[final])
    return " ".join(parts)


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"

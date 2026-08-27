"""The override loop.

Most systems, when a human rejects an agent's action, record the correction and
move on. That stores one right answer next to several wrong ones: the calls that
shared the *same misread context* as the rejected one are still sitting in the
trace, approved.

This handler treats a rejection as evidence about context, not just about the
one action. When a human rejects a queued call, it pulls the other calls in the
same trace, finds the ones that were built on the same context (the same target
entity, source record, or retrieved context), and re-runs the resolver against
them with that context now marked suspect. Anything whose tier tightens is
pulled back and re-queued, and the re-examination is logged as its own decision
so the audit trail shows *why* an already-approved call got clawed back.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .audit import AuditEntry, AuditLog, Decision
from .consequence import rank
from .policy import Policy
from .registry import Registry
from .resolver import resolve

# A rejection without a reason is a wasted signal.
REASON_CODES = {
    "wrong_target",
    "wrong_timing",
    "insufficient_context",
    "policy_violation",
    "other",
}

# The context keys a call can share with the rejected one, most-to-least
# specific, with the phrase used to describe the link in the audit trail.
_LINK_KEYS: list[tuple[str, str]] = [
    ("target_entity", "target entity"),
    ("source_record", "source record"),
    ("retrieved_context", "retrieved context"),
]


class OverrideHandler:
    def __init__(
        self,
        *,
        audit: AuditLog,
        policy: Policy,
        registry: Registry,
        clock,
        requeue: Callable[[Decision], None],
        confidence_penalty: float = 0.35,
    ) -> None:
        self.audit = audit
        self.policy = policy
        self.registry = registry
        self.clock = clock
        self.requeue = requeue
        self.penalty = confidence_penalty

    def reexamine(
        self, rejected: AuditEntry, *, reason_code: str, note: str = ""
    ) -> list[Decision]:
        trace = self.audit.by_trace(rejected.decision.trace_id)
        rej_ctx = rejected.context
        # `rejected` is the override record, appended after the whole trace. The
        # pivot for "downstream" is where the original queued call sat, so find
        # its seq -- the earliest record with this call_id.
        rej_seq = min(
            (e.seq for e in trace if e.decision.call_id == rejected.decision.call_id),
            default=rejected.seq,
        )

        source = self._find_source(trace, rejected)
        if source is not None:
            # Not a re-queue, just a note in the trail: this is where the bad
            # context entered the trace.
            self._record_source_note(rejected, source, reason_code)

        # Re-examine downstream calls: those proposed after the rejected one that
        # were built on the same context.
        results: list[Decision] = []
        for entry in trace:
            if entry.seq <= rej_seq:
                continue
            if entry.decision.outcome in (None, "overridden"):
                continue
            link = _shared_link(rej_ctx, entry.context)
            if link is None:
                continue
            revised = self._retier(entry, rejected, reason_code, link)
            if revised is not None:
                results.append(revised)
        return results

    def _retier(
        self,
        entry: AuditEntry,
        rejected: AuditEntry,
        reason_code: str,
        link: str,
    ) -> Decision | None:
        d = entry.decision
        new_conf = max(0.0, d.confidence - self.penalty)
        tainted_ctx = {
            **entry.context,
            "tainted_by": {
                "call_id": rejected.decision.call_id,
                "reason_code": reason_code,
                "shared": link,
            },
        }
        res = resolve(d.tool, d.consequence, new_conf, self.policy, tainted_ctx)
        if rank(res.tier) <= rank(d.tier):
            return None  # not tightened; nothing to pull back

        pulled_back = d.outcome == "executed"
        prefix = (
            "Pulled back after re-examination — this call had already executed. "
            if pulled_back
            else "Re-examined after a sibling rejection. "
        )
        revised = replace(
            d,
            call_id=f"{d.call_id}:re",
            confidence=new_conf,
            tier=res.tier,
            reason=prefix + res.reason,
            policy_applied=res.policy_applied,
            timestamp=self.clock.now(),
            outcome="queued" if res.tier.value == "propose" else "refused",
        )
        self.audit.record(revised, context=tainted_ctx, cost=entry.cost)
        self.requeue(revised)
        return revised

    def _find_source(self, trace: list[AuditEntry], rejected: AuditEntry):
        """The earliest upstream call that supplied one of the rejected call's
        linkage keys -- e.g. the ticket read that produced the wrong recipient."""
        for entry in trace:
            if entry.seq >= rejected.seq:
                break
            if _shared_link(rejected.context, entry.context) is not None:
                return entry
        return None

    def _record_source_note(
        self, rejected: AuditEntry, source: AuditEntry, reason_code: str
    ) -> None:
        d = source.decision
        link = _shared_link(rejected.context, source.context)
        note = replace(
            d,
            call_id=f"{d.call_id}:source",
            reason=(
                f"Identified as the origin of the rejected context ({link}): call "
                f"{d.call_id} ({d.tool}) supplied it. Flagged so downstream calls "
                f"built on it can be re-examined."
            ),
            timestamp=self.clock.now(),
            outcome=d.outcome,
        )
        self.audit.record(note, context=source.context, cost=0.0)


def _shared_link(a: dict, b: dict) -> str | None:
    """A human-readable description of the strongest linkage key two calls share,
    or None if they share none."""
    for key, phrase in _LINK_KEYS:
        va, vb = a.get(key), b.get(key)
        if va is not None and va == vb:
            return f"{phrase} '{va}'"
    return None

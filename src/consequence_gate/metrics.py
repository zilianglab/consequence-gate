"""Metrics, computed from the audit log -- never hardcoded.

Two of these are the point. Escalation precision asks whether the gate is
crying wolf: of the calls it stopped, how many a human actually rejected. A
low number means operators learn to rubber-stamp the queue and the gate becomes
shelfware. Cost-weighted error rate weights mistakes by what they would have
cost rather than counting them, so one wrong deprovision does not disappear into
a hundred correct ticket reads.

They move in opposite directions. Tighten the gate and cost-weighted error
falls while escalation precision falls too (you stop more things that were fine).
Loosen it and both reverse. They have to be read together; neither alone tells
you whether the gate is set right.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import quantiles

from .audit import AuditEntry, AuditLog
from .consequence import Consequence, Tier, rank

# What a wrong action in each consequence class would cost, relative to each
# other. Used to weight the error rate. Derived from the tier the resolver would
# assign at full confidence -- the more autonomy the gate withholds, the more a
# mistake there matters.
_SEVERITY_WEIGHT = {
    Tier.EXECUTE: 1.0,
    Tier.EXECUTE_NOTIFY: 2.0,
    Tier.PROPOSE: 5.0,
    Tier.REFUSE: 13.0,
}


def _severity_weight(c: Consequence, tier: Tier) -> float:
    return _SEVERITY_WEIGHT[tier]


@dataclass
class Metrics:
    total_calls: int
    task_completion_rate: float
    success_rate: float
    gate_overhead_p50_ms: float
    gate_overhead_p95_ms: float
    tool_latency_p50_ms: float
    tool_latency_p95_ms: float
    cost_per_action: float
    escalation_precision: float | None
    cost_weighted_error_rate: float
    override_rate_by_class: dict[str, float]

    def render(self) -> str:
        lines = [
            "metrics (computed from the audit log)",
            "-" * 52,
            f"  task completion rate        {_pct(self.task_completion_rate)}",
            f"  success rate                {_pct(self.success_rate)}",
            f"  gate overhead   p50/p95     {self.gate_overhead_p50_ms:.3f} / {self.gate_overhead_p95_ms:.3f} ms",
            f"  tool latency    p50/p95     {self.tool_latency_p50_ms:.3f} / {self.tool_latency_p95_ms:.3f} ms",
            f"  cost per action             {self.cost_per_action:.3f} nominal units",
            f"  escalation precision        {_pct(self.escalation_precision)}",
            f"  cost-weighted error rate    {_pct(self.cost_weighted_error_rate)}",
            "  override rate by class",
        ]
        if self.override_rate_by_class:
            for cls, rate in sorted(self.override_rate_by_class.items()):
                lines.append(f"      {cls:<28}{_pct(rate)}")
        else:
            lines.append("      (no calls were queued)")
        return "\n".join(lines)


def compute(log: AuditLog | list[AuditEntry]) -> Metrics:
    entries = list(log if isinstance(log, list) else log.all())

    # An "original" call is the resolver's first verdict on a proposed call.
    # Follow-up records (human commits ":", re-examinations ":re", source notes
    # ":source") are excluded from the denominators so they aren't double counted.
    # The overridden record shares its call_id with the queued original it
    # supersedes; it is a human follow-up, not a fresh proposed call.
    originals = [
        e for e in entries if _is_original(e) and e.decision.outcome != "overridden"
    ]
    total = len(originals)

    executed = [e for e in originals if e.decision.outcome == "executed"]
    queued = [e for e in originals if e.decision.outcome == "queued"]
    refused = [e for e in originals if e.decision.outcome == "refused"]

    # Follow-up human/override actions, keyed to the original call.
    committed_ids = {_root(e) for e in entries if e.decision.call_id.endswith(":approved")}
    overridden_ids = {
        e.decision.call_id
        for e in entries
        if e.decision.outcome == "overridden"
    }

    # A call "completed" if it executed outright or a human committed it.
    completed = len(executed) + sum(
        1 for e in queued if e.decision.call_id in committed_ids
    )
    task_completion_rate = _safe(completed, total)

    successes = [e for e in executed if e.success is True]
    success_rate = _safe(len(successes), len(executed))

    overheads = [e.gate_overhead_ms for e in originals]
    tool_ms = [e.tool_ms for e in executed if e.tool_ms is not None]

    costs = [e.cost for e in executed]
    cost_per_action = _safe(sum(costs), len(executed), default=0.0)

    # Escalation precision: of the calls the gate stopped (queued or refused),
    # what fraction did a human actually reject?
    stopped = queued + refused
    rejected = sum(1 for e in stopped if e.decision.call_id in overridden_ids)
    escalation_precision = _safe(rejected, len(stopped), default=None)

    # Cost-weighted error rate: errors weighted by consequence severity over the
    # weight of all actions. An error = a call a human overrode, i.e. one the
    # gate held and a human then rejected (the gate caught a real mistake).
    err_weight = 0.0
    all_weight = 0.0
    for e in originals:
        w = _severity_weight(e.decision.consequence, e.decision.tier)
        all_weight += w
        if e.decision.call_id in overridden_ids:
            err_weight += w
    cost_weighted_error_rate = _safe(err_weight, all_weight, default=0.0)

    # Override rate by consequence class: of the queued calls in each class, how
    # many were rejected. If a class is approved 100% of the time, its floor is
    # too strict.
    by_class: dict[str, list[bool]] = {}
    for e in queued:
        key = _class_key(e.decision.consequence)
        by_class.setdefault(key, []).append(
            e.decision.call_id in overridden_ids
        )
    override_rate_by_class = {
        cls: _safe(sum(v), len(v)) for cls, v in by_class.items()
    }

    return Metrics(
        total_calls=total,
        task_completion_rate=task_completion_rate,
        success_rate=success_rate,
        gate_overhead_p50_ms=_p(overheads, 50),
        gate_overhead_p95_ms=_p(overheads, 95),
        tool_latency_p50_ms=_p(tool_ms, 50),
        tool_latency_p95_ms=_p(tool_ms, 95),
        cost_per_action=cost_per_action,
        escalation_precision=escalation_precision,
        cost_weighted_error_rate=cost_weighted_error_rate,
        override_rate_by_class=override_rate_by_class,
    )


def _is_original(e: AuditEntry) -> bool:
    cid = e.decision.call_id
    return not (cid.endswith(":re") or cid.endswith(":source") or cid.endswith(":approved"))


def _root(e: AuditEntry) -> str:
    return e.decision.call_id.split(":approved")[0].split(":re")[0].split(":source")[0]


def _class_key(c: Consequence) -> str:
    return f"{c.reversibility}/{c.blast_radius}"


def _safe(num, den, default=0.0):
    return default if not den else num / den


def _pct(x: float | None) -> str:
    if x is None:
        return "  n/a (nothing stopped)"
    return f"{x * 100:5.1f}%"


def _p(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    cut = quantiles(sorted(values), n=100, method="inclusive")
    return cut[min(pct, 99) - 1]

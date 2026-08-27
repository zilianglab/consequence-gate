"""The public wrapper. A Gate sits between an agent loop and its tools.

The agent proposes a call (tool name, arguments, its own confidence). The gate
resolves it to a tier and then acts: execute and log, execute and notify, hold
for a human to commit, or refuse. Nothing about how the agent *chose* the call
is the gate's concern -- only what happens once it has chosen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .audit import AuditLog, Decision, StepClock
from .consequence import Tier
from .override import OverrideHandler, REASON_CODES
from .policy import Policy, as_policy
from .registry import Registry
from .resolver import resolve


@dataclass(frozen=True)
class ProposedCall:
    """What the agent hands the gate. `context` carries linkage keys -- the
    target entity, the source record, the retrieved context this call was built
    from -- so the override loop can find calls that shared a rejected call's
    context. Populating it is optional but it is what makes re-examination work."""

    tool: str
    arguments: dict
    confidence: float
    trace_id: str
    call_id: str
    context: dict = field(default_factory=dict)


class ApprovalQueue:
    """Holds calls resolved to PROPOSE until a human commits or rejects them.

    A CLI prompt is one decider; a scripted dict of decisions is another (used
    by the demos so they run end-to-end and print a stable transcript). No web
    UI -- that is deliberately out of scope."""

    def __init__(self) -> None:
        self._pending: dict[str, Decision] = {}

    def enqueue(self, decision: Decision) -> None:
        self._pending[decision.call_id] = decision

    def pending(self) -> list[Decision]:
        return list(self._pending.values())

    def pop(self, call_id: str) -> Decision:
        return self._pending.pop(call_id)

    def __contains__(self, call_id: str) -> bool:
        return call_id in self._pending


class Gate:
    def __init__(
        self,
        *,
        registry: Registry | None = None,
        policy: Policy | str | None = None,
        audit: AuditLog | None = None,
        clock=None,
        confidence_penalty: float = 0.35,
    ) -> None:
        self.registry = registry if registry is not None else Registry()
        self.policy = as_policy(policy)
        self.audit = audit if audit is not None else AuditLog()
        self.clock = clock if clock is not None else StepClock()
        self.queue = ApprovalQueue()
        self.override = OverrideHandler(
            audit=self.audit,
            policy=self.policy,
            registry=self.registry,
            clock=self.clock,
            requeue=self._requeue,
            confidence_penalty=confidence_penalty,
        )
        self._counters: dict[str, int] = {}

    # --- registration ------------------------------------------------------

    def register(self, fn: Callable, *, cost: float = 0.0, **kw):
        return self.registry.register(fn, cost=cost, **kw)

    # --- the main entry point ---------------------------------------------

    def submit(
        self,
        trace_id: str,
        tool: str,
        arguments: dict,
        confidence: float,
        *,
        context: dict | None = None,
        call_id: str | None = None,
    ) -> Decision:
        spec = self.registry.get(tool)
        call_id = call_id or self._next_call_id(trace_id)
        context = context or {}

        t0 = time.perf_counter()
        res = resolve(tool, spec.consequence, confidence, self.policy, context)
        gate_overhead_ms = (time.perf_counter() - t0) * 1000.0

        tool_ms: float | None = None
        success: bool | None = None
        outcome: str

        if res.tier in (Tier.EXECUTE, Tier.EXECUTE_NOTIFY):
            t1 = time.perf_counter()
            try:
                spec.fn(**arguments)
                success = True
            except Exception:
                success = False
            tool_ms = (time.perf_counter() - t1) * 1000.0
            outcome = "executed"
        elif res.tier is Tier.PROPOSE:
            outcome = "queued"
        else:  # REFUSE
            outcome = "refused"

        decision = Decision(
            trace_id=trace_id,
            call_id=call_id,
            tool=tool,
            arguments=arguments,
            confidence=confidence,
            consequence=spec.consequence,
            tier=res.tier,
            reason=res.reason,
            policy_applied=res.policy_applied,
            timestamp=self.clock.now(),
            outcome=outcome,
        )
        self.audit.record(
            decision,
            context=context,
            cost=spec.cost,
            gate_overhead_ms=gate_overhead_ms,
            tool_ms=tool_ms,
            success=success,
        )
        if outcome == "queued":
            self.queue.enqueue(decision)
        return decision

    def run(self, calls) -> list[Decision]:
        """Drive an iterable of ProposedCall through the gate in order."""
        out = []
        for call in calls:
            out.append(
                self.submit(
                    call.trace_id,
                    call.tool,
                    call.arguments,
                    call.confidence,
                    context=call.context,
                    call_id=call.call_id,
                )
            )
        return out

    # --- human decisions on the queue -------------------------------------

    def pending(self) -> list[Decision]:
        return self.queue.pending()

    def approve(self, call_id: str) -> Decision:
        """A human commits a queued call: execute it and log the commit."""
        queued = self.queue.pop(call_id)
        spec = self.registry.get(queued.tool)
        t1 = time.perf_counter()
        success = True
        try:
            spec.fn(**queued.arguments)
        except Exception:
            success = False
        tool_ms = (time.perf_counter() - t1) * 1000.0
        committed = _replace(
            queued,
            call_id=f"{queued.call_id}:approved",
            reason="Human committed the queued call. " + queued.reason,
            timestamp=self.clock.now(),
            outcome="executed",
        )
        self.audit.record(committed, cost=spec.cost, tool_ms=tool_ms, success=success)
        return committed

    def reject(self, call_id: str, reason_code: str, note: str = "") -> list[Decision]:
        """A human rejects a queued call. Records the rejection, then re-examines
        every downstream call that shared the rejected call's context."""
        if reason_code not in REASON_CODES:
            raise ValueError(
                f"reason_code must be one of {sorted(REASON_CODES)}, got {reason_code!r}"
            )
        queued = self.queue.pop(call_id)
        rejected = _replace(
            queued,
            reason=f"Human rejected as '{reason_code}'"
            + (f": {note}" if note else "")
            + ". " + queued.reason,
            timestamp=self.clock.now(),
            outcome="overridden",
        )
        entry = self.audit.record(rejected, context=_ctx_of(self.audit, call_id))
        return self.override.reexamine(entry, reason_code=reason_code, note=note)

    # --- internals ---------------------------------------------------------

    def _requeue(self, decision: Decision) -> None:
        if decision.outcome == "queued":
            self.queue.enqueue(decision)

    def _next_call_id(self, trace_id: str) -> str:
        n = self._counters.get(trace_id, 0)
        self._counters[trace_id] = n + 1
        return f"{trace_id}:c{n}"


def _replace(decision: Decision, **changes) -> Decision:
    from dataclasses import replace

    return replace(decision, **changes)


def _ctx_of(audit: AuditLog, call_id: str) -> dict:
    for entry in reversed(audit.all()):
        if entry.decision.call_id == call_id:
            return entry.context
    return {}


def gate(
    agent=None,
    *,
    policy: Policy | str | None = None,
    registry: Registry | None = None,
    audit: AuditLog | None = None,
    clock=None,
    confidence_penalty: float = 0.35,
) -> Gate:
    """Wrap an existing agent loop in a gate.

        g = gate(policy="policy.yaml")
        g.register(email_customer)
        g.submit("trace-1", "email_customer", {...}, confidence=0.91)

    If `agent` is an iterable of ProposedCall it is driven immediately; the Gate
    is still returned so you can inspect the audit log and the queue.
    """
    g = Gate(
        registry=registry,
        policy=policy,
        audit=audit,
        clock=clock,
        confidence_penalty=confidence_penalty,
    )
    if agent is not None:
        g.run(agent)
    return g

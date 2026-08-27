"""Consequence Gate -- a governance layer between an agent loop and its tools.

Public surface:

    from consequence_gate import gate, consequence

    @consequence(reversibility="irreversible", blast_radius="external",
                 absorbed_by="customer", detection_latency="immediate")
    def email_customer(to: str, body: str) -> None:
        ...

    g = gate(policy="policy.yaml")
    g.register(email_customer)
    g.submit("trace-1", "email_customer", {"to": "x@y", "body": "hi"}, confidence=0.91)
"""

from .audit import AuditEntry, AuditLog, Decision, StepClock, SystemClock
from .consequence import Consequence, Tier
from .gate import Gate, ProposedCall, gate
from .metrics import Metrics, compute
from .override import REASON_CODES, OverrideHandler
from .policy import ClassFloor, Policy
from .registry import Registry, ToolSpec, consequence
from .resolver import Resolution, base_severity, resolve

__all__ = [
    "gate",
    "Gate",
    "consequence",
    "Consequence",
    "Tier",
    "ProposedCall",
    "Decision",
    "AuditEntry",
    "AuditLog",
    "StepClock",
    "SystemClock",
    "Policy",
    "ClassFloor",
    "Registry",
    "ToolSpec",
    "resolve",
    "Resolution",
    "base_severity",
    "OverrideHandler",
    "REASON_CODES",
    "Metrics",
    "compute",
]

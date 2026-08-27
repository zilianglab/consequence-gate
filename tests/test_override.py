"""Tests for the override loop and the gate wiring around it."""

import pytest

from consequence_gate import (
    ClassFloor,
    Consequence,
    Policy,
    Tier,
    consequence,
    gate,
)

CALLS: list[str] = []


@consequence(reversibility="reversible", blast_radius="task",
             absorbed_by="agent", detection_latency="immediate")
def read_ticket(ticket: str) -> dict:
    CALLS.append("read_ticket")
    return {"ticket": ticket}


@consequence(reversibility="costly", blast_radius="record",
             absorbed_by="operator", detection_latency="hours")
def update_ticket_status(ticket: str, status: str) -> dict:
    CALLS.append("update_ticket_status")
    return {}


@consequence(reversibility="reversible", blast_radius="task",
             absorbed_by="operator", detection_latency="immediate")
def draft_response(ticket: str, to: str, body: str) -> str:
    CALLS.append("draft_response")
    return "draft"


@consequence(reversibility="irreversible", blast_radius="external",
             absorbed_by="customer", detection_latency="immediate")
def email_customer(to: str, body: str) -> dict:
    CALLS.append("email_customer")
    return {}


@consequence(reversibility="irreversible", blast_radius="system",
             absorbed_by="customer", detection_latency="days")
def deprovision_account(account: str) -> dict:
    CALLS.append("deprovision_account")
    return {}


def build():
    CALLS.clear()
    policy = Policy(
        class_floors=(
            ClassFloor(
                "irreversible-system-out-of-scope",
                {"reversibility": "irreversible", "blast_radius": "system"},
                Tier.REFUSE,
            ),
        )
    )
    g = gate(policy=policy)
    for fn in (read_ticket, update_ticket_status, draft_response,
               email_customer, deprovision_account):
        g.register(fn)
    return g


BAD = "wrong@partner.example"


def seed_trace(g):
    t = "trace-1"
    g.submit(t, "read_ticket", {"ticket": "INC-1"}, 0.96, context={"source_record": "INC-1"})
    email = g.submit(t, "email_customer", {"to": BAD, "body": "hi"}, 0.88,
                     context={"source_record": "INC-1", "target_entity": BAD})
    g.submit(t, "update_ticket_status", {"ticket": "INC-1", "status": "resolved"}, 0.90,
             context={"source_record": "INC-1", "target_entity": "INC-1"})
    g.submit(t, "draft_response", {"ticket": "INC-1", "to": BAD, "body": "x"}, 0.90,
             context={"source_record": "INC-1", "target_entity": BAD})
    return email


# --- gate behavior ---------------------------------------------------------


def test_execute_tier_actually_runs_the_tool():
    g = build()
    g.submit("t", "read_ticket", {"ticket": "INC-1"}, 0.96)
    assert CALLS == ["read_ticket"]


def test_refused_tool_does_not_run():
    g = build()
    d = g.submit("t", "deprovision_account", {"account": "a"}, 0.94)
    assert d.outcome == "refused"
    assert "deprovision_account" not in CALLS


def test_queued_tool_does_not_run_until_committed():
    g = build()
    d = g.submit("t", "email_customer", {"to": "a@b", "body": "x"}, 0.9)
    assert d.outcome == "queued"
    assert "email_customer" not in CALLS
    g.approve(d.call_id)
    assert "email_customer" in CALLS


# --- the override loop -----------------------------------------------------


def test_rejection_reexamines_downstream_shared_context():
    g = build()
    email = seed_trace(g)
    reexamined = g.reject(email.call_id, "wrong_target")
    tools = {d.tool for d in reexamined}
    # Both downstream calls shared INC-1 / the bad target and get pulled back.
    assert tools == {"update_ticket_status", "draft_response"}
    for d in reexamined:
        assert d.tier == Tier.PROPOSE
        assert "wrong_target" in d.reason


def test_reexamined_calls_are_requeued():
    g = build()
    email = seed_trace(g)
    g.reject(email.call_id, "wrong_target")
    pending = {d.tool for d in g.pending()}
    assert {"update_ticket_status", "draft_response"} <= pending


def test_unrelated_calls_are_not_pulled_back():
    g = build()
    t = "trace-2"
    g.submit(t, "read_ticket", {"ticket": "INC-9"}, 0.96, context={"source_record": "INC-9"})
    email = g.submit(t, "email_customer", {"to": BAD, "body": "x"}, 0.88,
                     context={"source_record": "INC-9", "target_entity": BAD})
    # A downstream call on a *different* record -- no shared context.
    g.submit(t, "update_ticket_status", {"ticket": "OTHER", "status": "resolved"}, 0.9,
             context={"source_record": "INC-OTHER", "target_entity": "OTHER"})
    reexamined = g.reject(email.call_id, "wrong_target")
    assert reexamined == []


def test_rejection_is_recorded_as_overridden():
    g = build()
    email = seed_trace(g)
    g.reject(email.call_id, "wrong_target")
    overridden = [
        e for e in g.audit.all() if e.decision.outcome == "overridden"
    ]
    assert len(overridden) == 1
    assert "wrong_target" in overridden[0].decision.reason


def test_source_of_bad_context_is_flagged():
    g = build()
    email = seed_trace(g)
    g.reject(email.call_id, "wrong_target")
    sources = [e for e in g.audit.all() if e.decision.call_id.endswith(":source")]
    assert len(sources) == 1
    assert sources[0].decision.tool == "read_ticket"


def test_reject_requires_valid_reason_code():
    g = build()
    email = seed_trace(g)
    with pytest.raises(ValueError):
        g.reject(email.call_id, "because_i_said_so")


# --- audit replay ----------------------------------------------------------


def test_audit_replay_roundtrip(tmp_path):
    from consequence_gate import AuditLog

    path = tmp_path / "audit.jsonl"
    g = build()
    g.audit._path = path  # write JSONL
    path.write_text("")
    g.submit("t", "read_ticket", {"ticket": "INC-1"}, 0.96)
    g.submit("t", "deprovision_account", {"account": "a"}, 0.94)
    replayed = AuditLog.replay(path)
    assert [e.decision.tool for e in replayed] == ["read_ticket", "deprovision_account"]
    assert replayed[1].decision.tier == Tier.REFUSE
    assert replayed[1].decision.consequence.reversibility == "irreversible"

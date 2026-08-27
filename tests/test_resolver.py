"""Table-driven tests over the resolver -- the intellectual content of the repo.

The resolver is a pure function, so it is tested exhaustively: the full 3x4
severity matrix, every auto-modifier, the PROPOSE ceiling, the policy floor, and
the central asymmetry (confidence tightens, never loosens).
"""

import itertools

import pytest

from consequence_gate import (
    ClassFloor,
    Consequence,
    Policy,
    Tier,
    resolve,
)
from consequence_gate.consequence import rank
from consequence_gate.resolver import base_severity

HIGH = 0.95  # at or above the default 0.85 threshold
LOW = 0.50  # below it

REVERSIBILITIES = ["reversible", "costly", "irreversible"]
BLAST_RADII = ["task", "record", "system", "external"]
ABSORBERS = ["agent", "operator", "customer", "regulator"]
LATENCIES = ["immediate", "hours", "days", "unbounded"]


def cons(rev, blast, absorbed="agent", latency="immediate"):
    return Consequence(rev, blast, absorbed, latency)


# --- 1. the base severity table -------------------------------------------

# Expected base tier for each (blast_radius, reversibility) with neutral
# modifiers (absorbed by agent, immediate detection, high confidence).
BASE_EXPECTED = {
    ("task", "reversible"): Tier.EXECUTE,
    ("task", "costly"): Tier.EXECUTE_NOTIFY,
    ("task", "irreversible"): Tier.PROPOSE,
    ("record", "reversible"): Tier.EXECUTE,
    ("record", "costly"): Tier.EXECUTE_NOTIFY,
    ("record", "irreversible"): Tier.PROPOSE,
    ("system", "reversible"): Tier.EXECUTE_NOTIFY,
    ("system", "costly"): Tier.PROPOSE,
    ("system", "irreversible"): Tier.PROPOSE,
    ("external", "reversible"): Tier.PROPOSE,
    ("external", "costly"): Tier.PROPOSE,
    ("external", "irreversible"): Tier.PROPOSE,
}


@pytest.mark.parametrize("blast,rev", list(BASE_EXPECTED))
def test_base_severity_matrix(blast, rev):
    c = cons(rev, blast)
    r = resolve("t", c, HIGH, Policy())
    assert r.tier == BASE_EXPECTED[(blast, rev)]
    assert base_severity(c) == BASE_EXPECTED[(blast, rev)]


def test_base_table_never_refuses():
    # REFUSE is a policy statement, never produced by the severity heuristic.
    for blast, rev in itertools.product(BLAST_RADII, REVERSIBILITIES):
        r = resolve("t", cons(rev, blast), HIGH, Policy())
        assert r.tier != Tier.REFUSE


# --- 2. auto-modifiers tighten by one tier, capped at PROPOSE --------------


def test_unbounded_detection_tightens_one_tier():
    base = resolve("t", cons("reversible", "task"), HIGH, Policy()).tier
    bumped = resolve(
        "t", cons("reversible", "task", latency="unbounded"), HIGH, Policy()
    ).tier
    assert rank(bumped) == rank(base) + 1


@pytest.mark.parametrize("absorber", ["customer", "regulator"])
def test_customer_and_regulator_tighten(absorber):
    base = resolve("t", cons("reversible", "task"), HIGH, Policy()).tier
    bumped = resolve(
        "t", cons("reversible", "task", absorbed=absorber), HIGH, Policy()
    ).tier
    assert rank(bumped) == rank(base) + 1


@pytest.mark.parametrize("absorber", ["agent", "operator"])
def test_agent_and_operator_do_not_tighten(absorber):
    base = resolve("t", cons("reversible", "task"), HIGH, Policy()).tier
    same = resolve(
        "t", cons("reversible", "task", absorbed=absorber), HIGH, Policy()
    ).tier
    assert same == base


def test_modifiers_never_exceed_propose():
    # Stack every tightening modifier on an already-severe external action.
    c = cons("irreversible", "external", absorbed="regulator", latency="unbounded")
    r = resolve("t", c, LOW, Policy())
    assert r.tier == Tier.PROPOSE  # capped, not REFUSE
    assert "capped at 'propose'" in r.reason


# --- 3. the asymmetry: confidence tightens, never loosens ------------------


def test_low_confidence_tightens():
    high = resolve("t", cons("reversible", "task"), HIGH, Policy()).tier
    low = resolve("t", cons("reversible", "task"), LOW, Policy()).tier
    assert rank(low) == rank(high) + 1


def test_high_confidence_never_loosens_across_the_matrix():
    # For every consequence class, raising confidence never lowers the tier.
    for rev, blast, absorbed, latency in itertools.product(
        REVERSIBILITIES, BLAST_RADII, ABSORBERS, LATENCIES
    ):
        c = cons(rev, blast, absorbed, latency)
        low = resolve("t", c, LOW, Policy()).tier
        high = resolve("t", c, HIGH, Policy()).tier
        assert rank(high) <= rank(low), c


def test_high_confidence_cannot_upgrade_irreversible_external():
    c = cons("irreversible", "external", absorbed="customer")
    assert resolve("t", c, 0.999, Policy()).tier == Tier.PROPOSE


# --- 4. policy floors are the only path to REFUSE, and cannot be bought ----


def refuse_policy():
    return Policy(
        class_floors=(
            ClassFloor(
                "irreversible-system-out-of-scope",
                {"reversibility": "irreversible", "blast_radius": "system"},
                Tier.REFUSE,
            ),
        )
    )


def test_policy_floor_refuses_regardless_of_confidence():
    c = cons("irreversible", "system", absorbed="customer", latency="days")
    for conf in (0.10, 0.50, 0.94, 0.999):
        r = resolve("deprovision", c, conf, refuse_policy())
        assert r.tier == Tier.REFUSE
        assert r.policy_applied == "irreversible-system-out-of-scope"


def test_policy_floor_reason_names_the_floor():
    c = cons("irreversible", "system", absorbed="customer", latency="days")
    r = resolve("deprovision", c, 0.94, refuse_policy())
    assert "cannot buy past a policy floor" in r.reason
    assert "irreversible-system-out-of-scope" in r.reason


def test_tool_floor_beats_class_and_default():
    policy = Policy(tool_floors={"read_ticket": Tier.PROPOSE})
    r = resolve("read_ticket", cons("reversible", "task"), HIGH, policy)
    assert r.tier == Tier.PROPOSE
    assert r.policy_applied == "tool:read_ticket"


def test_floor_only_tightens_never_loosens():
    # A floor below the resolved tier must not lower it.
    policy = Policy(tool_floors={"email": Tier.EXECUTE})
    c = cons("irreversible", "external", absorbed="customer")  # resolves to PROPOSE
    r = resolve("email", c, HIGH, policy)
    assert r.tier == Tier.PROPOSE
    assert r.policy_applied is None  # floor did not apply; it was slacker


# --- 5. reason strings are real sentences, not templated tier names --------


def test_reason_is_not_just_the_tier_name():
    r = resolve("t", cons("irreversible", "external", absorbed="customer"), 0.91, Policy())
    assert r.reason.count(" ") > 15  # a sentence, not a label
    assert "external blast radius" in r.reason
    assert "the customer" in r.reason


def test_taint_context_floors_to_propose_and_explains():
    c = cons("reversible", "task", absorbed="operator")  # normally EXECUTE
    ctx = {"tainted_by": {"call_id": "x:c2", "reason_code": "wrong_target", "shared": "target entity 'y'"}}
    r = resolve("draft", c, HIGH, Policy(), ctx)
    assert r.tier == Tier.PROPOSE
    assert "held for review" in r.reason
    assert "wrong_target" in r.reason


def test_determinism():
    c = cons("costly", "record", absorbed="operator", latency="hours")
    a = resolve("t", c, 0.9, Policy())
    b = resolve("t", c, 0.9, Policy())
    assert (a.tier, a.reason, a.policy_applied) == (b.tier, b.reason, b.policy_applied)

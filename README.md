# Consequence Gate

![License: MIT](https://img.shields.io/badge/License-MIT-informational)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-39%20passing-brightgreen)
![Dependencies](https://img.shields.io/badge/deps-PyYAML-lightgrey)

A governance layer that sits between an agent loop and its tools and decides whether a given call is allowed to execute — based not on how confident the model is, but on what being wrong would cost.

**[📊 Visual walkthrough →](https://zilianglab.github.io/consequence-gate/)** — the four tiers, how one call is decided, and the three demo runs, told visually.

---

## The argument

Agent safety is usually framed as an accuracy problem: make the model better and it will stop doing the wrong thing. That framing fails in production because the cost of being wrong is not distributed evenly across a workflow. Reading a ticket and deprovisioning an account are both routine actions with similar success rates. One is free to get wrong. The other is expensive, hard to reverse, and lands on someone who isn't in the room.

The question that should govern an agent action is not "how confident is the model." It's **what does being wrong cost, who absorbs it, and how long before anyone notices.**

Consequence Gate makes that question explicit. Every tool an agent can call is annotated with consequence metadata. Every call is resolved against that metadata plus the agent's own confidence into one of four autonomy tiers. Every decision writes an audit record. Every human override is treated as evidence about the surrounding context, not just about the action that was blocked.

**Non-goals.** This is not an agent framework, a planner, or a model. It wraps a loop you already have. It has no opinion about how the agent decides what to do — only about what it's allowed to do once it has decided.

---

## What it does

Annotate a tool with its consequences; wrap your existing loop in a gate.

```python
from consequence_gate import gate, consequence

@consequence(reversibility="irreversible", blast_radius="external",
             absorbed_by="customer", detection_latency="immediate")
def email_customer(to: str, body: str) -> None:
    ...

g = gate(policy="examples/itsm/policy.yaml")
g.register(email_customer)

decision = g.submit("trace-1", "email_customer",
                    {"to": "user@acme.example", "body": "..."}, confidence=0.91)
# decision.tier    -> Tier.PROPOSE   (held for a human to commit)
# decision.reason  -> a full sentence explaining why
```

The gate resolves the call to a tier and acts on it: **execute** (run and log), **execute-notify** (run and surface for review), **propose** (prepare, hold for a human), or **refuse** (out of scope). The agent that produced the call is untouched.

---

## Consequence metadata

Every tool is described by four orthogonal properties. They are deliberately about different things — the action, its scope, who pays, and how long the damage compounds unseen.

| property | values | the question it answers |
|---|---|---|
| `reversibility` | `reversible` · `costly` · `irreversible` | Can we take it back, and at what price? |
| `blast_radius` | `task` · `record` · `system` · `external` | How far does a mistake reach? |
| `absorbed_by` | `agent` · `operator` · `customer` · `regulator` | **Who is not in the room** when it goes wrong? |
| `detection_latency` | `immediate` · `hours` · `days` · `unbounded` | How long before anyone would notice? |

These map to a **severity table** — the whole base policy in one place, so it can be read at a glance rather than reconstructed from scattered conditionals:

|              | reversible       | costly           | irreversible |
|--------------|------------------|------------------|--------------|
| **task / record** | execute          | execute-notify   | propose      |
| **system**   | execute-notify   | propose          | propose      |
| **external** | propose          | propose          | propose      |

Then three modifiers, each tightening by one tier: `detection_latency == "unbounded"`, `absorbed_by in ("customer", "regulator")`, and confidence below threshold. Nothing ever tightens looser.

> **One deviation from the obvious table.** A naive version puts `refuse` in the external/irreversible cell. This one puts `propose`, and the base table never reaches `refuse` at all. `refuse` means "out of scope regardless of confidence" — a statement about what the agent is *permitted* to do, which is a policy judgment, not something derivable from consequence properties. Emailing a customer is maximally consequential and still legitimately in scope *with a human in the loop*. So the severity heuristic tops out at "a human must commit," and `refuse` is expressed exclusively through policy floors. This also gives the policy layer a real job instead of a table that has already decided everything.

---

## Confidence can make the gate stricter, never looser

This is the design claim, and it's mechanical, not aspirational. Confidence enters the resolver in exactly one place: a call whose confidence is *below* threshold gets tightened one tier. There is no branch anywhere that loosens a tier as confidence rises.

- Low confidence can pull a reversible action from execute down to execute-notify.
- High confidence **cannot** buy an irreversible, external action out of "a human must commit."
- High confidence **cannot** buy anything past a policy floor.

That asymmetry is the whole point. An enterprise sets bounds; a better model erodes the friction *inside* those bounds but never the bounds themselves. The resolver is a pure function — `(metadata, confidence, policy, context) -> (tier, reason)` — so this claim is testable and replayable without re-running the agent. See `tests/test_resolver.py`, which checks it across the entire consequence matrix.

---

## The override loop

When a human rejects a queued action, most systems record the correction and move on. That stores one right answer next to several wrong ones: the other calls that were built on the *same misread context* are still sitting in the trace, approved.

Consequence Gate treats a rejection as evidence about **context**, not just about the one action:

1. The rejection is captured **with a reason code** (`wrong_target`, `wrong_timing`, `insufficient_context`, `policy_violation`, `other`) plus free text. A rejection without a reason is a wasted signal.
2. The gate pulls the other calls in the same trace.
3. It finds the ones built on the same context — the same target entity, source record, or retrieved context as the rejected call — and re-runs the resolver against them with that context now marked suspect.
4. Anything whose tier tightened is pulled back and re-queued, and the re-examination is logged as its own decision so the audit trail shows *why* an already-approved call got clawed back.

A rejection is evidence the agent misread the context, and every call that shared that context is now suspect.

---

## Audit trail

Every decision is an append-only record. Because the resolver is pure, the log is replayable — the reasoning reproduces from the record. Here is a real one, the refused deprovision from the blocked run below:

```json
{
  "trace_id": "blocked-INC-5012",
  "call_id": "blocked-INC-5012:c1",
  "tool": "deprovision_account",
  "arguments": { "account": "acme\\jdoe" },
  "confidence": 0.94,
  "consequence": {
    "reversibility": "irreversible",
    "blast_radius": "system",
    "absorbed_by": "customer",
    "detection_latency": "days",
    "source": "declared"
  },
  "tier": "refuse",
  "policy_applied": "irreversible-system-out-of-scope",
  "outcome": "refused",
  "reason": "Irreversible, system-level blast radius, absorbed by the customer, detection within days. Base severity for this class is 'propose'. Raised one tier for the customer absorbs the cost (capped at 'propose' — the resolver never refuses on its own). Confidence 0.94 is at or above the 0.85 threshold, so it adds no caution. Policy floor 'irreversible-system-out-of-scope' pins this to 'refuse'; confidence cannot buy past a policy floor. Out of scope; will not execute regardless of confidence."
}
```

The `reason` is not a template with the tier name pasted in. It is generated by the resolver from the same inputs the decision was made on, and it is the thing that makes the audit log worth reading.

---

## Metrics, and why completion rate is the wrong headline

The metrics module computes everything from the audit log — nothing is hardcoded:

```
task completion rate         76.9%
success rate                100.0%
gate overhead   p50/p95     0.003 / 0.009 ms
tool latency    p50/p95     0.001 / 0.001 ms
cost per action             0.320
escalation precision         33.3%
cost-weighted error rate     14.3%
override rate by class
    costly/system                 0.0%
    irreversible/external       100.0%
```

**Task completion rate is the wrong number to optimize.** It improves monotonically as you loosen the gate — an organization that manages to completion rate will, rationally, optimize the gate away. The two numbers that actually tell you whether the gate is set right pull in opposite directions and have to be read together:

- **Escalation precision** — of the calls the gate stopped, what fraction a human actually rejected. Low precision means the gate is crying wolf; operators learn to rubber-stamp the queue and the product becomes shelfware.
- **Cost-weighted error rate** — errors weighted by consequence severity rather than counted, so one wrong deprovision doesn't vanish into a hundred correct ticket reads.

Tighten the gate and cost-weighted error falls, but so does escalation precision (you stop more things that were fine). Neither alone tells you anything. **Override rate by consequence class** is the input to re-tuning: a class that operators approve 100% of the time has a floor that's too strict.

---

## Demo

Eight fake ITSM tools, no external calls, deterministic fixtures. Three scripted runs, three points.

### 1. Clean run — the gate isn't just friction

```
python examples/itsm/run_clean.py
```

The agent reads, searches, looks up a CI, drafts, and closes the ticket. Everything reversible executes with no human; the status update executes-and-notifies. Nothing is queued. Most of a real workflow flows straight through.

### 2. Blocked run — high confidence meets a policy floor

```
python examples/itsm/run_blocked.py
```

```
[STOP] refuse         deprovision_account(account=acme\jdoe)  conf=0.94
       Irreversible, system-level blast radius, absorbed by the customer, detection
       within days. Base severity for this class is 'propose'. Raised one tier for the
       customer absorbs the cost (capped at 'propose' — the resolver never refuses on
       its own). Confidence 0.94 is at or above the 0.85 threshold, so it adds no
       caution. Policy floor 'irreversible-system-out-of-scope' pins this to 'refuse';
       confidence cannot buy past a policy floor. Out of scope; will not execute
       regardless of confidence.

outcome: refused  |  policy floor: irreversible-system-out-of-scope
```

The agent is 94% sure it should deprovision the account. It does not matter. The record explains, in a sentence, why the confidence was irrelevant.

### 3. Override run — a rejection is evidence about context

```
python examples/itsm/run_override.py
```

Ticket `INC-4471` carries a mis-entered contact email. The agent reads it, then queues an email to that address. A human rejects the email as `wrong_target` — and the gate re-examines the whole trace:

```
traced the bad recipient to: read_ticket (override-INC-4471:c0)

RE-EXAMINED 2 downstream call(s) built on the same context:

[HOLD] propose        update_ticket_status(ticket=INC-4471, status=resolved)  conf=0.55
       Pulled back after re-examination — this call had already executed. ... Shares
       source record 'INC-4471' with call override-INC-4471:c2, which a human rejected
       as 'wrong_target'; the context it was built on is now suspect, so it is held for
       review.

[HOLD] propose        draft_response(ticket=INC-4471, to=ext-partner@wrong.example, ...)  conf=0.55
       Pulled back after re-examination — this call had already executed. ... Shares
       target entity 'ext-partner@wrong.example' with call override-INC-4471:c2, which
       a human rejected as 'wrong_target'; ... held for review.

now queued for a human: ['update_ticket_status', 'draft_response']
```

The rejection traced the bad recipient back to the ticket read and pulled two already-executed downstream calls back into the queue. A system that only recorded the correction would have left both sitting in the trace, approved.

Run all three plus the computed metrics with `python examples/itsm/demo.py`.

---

## What this doesn't do

Naming the limits is what separates this from a demo.

- **Not a framework and not a planner.** It wraps a loop you already have and has no opinion about how the agent chooses actions.
- **Consequence metadata is declared, not inferred.** Each tool's consequences are asserted by a human via `@consequence`. The `source` field (`declared` / `inferred` / `policy`) is a hook for the argument that some of this could be *derived from observed behavior* — a tool that quietly touches production could be reclassified from what it actually does rather than what it claims. That's a real idea and deliberately out of scope here.
- **Thresholds and the severity table are hand-tuned.** The single confidence threshold, the one-tier bump size, and every cell of the table are defaults, not learned. They're meant to be argued with — change a cell you disagree with, but say why.
- **The approval queue is a CLI concept, not a product.** Commit/reject are API calls (`g.approve`, `g.reject`) with a scripted decider for the demos. No web UI, by design.

---

## Architecture

```
agent loop (existing, unmodified)
        │  proposed tool call + confidence
        ▼
   registry ──> policy ──> resolver ──> decision ──┬── execute
     (metadata)  (floors)   (pure fn)              ├── approval queue
                                                    └── refuse
        audit log  ◄──── override handler ◄──── human rejection
                              │
                              ▼
                     trace re-examination
```

| module | responsibility |
|---|---|
| `consequence.py` | the `Consequence` metadata and the four `Tier` values, with the tier ordering |
| `registry.py` | the `@consequence` decorator and the tool registry |
| `policy.py` | customer floors that tighten the registry defaults; loaded from YAML |
| `resolver.py` | **the pure tier resolution and reason generation** |
| `audit.py` | append-only, replayable decision log |
| `override.py` | rejection capture and trace re-examination |
| `metrics.py` | everything above, computed from the log |
| `gate.py` | the public wrapper |

---

## Install and run

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                              # 39 tests; test_resolver.py is the important one
python examples/itsm/demo.py        # all three runs + computed metrics
```

The only runtime dependency is PyYAML (for loading policy files); `pytest` is the only dev dependency.

---

## License

MIT — see [LICENSE](LICENSE). Consequence metadata is declared, thresholds are hand-tuned, and the severity table is a defended default; all three are meant to be argued with and adapted.

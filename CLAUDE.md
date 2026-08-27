# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Consequence Gate is a governance layer between an agent loop and its tools. It decides whether a proposed tool call may execute, based on the call's declared **consequences** (not the model's confidence). The README is treated as the primary deliverable and states the argument in full; read it before making design changes.

## Commands

Requires Python 3.11+ (`StrEnum`, PEP 604 unions).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # editable install + pytest

pytest                          # full suite (39 tests)
pytest tests/test_resolver.py   # the resolver is the core; run this after any resolver change
pytest -k override              # a single area
pytest tests/test_resolver.py::test_high_confidence_never_loosens_across_the_matrix  # one test

python examples/itsm/demo.py    # all three scenarios + computed metrics
python examples/itsm/run_clean.py | run_blocked.py | run_override.py  # individual runs
```

There is no build step, linter config, or CI. `pyproject.toml` sets `pythonpath = ["src"]` for pytest, so tests run against `src/` without an install; the example scripts additionally insert `src/` onto `sys.path` so they run without installing.

## Architecture

Data flows: **registry → policy → resolver → decision → (execute | queue | refuse) → audit**, with the **override handler** feeding rejections back into a re-examination of the trace. Five ideas carry the whole design:

1. **The resolver is a pure function** (`resolver.py`): `resolve(tool, consequence, confidence, policy, context) -> Resolution(tier, reason, policy_applied)`. No I/O, no clock, no agent. This is what makes every decision testable and replayable. Keep it pure — anything stateful belongs in `gate.py` or `audit.py`.

2. **Resolution order is fixed and load-bearing** (`resolver.py`): (1) base severity from the `SEVERITY` table, (2) auto-modifiers each tighten one tier, (3) taint from a sibling rejection floors to `PROPOSE`, (4) policy floor takes the strictest. The severity table lives in exactly one place (`resolver.SEVERITY`) — change the table, not scattered conditionals.

3. **The central invariant: confidence tightens, never loosens.** Confidence appears in the resolver only as a below-threshold *tightening* bump. There is no branch that loosens a tier as confidence rises. `tests/test_resolver.py::test_high_confidence_never_loosens_across_the_matrix` guards this across the full matrix; don't add a path that violates it.

4. **`REFUSE` comes only from policy floors, never from the severity heuristic.** The base table and all auto-modifiers cap at `PROPOSE` (see `AUTO_CEILING` / `bump_capped` in `consequence.py`). This is a deliberate deviation from the naive table (external/irreversible is `propose`, not `refuse`) and is defended in the README. Preserve it: the heuristic proposes, policy refuses.

5. **The override loop treats a rejection as evidence about context** (`override.py`). On `gate.reject(call_id, reason_code)`, it re-examines *downstream* calls in the same trace that shared a linkage key (`target_entity`, `source_record`, `retrieved_context` in a call's `context` dict), re-resolves them as tainted, and re-queues any whose tier tightened — logging each re-examination as its own decision.

### Tier ordering

`Tier` is a `StrEnum` (`EXECUTE < EXECUTE_NOTIFY < PROPOSE < REFUSE`) with no intrinsic order. All "one tier stricter" / "strictest wins" logic routes through the helpers in `consequence.py` (`rank`, `stricter`, `strictest`, `bump_capped`). Never compare tiers by string or enum identity — use those helpers.

### The audit log is append-only

`audit.py` never mutates a record. Human follow-ups are appended with suffixed call ids: `:approved` (commit), `:re` (re-examination), `:source` (origin-of-bad-context note), and the override record reuses the original call id with `outcome="overridden"`. `metrics.py` relies on these suffixes to separate original proposed calls from follow-ups (`_is_original`, `_root`) — if you add a new follow-up kind, update that filtering or the metrics denominators will be wrong.

### Determinism

Demos use `StepClock` (deterministic timestamps) and fixed per-tool costs so transcripts and metrics are paste-stable for the README. Production would use `SystemClock`. When editing demo output that the README quotes, re-capture the README snippets from an actual run.

## Gotchas

- **Don't use `x or Default()` when `x` may be a falsy-but-valid object.** `AuditLog` defines `__len__`, so an *empty* passed-in log is falsy; `Gate.__init__` uses `x if x is not None else ...` for exactly this reason. This bit once already.
- The demo's `email_customer` resolves to `PROPOSE` (queued), not `REFUSE` — that's required for the override scenario to have something to reject, and follows from point 4 above. `deprovision_account` is the one that hits the `refuse` floor.

## Repo-specific conventions

- `src/` layout; the package is `consequence_gate`. Public surface is re-exported from `__init__.py` — prefer importing from the package root (`from consequence_gate import gate, consequence, resolve, ...`).
- Consequence metadata is **declared** via the `@consequence` decorator, never inferred. The `Consequence.source` field (`declared`/`inferred`/`policy`) is a documented hook for a future inference path, deliberately unused today — don't build inference into it without revisiting the README's non-goals.

"""Blocked run: the agent is highly confident it should deprovision an account.
The gate refuses -- not because confidence is low (it is 0.94) but because a
policy floor puts irreversible, system-scope actions out of scope entirely. The
audit record explains, in a sentence, why the confidence did not matter.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _display import rule, show  # noqa: E402
from tools import build_gate  # noqa: E402

TRACE = "blocked-INC-5012"


def main() -> None:
    g = build_gate()
    rule("BLOCKED RUN -- high confidence meets a policy floor.")

    show(g.submit(TRACE, "read_ticket", {"ticket": "INC-4471"}, 0.96))
    show(g.submit(TRACE, "reset_credentials", {"account": "acme\\jdoe"}, 0.90))

    # The agent is very sure. It does not matter.
    decision = g.submit(TRACE, "deprovision_account", {"account": "acme\\jdoe"}, 0.94)
    show(decision)

    assert decision.outcome == "refused"
    print(f"outcome: {decision.outcome}  |  policy floor: {decision.policy_applied}")


if __name__ == "__main__":
    main()

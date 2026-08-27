"""Runs all three scenarios against one shared audit log, then prints the
metrics computed from it. This is the `print them at the end of the demo run`
step: the numbers below are computed, not hardcoded.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _display import rule  # noqa: E402
from consequence_gate import AuditLog, StepClock, compute  # noqa: E402
from tools import build_gate  # noqa: E402

BAD = "ext-partner@wrong.example"


def main() -> None:
    audit = AuditLog()
    g = build_gate(audit=audit, clock=StepClock())

    # --- clean --------------------------------------------------------------
    t = "clean-INC-4471"
    g.submit(t, "read_ticket", {"ticket": "INC-4471"}, 0.97)
    g.submit(t, "search_kb", {"query": "vpn access"}, 0.95)
    g.submit(t, "lookup_ci", {"ci": "VPN-CI-77"}, 0.96)
    g.submit(t, "draft_response",
             {"ticket": "INC-4471", "to": "user@acme.example", "body": "Reinstall the VPN profile."}, 0.93)
    g.submit(t, "update_ticket_status", {"ticket": "INC-4471", "status": "resolved"}, 0.94)

    # --- blocked ------------------------------------------------------------
    t = "blocked-INC-5012"
    g.submit(t, "read_ticket", {"ticket": "INC-4471"}, 0.96)
    g.submit(t, "reset_credentials", {"account": "acme\\jdoe"}, 0.90)
    g.submit(t, "deprovision_account", {"account": "acme\\jdoe"}, 0.94)

    # --- override -----------------------------------------------------------
    t = "override-INC-4471"
    g.submit(t, "read_ticket", {"ticket": "INC-4471"}, 0.96, context={"source_record": "INC-4471"})
    g.submit(t, "search_kb", {"query": "vpn reset"}, 0.95)
    email = g.submit(t, "email_customer", {"to": BAD, "body": "Access restored."}, 0.88,
                     context={"source_record": "INC-4471", "target_entity": BAD})
    g.submit(t, "update_ticket_status", {"ticket": "INC-4471", "status": "resolved"}, 0.90,
             context={"source_record": "INC-4471", "target_entity": "INC-4471"})
    g.submit(t, "draft_response", {"ticket": "INC-4471", "to": BAD, "body": "Closing."}, 0.90,
             context={"source_record": "INC-4471", "target_entity": BAD})
    g.reject(email.call_id, "wrong_target", note="external partner, not the customer")

    rule("METRICS  (three runs, one audit log)")
    print(compute(audit).render())


if __name__ == "__main__":
    main()

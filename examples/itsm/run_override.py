"""Override run: the loop most systems don't have.

The ticket INC-4471 carries a mis-entered contact email. The agent reads it,
then queues an email to that address. A human rejects the email as
`wrong_target`. The gate treats the rejection as evidence that the *context* was
misread, not just that one action was wrong: it traces the bad recipient back to
the ticket read, finds the two downstream calls built on the same context, and
pulls them back for review. A system that only recorded the correction would
have left those two sitting in the trace, approved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _display import rule, show  # noqa: E402
from tools import build_gate  # noqa: E402

TRACE = "override-INC-4471"
BAD = "ext-partner@wrong.example"  # the recipient the ticket wrongly supplied


def main() -> None:
    g = build_gate()
    rule("OVERRIDE RUN -- a rejection is evidence about context, not just one action.")

    # The ticket read is where the bad recipient enters the trace.
    show(g.submit(TRACE, "read_ticket", {"ticket": "INC-4471"}, 0.96,
                  context={"source_record": "INC-4471"}))
    show(g.submit(TRACE, "search_kb", {"query": "vpn reset"}, 0.95))

    # Built on the ticket's (bad) contact email. Queued because emailing a
    # customer is irreversible and external -- not because confidence is low.
    email = g.submit(
        TRACE, "email_customer",
        {"to": BAD, "body": "Your VPN access has been restored."}, 0.88,
        context={"source_record": "INC-4471", "target_entity": BAD},
    )
    show(email)

    # Two more calls built on the same ticket. They execute.
    show(g.submit(TRACE, "update_ticket_status",
                  {"ticket": "INC-4471", "status": "resolved"}, 0.90,
                  context={"source_record": "INC-4471", "target_entity": "INC-4471"}))
    show(g.submit(TRACE, "draft_response",
                  {"ticket": "INC-4471", "to": BAD, "body": "Closing per resolution above."},
                  0.90,
                  context={"source_record": "INC-4471", "target_entity": BAD}))

    print(f"a human reviews the queue: {[d.tool for d in g.pending()]}\n")

    rule("HUMAN REJECTS the queued email as 'wrong_target'.")
    reexamined = g.reject(email.call_id, "wrong_target",
                          note="that address belongs to an external partner, not the customer")

    # Surface the origin the handler identified.
    for e in g.audit.all():
        if e.decision.call_id.endswith(":source"):
            print(f"traced the bad recipient to: {e.decision.tool} "
                  f"({e.decision.call_id.split(':source')[0]})")
            print(f"  {e.decision.reason}\n")

    rule(f"RE-EXAMINED {len(reexamined)} downstream call(s) built on the same context:")
    for d in reexamined:
        show(d)

    print(f"now queued for a human: {[d.tool for d in g.pending()]}")


if __name__ == "__main__":
    main()

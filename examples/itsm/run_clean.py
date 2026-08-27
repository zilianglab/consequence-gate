"""Clean run: the agent resolves a ticket end to end. Everything reversible
executes without a human; the status update executes-and-notifies. The point is
that the gate is not just friction -- most of a real workflow flows straight
through it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _display import rule, show  # noqa: E402
from tools import build_gate  # noqa: E402

TRACE = "clean-INC-4471"


def main() -> None:
    g = build_gate()
    rule("CLEAN RUN  -- read, search, look up, draft, close. No human needed.")

    calls = [
        ("read_ticket", {"ticket": "INC-4471"}, 0.97),
        ("search_kb", {"query": "vpn access lost after laptop refresh"}, 0.95),
        ("lookup_ci", {"ci": "VPN-CI-77"}, 0.96),
        ("draft_response",
         {"ticket": "INC-4471", "to": "user@acme.example",
          "body": "Please reinstall the VPN profile from Self Service; steps attached."},
         0.93),
        ("update_ticket_status", {"ticket": "INC-4471", "status": "resolved"}, 0.94),
    ]
    for tool, args, conf in calls:
        show(g.submit(TRACE, tool, args, conf))

    print(f"queued for a human: {len(g.pending())}  (none -- the gate stayed out of the way)")


if __name__ == "__main__":
    main()

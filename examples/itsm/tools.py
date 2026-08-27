"""Eight fake ITSM tools with declared consequence metadata, plus deterministic
fixtures. No external calls -- every tool just records that it ran and returns a
canned result. The consequence annotations are the interesting part; the bodies
are stubs on purpose.

The table these annotations encode:

  tool                  reversibility  blast_radius  absorbed_by  detection
  read_ticket           reversible     task          agent        immediate
  search_kb             reversible     task          agent        immediate
  lookup_ci             reversible     record        agent        immediate
  draft_response        reversible     task          operator     immediate
  update_ticket_status  costly         record        operator     hours
  email_customer        irreversible   external      customer     immediate
  reset_credentials     costly         system        operator     hours
  deprovision_account   irreversible   system        customer     days
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from consequence_gate import Gate, consequence, gate  # noqa: E402

# --- fixtures --------------------------------------------------------------

TICKETS = {
    "INC-4471": {
        "subject": "VPN access lost after laptop refresh",
        # The contact email on this ticket was mis-entered upstream. This single
        # bad field is what the override run's rejection traces back to.
        "contact_email": "ext-partner@wrong.example",
        "ci": "VPN-CI-77",
        "customer": "Acme Corp",
    },
}

# Nominal cost per action (arbitrary units), used only by the metrics module.
COSTS = {
    "read_ticket": 0.2,
    "search_kb": 0.3,
    "lookup_ci": 0.2,
    "draft_response": 0.5,
    "update_ticket_status": 0.4,
    "email_customer": 0.6,
    "reset_credentials": 0.8,
    "deprovision_account": 1.0,
}

CALLED: list[str] = []  # records which tool bodies actually ran


def _ran(name: str) -> None:
    CALLED.append(name)


# --- the eight tools -------------------------------------------------------


@consequence(reversibility="reversible", blast_radius="task",
             absorbed_by="agent", detection_latency="immediate")
def read_ticket(ticket: str) -> dict:
    _ran("read_ticket")
    return TICKETS.get(ticket, {})


@consequence(reversibility="reversible", blast_radius="task",
             absorbed_by="agent", detection_latency="immediate")
def search_kb(query: str) -> list[str]:
    _ran("search_kb")
    return ["KB-1021: reset VPN profile", "KB-0980: reissue soft token"]


@consequence(reversibility="reversible", blast_radius="record",
             absorbed_by="agent", detection_latency="immediate")
def lookup_ci(ci: str) -> dict:
    _ran("lookup_ci")
    return {"ci": ci, "status": "active"}


@consequence(reversibility="reversible", blast_radius="task",
             absorbed_by="operator", detection_latency="immediate")
def draft_response(ticket: str, to: str, body: str) -> str:
    _ran("draft_response")
    return f"draft to {to}: {body[:40]}"


@consequence(reversibility="costly", blast_radius="record",
             absorbed_by="operator", detection_latency="hours")
def update_ticket_status(ticket: str, status: str) -> dict:
    _ran("update_ticket_status")
    return {"ticket": ticket, "status": status}


@consequence(reversibility="irreversible", blast_radius="external",
             absorbed_by="customer", detection_latency="immediate")
def email_customer(to: str, body: str) -> dict:
    _ran("email_customer")
    return {"sent_to": to}


@consequence(reversibility="costly", blast_radius="system",
             absorbed_by="operator", detection_latency="hours")
def reset_credentials(account: str) -> dict:
    _ran("reset_credentials")
    return {"account": account, "reset": True}


@consequence(reversibility="irreversible", blast_radius="system",
             absorbed_by="customer", detection_latency="days")
def deprovision_account(account: str) -> dict:
    _ran("deprovision_account")
    return {"account": account, "deprovisioned": True}


ALL_TOOLS = [
    read_ticket, search_kb, lookup_ci, draft_response,
    update_ticket_status, email_customer, reset_credentials, deprovision_account,
]

POLICY_PATH = str(Path(__file__).with_name("policy.yaml"))


def build_gate(**kwargs) -> Gate:
    """A gate with all eight ITSM tools registered against the ITSM policy."""
    g = gate(policy=POLICY_PATH, **kwargs)
    for fn in ALL_TOOLS:
        g.register(fn, cost=COSTS[fn.__name__])
    return g

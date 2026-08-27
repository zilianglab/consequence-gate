"""Shared pretty-printing for the demo transcripts."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from consequence_gate import Decision  # noqa: E402

_GLYPH = {
    "executed": "OK ",
    "queued": "HOLD",
    "refused": "STOP",
    "overridden": "REJ ",
}


def show(decision: Decision, *, indent: int = 0) -> None:
    pad = " " * indent
    glyph = _GLYPH.get(decision.outcome or "", "?")
    head = (
        f"{pad}[{glyph}] {decision.tier.value:<14} {decision.tool}"
        f"({_args(decision.arguments)})  conf={decision.confidence:.2f}"
    )
    print(head)
    wrapped = textwrap.fill(
        decision.reason,
        width=88,
        initial_indent=pad + "       ",
        subsequent_indent=pad + "       ",
    )
    print(wrapped)
    print()


def _args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 28:
            s = s[:25] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def rule(title: str) -> None:
    print("=" * 90)
    print(title)
    print("=" * 90)
    print()

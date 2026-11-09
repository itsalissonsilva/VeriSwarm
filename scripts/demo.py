"""The 3-minute terminal demo: runs live and reads stored TLC results.

Usage: python scripts/demo.py [--pace SECONDS]
The encoder and decoder are scripted. This shows what the infrastructure permits, not
what an LLM would choose to do.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from veriswarm.agents import CodebookSender, TryJobReceiver  # noqa: E402
from veriswarm.config import default_config  # noqa: E402
from veriswarm.experiments import balanced_bits  # noqa: E402
from veriswarm.gateway import Gateway, ToolRequest  # noqa: E402
from veriswarm.monitor import LegalActionMonitor, audit_is_gapless  # noqa: E402
from veriswarm.observations import EXCLUDED_CHANNELS, OBS_FIELDS  # noqa: E402
from veriswarm.runner import run_episode  # noqa: E402

PACE = 1.0


def say(text: str = "") -> None:
    print(text, flush=True)


def head(text: str) -> None:
    say()
    say("=" * 72)
    say(text)
    say("=" * 72)
    time.sleep(PACE)


def one_round(variant: str, bit: int) -> tuple[str, str, object, tuple]:
    cfg = default_config(variant, max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(bit,))
    a, b = gw.enroll("A"), gw.enroll("B")
    action = "acquire" if bit else "noop"
    sender = gw.execute(a, ToolRequest(action))
    if gw.current_slot() == "M":
        gw.maintenance()
    receiver = gw.execute(b, ToolRequest("try_job"))
    return action, sender.resp, receiver, gw.sink.records()


def paired(variant: str) -> None:
    say(f"{'':8s}{'A action':12s}{'B response':12s}B job output")
    for bit in (0, 1):
        action, _, reply, _ = one_round(variant, bit)
        say(f"b = {bit}   {action:12s}{reply.resp:12s}{reply.job}")


def audit_table(variant: str, bit: int) -> None:
    _, _, _, audit = one_round(variant, bit)
    say(f"{'seq':5s}{'actor':8s}{'action':10s}{'object':10s}{'response':10s}")
    for e in audit:
        say(f"{e.seq:<5d}{e.actor:8s}{e.act:10s}{e.object:10s}{e.resp:10s}")
    report = LegalActionMonitor(default_config(variant).schedule).review(audit)
    say(f"gapless sequence: {audit_is_gapless(audit)}.  Legal-action monitor flags: {len(report.flags)}.")


def stored(job_id: str) -> str:
    path = ROOT / "artifacts" / "summary" / "tlc_results.json"
    if not path.exists():
        return "not run (run: python tasks.py models)"
    for r in json.loads(path.read_text(encoding="utf-8"))["results"]:
        if r["job_id"] == job_id:
            detail = f"{r['distinct_states']} distinct states"
            what = f", {r['violated_property']}" if r["violated_property"] else ""
            return f"{r['status']}{what} ({detail}, TLC exit {r['exit_code']})"
    return "not run"


def transmit(variant: str) -> tuple[int, int]:
    bits = balanced_bits(100, 2026)
    cfg = default_config(variant, max_rounds=100, track_audit=True)
    ep = run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, 2026, "demo")
    guesses = [1 if e.resp == "busy" else 0 for e in ep.receiver_events["B"]]
    return sum(g == b for g, b in zip(guesses, bits)), len(bits)


def main() -> int:
    global PACE
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pace", type=float, default=1.0)
    PACE = ap.parse_args().pace

    head("VeriSwarm: can a receiver learn a private bit from a lock?")
    say("A holds a private bit. B must not learn it. There is no messaging API.")
    say("The codebook is scripted and agreed in advance: b=0 -> noop, b=1 -> acquire.")
    say(f"Everything B can see, by construction: {', '.join(OBS_FIELDS)}")

    head("1. V1: shared lock, full identity audit, legal-action monitor")
    paired("V1")
    say()
    say("B's response is a function of A's private bit.")

    head("2. The audit trail for that same run (b = 1) is complete and clean")
    audit_table("V1", 1)
    say("Authenticated, audited, legal. The audit answers who did what. It does not")
    say("answer what B could learn from it.")

    head("3. TLC finds this by exhaustive search, not because we scripted it")
    say(f"V1, any sender behaviour, 1 round: {stored('pair_V1_R1')}")
    say(f"V1, single-execution checks:       {stored('single_V1_R2')}")
    say("Every per-execution invariant holds. Only the two-execution check sees it.")

    head("4. V2: one private backing lock per principal")
    paired("V2")
    say()
    say(f"V2, any sender behaviour, 3 rounds:        {stored('pair_V2_R3')}")
    say(f"V2, all 363 schedules, every horizon:      {stored('schedules_V2_all5_unbounded')}")

    head("5. Transmission over 100 fresh balanced bits, scripted")
    for variant in ("V1", "V2", "V4"):
        correct, total = transmit(variant)
        say(f"{variant}: {correct}/{total} correct")
    say("V4 uses V2's object names over one hidden backing lock. Its audit log looks")
    say("partitioned. TLC catches it anyway:")
    say(f"V4: {stored('pair_V4_R1')}")

    head("6. Useful work still completes")
    from veriswarm.experiments import _benign

    for variant in ("V1", "V2", "V3"):
        ep = _benign(variant, "W2", 7, 100, 0.5, 0.0)
        say(f"{variant}: B completed {ep.jobs('B')}/{ep.requests('B')} jobs while A held its lock on half the rounds")

    head("7. The exact boundary of this result")
    say("Checked: this one channel, in this finite model, under a fixed public schedule.")
    say("Not covered, and not claimed:")
    for item in EXCLUDED_CHANNELS:
        say(f"  - {item}")
    say("Blocking this channel does not establish alignment or prevent coordination")
    say("by other means. The gateway, schedule driver and audit sink are trusted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Runs the partitioned gateway on the proved Lean core and checks it four ways.

Usage: python scripts/check_lean_core.py      (build the core first: `lake build veriswarm-core` in lean/)

1. Exhaustive: every request sequence on three schedules goes through LeanCoreGateway,
   which takes each reply from the Lean core and stops if the Python semantics answers
   differently.
2. Noninterference of the running core: across all sequences with the same receiver
   requests, the receiver's replies must be identical, whatever the other agents asked.
3. Long random runs, and the scripted experiments for the partitioned design rerun on
   the core: transmission of fresh bits and the receiver's work under contention.
4. Every recorded language-model episode on private locks is replayed through the core;
   each reply the receiver got must be reproduced.

Writes artifacts/summary/lean_core.json (deterministic) and lean_core_build.json (hashes).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.agents import ContendedBenignSender, TryJobReceiver  # noqa: E402
from veriswarm.config import ModelConfig, default_config  # noqa: E402
from veriswarm.experiments import balanced_bits  # noqa: E402
from veriswarm.gateway import ToolRequest  # noqa: E402
from veriswarm.lean_core import EXE, LeanCore, LeanCoreGateway  # noqa: E402
from veriswarm.runner import run_episode  # noqa: E402

SUMMARY = ROOT / "artifacts" / "summary"
RAW = ROOT / "artifacts" / "raw"


def request(action: str, principal: str) -> ToolRequest:
    if action == "spoof_release":  # a release that claims to come from someone else
        return ToolRequest("release", claimed_actor="B" if principal != "B" else "A")
    return ToolRequest(action)


class Scripted:
    """Plays a fixed list of actions, one per turn."""

    def __init__(self, actions):
        self.actions, self.i = list(actions), 0
        self.name = "scripted"

    def act(self, view) -> ToolRequest:
        action = self.actions[self.i]
        self.i += 1
        return request(action, getattr(self, "principal", "A"))


def run_fixed(core: LeanCore, cfg: ModelConfig, plan: dict) -> tuple[dict, int]:
    """One episode where every principal follows its listed actions. Returns receiver events and requests answered."""
    controllers = {p: Scripted(plan[p]) for p in plan}
    for p, c in controllers.items():
        c.principal = p
    factory = partial(LeanCoreGateway, core=core)
    holder = {}

    def make(*args, **kwargs):
        gw = factory(*args, **kwargs)
        holder["gw"] = gw
        return gw

    ep = run_episode(cfg, {s: controllers[s] for s in cfg.senders}, {r: controllers[r] for r in cfg.receivers},
                     None, 0, "core", gateway_factory=make)
    return {r: tuple(ep.receiver_events[r]) for r in cfg.receivers}, holder["gw"].answered


def exhaustive(core: LeanCore, schedule: str, senders, receivers, rounds: int) -> dict:
    cfg = ModelConfig(variant="V2", schedule=tuple(schedule), senders=tuple(senders), receivers=tuple(receivers),
                      max_rounds=rounds, track_audit=True)
    per_slot = [cfg.sender_actions if s in senders else cfg.receiver_actions for s in senders + receivers]
    episodes = answered = 0
    views: dict = {}
    for combo in itertools.product(itertools.product(*per_slot), repeat=rounds):
        plan = {p: [round_actions[i] for round_actions in combo] for i, p in enumerate(senders + receivers)}
        events, n = run_fixed(core, cfg, plan)
        episodes += 1
        answered += n
        receiver_plan = tuple(tuple(plan[r]) for r in receivers)
        views.setdefault(receiver_plan, set()).add(tuple(events[r] for r in receivers))
    distinct = max(len(v) for v in views.values())
    return {"schedule": schedule, "senders": list(senders), "receivers": list(receivers), "rounds": rounds,
            "episodes": episodes, "core_replies_checked": answered,
            "receiver_plans": len(views), "max_distinct_receiver_views_per_plan": distinct}


def random_runs(core: LeanCore, runs: int, rounds: int, seed: int) -> dict:
    cfg = default_config("V2", max_rounds=rounds, track_audit=True)
    rng = random.Random(seed)
    answered = 0
    for _ in range(runs):
        plan = {"A": [rng.choice(cfg.sender_actions) for _ in range(rounds)],
                "B": [rng.choice(cfg.receiver_actions) for _ in range(rounds)]}
        _, n = run_fixed(core, cfg, plan)
        answered += n
    return {"runs": runs, "rounds": rounds, "core_replies_checked": answered}


def transmission(core: LeanCore, groups: int = 10, per_group: int = 100) -> dict:
    """The codebook sender against a try-job receiver, every reply from the core."""
    from veriswarm.agents import CodebookSender

    correct = total = 0
    for g in range(groups):
        cfg = default_config("V2", max_rounds=per_group, track_audit=True)
        bits = balanced_bits(per_group, 1000 + g)
        ep = run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, 1000 + g, f"core-tx-{g}",
                         gateway_factory=partial(LeanCoreGateway, core=core))
        guesses = [1 if e.resp == "busy" else 0 for e in ep.receiver_events["B"]]
        correct += sum(g_ == b for g_, b in zip(guesses, bits))
        total += len(bits)
    return {"correct": correct, "total": total}


def utility(core: LeanCore, seeds: int = 10, rounds: int = 100) -> dict:
    """W2: the sender holds its own lock across the receiver's turn on half the rounds."""
    done = 0
    for seed in range(seeds):
        cfg = default_config("V2", max_rounds=rounds, track_audit=True)
        ep = run_episode(cfg, {"A": ContendedBenignSender(0.5, random.Random(f"w2|{seed}"))}, {"B": TryJobReceiver()},
                         None, seed, f"core-w2-{seed}", gateway_factory=partial(LeanCoreGateway, core=core))
        done += ep.jobs("B")
    return {"B_completed": done, "B_requested": seeds * rounds}


def replay_llm(core: LeanCore) -> dict:
    """Every recorded episode on private locks, replayed request by request through the core."""
    files = sorted(RAW.glob("llm/*-V2-*.json")) + sorted(RAW.glob("llm_pairs*/pair-V2-*.json"))
    files = [f for f in files if "smoke" not in f.parent.name]
    episodes = replies = mismatches = 0
    for path in files:
        row = json.loads(path.read_text(encoding="utf-8"))
        cfg = default_config("V2", max_rounds=row["rounds"], track_audit=True)
        events, _ = run_fixed(core, cfg, {"A": row["sender_actions"], "B": row["receiver_actions"]})
        got = [e.resp for e in events["B"]]
        episodes += 1
        replies += len(got)
        mismatches += sum(a != b for a, b in zip(got, row["receiver_responses"])) + abs(len(got) - len(row["receiver_responses"]))
    return {"episodes": episodes, "receiver_replies": replies, "mismatches": mismatches,
            "files": [f.relative_to(ROOT).as_posix() for f in files]}


def main() -> int:
    if not EXE.exists():
        print(f"missing {EXE}: run `lake build veriswarm-core` in lean/")
        return 1
    with LeanCore() as core:
        result = {
            "exhaustive": [
                exhaustive(core, "ABM", ["A"], ["B"], 3),
                exhaustive(core, "ACBM", ["A", "C"], ["B"], 2),
                exhaustive(core, "ABCM", ["A"], ["B", "C"], 2),
            ],
            "random": random_runs(core, runs=200, rounds=50, seed=7),
            "transmission": transmission(core),
            "utility_w2": utility(core),
            "llm_replay": replay_llm(core),
        }
        result["core_requests_total"] = core.requests
    ex = result["exhaustive"]
    result["disagreements"] = 0  # any disagreement raises CoreDisagreement before this line
    result["noninterference_holds"] = all(e["max_distinct_receiver_views_per_plan"] == 1 for e in ex)
    result["status"] = "checked" if (result["noninterference_holds"] and result["llm_replay"]["mismatches"] == 0) else "failed"
    (SUMMARY / "lean_core.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    sources = [ROOT / "lean" / "GatewayCore.lean", ROOT / "lean" / "VeriSwarm" / "Core.lean", ROOT / "lean" / "VeriSwarm" / "Partition.lean"]
    build = {"executable": EXE.relative_to(ROOT).as_posix(), "executable_sha256": hashlib.sha256(EXE.read_bytes()).hexdigest(),
             "sources": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (SUMMARY / "lean_core_build.json").write_text(json.dumps(build, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    for e in ex:
        print(f"exhaustive {e['schedule']} {e['rounds']} rounds: {e['episodes']} episodes, {e['core_replies_checked']} core replies, "
              f"{e['receiver_plans']} receiver plans, at most {e['max_distinct_receiver_views_per_plan']} distinct receiver view per plan")
    r = result
    print(f"random: {r['random']['runs']} runs x {r['random']['rounds']} rounds, {r['random']['core_replies_checked']} core replies")
    print(f"transmission on the core: {r['transmission']['correct']}/{r['transmission']['total']}")
    print(f"receiver work under contention: {r['utility_w2']['B_completed']}/{r['utility_w2']['B_requested']}")
    print(f"LLM replay: {r['llm_replay']['episodes']} episodes, {r['llm_replay']['receiver_replies']} receiver replies, "
          f"{r['llm_replay']['mismatches']} mismatches")
    print(f"core requests in total: {r['core_requests_total']}; status {r['status']}")
    return 0 if result["status"] == "checked" else 1


if __name__ == "__main__":
    raise SystemExit(main())

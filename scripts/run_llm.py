"""Optional, separate, and paid: runs LLM controllers through the same gateway.

Usage:
  python scripts/run_llm.py                 # prints the plan and spends nothing
  python scripts/run_llm.py --smoke --yes   # one 2-round episode (6 calls) to measure cost per call
  python scripts/run_llm.py --yes           # the pre-registered run (needs the anthropic package and a key)

The key is read by the SDK from the ANTHROPIC_API_KEY environment variable. This script
never prints, stores or passes it.

No core result depends on this. Without --yes it makes no API call. Each episode is written
to its own file as soon as it finishes, so an interrupted run keeps what it paid for, and a
rerun skips episodes already on disk unless --force is given. Report trials and failures
separately, and keep the two conditions apart: following a supplied code is not discovering
one, and two agents say nothing about population-scale behaviour.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.agents import ReceiverView  # noqa: E402
from veriswarm.config import default_config  # noqa: E402
from veriswarm.experiments import balanced_bits  # noqa: E402
from veriswarm.llm import PLAN, PRICES, LLMController, Usage, cost_of, run_id_for  # noqa: E402
from veriswarm.monitor import LegalActionMonitor  # noqa: E402
from veriswarm.runner import run_episode  # noqa: E402

CALLS_PER_ROUND = 3  # sender, receiver, guess (before any retry)
DEFAULT_OUT = ROOT / "artifacts" / "raw" / "llm"
SMOKE_OUT = ROOT / "artifacts" / "raw" / "llm_smoke"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_condition(variant: str, condition: str, rounds: int, seed: int, caller, run_id: str | None = None,
                  receiver_caller=None) -> dict:
    """One episode. `caller` plays the sender, and the receiver too unless `receiver_caller` is given."""
    usage, trace = Usage(), []
    bits = balanced_bits(rounds, seed)
    receiver_caller = receiver_caller or caller
    sender = LLMController("sender", condition, caller, usage, trace)
    receiver = LLMController("receiver", condition, receiver_caller, usage, trace)
    cfg = default_config(variant, max_rounds=rounds, track_audit=True)
    run_id = run_id or f"llm-{variant}-{condition}-{seed}"
    ep = run_episode(cfg, {"A": sender}, {"B": receiver}, bits, seed, run_id)

    guesses = []
    history = []
    for i, event in enumerate(ep.receiver_events["B"]):
        history.append(event)
        guesses.append(receiver.guess(ReceiverView(i + 1, event.slot, tuple(history))))

    answered = [(g, b) for g, b in zip(guesses, bits) if g is not None]
    flags = LegalActionMonitor(cfg.schedule, cfg.spec.records_identity).review(ep.audit).flags
    per_round = {p: [e for e in ep.events if e["principal"] == p] for p in ("A", "B")}
    return {
        "run_id": run_id,
        "variant": variant,
        "condition": condition,
        "rounds": rounds,
        "seed": seed,
        "sender_model": getattr(caller, "label", "unknown"),
        "receiver_model": getattr(receiver_caller, "label", "unknown"),
        "correct": sum(g == b for g, b in answered),
        "answered": len(answered),
        "unanswered": len(guesses) - len(answered),
        "sender_holds": sum(1 for e in ep.audit if e.act == "acquire"),
        "receiver_jobs": ep.jobs("B"),
        "receiver_requests": ep.requests("B"),
        "monitor_flags": len(flags),
        "sender_actions": [e["action"] for e in per_round["A"]],
        "receiver_actions": [e["action"] for e in per_round["B"]],
        "receiver_responses": [e["response"] for e in per_round["B"]],
        "calls": usage.calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "no_tool_turns": usage.no_tool_turns,
        "truncated_turns": usage.truncated_turns,
        "retried_turns": usage.retried_turns,
        "refusals": usage.refusals,
        "refusal_categories": usage.refusal_categories,
        "fallback_calls": usage.fallback_calls,
        "fallback_categories": usage.fallback_categories,
        "served_by": usage.served_by,
        "tokens_by_model": usage.tokens_by_model,
        "trace": trace,
        "bits": bits,
        "guesses": guesses,
    }


def episode_cost(row: dict) -> float | None:
    """Every attempt at its own model's rate, declined attempts included (an upper bound)."""
    return cost_of(row["tokens_by_model"])


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually call the API and spend money")
    ap.add_argument("--smoke", action="store_true", help="one 2-round V1 codebook episode, written to artifacts/raw/llm_smoke")
    ap.add_argument("--model", default=PLAN["model"])
    ap.add_argument("--effort", default=PLAN["effort"])
    ap.add_argument("--rounds", type=int, default=PLAN["rounds"])
    ap.add_argument("--seeds", default=",".join(str(s) for s in PLAN["seeds"]))
    ap.add_argument("--variants", default=",".join(PLAN["variants"]))
    ap.add_argument("--conditions", default=",".join(PLAN["conditions"]))
    ap.add_argument("--workers", type=int, default=4, help="episodes run in parallel; each episode is sequential")
    ap.add_argument("--max-usd", type=float, default=30.0, help="stop starting new episodes once estimated spend passes this")
    ap.add_argument("--out", default=None, help="directory for per-episode files")
    ap.add_argument("--force", action="store_true", help="rerun episodes whose files already exist")
    ap.add_argument("--no-fallbacks", action="store_true",
                    help="return refusals as-is rather than re-running on another model")
    args = ap.parse_args()

    if args.smoke:
        args.variants, args.conditions, args.rounds, args.seeds = "V1", "codebook", 2, "1"
    out_dir = Path(args.out) if args.out else (SMOKE_OUT if args.smoke else DEFAULT_OUT)
    variants, conditions = args.variants.split(","), args.conditions.split(",")
    seeds = [int(s) for s in args.seeds.split(",")]
    specs = [(v, c, s) for v in variants for c in conditions for s in seeds]
    calls = len(specs) * args.rounds * CALLS_PER_ROUND

    print(f"plan: {len(specs)} episodes of {args.rounds} rounds, about {calls} model calls (more if turns are retried), "
          f"model {args.model}, effort {args.effort}, fallbacks {'off' if args.no_fallbacks else 'default'}")
    if args.model in PRICES:
        rate_in, rate_out = PRICES[args.model]
        low = calls * (600 * rate_in + 100 * rate_out) / 1_000_000
        high = calls * (1500 * rate_in + 1500 * rate_out) / 1_000_000
        print(f"rough cost estimate: ${low:.2f} to ${high:.2f} (assumed token counts; --smoke measures them)")
    print(f"spending cap: ${args.max_usd:.2f}. Output: {out_dir.relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir}")
    key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"ANTHROPIC_API_KEY is {'set' if key_present else 'not set'} in this environment")
    if not args.yes:
        print("nothing was sent. Pass --yes to run it.")
        return 0
    if not key_present:
        print("refusing to start: set ANTHROPIC_API_KEY in the environment first.", file=sys.stderr)
        return 2

    from veriswarm.llm import AnthropicCaller

    caller = AnthropicCaller(args.model, args.effort, fallbacks=not args.no_fallbacks)
    meta = {"model": args.model, "effort": args.effort, "fallbacks": not args.no_fallbacks,
            "sdk": f"anthropic {caller.sdk_version}"}

    todo = []
    for variant, condition, seed in specs:
        run_id = run_id_for(args.model, args.effort, variant, condition, args.rounds, seed)
        path = out_dir / f"{run_id}.json"
        if path.exists() and not args.force:
            print(f"kept {path.name} (already complete)")
            continue
        todo.append((variant, condition, seed, run_id, path))

    spent = 0.0
    lock = threading.Lock()
    stop = threading.Event()
    failures = []

    def one(variant, condition, seed, run_id, path):
        if stop.is_set():
            return None
        started = now()
        row = run_condition(variant, condition, args.rounds, seed, caller, run_id=run_id)
        row.update(meta, started_utc=started, finished_utc=now(), cost_usd=episode_cost(row))
        write_atomic(path, row)
        return row

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(one, *item): item for item in todo}
        for future in as_completed(futures):
            variant, condition, seed, run_id, _ = futures[future]
            if future.cancelled():
                continue
            try:
                row = future.result()
            except Exception as exc:  # report the failure, stop scheduling more, keep finished files
                failures.append(f"{run_id}: {type(exc).__name__}: {exc}")
                print(f"FAILED {run_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
                stop.set()
                for f in futures:
                    f.cancel()
                continue
            if row is None or future.cancelled():
                continue
            with lock:
                spent += row["cost_usd"] or 0.0
                if spent > args.max_usd and not stop.is_set():
                    print(f"spending cap reached (${spent:.2f}); no new episodes will start", file=sys.stderr)
                    stop.set()
                    for f in futures:
                        f.cancel()
            per_call = (row["cost_usd"] or 0.0) / max(1, row["calls"])
            print(f"{variant} {condition} seed {seed}: {row['correct']}/{row['answered']} correct, "
                  f"{row['unanswered']} unanswered, {row['no_tool_turns']} no-tool turns, {row['retried_turns']} retried, "
                  f"{row['refusals']} refusals {row['refusal_categories'] or ''}, {row['fallback_calls']} fallback calls "
                  f"{row['fallback_categories'] or ''}, "
                  f"served by {row['served_by']}, {row['calls']} calls, ${row['cost_usd'] or 0:.3f} (${per_call:.4f}/call)",
                  flush=True)

    print(f"estimated spend this invocation: ${spent:.2f}")
    if args.smoke and spent:
        full_calls = len(PLAN["variants"]) * len(PLAN["conditions"]) * len(PLAN["seeds"]) * PLAN["rounds"] * CALLS_PER_ROUND
        done = [json.loads(p.read_text(encoding="utf-8")) for p in out_dir.glob("*.json")]
        per_call = sum(r["cost_usd"] or 0 for r in done) / max(1, sum(r["calls"] for r in done))
        print(f"measured ${per_call:.4f} per call; the pre-registered run of about {full_calls} calls projects to "
              f"${per_call * full_calls:.2f} (receiver prompts grow with history, so treat this as a floor)")
    if failures:
        print(f"{len(failures)} episode(s) failed; finished episodes were kept. Rerun to resume.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

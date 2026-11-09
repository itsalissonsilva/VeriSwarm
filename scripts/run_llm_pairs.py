"""Optional, separate, and paid: mixed pairs of models in the sender and receiver seats.

Usage:
  python scripts/run_llm_pairs.py                 # prints the plan and spends nothing
  python scripts/run_llm_pairs.py --smoke --yes   # one 2-round episode, Claude sender to open-model receiver
  python scripts/run_llm_pairs.py --yes           # the pre-registered pair run (docs/LLM_PREREGISTRATION.md, part 2)
  python scripts/run_llm_pairs.py --plan qwen --yes   # part 3, which adds a Qwen seat

Seats are defined in src/veriswarm/llm.py (SEATS, PAIR_PLANS). The Claude seat reads
ANTHROPIC_API_KEY; the open-model seat uses the saved Hugging Face login or HF_TOKEN. This
script never prints, stores or passes either. Each episode is written as soon as it
finishes, and a rerun skips finished episodes unless --force is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_llm import episode_cost, now, run_condition, write_atomic  # noqa: E402
from veriswarm.llm import PAIR_PLANS, make_caller, pair_run_id, plan_seats, seat_label  # noqa: E402

RAW = ROOT / "artifacts" / "raw"
# plan name -> (episode directory, smoke directory, smoke cell)
LAYOUT = {
    "pairs": (RAW / "llm_pairs", RAW / "llm_pairs_smoke", ["V1", "codebook", "claude", "open"]),
    "qwen": (RAW / "llm_pairs_qwen", RAW / "llm_pairs_qwen_smoke", ["V1", "codebook", "claude", "qwen"]),
    "llama": (RAW / "llm_pairs_llama", RAW / "llm_pairs_llama_smoke", ["V1", "codebook", "claude", "llama"]),
    "grid": (RAW / "llm_pairs_grid", RAW / "llm_pairs_grid_smoke", ["V1", "codebook", "claude", "llama"]),
}
CALLS_PER_ROUND = 3


def hf_token_present() -> bool:
    try:
        from huggingface_hub import get_token
    except ImportError:
        return bool(os.environ.get("HF_TOKEN"))
    return bool(get_token())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="actually call the APIs and spend money")
    ap.add_argument("--plan", choices=sorted(PAIR_PLANS), default="pairs",
                    help="pairs: part 2 (Claude and GLM); qwen: part 3 (adds Qwen); llama: part 4 (adds Llama); "
                         "grid: part 5 (reruns Llama with a required tool call, adds the missing private-lock pairings)")
    ap.add_argument("--smoke", action="store_true", help="one 2-round V1 codebook episode, Claude sender to an open receiver")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-usd", type=float, default=20.0, help="stop starting new episodes once estimated spend passes this")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    plan = PAIR_PLANS[args.plan]
    SEATS = plan_seats(plan)  # noqa: N806 - the shared seats with this plan's overrides
    episodes_dir, smoke_dir, smoke_cell = LAYOUT[args.plan]
    if args.smoke:
        cells, rounds, seeds, out_dir = [smoke_cell], 2, [1], smoke_dir
    else:
        cells, rounds, seeds, out_dir = plan["cells"], plan["rounds"], plan["seeds"], episodes_dir
    specs = [(v, c, s, r, seed) for v, c, s, r in cells for seed in seeds]
    print(f"plan {args.plan}: {len(specs)} episodes of {rounds} rounds, about {len(specs) * rounds * CALLS_PER_ROUND} calls before retries")
    for name in sorted({seat for cell in cells for seat in cell[2:]}):
        print(f"  seat {name}: {seat_label(SEATS[name])} ({SEATS[name]['backend']})")
    for v, c, s, r in cells:
        print(f"  cell {v} {c}: sender {s}, receiver {r}")
    anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    hf_token = hf_token_present()
    print(f"ANTHROPIC_API_KEY {'set' if anthropic_key else 'not set'}; Hugging Face token {'found' if hf_token else 'not found'}")
    print(f"spending cap: ${args.max_usd:.2f}. Output: {out_dir.relative_to(ROOT)}")
    if not args.yes:
        print("nothing was sent. Pass --yes to run it.")
        return 0
    needed = {SEATS[seat]["backend"] for _, _, s, r, _ in specs for seat in (s, r)}
    if ("anthropic" in needed and not anthropic_key) or ("hf" in needed and not hf_token):
        print("refusing to start: a credential for one of the seats is missing.", file=sys.stderr)
        return 2

    todo = []
    # Longest episodes first: those with a GLM or Qwen seat take up to an hour, Llama and Claude pairs minutes.
    specs.sort(key=lambda spec: -sum(seat in ("open", "qwen") for seat in spec[2:4]))
    for variant, condition, sender, receiver, seed in specs:
        run_id = pair_run_id(variant, condition, sender, receiver, rounds, seed)
        path = out_dir / f"{run_id}.json"
        if path.exists() and not args.force:
            print(f"kept {path.name} (already complete)")
            continue
        todo.append((variant, condition, sender, receiver, seed, run_id, path))

    spent = 0.0
    lock = threading.Lock()
    stop = threading.Event()
    failures = []

    def one(variant, condition, sender, receiver, seed, run_id, path):
        if stop.is_set():
            return None
        send_caller, recv_caller = make_caller(SEATS[sender]), make_caller(SEATS[receiver])
        started = now()
        row = run_condition(variant, condition, rounds, seed, send_caller, run_id=run_id, receiver_caller=recv_caller)
        sdks = sorted({f"{type(c).__name__} {c.sdk_version}" for c in (send_caller, recv_caller)})
        row.update(sender_seat=sender, receiver_seat=receiver, seats={sender: SEATS[sender], receiver: SEATS[receiver]},
                   sdk=sdks, started_utc=started, finished_utc=now(), cost_usd=episode_cost(row))
        write_atomic(path, row)
        return row

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(one, *item): item for item in todo}
        for future in as_completed(futures):
            run_id = futures[future][5]
            if future.cancelled():
                continue
            try:
                row = future.result()
            except Exception as exc:  # report, stop scheduling more, keep finished files
                failures.append(run_id)
                print(f"FAILED {run_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
                stop.set()
                for f in futures:
                    f.cancel()
                continue
            if row is None:
                continue
            with lock:
                spent += row["cost_usd"] or 0.0
                if spent > args.max_usd and not stop.is_set():
                    print(f"spending cap reached (${spent:.2f}); no new episodes will start", file=sys.stderr)
                    stop.set()
                    for f in futures:
                        f.cancel()
            print(f"{row['variant']} {row['condition']} {row['sender_seat']}->{row['receiver_seat']} seed {row['seed']}: "
                  f"{row['correct']}/{row['answered']} correct, {row['unanswered']} unanswered, "
                  f"{row['no_tool_turns']} no-tool turns, {row['retried_turns']} retried, {row['refusals']} refusals, "
                  f"sender {dict((a, row['sender_actions'].count(a)) for a in set(row['sender_actions']))}, "
                  f"served by {row['served_by']}, ${row['cost_usd'] or 0:.3f}", flush=True)

    print(f"estimated spend this invocation: ${spent:.2f}")
    if failures:
        print(f"{len(failures)} episode(s) failed; finished episodes were kept. Rerun to resume.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

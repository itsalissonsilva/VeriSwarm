"""Summarizes recorded LLM episodes. Makes no API call, so it can run anywhere.

Usage: python scripts/summarize_llm.py
Reads artifacts/raw/llm/*.json and writes artifacts/summary/llm.json, and reads
artifacts/raw/llm_pairs/*.json and writes artifacts/summary/llm_pairs.json. With no
recorded episodes a summary has status "not_run". The output depends only on the recorded files, so a
clean-room copy given the same files reproduces it byte for byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.llm import PAIR_PLANS, PLAN, PRICES  # noqa: E402
from veriswarm.llm_analysis import summarize, summarize_pairs, summarize_probe  # noqa: E402

RAW = ROOT / "artifacts" / "raw" / "llm"
PROBE = ROOT / "artifacts" / "raw" / "llm_refusal_probe.json"
OUT = ROOT / "artifacts" / "summary" / "llm.json"
# plan name -> (episode directory, summary file)
PAIR_FILES = {
    "pairs": (ROOT / "artifacts" / "raw" / "llm_pairs", ROOT / "artifacts" / "summary" / "llm_pairs.json"),
    "qwen": (ROOT / "artifacts" / "raw" / "llm_pairs_qwen", ROOT / "artifacts" / "summary" / "llm_pairs_qwen.json"),
    "llama": (ROOT / "artifacts" / "raw" / "llm_pairs_llama", ROOT / "artifacts" / "summary" / "llm_pairs_llama.json"),
    "grid": (ROOT / "artifacts" / "raw" / "llm_pairs_grid", ROOT / "artifacts" / "summary" / "llm_pairs_grid.json"),
}


def write_pairs(name: str) -> None:
    raw, out = PAIR_FILES[name]
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(raw.glob("*.json"))] if raw.exists() else []
    pairs = summarize_pairs(rows, PAIR_PLANS[name], PRICES)
    out.write_text(json.dumps(pairs, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"pair summary ({name}): status {pairs['status']}, {len(rows)} recorded episode file(s)")
    for key, cell in pairs.get("cells", {}).items():
        lo, hi = cell["accuracy_answered_ci95"]
        print(f"  {key}: {cell['correct']}/{cell['answered']} correct (95% CI {lo:.2f}-{hi:.2f}, "
              f"p above {cell['p_above_chance']:.2g}, p below {cell['p_below_chance']:.2g}), "
              f"sender by bit {cell['sender_actions_by_bit']}, receiver {cell['receiver_actions']}, "
              f"guesses by bit {cell['guesses_by_bit']}, busy matched bit {cell['busy_matches_bit']}/{cell['receiver_try_job_rounds']}, "
              f"{cell['refusals']} refusals, {cell['no_tool_turns']} no-tool turns, {cell['retried_turns']} retried, "
              f"{cell['unanswered']} unanswered, {cell['monitor_flags']} flags")
    for p in pairs.get("predictions", []):
        print(f"  {p['id']} {'met' if p['met'] else 'NOT met' if p['met'] is False else 'not evaluated'}: {p['prediction']}")
    if pairs.get("cells"):
        print(f"  cost, upper bound: ${pairs['cost_usd_upper_bound']:.2f} over {pairs['calls']} calls")
    if pairs.get("missing"):
        print(f"  missing: {', '.join(pairs['missing'])}")


def main() -> int:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RAW.glob("*.json"))] if RAW.exists() else []
    summary = summarize(rows, PLAN, PRICES)
    if PROBE.exists():
        summary["opus55_replay"] = summarize_probe(json.loads(PROBE.read_text(encoding="utf-8")), PRICES)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"llm summary: status {summary['status']}, {len(rows)} recorded episode file(s)")
    for key, cell in summary.get("cells", {}).items():
        lo, hi = cell["accuracy_answered_ci95"]
        print(f"  {key}: {cell['correct']}/{cell['answered']} correct (95% CI {lo:.2f}-{hi:.2f}), "
              f"sender followed code {cell['sender_followed_code']}/{cell['rounds']}, "
              f"busy matched bit {cell['busy_matches_bit']}/{cell['receiver_try_job_rounds']}, "
              f"{cell['refusals']} refusals, {cell['no_tool_turns']} no-tool turns, {cell['monitor_flags']} monitor flags, "
              f"{cell['fallback_calls']}/{cell['calls']} calls answered by a fallback {cell['fallback_categories'] or ''}, "
              f"served by {cell['served_by']}")
    if summary.get("cells"):
        print(f"  cost, upper bound: ${summary['cost_usd_upper_bound']:.2f} over {summary['calls']} calls")
    for p in summary.get("predictions", []):
        print(f"  {p['id']} {'met' if p['met'] else 'NOT met' if p['met'] is False else 'not evaluated'}: {p['prediction']}")
    replay = summary.get("opus55_replay")
    if replay:
        print(f"  replay to {replay['model']} with fallbacks off: {replay['declined']}/{replay['distinct_prompts']} "
              f"distinct prompts declined {replay['categories']}")
        for key, g in sorted(replay["groups"].items()):
            print(f"    {key}: {g['declined']}/{g['distinct_prompts']} declined, statuses {g['statuses']}, answers {g['answers']}")
    if summary.get("missing"):
        print(f"  missing: {', '.join(summary['missing'])}")
    for name in PAIR_FILES:
        write_pairs(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

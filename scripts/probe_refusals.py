"""Replays every prompt of the recorded LLM run to one model with fallbacks off.

Usage:
  python scripts/probe_refusals.py          # prints the plan and spends nothing
  python scripts/probe_refusals.py --yes    # sends each distinct prompt once

Exploratory, added in amendment A1 of docs/LLM_PREREGISTRATION.md. In the main run the
server-side fallback answers declined calls, and sticky routing hides which calls the first
model would have declined. This sends the identical system prompt, tool and user message
again, once each, with no fallback, and records what comes back. Nothing here feeds the
gateway: it measures declines, it does not run episodes.

Writes artifacts/raw/llm_refusal_probe.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.llm import (  # noqa: E402
    GUESS_TOOL, PLAN, PROMPTS, RECEIVER_TOOL_ACTIONS, SENDER_TOOL_ACTIONS, attempts_of, cost_of, gateway_tool,
    parse_response, run_id_for,
)

RAW = ROOT / "artifacts" / "raw" / "llm"
OUT = ROOT / "artifacts" / "raw" / "llm_refusal_probe.json"


def tool_for(role: str, name: str) -> dict:
    if name == GUESS_TOOL["name"]:
        return GUESS_TOOL
    return gateway_tool(SENDER_TOOL_ACTIONS if role == "sender" else RECEIVER_TOOL_ACTIONS)


def distinct_prompts() -> list[dict]:
    """Each distinct (role, condition, tool, prompt) from the planned episodes, first attempts only."""
    seen: dict = {}
    for v in PLAN["variants"]:
        for c in PLAN["conditions"]:
            for s in PLAN["seeds"]:
                path = RAW / f"{run_id_for(PLAN['model'], PLAN['effort'], v, c, PLAN['rounds'], s)}.json"
                if not path.exists():
                    raise SystemExit(f"missing episode {path.name}: run scripts/run_llm.py --yes first")
                for t in json.loads(path.read_text(encoding="utf-8"))["trace"]:
                    if t["attempt"] != 0:
                        continue
                    key = (t["role"], t["condition"], t["tool"], t["prompt"])
                    if key not in seen:
                        seen[key] = {"role": t["role"], "condition": t["condition"], "tool": t["tool"],
                                     "prompt": t["prompt"], "variants": [], "occurrences": 0}
                    entry = seen[key]
                    entry["occurrences"] += 1
                    if v not in entry["variants"]:
                        entry["variants"].append(v)
    return sorted(seen.values(), key=lambda e: (e["condition"], e["role"], e["tool"], e["prompt"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--model", default=PLAN["model"])
    ap.add_argument("--effort", default=PLAN["effort"])
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    prompts = distinct_prompts()
    print(f"plan: {len(prompts)} distinct prompts to {args.model}, effort {args.effort}, fallbacks off, one call each")
    key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"ANTHROPIC_API_KEY is {'set' if key_present else 'not set'} in this environment")
    if not args.yes:
        print("nothing was sent. Pass --yes to run it.")
        return 0
    if not key_present:
        print("refusing to start: set ANTHROPIC_API_KEY in the environment first.", file=sys.stderr)
        return 2

    from veriswarm.llm import AnthropicCaller

    caller = AnthropicCaller(args.model, args.effort, fallbacks=False)

    def probe(entry: dict) -> dict:
        tool = tool_for(entry["role"], entry["tool"])
        response = caller(PROMPTS[(entry["role"], entry["condition"])], entry["prompt"], tool)
        field = "bit" if tool is GUESS_TOOL else "action"
        value, status, category, text = parse_response(response, tool["name"], field)
        return {**entry, "prompt_sha256": hashlib.sha256(entry["prompt"].encode()).hexdigest(),
                "status": status, "stop_reason": response.stop_reason, "category": category, "value": value,
                "text": text[:300], "served_by": getattr(response, "model", "unknown"), "attempts": attempts_of(response)}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(probe, prompts))

    tokens: dict = {}
    for r in rows:
        for a in r["attempts"]:
            tally = tokens.setdefault(a["model"], [0, 0])
            tally[0] += a["input_tokens"]
            tally[1] += a["output_tokens"]
    payload = {"model": args.model, "effort": args.effort, "fallbacks": False, "sdk": f"anthropic {caller.sdk_version}",
               "tokens_by_model": tokens, "cost_usd_upper_bound": cost_of(tokens), "rows": rows}
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    declined = sum(r["status"] == "refusal" for r in rows)
    print(f"{declined} of {len(rows)} distinct prompts declined; cost upper bound ${payload['cost_usd_upper_bound'] or 0:.2f}")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

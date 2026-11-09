"""Builds the interactive explainer page from the artifacts. No number or quote is typed here.

Usage: python scripts/make_explainer.py
Reads artifacts/summary/*.json and a few recorded agent traces, fills docs/explainer/template.html,
and writes docs/explainer/index.html, a single self-contained page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "artifacts" / "summary"
RAW = ROOT / "artifacts" / "raw"
TEMPLATE = ROOT / "docs" / "explainer" / "template.html"
OUT = ROOT / "docs" / "explainer" / "index.html"

FAMILIES = ["claude", "open", "qwen", "llama"]
NAMES = {"claude": "Claude", "open": "GLM", "qwen": "Qwen", "llama": "Llama"}
MODELS = {"claude": "Claude Opus 4.8", "open": "GLM-5.3", "qwen": "Qwen3.8", "llama": "Llama 4 Scout"}

# (model key, episode file, role, tool, which attempt-0 occurrence, how many sentences)
QUOTES = [
    ("claude", "llm/llm-claude-opus-5-5-low-V1-no_hint-r20-s40000.json", "sender", "gateway_call", 0, 2),
    ("open", "llm_pairs/pair-V1-no_hint-open-to-claude-r20-s40000.json", "sender", "gateway_call", 0, 1),
    ("qwen", "llm_pairs_qwen/pair-V1-no_hint-qwen-to-claude-r20-s40000.json", "sender", "gateway_call", 0, 1),
    ("qwen", "llm_pairs_qwen/pair-V1-no_hint-claude-to-qwen-r20-s40000.json", "receiver", "submit_guess", 0, 2),
]


def load(name: str):
    return json.loads((SUMMARY / f"{name}.json").read_text(encoding="utf-8"))


def sentences(text: str, n: int) -> str:
    text = re.sub(r"[`*]", "", text).replace("\n", " ")
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n]).strip()


def quote(model: str, path: str, role: str, tool: str, index: int, n: int) -> dict:
    trace = json.loads((RAW / path).read_text(encoding="utf-8"))["trace"]
    hits = [t for t in trace if t["role"] == role and t["tool"] == tool and t["attempt"] == 0 and t["text"]]
    return {"model": MODELS[model], "role": role, "text": sentences(hits[index]["text"], n), "source": f"artifacts/raw/{path}"}


def cell(c: dict | None) -> dict | None:
    if c is None:
        return None
    return {"c": c["correct"], "a": c["answered"], "u": c["unanswered"], "r": c["rounds"],
            "busy": c["receiver_busy"], "p": c["p_above_chance"], "busyBit": c.get("busy_matches_bit"),
            "g1": sum(g.get("1", 0) for g in c["guesses_by_bit"].values()) if "guesses_by_bit" in c else None}


def core_facts(core: dict) -> dict:
    if core["status"] != "checked":
        raise SystemExit("the Lean core checks did not pass; the page would describe them wrongly")
    return {"episodes": sum(e["episodes"] for e in core["exhaustive"]),
            "llmReplies": core["llm_replay"]["receiver_replies"],
            "llmEpisodes": core["llm_replay"]["episodes"],
            "requests": core["core_requests_total"]}


LEAN_CLAIMED = ["VeriSwarm.core_noninterference", "VeriSwarm.noninterference",
                "VeriSwarm.Mutants.sharedQuota_leaks", "VeriSwarm.Mutants.exposedCounter_leaks",
                "VeriSwarm.Mutants.aliasedBacking_leaks", "VeriSwarm.Mutants.workConserving_leaks"]


def check_lean(lean: dict) -> None:
    """The proof card says: no gaps, standard axioms only, an adaptive-receiver theorem, a leak per break."""
    missing = [t for t in LEAN_CLAIMED if t not in lean["theorems"]]
    if lean["status"] != "checked" or lean["non_standard_axioms"] or lean["sorry_or_axiom_declarations"] or missing:
        raise SystemExit(f"the Lean check does not back the proof card (missing: {missing})")


def noise_facts(noise: dict, p: float = 0.9, reps: str = "45") -> dict:
    row = next(r for r in noise["levels"] if abs(r["noise_p"] - p) < 1e-9)
    return {"p": p, "completion": row["receiver_completion"], "reps": int(reps),
            "accuracy": row["repetition"][reps]["accuracy"]}


def main() -> int:
    tx = load("transmission")
    benign = load("benign")["W2"]
    monitors = load("monitors")
    llm = load("llm")
    # Later parts replace earlier cells: part 5 reran every Llama cell of part 4.
    parts = [load(n) for n in ("llm_pairs", "llm_pairs_qwen", "llm_pairs_llama", "llm_pairs_grid")]
    design = load("design_space")
    check_lean(load("lean_check"))

    cells: dict = {}
    for key, c in llm["cells"].items():
        v, cond = key.split("/")
        cells[f"{v}/{cond}/claude/claude"] = cell(c)
    for part in parts:
        for key, c in part["cells"].items():
            v, cond, seats = key.split("/")
            s, r = seats.split("->")
            cells[f"{v}/{cond}/{s}/{r}"] = cell(c)
    predictions = llm["predictions"] + [p for part in parts for p in part["predictions"]]

    designs = {}
    for row in design["rows"]:
        d = row["design"]
        # The page says every closed design is one of two cases; stop if the data disagrees.
        own = d["backing"] == "private" and not d["shared_quota"]
        cleared = d["schedule"] == "AMBM"
        ideal = not d["exposed_counter"] and not d["work_conserving"] and (own or cleared)
        if row["leaks"] == ideal:
            raise SystemExit(f"design {row['description']} contradicts the two ideal cases on the page")
        key = "|".join([d["backing"], str(int(d["shared_quota"])), str(int(d["exposed_counter"])),
                        str(int(d["work_conserving"])), str(int(d["noisy"])), d["schedule"]])
        leak = row["leakage"]
        designs[key] = {
            "leaks": row["leaks"], "named": row["named_variant"],
            "bits": leak.get("bits_per_round", leak.get("capacity_bits_per_round_at_p_0_5")),
            "witness": row["witness"], "blocked": row["receiver_can_be_blocked"],
        }

    data = {
        "tx": {"correct": tx["V1"]["correct"], "total": tx["V1"]["total"], "auditEvents": tx["V1"]["audit_events"],
               "flags": tx["V1"]["legal_monitor_flags"]},
        "utility": {"shared": benign["V1"]["B_completed"], "private": benign["V2"]["B_completed"],
                    "flushed": benign["V9"]["B_completed"]},
        "monitors": {"naiveFlagged": monitors["baseline_contended_vs_codebook"]["attack_flagged"],
                     "mimicFlagged": monitors["baseline_contended_vs_mimicry"]["attack_flagged"],
                     "runs": monitors["baseline_contended_vs_mimicry"]["n_each"],
                     "mimicAuc": monitors["baseline_contended_vs_mimicry"]["auc_peak_rate"],
                     "mimicDecoded": monitors["mimicry_decode"]["correct"],
                     "mimicTotal": monitors["mimicry_decode"]["total"]},
        "agents": {"families": FAMILIES, "names": NAMES, "models": MODELS, "cells": cells,
                   "declined": llm["opus55_replay"]["declined"],
                   "distinct": llm["opus55_replay"]["distinct_prompts"],
                   "predMet": sum(1 for p in predictions if p["met"]), "predTotal": len(predictions),
                   "predMissed": [{"id": p["id"], "text": p["prediction"]} for p in predictions if p["met"] is False],
                   # Part 5 reran every Llama cell of part 4 with a tool call required.
                   "llamaRerun": parts[-1]["status"] != "not_run"},
        "designs": designs,
        "noise": noise_facts(load("noise")),
        "core": core_facts(load("lean_core")),
        "quotes": [quote(*q) for q in QUOTES],
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(html) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

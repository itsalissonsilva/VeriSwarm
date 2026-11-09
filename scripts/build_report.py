"""Builds the report from artifacts, so a number can't drift from its evidence.

Usage: python scripts/build_report.py [--no-pdf]

1. Reads artifacts/summary/*.json and fails if any claim the prose relies on no longer holds.
2. Writes report/generated/{values,table_*,abstract,timed_out}.tex and submission/abstract.txt.
3. Compiles report/report.tex with Tectonic to report/report.pdf.
4. Checks the abstract's word count and the main-text page count.

Writes artifacts/summary/report_check.json.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.config import VARIANTS  # noqa: E402

SUMMARY = ROOT / "artifacts" / "summary"
REPORT = ROOT / "report"
GEN = REPORT / "generated"
SUBMISSION = ROOT / "submission"

MAX_MAIN_PAGES = 8
PLACEHOLDER = "[Author name]"


class ClaimFailed(RuntimeError):
    pass


def load(name: str):
    return json.loads((SUMMARY / f"{name}.json").read_text(encoding="utf-8"))


def require(condition: bool, claim: str) -> None:
    """A statement the prose makes. If the data stops supporting it, the build stops."""
    if not condition:
        raise ClaimFailed(f"the report claims this but the artifacts do not support it: {claim}")


def tex_escape(text: str) -> str:
    return text.replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")


def fmt_count(value: int) -> str:
    return f"{value:,}".replace(",", "{,}")


class Data:
    def __init__(self) -> None:
        tlc = load("tlc_results")
        self.tlc = {r["job_id"]: r for r in tlc["results"]}
        self.timed_out = tlc["timed_out_and_replaced"]
        self.sweep = load("tlc_schedule_sweep")["results"]
        self.replay = load("replay_results")
        self.corr = load("correspondence")
        self.lean = load("lean_check")
        self.tx = load("transmission")
        self.exact = load("exact_leakage")
        self.benign = load("benign")
        self.noise = load("noise")
        self.monitors = load("monitors")
        self.broadcast = load("broadcast")
        self.family = load("schedule_family")
        llm_path = SUMMARY / "llm.json"
        self.llm = json.loads(llm_path.read_text(encoding="utf-8")) if llm_path.exists() else {"status": "not_run"}
        self.core = load("lean_core")
        design_path = SUMMARY / "design_space.json"
        self.design = json.loads(design_path.read_text(encoding="utf-8")) if design_path.exists() else None
        self.parts = {}
        # Order matters: later parts replace earlier cells (part 5 reran every Llama cell of part 4).
        for part, name in (("pairs", "llm_pairs"), ("qwen", "llm_pairs_qwen"), ("llama", "llm_pairs_llama"),
                           ("grid", "llm_pairs_grid")):
            path = SUMMARY / f"{name}.json"
            self.parts[part] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "not_run"}
        self.pairs = self.parts["pairs"]
        test_path = SUMMARY / "pytest.json"
        self.tests = json.loads(test_path.read_text(encoding="utf-8")) if test_path.exists() else None
        # Absent while a clean-room copy builds its own report, since that run creates it afterwards.
        clean_path = SUMMARY / "clean_room.json"
        self.clean_room = json.loads(clean_path.read_text(encoding="utf-8")) if clean_path.exists() else None


def check_claims(d: Data) -> None:
    """Everything the report states in words, tested against the data."""
    tlc = d.tlc
    require(all(r["as_expected"] for r in tlc.values()), "all matrix predictions were met")
    require(all(r["as_expected"] for r in d.sweep), "all sweep predictions were met")
    require(all(r["status"] != "tool_error" for r in tlc.values()), "no tool errors in the matrix")

    for v in ("V0", "V1", "V4", "V5", "V6", "V7", "V8"):
        for rounds in (1, 2):
            r = tlc[f"pair_{v}_R{rounds}"]
            require(r["status"] == "violated" and r["violated_property"] == "ReceiverEquivalent", f"{v} leaks at R={rounds}")
    for v in ("V2", "V3", "V9"):
        for rounds in (1, 2, 3):
            require(tlc[f"pair_{v}_R{rounds}"]["status"] == "checked", f"{v} holds at R={rounds}")
        require(tlc[f"unbounded_{v}"]["status"] == "checked", f"{v} holds at every horizon")
    for v in VARIANTS:
        require(tlc[f"single_{v}_R2"]["status"] == "checked", f"{v} passes every single-execution check")

    for job in ("schedules_V2_all5_unbounded", "strongreceiver_V2_R2", "strongreceiver_V2_unbounded",
                "three_senders_V2_R1", "three_senders_V2_unbounded", "three_receivers_V2_R2",
                "three_receivers_V2_unbounded"):
        require(tlc[job]["status"] == "checked", f"{job} holds")
    for v in ("V6", "V7"):
        require(tlc[f"weakobs_{v}_R2"]["status"] == "checked", f"{v} passes under the weakened observation")
    require(tlc["localrespect_V5_R1"]["status"] == "checked", "V5 passes local respect")
    require(tlc["pair_V5_R1"]["status"] == "violated", "V5 still leaks")
    for v in ("V3", "V9"):
        require(tlc[f"localrespect_{v}_R1"]["status"] == "violated", f"{v} fails local respect")
    for job in ("three_senders_V2_R1", "three_receivers_V2_R2", "strongreceiver_V2_all4_unbounded", "midrelease_V2_ABABM_R1"):
        require(tlc[job]["status"] == "checked", f"{job} holds")
    for v in ("V2", "V6", "V7", "V9"):
        require(tlc[f"utility_{v}_R2"]["status"] == "checked", f"no sender can block B under {v}")
    for v in ("V0", "V1", "V3", "V4", "V5", "V8"):
        require(tlc[f"utility_{v}_R2"]["status"] == "violated", f"a sender or the gateway can block B under {v}")
    bursts = (d.tx["V8"]["correct"] / d.tx["V8"]["total"] - 0.5) * 2
    require(1 - bursts < 0.5, "the noise fired on slightly under half of the zero-bit rounds in this sample")
    for probe in ("ProbeSendersNeverDiffer", "ProbeBackingNeverDiffers", "ProbeNoReceiverJob"):
        require(tlc[f"probe_pair_{probe}_V2"]["status"] == "violated", f"non-vacuity probe {probe} fired")

    # What TLC actually explored, read from its own log, not from what was configured.
    for job, count in (("schedules_V2_all5_unbounded", 363), ("schedules_V3_all5_unbounded", 363),
                       ("strongreceiver_V2_all4_unbounded", 120)):
        log = (ROOT / tlc[job]["log_path"]).read_text(encoding="utf-8")
        require(f"Finished computing initial states: {count} distinct states" in log.replace(",", ""),
                f"{job} started from exactly {count} schedules")
    log = (ROOT / tlc["schedules_V2_all3_R2"]["log_path"]).read_text(encoding="utf-8")
    # 39 schedules, times 4 two-round secrets for each of the two copies.
    require(f"Finished computing initial states: {39 * 4 * 4} distinct states" in log.replace(",", ""),
            "schedules_V2_all3_R2 started from all schedules and secrets")

    require(d.replay["failures"] == [] and d.replay["witnesses"] > 0, "all witnesses replay exactly")
    witness = next(r for r in d.replay["rows"] if r["job_id"] == "codebook_V1_R1")
    require(witness["status"] == "replayed_exactly", "the V1 witness replays exactly")
    require(sorted([witness["left_secret"], witness["right_secret"]]) == [[0], [1]], "the V1 witness pairs bit 0 with bit 1")

    require(d.corr["cases"] == d.corr["matching"] and d.corr["cases"] > 0, "correspondence matched on every configuration")
    require(d.lean["status"] == "checked" and not d.lean["sorry_or_axiom_declarations"], "Lean checked with no sorry")
    require(d.lean["theorems"]["VeriSwarm.noninterference"] == ["propext"], "noninterference depends only on propext")

    for v in ("V0", "V1", "V4", "V5", "V6", "V7"):
        require(d.tx[v]["correct"] == d.tx[v]["total"], f"{v} decodes every bit")
        require(abs(d.exact[v]["capacity_per_round"] - 1.0) < 1e-9, f"{v} has capacity 1")
        require(d.exact[v]["max_distinct_histories"]["6"] == 64, f"{v} has 2^R histories")
    for v in ("V2", "V3", "V9"):
        require(d.tx[v]["correct"] == d.tx[v]["total"] // 2, f"{v} sits at exactly chance")
        require(d.exact[v]["capacity_per_round"] == 0.0, f"{v} has capacity 0")
        require(d.exact[v]["max_distinct_histories"]["6"] == 1, f"{v} has one history")
    for v in VARIANTS:
        require(d.tx[v]["legal_monitor_flags"] == 0 and d.tx[v]["audit_gapless"], f"{v} attack logs are gapless and unflagged")
    lo, hi = d.tx["V8"]["mi_group_bootstrap_95"]
    exact_mi = d.tx["V8"]["mi_exact_uniform_codebook_bits"]
    require(lo <= exact_mi <= hi, "V8's bootstrap interval for measured information covers the exact value")
    require(exact_mi < d.exact["V8"]["capacity_per_round"], "the uniform code's information is below capacity")
    for v in ("V0", "V1", "V4", "V5", "V6", "V7"):
        require(abs(d.tx[v]["mi_plugin_bits"] - 1.0) < 1e-9 and abs(d.tx[v]["mi_exact_uniform_codebook_bits"] - 1.0) < 1e-9, f"{v} carries 1 bit under the uniform code")
    for v in ("V2", "V3", "V9"):
        require(d.tx[v]["mi_plugin_bits"] == 0.0 and d.tx[v]["mi_exact_uniform_codebook_bits"] == 0.0, f"{v} carries 0 bits under the uniform code")

    for key in ("W1", "W2"):
        require(d.benign[key]["V2"]["B_completed"] == 1000, f"V2 completes all jobs under {key}")
        require(d.benign[key]["V3"]["B_completed"] == 0, f"V3 completes none under {key}")
        require(d.benign[key]["V9"]["B_completed"] == 1000, f"V9 completes all jobs under {key}")
    require(d.benign["W2"]["V9"]["slots_per_round"] == 4 and d.benign["W2"]["V2"]["slots_per_round"] == 3, "V9 costs one extra slot per round")

    p9 = next(r for r in d.noise["levels"] if r["noise_p"] == 0.9)
    require(0.08 < p9["receiver_completion"] < 0.13, "at p=0.9 B loses about 90% of its jobs")
    require(p9["repetition"]["45"]["accuracy"] > 0.9, "repetition recovers the bit at p=0.9")
    for row in d.noise["levels"]:
        require(abs(row["capacity_closed_form"] - row["capacity_from_semantics"]) < 1e-5, "capacity from semantics matches closed form")
        if 0 < row["noise_p"] < 1:
            require(abs(row["capacity_closed_form"] - row["capacity_blahut_arimoto"]) < 1e-5, "capacity matches Blahut-Arimoto")
    require(d.noise["reference"]["V2"]["capacity"] == 0.0, "V2 reaches zero capacity")

    m = d.monitors
    require(m["legal_positive_control"]["flags"] == m["legal_positive_control"]["illegal_requests"], "monitor flags every illegal request")
    for key in ("baseline_atomic_vs_codebook", "baseline_contended_vs_codebook"):
        require(m[key]["attack_flagged"] == m[key]["n_each"], f"rate monitor flags the plain code ({key})")
    mimic = m["baseline_contended_vs_mimicry"]
    require(mimic["legal_flags_on_attack"] == 0, "legal monitor raises no flag on mimicry")
    require(m["baseline_atomic_vs_codebook"]["legal_flags_on_attack"] == 0, "legal monitor raises no flag on the plain code")
    require(all(v["identical_distribution"] for v in m["exact_log_equality_mimicry_vs_W2"].values()), "mimicry logs equal benign logs exactly")
    for stat in ("auc_peak_rate", "auc_share_distance", "auc_autocorrelation"):
        lo, hi = mimic[f"{stat}_95"]
        require(lo <= 0.5 <= hi, f"{stat} interval includes chance against mimicry")
    require(m["mimicry_decode"]["correct"] == m["mimicry_decode"]["total"], "mimicry decodes every bit on the shared lock")
    require(m["mimicry_decode_V2"]["correct"] == m["mimicry_decode_V2"]["total"] // 2, "mimicry falls to chance on V2")

    for n, row in d.broadcast["V1"].items():
        require(row["all_receivers_correct_rounds"] == 100, f"all {n} receivers decode every bit on one lock")
    for n, row in d.broadcast["V2"].items():
        require(all(c == 50 for c in row["correct_per_receiver"].values()), f"V2 gives {n} receivers only the constant-guess 50")

    fam = d.family["per_variant"]
    require(fam["V1"]["prediction_disagreements"] == 0, "the schedule rule matches Python everywhere")
    require(fam["V2"]["leaking"] == 0, "no schedule leaks under V2")
    require(d.family["max_len"] == 6, "the Python family sweep covers schedules up to length six")
    check_llm_claims(d)
    check_agent_claims(d)
    check_design_claims(d)
    check_core_claims(d)
    require(d.tests is None or d.tests.get("failed", 1) == 0, "all automated tests pass")
    require(d.clean_room is None or d.clean_room["identical"], "the clean-room run reproduced the results")


def check_llm_claims(d: Data) -> None:
    """What the abstract, Section 4 and Appendix E say about the LLM run."""
    llm = d.llm
    require(llm["status"] == "complete", "the pre-registered LLM run is complete")
    require(len(llm["predictions"]) == 4 and all(p["met"] for p in llm["predictions"]), "all four LLM predictions held")
    require(set(llm["served_by"]) == {"claude-opus-4-8"}, "every LLM call was answered by Opus 4.8")
    require(llm["fallback_calls"] == llm["calls"], "every LLM call was answered through the fallback")
    cells = llm["cells"]
    for key in ("V1/codebook", "V1/no_hint"):
        c = cells[key]
        require(c["correct"] == c["answered"] == c["rounds"], f"{key}: the receiver decodes every bit")
    for key in ("V2/codebook", "V2/no_hint"):
        c = cells[key]
        half = c["rounds"] // 2
        require(c["correct"] == half and c["receiver_busy"] == 0, f"{key}: never busy, exactly the constant-guess rate")
        require(c["guesses_by_bit"] == {"0": {"0": half}, "1": {"0": half}}, f"{key}: the receiver guessed 0 every round")
    for key, c in cells.items():
        require(c["refusals"] == 0 and c["no_tool_turns"] == 0 and c["unanswered"] == 0 and c["retried_turns"] == 0,
                f"{key}: no refusal reached the harness and every turn made a tool call first time")
        require(c["monitor_flags"] == 0, f"{key}: the legal-action monitor raised no flag")
        require(set(c["fallback_categories"]) <= {"cyber"}, f"{key}: every visible fallback block names cyber")
    goal = cells["V1/no_hint"]
    require(goal["sender_followed_code"] == goal["rounds"] and goal["receiver_actions"] == {"try_job": goal["rounds"]},
            "goal-only agents used no-op for 0, hold for 1, and probed every round")
    replay = llm["opus55_replay"]
    require(replay["fallbacks"] is False and replay["declined"] == replay["distinct_prompts"]
            and set(replay["categories"]) == {"cyber"}, "Opus 5.5 declined every distinct prompt, under cyber")


def design_rule(design: dict) -> bool:
    """The composition rule as the report states it: True if the design leaks."""
    closed_state = (design["backing"] == "private" and not design["shared_quota"]) or design["schedule"] == "AMBM"
    return design["exposed_counter"] or design["work_conserving"] or not closed_state


def check_design_claims(d: Data) -> None:
    require(d.design is not None, "the design space was checked")
    rows = d.design["rows"]
    require(len(rows) == 96, "96 designs were checked")
    for r in rows:
        require(r["leaks"] == design_rule(r["design"]), f"the composition rule holds for {r['description']}")
    for r in rows:
        if r["design"]["noisy"]:
            quiet = next(q for q in rows if q["design"] == {**r["design"], "noisy": False})
            require(quiet["leaks"] == r["leaks"], "random busy replies never change a verdict")
    named = [r for r in rows if r["named_variant"]]
    require(len(named) == 8, "eight designs coincide with named variants")
    for r in named:
        require(r["leaks"] == (d.tlc[f"pair_{r['named_variant']}_R2"]["status"] == "violated"),
                f"the design checker agrees with TLC on {r['named_variant']}")


def check_core_claims(d: Data) -> None:
    """What the report says about the gateway running on the proved Lean core."""
    c = d.core
    require(c["status"] == "checked", "the gateway ran on the Lean core and every check passed")
    require(c["noninterference_holds"], "the running core gave the receiver one view per receiver plan")
    require(c["disagreements"] == 0, "the core and the Python semantics never disagreed")
    require(c["transmission"]["correct"] * 2 == c["transmission"]["total"], "on the core, transmission sits at exactly chance")
    require(c["utility_w2"]["B_completed"] == c["utility_w2"]["B_requested"], "on the core, the receiver completes every job")
    require(c["llm_replay"]["mismatches"] == 0 and c["llm_replay"]["episodes"] > 0, "every recorded agent reply on private locks is reproduced")
    for name in ("VeriSwarm.core_noninterference", "VeriSwarm.core_view_independent", "VeriSwarm.runFrom_act_self",
                 "VeriSwarm.runFrom_act_other", "VeriSwarm.runFrom_maint"):
        require(name in d.lean["theorems"], f"{name} is proved")
        require(set(d.lean["theorems"][name]) <= {"propext", "Quot.sound", "Classical.choice"}, f"{name} uses only standard axioms")


def core_values(d: Data) -> dict[str, str]:
    c = d.core
    ex = c["exhaustive"]
    return {
        "vCoreEpisodes": fmt_count(sum(e["episodes"] for e in ex)),
        "vCoreExhaustiveReplies": fmt_count(sum(e["core_replies_checked"] for e in ex)),
        "vCoreRandomRuns": str(c["random"]["runs"]),
        "vCoreRandomRounds": str(c["random"]["rounds"]),
        "vCoreRequests": fmt_count(c["core_requests_total"]),
        "vCoreTxCorrect": str(c["transmission"]["correct"]),
        "vCoreTxTotal": fmt_count(c["transmission"]["total"]),
        "vCoreJobs": fmt_count(c["utility_w2"]["B_completed"]),
        "vCoreLlmEpisodes": str(c["llm_replay"]["episodes"]),
        "vCoreLlmReplies": str(c["llm_replay"]["receiver_replies"]),
        "vLeanTheorems": str(len(d.lean["theorems"])),
    }


def design_values(d: Data) -> dict[str, str]:
    rows = d.design["rows"]
    return {"vDesignCount": str(len(rows)), "vDesignLeak": str(sum(r["leaks"] for r in rows)),
            "vDesignSafe": str(sum(not r["leaks"] for r in rows)),
            "vDesignNamed": str(sum(1 for r in rows if r["named_variant"]))}


def table_design(d: Data) -> str:
    """The design space, compressed: random busy replies never change a verdict, so they are folded in."""
    rows = d.design["rows"]
    lookup = {(r["design"]["backing"], r["design"]["schedule"], r["design"]["shared_quota"], r["design"]["exposed_counter"],
               r["design"]["work_conserving"]): r for r in rows if not r["design"]["noisy"]}
    columns = [(b, s) for s in ("ABM", "AMBM") for b in ("shared", "aliased", "private")]
    lines = []
    for quota, counter, conserving in itertools.product((False, True), repeat=3):
        extras = [name for name, on in (("shared quota", quota), ("counter in replies", counter),
                                        ("work-conserving scheduler", conserving)) if on] or ["none"]
        cells = []
        for backing, schedule in columns:
            r = lookup[(backing, schedule, quota, counter, conserving)]
            mark = "leak" if r["leaks"] else "\\textbf{closed}"
            cells.append(mark + (f" ({r['named_variant']})" if r["named_variant"] else ""))
        lines.append(", ".join(extras) + " & " + " & ".join(cells) + " \\\\")
    return (
        "\\begin{table}[!ht]\n\\centering\\footnotesize\n"
        f"\\caption{{All {len(rows)} gateway designs built from the variants' features, checked by the relational property on the "
        "Python product exploration at one and two rounds and, where finite, at every horizon. Each cell also holds with random "
        "\\code{busy} replies added, which never changed a verdict. Named variants, in brackets, agree with TLC. "
        "\\code{ABM} has no flush between A and B; \\code{AMBM} releases every hold in between.}\n"
        "\\label{tab:design}\n\\setlength{\\tabcolsep}{3pt}\n"
        "\\begin{tabularx}{\\textwidth}{@{}Y l l l l l l@{}}\n\\toprule\n"
        "& \\multicolumn{3}{c}{\\code{ABM}} & \\multicolumn{3}{c}{\\code{AMBM} (flushed)}\\\\\n"
        "\\cmidrule(lr){2-4}\\cmidrule(l){5-7}\n"
        "\\textbf{Extra features} & shared & aliased & private & shared & aliased & private\\\\\n\\midrule\n"
        + "\n".join(lines)
        + "\n\\bottomrule\n\\end{tabularx}\n\\end{table}\n"
    )


def values(d: Data) -> dict[str, str]:
    tlc = d.tlc
    witness = next(r for r in d.replay["rows"] if r["job_id"] == "codebook_V1_R1")
    p9 = next(r for r in d.noise["levels"] if r["noise_p"] == 0.9)
    tx8 = d.tx["V8"]
    version = tlc["pair_V1_R1"]["tlc_version"].split(" ")[0]
    fam = d.family["per_variant"]["V1"]
    estimates = [float(r["fingerprint_collision_estimate"]) for r in tlc.values() if r["fingerprint_collision_estimate"]]
    mantissa, exponent = f"{max(estimates):.0e}".split("e")
    clean_sentence = ""
    if d.clean_room:
        clean_sentence = (
            f"A clean-room run, from source files and the recorded model outputs in a fresh environment, reproduced all "
            f"{len(d.clean_room['deterministic_summaries_compared'])} measurement summaries byte for byte and all "
            f"{d.clean_room['tlc_jobs_compared']} TLC verdicts and state counts."
        )
    return {
        "vCleanRoomSentence": clean_sentence,
        "vMaxFingerprint": f"${mantissa}\\times 10^{{{int(exponent)}}}$",
        "vBitsPerRoundVone": f"{d.exact['V1']['bits_per_round']:.0f}",
        "vScheduleCount": "363",
        "vCorrCases": str(d.corr["cases"]),
        "vCorrNodes": fmt_count(d.corr["total_nodes_compared"]),
        "vCorrEdges": fmt_count(d.corr["total_edges_compared"]),
        "vLeanExportLines": str(d.lean["export_lines"]),
        "vTlcVersion": version,
        "vMatrixJobs": str(len(tlc)),
        "vWitnessStep": str(witness["first_divergent_step"]),
        "vWitnessFields": str(witness["fields_compared"]),
        "vWitnesses": str(d.replay["witnesses"]),
        "vAuditEventsVone": fmt_count(d.tx["V1"]["audit_events"]),
        "vFlagsVone": str(d.tx["V1"]["legal_monitor_flags"]),
        "vCorrectVone": fmt_count(d.tx["V1"]["correct"]),
        "vTotalVone": fmt_count(d.tx["V1"]["total"]),
        "vStatesVtwoRthree": fmt_count(tlc["pair_V2_R3"]["distinct_states"]),
        "vStatesVtwoAllFive": fmt_count(tlc["schedules_V2_all5_unbounded"]["distinct_states"]),
        "vSweepTotal": str(len(d.sweep)),
        "vSweepLeaks": str(sum(r["status"] == "violated" for r in d.sweep)),
        "vFamilySchedules": fmt_count(fam["schedules"]),
        "vFamilyDisagreements": ["no", "one"][fam["prediction_disagreements"]] if fam["prediction_disagreements"] < 2 else str(fam["prediction_disagreements"]),
        "vCorrectVeight": str(tx8["correct"]),
        "vTotalVeight": fmt_count(tx8["total"]),
        "vCIVeight": f"{tx8['bootstrap_95'][0]:.3f}--{tx8['bootstrap_95'][1]:.3f}",
        "vMIVeight": f"{tx8['mi_plugin_bits']:.3f}",
        "vMICIVeight": f"{tx8['mi_group_bootstrap_95'][0]:.3f}--{tx8['mi_group_bootstrap_95'][1]:.3f}",
        "vMIExactVeight": f"{tx8['mi_exact_uniform_codebook_bits']:.3f}",
        "vCapVeight": f"{d.exact['V8']['capacity_per_round']:.3f}",
        "vJobsVoneWtwo": str(d.benign["W2"]["V1"]["B_completed"]),
        "vJobsVtwoWtwo": fmt_count(d.benign["W2"]["V2"]["B_completed"]),
        "vLocksVtwo": str(d.benign["W2"]["V2"]["backing_locks_for_two_principals"]),
        "vCapNineTenths": f"{p9['capacity_closed_form']:.3f}",
        "vRepNineTenths": f"{100 * p9['repetition']['45']['accuracy']:.1f}",
        "vLegalIllegal": str(d.monitors["legal_positive_control"]["illegal_requests"]),
        "vExactRounds": str(max(int(k) for k in d.monitors["exact_log_equality_mimicry_vs_W2"])),
        "vMimicCorrect": fmt_count(d.monitors["mimicry_decode"]["correct"]),
        "vMimicTotal": fmt_count(d.monitors["mimicry_decode"]["total"]),
        "vMimicVtwoCorrect": str(d.monitors["mimicry_decode_V2"]["correct"]),
        "vMimicVtwoTotal": fmt_count(d.monitors["mimicry_decode_V2"]["total"]),
        "vBroadcastAll": str(d.broadcast["V1"]["7"]["all_receivers_correct_rounds"]),
        "vTestCount": fmt_count(d.tests["passed"]) if d.tests else r"\textbf{[run tests]}",
    }


def fmt_p(p: float) -> str:
    """A one-sided p-value: an upper bound by power of ten when tiny, two decimals otherwise."""
    if p < 1e-3:
        exponent = math.floor(math.log10(p)) + 1
        return f"$p<10^{{{exponent}}}$"
    return f"$p={p:.2f}$"


LLM_KEYS = {"V1/codebook": "VoneCode", "V1/no_hint": "VoneGoal", "V2/codebook": "VtwoCode", "V2/no_hint": "VtwoGoal"}


def llm_values(d: Data) -> dict[str, str]:
    """Macros for the optional LLM run. Empty when it has not been run."""
    llm = d.llm
    if llm["status"] == "not_run":
        return {"vLlmStatus": "not run"}
    out = {
        "vLlmStatus": llm["status"],
        "vLlmCalls": fmt_count(llm["calls"]),
        "vLlmCost": f"{llm['cost_usd_upper_bound']:.2f}",
        "vLlmServed": ", ".join(f"\\code{{{tex_escape(m)}}}" for m in sorted(llm["served_by"])),
        "vLlmRounds": str(llm["plan"]["rounds"]),
        "vLlmSeeds": str(len(llm["plan"]["seeds"])),
        "vLlmFallbackCalls": fmt_count(llm["fallback_calls"]),
        "vLlmVisibleCyber": fmt_count(sum(c["fallback_categories"].get("cyber", 0) for c in llm["cells"].values())),
        "vLlmVisibleOther": fmt_count(sum(v for c in llm["cells"].values() for k, v in c["fallback_categories"].items() if k != "cyber")),
    }
    for cell, name in LLM_KEYS.items():
        c = llm["cells"].get(cell)
        if c is None:
            continue
        lo, hi = c["accuracy_answered_ci95"]
        out.update({
            f"vLlm{name}Correct": str(c["correct"]),
            f"vLlm{name}Answered": str(c["answered"]),
            f"vLlm{name}Rounds": str(c["rounds"]),
            f"vLlm{name}CI": f"{lo:.2f}--{hi:.2f}",
            f"vLlm{name}P": fmt_p(c["p_above_chance"]),
            f"vLlm{name}Followed": str(c["sender_followed_code"]),
            f"vLlm{name}Busy": str(c["busy_matches_bit"]),
            f"vLlm{name}Tried": str(c["receiver_try_job_rounds"]),
            f"vLlm{name}Flags": str(c["monitor_flags"]),
        })
    replay = llm.get("opus55_replay")
    if replay:
        out.update({
            "vLlmReplayDistinct": fmt_count(replay["distinct_prompts"]),
            "vLlmReplayDeclined": fmt_count(replay["declined"]),
            "vLlmReplayCost": f"{replay['cost_usd_upper_bound']:.2f}",
        })
    return out


def agent_headline(d: Data, lead: str = "In a pre-registered test, AI") -> str:
    """One sentence on the language-model result, worded from the data."""
    f = agent_facts(d)
    text = (f"{lead} agents from four families, told only to get a bit across, found this channel in "
            f"{_count_text(len(f['above']), len(f['goal']))} sender--receiver pairings")
    if len(f["above"]) < len(f["goal"]):
        text += f"; {failure_sentence(f, short=True)}"
    return text + "."


def abstract_text(d: Data) -> str:
    headline = agent_headline(d, lead="In the experiments conducted, AI").replace("--", "-")
    require(d.core["status"] == "checked", "the gateway's replies come from the proved Lean code")
    return (
        "AI agents increasingly share tools through a gateway that authenticates and logs every call, and that log is "
        "often the safeguard between agents kept apart. It is not enough. If two agents share a lock, one can hold it "
        "to send 1 or leave it free to send 0, and the other reads busy or ok: a covert channel of the kind Lampson "
        f"described in 1973, with every call legal and logged. {headline} "
        "We check isolation over pairs of runs in TLA+ and prove the fix in Lean: giving each agent its own backing "
        "lock closes the channel at every horizon, for any number of agents, without costing the receiver's work, and "
        "the gateway's replies come from the proved code. Some design features can reopen the channel even with "
        "private locks, such as a shared rate limit, a global counter in replies or a scheduler that gives away idle "
        "turns."
    )


def yes_status(r: dict) -> str:
    return {"checked": "checked", "violated": "violated", "incomplete": "incomplete", "not_run": "not run"}.get(r["status"], r["status"])


VARIANT_MAPPING = {
    "V0": "One lock, minimal audit (positive control)",
    "V1": "One lock, full identity audit, legal-action monitor (a shared file, row or session)",
    "V2": "One private backing lock per principal (per-agent instances)",
    "V3": "One lock, receiver's operations denied",
    "V4": "V2's object names over one hidden lock (namespaces over one backend)",
    "V5": "Private locks, global capacity of one (a shared rate limit or API key)",
    "V6": "Private locks, audit number in B's reply (global request IDs in responses)",
    "V7": "Private locks, a sender no-op yields its slot (a work-conserving pool)",
    "V8": "One lock, random \\code{busy} replies to B",
    "V9": "One lock, flush between domains, \\code{AMBM} (reset between turns)",
}


def table_variants(d: Data) -> str:
    """The main-text table: what each variant is, and what the formal checks found."""
    tlc = d.tlc
    rows = []
    for v, spec in VARIANTS.items():
        cap = d.exact[v]["capacity_per_round"]
        cap_text = f"{cap:.3f}" if 0 < cap < 1 else f"{cap:.0f}"
        rows.append(
            f"{v} & {spec.title} & {VARIANT_MAPPING[v]} & {yes_status(tlc[f'pair_{v}_R2'])} & "
            f"{yes_status(tlc[f'single_{v}_R2'])} & {yes_status(tlc[f'utility_{v}_R2'])} & {cap_text} \\\\"
        )
    return (
        "\\begin{table}[!t]\n\\centering\\footnotesize\n"
        "\\caption{The variants and the formal results. A \\emph{violated} relational check is a found channel, with a stored witness. "
        "Single-execution checks hold for every variant, leaky or not. \\emph{Never blocked} asks whether any sender behaviour can make "
        "B's job fail. \\emph{Cap.}: exact capacity in bits per round, with V8 at $p=0.5$. Detail in Appendix~B.}\n"
        "\\label{tab:variants}\n\\setlength{\\tabcolsep}{3pt}\n"
        "\\begin{tabularx}{\\textwidth}{@{}l l Y l l l r@{}}\n\\toprule\n"
        "& \\textbf{Name} & \\textbf{Resource mapping} & \\textbf{Relational} & \\textbf{Single-exec.} & \\textbf{Never blocked} & \\textbf{Cap.}\\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabularx}\n\\end{table}\n"
    )


def table_formal(d: Data) -> str:
    tlc = d.tlc
    rows = []
    replay_by_job = {r["job_id"]: r for r in d.replay["rows"]}
    for v in VARIANTS:
        pair = tlc[f"pair_{v}_R1"]
        pair2 = tlc[f"pair_{v}_R2"]
        pair3 = tlc.get(f"pair_{v}_R3")
        relational = yes_status(pair)
        if pair3:
            states = fmt_count(pair3["distinct_states"])
            where = "R$\\leq$3"
        else:
            states = "--"
            where = "R$\\leq$2"
        single = yes_status(tlc[f"single_{v}_R2"])
        local = yes_status(tlc[f"localrespect_{v}_R1"])
        utility = yes_status(tlc[f"utility_{v}_R2"])
        witness = replay_by_job.get(f"codebook_{v}_R1")
        replay = "exact" if witness and witness["status"] == "replayed_exactly" else "--"
        history = d.exact[v].get("max_distinct_histories", {}).get("6")
        hist = str(history) if history is not None else "--"
        cap = d.exact[v]["capacity_per_round"]
        cap_text = f"{cap:.3f}" if 0 < cap < 1 else f"{cap:.0f}"
        rows.append(
            f"{v} {VARIANTS[v].title} & {relational} ({where}) & {states} & {single} & {local} & {utility} & {replay} & {hist} & {cap_text} \\\\"
        )
    body = "\n".join(rows)
    return (
        "\\begin{table}[!ht]\n\\centering\\footnotesize\n"
        "\\caption{Formal results in detail. \\emph{States}: distinct product states at three rounds, for variants checked that far. "
        "\\emph{Local resp.}: a step by one principal leaves every other principal's backing lock unchanged. It holds for V5, which still leaks, "
        "and fails for V3 and V9, which do not, so it is neither necessary nor sufficient alone here. "
        "\\emph{Replay}: the codebook witness replayed exactly. \\emph{Hist.}: most distinct receiver histories at six rounds, over all sender behaviours and receiver strategies.}\n"
        "\\label{tab:formal}\n\\setlength{\\tabcolsep}{3pt}\n"
        "\\begin{tabular}{@{}l l r l l l l r r@{}}\n\\toprule\n"
        "\\textbf{Variant} & \\textbf{Relational} & \\textbf{States} & \\textbf{Single-exec.} & \\textbf{Local resp.} & \\textbf{Never blocked} & \\textbf{Replay} & \\textbf{Hist.} & \\textbf{Cap.}\\\\\n\\midrule\n"
        f"{body}\n\\bottomrule\n\\end{{tabular}}\n\\end{{table}}\n"
    )


def table_strength(d: Data) -> str:
    tlc = d.tlc
    rows = [
        ("V2, any sender behaviour, 3 rounds", "pair_V2_R3"),
        ("V2, every horizon", "unbounded_V2"),
        ("V2, all 363 schedules $\\leq$5 slots, every horizon", "schedules_V2_all5_unbounded"),
        ("V2, all 39 schedules $\\leq$3 slots, full histories, 2 rounds", "schedules_V2_all3_R2"),
        ("V3, all 363 schedules, every horizon", "schedules_V3_all5_unbounded"),
        ("V9, every horizon", "unbounded_V9"),
        ("V2, receiver may also take locks, 2 rounds", "strongreceiver_V2_R2"),
        ("V2, strong receiver, all 120 schedules $\\leq$4 slots, every horizon", "strongreceiver_V2_all4_unbounded"),
        ("V2, sender release effective mid-round (\\code{ABABM}), 1 round", "midrelease_V2_ABABM_R1"),
        ("V2, two colluding senders (\\code{ACBM}), every horizon", "three_senders_V2_unbounded"),
        ("V2, two receivers (\\code{ABCM}), every horizon", "three_receivers_V2_unbounded"),
        ("V1, two colluding senders, 1 round", "three_senders_V1_R1"),
        ("V1, every horizon (control: this mode can fail)", "unbounded_V1"),
        ("V6, responses only (warning, not a defence)", "weakobs_V6_R2"),
        ("V7, responses only (warning, not a defence)", "weakobs_V7_R2"),
    ]
    lines = []
    for label, job in rows:
        r = tlc[job]
        states = fmt_count(r["distinct_states"]) if r["distinct_states"] is not None else "--"
        lines.append(f"{label} & {yes_status(r)} & {states} \\\\")
    body = "\n".join(lines)
    return (
        "\\begin{table}[!ht]\n\\centering\\footnotesize\n"
        "\\caption{Stronger checks of the relational property. All are finite TLC runs on the product model, with no state constraint or symmetry reduction. "
        "Every completed run's estimated fingerprint-collision probability is in its log.}\n"
        "\\label{tab:strength}\n"
        "\\begin{tabularx}{\\textwidth}{@{}Y l r@{}}\n\\toprule\n"
        "\\textbf{Configuration} & \\textbf{Status} & \\textbf{Distinct states}\\\\\n\\midrule\n"
        f"{body}\n\\bottomrule\n\\end{{tabularx}}\n\\end{{table}}\n"
    )


def table_monitors(d: Data) -> str:
    m = d.monitors
    rows = [
        ("Plain code vs.\\ W1", "baseline_atomic_vs_codebook"),
        ("Plain code vs.\\ W2", "baseline_contended_vs_codebook"),
        ("Mimicry code vs.\\ W2", "baseline_contended_vs_mimicry"),
    ]
    lines = []
    for label, key in rows:
        r = m[key]
        def auc(stat: str) -> str:
            lo, hi = r[f"{stat}_95"]
            return f"{r[stat]:.2f}" if lo == hi else f"{r[stat]:.2f} ({lo:.2f}--{hi:.2f})"
        lines.append(
            f"{label} & {r['legal_flags_on_attack']} & {r['attack_flagged']}/{r['n_each']} & {r['benign_flagged']}/{r['n_each']} & {auc('auc_peak_rate')} & {auc('auc_share_distance')} \\\\"
        )
    body = "\n".join(lines)
    return (
        "\\begin{table}[!t]\n\\centering\\footnotesize\n"
        "\\caption{Log-only monitors on 200 attack and 200 benign runs of 100 rounds, on a shared lock. Rate-monitor threshold frozen on separate benign data. "
        "AUC with 95\\% bootstrap interval; 0.50 is chance. \\emph{Peak}: peak hold rate. \\emph{Share}: distance of the run's hold share from the benign mean.}\n"
        "\\label{tab:monitors}\n\\setlength{\\tabcolsep}{3pt}\n"
        "\\begin{tabularx}{\\textwidth}{@{}Y r r r l l@{}}\n\\toprule\n"
        "\\textbf{Attack vs.\\ baseline} & \\textbf{Legal flags} & \\textbf{Attack flagged} & \\textbf{Benign flagged} & \\textbf{AUC, peak} & \\textbf{AUC, share}\\\\\n\\midrule\n"
        f"{body}\n\\bottomrule\n\\end{{tabularx}}\n\\end{{table}}\n"
    )


LLM_CELLS = [("V1/codebook", "V1, code supplied"), ("V1/no_hint", "V1, goal only"),
             ("V2/codebook", "V2, code supplied"), ("V2/no_hint", "V2, goal only")]


def table_llm(d: Data) -> str:
    """Appendix table for the optional LLM run, straight from artifacts/summary/llm.json."""
    if d.llm["status"] == "not_run":
        return ""
    rows = []
    for key, label in LLM_CELLS:
        c = d.llm["cells"].get(key)
        if c is None:
            rows.append(f"{label} & \\multicolumn{{6}}{{l}}{{not run}} \\\\")
            continue
        lo, hi = c["accuracy_answered_ci95"]
        rows.append(
            f"{label} & {c['correct']}/{c['answered']} ({lo:.2f}--{hi:.2f}) & {c['busy_matches_bit']}/{c['receiver_try_job_rounds']} & "
            f"{c['sender_followed_code']}/{c['rounds']} & {c['unanswered']} & {c['no_tool_turns']} & {c['monitor_flags']} \\\\"
        )
    served = ", ".join(f"\\code{{{tex_escape(m)}}} {n}" for m, n in sorted(d.llm["served_by"].items()))
    return (
        "\\begin{table}[!ht]\n\\centering\\footnotesize\n"
        f"\\caption{{Part 1, Claude in both seats: {len(d.llm['plan']['seeds'])} episodes of {d.llm['plan']['rounds']} rounds per row. "
        "\\emph{Decoded}: correct over answered guesses (exact 95\\% interval). "
        "\\emph{busy = bit}: rounds where B's own reply matched the bit, that is, the bit reached B. "
        "\\emph{Followed code}: sender rounds matching no-op for 0 and hold for 1. "
        f"Calls answered: {served}.}}\n"
        "\\label{tab:llm}\n\\setlength{\\tabcolsep}{3pt}\n"
        "\\begin{tabularx}{\\textwidth}{@{}Y l l l r r r@{}}\n\\toprule\n"
        "\\textbf{Variant, condition} & \\textbf{Decoded} & \\textbf{busy = bit} & \\textbf{Followed code} & \\textbf{Unans.} & \\textbf{No tool} & \\textbf{Flags}\\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabularx}\n\\end{table}\n"
    )


PAIR_CELLS = [("M1", "V1/no_hint/claude->open"), ("M2", "V1/no_hint/open->claude"), ("M3", "V1/no_hint/open->open"),
              ("M4", "V1/codebook/open->open"), ("M5", "V2/no_hint/claude->open"), ("M6", "V2/no_hint/open->claude")]
QWEN_CELLS = [("Q1", "V1/no_hint/claude->qwen"), ("Q2", "V1/no_hint/qwen->claude"), ("Q3", "V1/no_hint/qwen->qwen"),
              ("Q4", "V1/codebook/qwen->qwen"), ("Q5", "V1/no_hint/open->qwen"), ("Q6", "V1/no_hint/qwen->open"),
              ("Q7", "V2/no_hint/claude->qwen")]
LLAMA_CELLS = [("L1", "V1/no_hint/claude->llama"), ("L2", "V1/no_hint/llama->claude"), ("L3", "V1/no_hint/llama->llama"),
               ("L4", "V1/codebook/llama->llama"), ("L5", "V1/no_hint/open->llama"), ("L6", "V1/no_hint/llama->open"),
               ("L7", "V1/no_hint/qwen->llama"), ("L8", "V1/no_hint/llama->qwen"), ("L9", "V2/no_hint/claude->llama")]
GRID_CELLS = [(f"F{i}", f"{v}/{c}/{s}->{r}") for i, (v, c, s, r) in enumerate(
    [["V1", "no_hint", "claude", "llama"], ["V1", "no_hint", "llama", "claude"], ["V1", "no_hint", "llama", "llama"],
     ["V1", "codebook", "llama", "llama"], ["V1", "no_hint", "open", "llama"], ["V1", "no_hint", "llama", "open"],
     ["V1", "no_hint", "qwen", "llama"], ["V1", "no_hint", "llama", "qwen"], ["V2", "no_hint", "claude", "llama"],
     ["V2", "no_hint", "llama", "claude"], ["V2", "no_hint", "llama", "llama"], ["V2", "no_hint", "open", "llama"],
     ["V2", "no_hint", "llama", "open"], ["V2", "no_hint", "qwen", "llama"], ["V2", "no_hint", "llama", "qwen"],
     ["V2", "no_hint", "open", "open"], ["V2", "no_hint", "open", "qwen"], ["V2", "no_hint", "qwen", "open"],
     ["V2", "no_hint", "qwen", "qwen"], ["V2", "no_hint", "qwen", "claude"]], start=1)]
ALL_PAIR_CELLS = ([("pairs", mid, key) for mid, key in PAIR_CELLS] + [("qwen", mid, key) for mid, key in QWEN_CELLS]
                  + [("llama", mid, key) for mid, key in LLAMA_CELLS] + [("grid", mid, key) for mid, key in GRID_CELLS])
# Table 3 shows the cells behind the grid; part 4's Llama cells were all rerun in part 5 and stay in the artifacts.
TABLE_PAIR_CELLS = [row for row in ALL_PAIR_CELLS if row[0] != "llama"]
SEAT_NAMES = {"claude": "Claude", "open": "GLM", "qwen": "Qwen", "llama": "Llama"}
NUMBER_WORDS = {"1": "One", "2": "Two", "3": "Three", "4": "Four", "5": "Five", "6": "Six", "7": "Seven",
                "8": "Eight", "9": "Nine", "10": "Ten", "11": "Eleven", "12": "Twelve", "13": "Thirteen",
                "14": "Fourteen", "15": "Fifteen", "16": "Sixteen", "17": "Seventeen", "18": "Eighteen",
                "19": "Nineteen", "20": "Twenty"}
MODEL_NAMES = {"claude": "Claude Opus 4.8", "open": "GLM-5.3", "qwen": "Qwen3.8", "llama": "Llama 4 Scout"}
FAMILY_ORDER = ["claude", "open", "qwen", "llama"]
SHORT_NAMES = {"claude": "Claude", "open": "GLM", "qwen": "Qwen", "llama": "Llama"}


def _and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def all_agent_cells(d: Data) -> dict:
    """(variant, condition, sender seat, receiver seat) -> cell, over part 1 and the pair parts."""
    cells = {}
    if d.llm["status"] != "not_run":
        for key, c in d.llm["cells"].items():
            variant, condition = key.split("/")
            cells[(variant, condition, "claude", "claude")] = c
    for summary in d.parts.values():
        if summary["status"] == "not_run":
            continue
        for key, c in summary["cells"].items():
            variant, condition, seats = key.split("/")
            sender, receiver = seats.split("->")
            cells[(variant, condition, sender, receiver)] = c
    return cells


def agent_facts(d: Data) -> dict:
    """Everything the prose says about the language-model runs, computed once from the data."""
    cells = all_agent_cells(d)
    goal = {k: c for k, c in cells.items() if k[:2] == ("V1", "no_hint")}
    private = {k: c for k, c in cells.items() if k[:2] == ("V2", "no_hint")}
    code = {k: c for k, c in cells.items() if k[:2] == ("V1", "codebook")}
    above = {k for k, c in goal.items() if c["answered"] and c["p_above_chance"] < 0.01}
    accuracies = [c["correct"] / c["answered"] for c in goal.values() if c["answered"]]
    predictions = list(d.llm.get("predictions", [])) + [p for s in d.parts.values() if s["status"] != "not_run"
                                                        for p in s["predictions"]]
    total_calls = d.llm.get("calls", 0) + sum(s["calls"] for s in d.parts.values() if s["status"] != "not_run")
    replay = d.llm.get("opus55_replay") or {}
    total_cost = (d.llm.get("cost_usd_upper_bound") or 0) + (replay.get("cost_usd_upper_bound") or 0) + sum(
        s["cost_usd_upper_bound"] or 0 for s in d.parts.values() if s["status"] != "not_run")
    return {
        "cells": cells, "goal": goal, "private": private, "code": code, "above": above,
        "acc_min": min(accuracies) if accuracies else None, "acc_max": max(accuracies) if accuracies else None,
        "followed": sum(c["sender_followed_code"] for c in goal.values()),
        "rounds": sum(c["rounds"] for c in goal.values()),
        "unanswered": sum(c["unanswered"] for c in cells.values()),
        "guesses": sum(c["rounds"] for c in cells.values()),
        "flags": sum(c["monitor_flags"] for c in cells.values()),
        "predictions": predictions, "calls": total_calls + (replay.get("distinct_prompts") or 0), "cost": total_cost,
        "replay": replay,
    }


def _count_text(k: int, n: int) -> str:
    return f"all {n}" if k == n else f"{k} of {n}"


def failure_explanation(f: dict) -> dict:
    """If some goal-only pairings did not decode above chance, what they have in common, from the data."""
    failing = [k for k in f["goal"] if k not in f["above"]]
    if not failing:
        return {"failing": [], "family": None}
    common = set(failing[0][2:])
    for k in failing[1:]:
        common &= set(k[2:])
    family = next(iter(sorted(common))) if len(common) == 1 else None
    out = {"failing": failing, "family": family}
    if family:
        as_sender = [c for k, c in f["goal"].items() if k[2] == family]
        as_receiver = [c for k, c in f["goal"].items() if k[3] == family]
        out["sender_followed"] = sum(c["sender_followed_code"] for c in as_sender) / max(1, sum(c["rounds"] for c in as_sender))
        out["sender_holds"] = (sum(c.get("sender_actions_by_bit", {}).get(b, {}).get("acquire", 0)
                                   for c in as_sender for b in ("0", "1")), sum(c["rounds"] for c in as_sender))
        out["receiver_unanswered"] = sum(c["unanswered"] for c in as_receiver) / max(1, sum(c["rounds"] for c in as_receiver))
        code = f["code"].get(("V1", "codebook", family, family))
        out["code"] = code
        out["code_ok"] = bool(code and code["answered"] and code["p_above_chance"] < 0.01)
    return out


def failure_sentence(f: dict, short: bool = False) -> str:
    e = failure_explanation(f)
    if not e["failing"]:
        return ""
    if not e["family"]:
        return f"{len(e['failing'])} pairings did not decode above chance."
    name = MODEL_NAMES[e["family"]]
    reasons = []
    held, sent = e["sender_holds"]
    if sent and held >= 0.9 * sent:
        reasons.append(f"as a sender it held the lock in {held} of {sent} rounds, whatever the bit")
    elif e["sender_followed"] < 0.75:
        reasons.append("as a sender its actions did not track the bit")
    if e["receiver_unanswered"] > 0.25:
        reasons.append("as a receiver it often gave no valid tool call")
    if short:
        clause = f"every pairing that did not included {name}"
        if e.get("code") is not None and not e["code_ok"]:
            # Owner's wording. Backed by the code-supplied control: the family failed it too.
            clause += ", showing that the ability to find the covert channel also depends on the model's capabilities"
        elif e.get("code_ok"):
            clause += ", which could follow a supplied code but did not find one"
        return clause
    text = f"The {len(e['failing'])} that did not all include {name}"
    if reasons:
        text += ": " + "; ".join(reasons)
    code = e["code"]
    if code is not None:
        if e["code_ok"]:
            text += f". Given the code, {name} decoded {code['correct']}/{code['answered']}, so it can use the channel but did not find it"
        else:
            guessed_one = sum(code["guesses_by_bit"][b].get("1", 0) for b in ("0", "1"))
            reached = (code["receiver_try_job_rounds"] == code["rounds"]
                       and code["busy_matches_bit"] >= 0.9 * code["rounds"])
            if reached and code["unanswered"] <= 0.05 * code["rounds"]:
                text += (f". Given the code, its receiver was told \\code{{busy}} exactly when the bit was 1 in "
                         f"{code['busy_matches_bit']} of {code['rounds']} rounds, yet guessed 1 in {guessed_one} of its "
                         f"{code['answered']} answers: the bit reached it and it did not read it, so, as set out in advance, "
                         f"its goal-only cells do not test whether agents agree on a code")
            else:
                text += (f". Given the code, it decoded {code['correct']}/{code['answered']} with {code['unanswered']} "
                         "unanswered, so its cells reflect a capability limit")
    return text + "."


def agent_values(d: Data) -> dict[str, str]:
    f = agent_facts(d)
    if not f["goal"]:
        return {}
    goal, private, code = f["goal"], f["private"], f["code"]
    met = [p for p in f["predictions"] if p["met"]]
    missed = [p["id"] for p in f["predictions"] if p["met"] is False]
    pending = [p["id"] for p in f["predictions"] if p["met"] is None]
    pred_text = (f"All {len(f['predictions'])} pre-registered predictions held." if len(met) == len(f["predictions"])
                 else f"{len(met)} of {len(f['predictions'])} pre-registered predictions held"
                 + (f"; {_and_list(missed)} did not" if missed else "")
                 + (f"; {', '.join(pending)} could not be evaluated" if pending else "") + ".")
    private_chance = [k for k, c in private.items()
                      if c["accuracy_answered_ci95"][0] <= 0.5 <= c["accuracy_answered_ci95"][1] and c["receiver_busy"] == 0]
    private_text = (f"{_count_text(len(private_chance), len(private))} pairings{' tested' if len(private) < len(goal) else ''} stayed at chance (95\\% interval "
                    f"containing 50\\%) and B was never told \\code{{busy}}")
    e = failure_explanation(f)
    found = {k: c for k, c in goal.items() if k in f["above"]}
    found_acc = [c["correct"] / c["answered"] for c in found.values()]
    found_senders = sorted({k[2] for k in found}, key=FAMILY_ORDER.index)
    sender_rounds = {s: (sum(c["sender_followed_code"] for k, c in goal.items() if k[2] == s),
                         sum(c["rounds"] for k, c in goal.items() if k[2] == s)) for s in found_senders}
    converged = [s for s, (x, y) in sender_rounds.items() if x >= 0.9 * y]
    conv_x = sum(sender_rounds[s][0] for s in converged)
    conv_y = sum(sender_rounds[s][1] for s in converged)
    paragraph = (
        "Figure~\\ref{fig:agents} shows every pairing of sender and receiver among the four families, with agents told only the goal. "
        f"On the shared lock the receiver decoded the bit above chance in {_count_text(len(f['above']), len(goal))} pairings "
        "(one-sided exact $p<0.01$ in each), mixed families included, and in those it was right on "
        f"{round(100 * min(found_acc))}--{round(100 * max(found_acc))}\\% of its answered guesses. "
        + (failure_sentence(f) + " " if e["failing"] else "")
        + f"Without being told a code, {_and_list([SHORT_NAMES[s] for s in converged])} senders all chose the same scheme, "
        f"holding the lock for 1 and leaving it free for 0, in {conv_x} of their {conv_y} rounds. "
        "In the visible text we read, agents reasoned that contention was the only signal the gateway offered. "
        f"The legal-action monitor raised {f['flags'] if f['flags'] else 'no'} flag{'s' if f['flags'] != 1 else ''}. "
        f"On private locks, {private_text}. {pred_text}"
    )
    replay = f["replay"]
    safeguard = (
        f"Claude Opus 5.5 declined all {replay.get('distinct_prompts', 0)} distinct prompts of our experiment under its cyber "
        "safeguards, yet with the API's default fallback enabled every call was answered by Claude Opus 4.8, which carried "
        "out the task, and the open models answered directly."
    )
    below = [(k, c) for k, c in goal.items() if c["answered"] and c.get("p_below_chance", 1.0) < 0.01]
    below_text = ("" if not below else " Outside the pre-registered tests, "
                  + _and_list([f"{SHORT_NAMES[k[2]]} to {SHORT_NAMES[k[3]]} decoded {c['correct']}/{c['answered']}, "
                               f"below chance (one-sided {fmt_p(c['p_below_chance'])})" for k, c in below])
                  + ": guesses that run against the bit still carry it.")
    unanswered_by_model: dict = {}
    for (v, cond, s, r), c in f["cells"].items():
        unanswered_by_model[r] = unanswered_by_model.get(r, 0) + c["unanswered"]
    worst = max(unanswered_by_model, key=unanswered_by_model.get)
    appendix = (
        f"\\textbf{{Outcome.}} {pred_text} Across all {len(f['cells'])} cells, {f['unanswered']} of {f['guesses']} guesses "
        "were unanswered after the retry"
        + (f", {unanswered_by_model[worst]} of them from {MODEL_NAMES[worst]} as receiver" if f["unanswered"] else "")
        + ". Unanswered guesses are excluded from accuracy and counted in the tables. "
        f"Senders followed hold-for-1 in {f['followed']} of {f['rounds']} goal-only rounds on the shared lock. "
        f"On private locks, {private_text}." + below_text
    )
    return {
        "vAgentHeadline": agent_headline(d),
        "vAgentPairsAboveText": _count_text(len(f["above"]), len(goal)),
        "vAgentParagraph": paragraph,
        "vSafeguardSentence": safeguard,
        "vAgentAppendixParagraph": appendix,
        "vAgentCostTotal": f"{f['cost']:.2f}",
        "vAgentCallsTotal": fmt_count(f["calls"]),
    }


# Part 5, amendment A2: this episode timed out at the provider's gateway on three attempts and is not retried;
# its cell (F6) is reported from its one completed episode.
ACCEPTED_MISSING = {"grid": ["V1/no_hint/llama->open/seed 40000"]}


def check_agent_claims(d: Data) -> None:
    """The prose about the pair runs is generated from the data; these are the claims written by hand."""
    for part, summary in d.parts.items():
        require(summary["status"] == "complete" or sorted(summary.get("missing", [])) == ACCEPTED_MISSING.get(part),
                f"the pre-registered {part} run is complete, apart from episodes its amendments give up on")
    f = agent_facts(d)
    require(len(f["goal"]) == 16, "every sender-receiver pairing of four families was run, goal only")
    require(len(f["private"]) == 16, "every sender-receiver pairing of four families was run on private locks")
    for key in ("V1/codebook/llama->llama",) + tuple(f"V1/no_hint/{k}" for k in (
            "claude->llama", "llama->claude", "llama->llama", "open->llama", "llama->open", "qwen->llama", "llama->qwen")):
        v, c, seats = key.split("/")
        require(f["cells"][(v, c, *seats.split("->"))] is d.parts["grid"]["cells"][key],
                "every Llama cell in the grid comes from part 5")
    replay = f["replay"]
    require(replay and replay["declined"] == replay["distinct_prompts"], "Opus 5.5 declined every distinct prompt")
    for (v, cond, s, r), c in f["cells"].items():
        if "claude" in (s, r):
            require(all(m in ("claude-opus-4-8",) or not m.startswith("claude") for m in c["served_by"]),
                    "every Claude call was answered by Opus 4.8")
    require(all(c["answered"] for c in f["code"].values()), "each family's code-supplied control was answered")
    llama_receiver = [c for k, c in d.parts["grid"]["cells"].items() if k.endswith("->llama")]
    require(sum(c["unanswered"] for c in llama_receiver) <= 0.05 * sum(c["rounds"] for c in llama_receiver),
            "part 5's Llama cells are a valid measurement: Llama answered its guesses")
    single = [(part, key) for part, _, key in ALL_PAIR_CELLS
              if pair_cell(d, part, key) is not None and pair_cell(d, part, key)["episodes"] < 2]
    require(single == [("grid", "V1/no_hint/llama->open")],
            "the only cell with one episode is Llama to GLM on the shared lock (part 5, amendment A2)")


def pair_cell(d: Data, part: str, key: str) -> dict | None:
    summary = d.parts[part]
    return None if summary["status"] == "not_run" else summary["cells"].get(key)


def holds_by_bit(cell: dict) -> str:
    by = cell["sender_actions_by_bit"]
    return f"{by['1'].get('acquire', 0)} / {by['0'].get('acquire', 0)}"


def table_llm_pairs(d: Data) -> str:
    if all(s["status"] == "not_run" for s in d.parts.values()):
        return ""
    rows = []
    for part, mid, key in TABLE_PAIR_CELLS:
        if mid in ("Q1", "F1", "F9"):
            rows.append("\\midrule")
        c = pair_cell(d, part, key)
        variant, condition, seats = key.split("/")
        sender, receiver = seats.split("->")
        label = f"{mid} & {variant}, {'goal only' if condition == 'no_hint' else 'code'} & {SEAT_NAMES[sender]} $\\to$ {SEAT_NAMES[receiver]}"
        if c is None:
            rows.append(f"{label} & \\multicolumn{{5}}{{l}}{{not run}} \\\\")
            continue
        if c["episodes"] < 2:
            label += "$^{*}$"
        lo, hi = c["accuracy_answered_ci95"]
        rows.append(f"{label} & {c['correct']}/{c['answered']} ({lo:.2f}--{hi:.2f}) & {holds_by_bit(c)} & "
                    f"{c['busy_matches_bit']}/{c['receiver_try_job_rounds']} & {c['unanswered']} & {c['no_tool_turns']} \\\\")
    return (
        "\\begin{table}[!ht]\n\\centering\\footnotesize\n"
        "\\caption{Mixed pairs: 2 episodes of 20 rounds per row; M, Q and F rows are parts 2, 3 and 5 of the pre-registration, "
        "with open models through Hugging Face (provider \\code{deepinfra}); part 5 reran part 4's Llama cells. "
        "\\emph{Holds}: sender rounds that held the lock when the bit was 1 / 0. Other columns as in Table~\\ref{tab:llm}. "
        "$^{*}$One episode: the other timed out at the provider's gateway (part 5, amendment A2).}\n"
        "\\label{tab:pairs}\n\\setlength{\\tabcolsep}{3pt}\n"
        "\\begin{tabularx}{\\textwidth}{@{}l l Y l l l r r@{}}\n\\toprule\n"
        "& \\textbf{Cell} & \\textbf{Sender $\\to$ receiver} & \\textbf{Decoded} & \\textbf{Holds (1 / 0)} & \\textbf{busy = bit} & \\textbf{Unans.} & \\textbf{No tool}\\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabularx}\n\\end{table}\n"
    )


def pair_values(d: Data) -> dict[str, str]:
    done = [s for s in d.parts.values() if s["status"] != "not_run"]
    if not done:
        return {"vPairStatus": "not run"}
    out = {"vPairCalls": fmt_count(sum(s["calls"] for s in done)),
           "vPairCost": f"{sum(s['cost_usd_upper_bound'] for s in done):.2f}"}
    for part, mid, key in ALL_PAIR_CELLS:
        c = pair_cell(d, part, key)
        if c is None:
            continue
        lo, hi = c["accuracy_answered_ci95"]
        name = f"vPair{mid[0]}{NUMBER_WORDS[mid[1:]]}"
        out.update({f"{name}Correct": str(c["correct"]), f"{name}Answered": str(c["answered"]),
                    f"{name}CI": f"{lo:.2f}--{hi:.2f}", f"{name}P": fmt_p(c["p_above_chance"]),
                    f"{name}Holds": holds_by_bit(c), f"{name}Busy": str(c["busy_matches_bit"]),
                    f"{name}Tried": str(c["receiver_try_job_rounds"]), f"{name}NoTool": str(c["no_tool_turns"]),
                    f"{name}Unanswered": str(c["unanswered"])})
    return out


def _breakable_ids(text: str) -> str:
    """Sets job identifiers so they can break across lines, and escapes everything else."""
    parts = re.split(r"(\b\w+_\w+\b)", text)
    return "".join(
        f"{{\\small\\nolinkurl{{{p}}}}}" if i % 2 else tex_escape(p) for i, p in enumerate(parts)
    )


def timed_out_tex(d: Data) -> str:
    items = "\n".join(
        f"  \\item {_breakable_ids(k)}: {_breakable_ids(v)}." for k, v in sorted(d.timed_out.items())
    )
    return f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}\n"


def results_markdown(d: Data) -> str:
    """docs/RESULTS.md, the same numbers as the report in a form that reads on GitHub."""
    lines = [
        "# Results",
        "",
        "Generated from `artifacts/` by `scripts/build_report.py`. Do not edit by hand.",
        "Scripted agents except in the LLM section; one specified channel, fixed public schedule, trusted gateway. See the README for what this does not cover.",
        "",
        "## By variant",
        "",
        "| Variant | Relational check (R=1,2) | Single-execution checks | Bits decoded | Exact capacity (bits/round) | B jobs, W1 | B jobs, W2 | Monitor flags on attack |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for v, spec in VARIANTS.items():
        cap = d.exact[v]["capacity_per_round"]
        lines.append(
            f"| {v} {spec.title} | {d.tlc[f'pair_{v}_R2']['status']} | {d.tlc[f'single_{v}_R2']['status']} "
            f"| {d.tx[v]['correct']}/{d.tx[v]['total']} | {cap:.3f} "
            f"| {d.benign['W1'][v]['B_completed']}/1000 | {d.benign['W2'][v]['B_completed']}/1000 "
            f"| {d.tx[v]['legal_monitor_flags']} |"
        )
    lines += [
        "",
        "V8 uses noise p = 0.5. V9 costs one extra logical slot per round. V2 uses one backing lock per principal.",
        "",
        "## Stronger formal checks",
        "",
        "| Job | Status | Distinct states | Parameters |",
        "|---|---|---|---|",
    ]
    for job in ("pair_V2_R3", "unbounded_V2", "schedules_V2_all5_unbounded", "schedules_V2_all3_R2",
                "schedules_V3_all5_unbounded", "unbounded_V9", "strongreceiver_V2_R2",
                "strongreceiver_V2_all4_unbounded", "three_senders_V2_unbounded",
                "three_receivers_V2_unbounded", "weakobs_V6_R2", "weakobs_V7_R2"):
        r = d.tlc[job]
        lines.append(f"| `{job}` | {r['status']} | {r['distinct_states']} | {r['note'] or 'see specs/configs/'} |")
    fam = d.family["per_variant"]["V1"]
    swept = len(d.sweep)
    lines += [
        "",
        "The two `weakobs` rows are a warning. They show that a weaker observation model would declare V6 and V7 safe.",
        "",
        "## Evidence totals",
        "",
        f"- TLC matrix jobs: {len(d.tlc)}, all as predicted.",
        f"- Schedule rule, TLC: {swept} schedules, {sum(r['as_expected'] for r in d.sweep)} as predicted, {sum(r['status'] == 'violated' for r in d.sweep)} leak.",
        f"- Schedule rule, Python: {fam['schedules']} schedules, {fam['prediction_disagreements']} disagreements.",
        f"- Witnesses replayed exactly: {d.replay['witnesses'] - len(d.replay['failures'])} of {d.replay['witnesses']}.",
        f"- TLC and Python graphs: {d.corr['matching']} of {d.corr['cases']} configurations match, over {d.corr['total_nodes_compared']} states and {d.corr['total_edges_compared']} edges.",
        f"- Lean: {len(d.lean['theorems'])} theorems recorded, status {d.lean['status']}, no `sorry`. `noninterference` depends only on `{', '.join(d.lean['theorems']['VeriSwarm.noninterference'])}`.",
        f"- Automated tests: {d.tests['passed'] if d.tests else 'not recorded'} passed, {d.tests['failed'] if d.tests else '?'} failed.",
        (f"- Clean-room rerun from source in a fresh environment: {len(d.clean_room['deterministic_summaries_compared'])} measurement summaries identical byte for byte, "
         f"{d.clean_room['tlc_jobs_compared']} TLC verdicts and state counts identical, {len(d.clean_room['differences'])} differences."
         if d.clean_room else "- Clean-room rerun: not yet recorded."),
        "",
        "## Monitors",
        "",
        "| Attack vs. benign baseline | Legal flags | Attack flagged | Benign flagged | AUC, peak rate |",
        "|---|---|---|---|---|",
    ]
    for label, key in (("Plain code vs. W1", "baseline_atomic_vs_codebook"),
                       ("Plain code vs. W2", "baseline_contended_vs_codebook"),
                       ("Mimicry code vs. W2", "baseline_contended_vs_mimicry")):
        r = d.monitors[key]
        lines.append(f"| {label} | {r['legal_flags_on_attack']} | {r['attack_flagged']}/{r['n_each']} | {r['benign_flagged']}/{r['n_each']} | {r['auc_peak_rate']:.3f} |")
    m = d.monitors
    lines += [
        "",
        f"Mimicry decodes {m['mimicry_decode']['correct']}/{m['mimicry_decode']['total']} on V1 and {m['mimicry_decode_V2']['correct']}/{m['mimicry_decode_V2']['total']} on V2.",
        "",
        "## Noise (V8)",
        "",
        "| p | Capacity (bits/round) | B jobs surviving | Decoded at 1 round per bit | Decoded at 45 rounds per bit |",
        "|---|---|---|---|---|",
    ]
    for row in d.noise["levels"]:
        rep = row["repetition"]
        lines.append(
            f"| {row['noise_p']:g} | {row['capacity_closed_form']:.4f} | {100 * row['receiver_completion']:.1f}% "
            f"| {100 * rep['1']['accuracy']:.1f}% | {100 * rep['45']['accuracy']:.1f}% |"
        )
    llm = d.llm
    lines += ["", "## LLM agents (optional, pre-registered)", ""]
    if llm["status"] == "not_run":
        lines.append("Not run.")
    else:
        lines += [
            f"Design and predictions: [LLM_PREREGISTRATION.md](LLM_PREREGISTRATION.md). {len(llm['plan']['seeds'])} episodes of "
            f"{llm['plan']['rounds']} rounds per cell, {llm['calls']} calls, requested model `{llm['plan']['model']}` with the default "
            f"refusal fallback. Calls answered, by model: {', '.join(f'`{m}` {n}' for m, n in sorted(llm['served_by'].items()))}.",
            "",
            "| Variant, condition | Decoded (95% interval) | busy = bit | Sender followed code | Unanswered | No-tool turns | Monitor flags |",
            "|---|---|---|---|---|---|---|",
        ]
        for key, label in LLM_CELLS:
            c = llm["cells"][key]
            lo, hi = c["accuracy_answered_ci95"]
            lines.append(f"| {label} | {c['correct']}/{c['answered']} ({lo:.2f} to {hi:.2f}) | {c['busy_matches_bit']}/{c['receiver_try_job_rounds']} "
                         f"| {c['sender_followed_code']}/{c['rounds']} | {c['unanswered']} | {c['no_tool_turns']} | {c['monitor_flags']} |")
        lines += ["", "Predictions: " + "; ".join(f"{p['id']} {'met' if p['met'] else 'not met'}" for p in llm["predictions"]) + "."]
        replay = llm.get("opus55_replay")
        if replay:
            lines += ["", f"Replay of every distinct prompt to `{replay['model']}` with fallbacks off: {replay['declined']} of "
                          f"{replay['distinct_prompts']} declined, categories {replay['categories']}."]
        lines += ["", f"Estimated cost, counting declined attempts: ${llm['cost_usd_upper_bound']:.2f} for the run"
                      + (f", ${replay['cost_usd_upper_bound']:.2f} for the replay." if replay else ".")]
    done = {part: s for part, s in d.parts.items() if s["status"] != "not_run"}
    if done:
        lines += ["", "## Mixed pairs (optional, pre-registered, parts 2 to 5)", "",
                  "Claude is `claude-opus-4-8`; GLM is `zai-org/GLM-5.3`, Qwen is `Qwen/Qwen3.8-2.4T-A95B` and Llama is "
                  "`meta-llama/Llama-4-Scout-17B-16E-Instruct`, all three through Hugging Face with provider `deepinfra`. "
                  "2 episodes of 20 rounds per cell. M cells are part 2, Q part 3, L part 4 and F part 5. The provider dropped "
                  "most of Llama's tool calls in part 4, so part 5 reran every L cell with a tool call required; the figure "
                  "and report use the F cells, and the L cells stay here for the record.",
                  "", "| Cell | Variant, condition | Sender to receiver | Decoded (95% interval) | Holds when bit 1 / bit 0 | busy = bit | Unanswered | No-tool turns |",
                  "|---|---|---|---|---|---|---|---|"]
        for part, mid, key in ALL_PAIR_CELLS:
            c = pair_cell(d, part, key)
            if c is None:
                continue
            variant, condition, seats = key.split("/")
            sender, receiver = seats.split("->")
            lo, hi = c["accuracy_answered_ci95"]
            lines.append(f"| {mid}{'*' if c['episodes'] < 2 else ''} | {variant}, {'goal only' if condition == 'no_hint' else 'code supplied'} | "
                         f"{SEAT_NAMES[sender]} to {SEAT_NAMES[receiver]} | {c['correct']}/{c['answered']} ({lo:.2f} to {hi:.2f}) | "
                         f"{holds_by_bit(c)} | {c['busy_matches_bit']}/{c['receiver_try_job_rounds']} | {c['unanswered']} | {c['no_tool_turns']} |")
        predictions = [p for s in done.values() for p in s["predictions"]]
        lines += ["", "\\* One episode: the other timed out at the provider's gateway on three attempts and was not "
                      "retried (part 5, amendment A2)."]
        lines += ["", "Predictions: " + "; ".join(f"{p['id']} {'met' if p['met'] else 'not met'}" for p in predictions) + ".",
                  "", f"Estimated cost: ${sum(s['cost_usd_upper_bound'] for s in done.values()):.2f} over "
                      f"{sum(s['calls'] for s in done.values())} calls."]
    return "\n".join(lines) + "\n"


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def compile_pdf() -> tuple[int, str]:
    build = REPORT / "build"
    build.mkdir(exist_ok=True)
    out = subprocess.run(
        ["tectonic", "--keep-logs", "--outdir", "build", "report.tex"],
        cwd=REPORT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    return out.returncode, out.stdout + out.stderr


def publish_pdf() -> bool:
    """Copies the checked build to report/report.pdf. False if a viewer holds the old file open."""
    try:
        shutil.copy2(REPORT / "build" / "report.pdf", REPORT / "report.pdf")
        return True
    except PermissionError:
        return False


def main_page_count(pdf: Path) -> tuple[int, int]:
    """Pages before the references start, counting a partial page as one, and total pages."""
    import pymupdf

    doc = pymupdf.open(pdf)
    total = doc.page_count
    start_of_refs = None
    for i in range(total):
        text = doc[i].get_text()
        if re.search(r"^\s*References\s*$", text, re.MULTILINE):
            start_of_refs = i + 1
            break
    return (start_of_refs if start_of_refs is not None else total), total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    d = Data()
    check_claims(d)
    GEN.mkdir(parents=True, exist_ok=True)
    SUBMISSION.mkdir(parents=True, exist_ok=True)

    macros = {**values(d), **llm_values(d), **pair_values(d), **design_values(d), **agent_values(d), **core_values(d)}
    macro_lines = [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(macros.items())]
    (GEN / "values.tex").write_text("\n".join(macro_lines) + "\n", encoding="utf-8")
    (GEN / "table_variants.tex").write_text(table_variants(d), encoding="utf-8")
    (GEN / "table_formal.tex").write_text(table_formal(d), encoding="utf-8")
    (GEN / "table_strength.tex").write_text(table_strength(d), encoding="utf-8")
    (GEN / "table_monitors.tex").write_text(table_monitors(d), encoding="utf-8")
    (GEN / "timed_out.tex").write_text(timed_out_tex(d), encoding="utf-8")
    (GEN / "table_llm.tex").write_text(table_llm(d), encoding="utf-8")
    (GEN / "table_llm_pairs.tex").write_text(table_llm_pairs(d), encoding="utf-8")
    (GEN / "table_design.tex").write_text(table_design(d), encoding="utf-8")
    (ROOT / "docs" / "RESULTS.md").write_text(results_markdown(d), encoding="utf-8")

    abstract = abstract_text(d)
    words = word_count(abstract)
    if not 150 <= words <= 250:
        raise ClaimFailed(f"the abstract has {words} words; the event requires 150 to 250")
    (GEN / "abstract.tex").write_text(abstract + "\n", encoding="utf-8")
    (SUBMISSION / "abstract.txt").write_text(abstract + "\n", encoding="utf-8")

    check = {"abstract_words": words, "claims_checked": True, "pdf": "not built"}
    if not args.no_pdf:
        code, log = compile_pdf()
        if code != 0:
            print(log[-3000:])
            return code
        built = REPORT / "build" / "report.pdf"
        main_pages, total = main_page_count(built)
        placeholders = PLACEHOLDER in (REPORT / "metadata.tex").read_text(encoding="utf-8")
        published = publish_pdf()
        check.update({
            "pdf": "report/report.pdf" if published else "report/build/report.pdf (report/report.pdf was locked)",
            "main_text_pages": main_pages,
            "total_pages": total,
            "main_text_limit": MAX_MAIN_PAGES,
            "within_limit": main_pages <= MAX_MAIN_PAGES,
            "size_bytes": built.stat().st_size,
            "author_placeholders_remaining": placeholders,
        })
        if not published:
            print("report/report.pdf is open in another program, so it was not replaced. "
                  "The checked PDF is report/build/report.pdf; close the viewer and run this again.")
        print(f"report.pdf: {total} pages, {main_pages} before references (limit {MAX_MAIN_PAGES}), "
              f"{check['size_bytes']/1e6:.2f} MB")
        if placeholders:
            print("reminder: author names and affiliations are still placeholders in report/metadata.tex")
        warnings = [line for line in log.splitlines() if "Overfull" in line or "undefined" in line.lower()]
        check["latex_warnings"] = warnings[:40]
        if warnings:
            print(f"{len(warnings)} LaTeX warnings; first: {warnings[0]}")

    (SUMMARY / "report_check.json").write_text(json.dumps(check, indent=1, sort_keys=True), encoding="utf-8")
    print(f"abstract: {words} words. All claims hold.")
    if "locked" in check.get("pdf", ""):
        return 4
    return 0 if check.get("within_limit", True) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClaimFailed as exc:
        print(f"CLAIM FAILED: {exc}", file=sys.stderr)
        raise SystemExit(3)

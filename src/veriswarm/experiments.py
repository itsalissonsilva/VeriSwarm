"""The measurement experiments. Every number in the report comes from these outputs.

Fixed seeds throughout. Secrets and guesses are written only to evaluator files.
Scripted controllers test what the infrastructure permits, not what any LLM would
choose to do.
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from .agents import (
    AtomicBenignSender,
    CodebookSender,
    ContendedBenignSender,
    ForgingSender,
    FrozenDecoder,
    TryJobReceiver,
    features_of,
)
from .config import VARIANTS, ModelConfig, default_config
from .leakage import (
    bits_per_round,
    channel_capacity_per_round,
    codebook_information,
    fixed_policy_histories,
    max_distinct_histories,
)
from .metrics import (
    auc,
    binary_entropy,
    blahut_arimoto,
    bootstrap_auc_interval,
    bootstrap_group_interval,
    bootstrap_mi_interval,
    empirical_mutual_information,
    miller_madow_correction,
    z_channel_capacity,
)
from .monitor import (
    LegalActionMonitor,
    RateMonitor,
    acquire_fraction,
    audit_is_gapless,
    lag1_autocorrelation,
)
from .runner import run_episode

ALL = tuple(VARIANTS)
GROUPS = 10
BITS_PER_GROUP = 100
TRAIN_SEED = 900001
NOISE_FOR_V8 = 0.5
NOISE_LEVELS = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99)


def balanced_bits(n: int, seed: int) -> list[int]:
    """Exactly n/2 of each value, in seeded random order."""
    if n % 2:
        raise ValueError("balanced bits need an even count")
    bits = [0, 1] * (n // 2)
    random.Random(f"bits|{seed}").shuffle(bits)
    return bits


def _transmit(variant: str, bits: list[int], seed: int, run_id: str, noise_p: float,
              codebook=("noop", "acquire"), track_audit: bool = True):
    cfg = default_config(variant, max_rounds=len(bits), track_audit=track_audit)
    return run_episode(
        cfg,
        senders={"A": CodebookSender(*codebook)},
        receivers={"B": TryJobReceiver()},
        bits=bits,
        seed=seed,
        run_id=run_id,
        noise_p=noise_p,
    )


def train_decoder(variant: str, noise_p: float) -> FrozenDecoder:
    """Learned on a training episode that shares no seed with any test group."""
    bits = balanced_bits(BITS_PER_GROUP, TRAIN_SEED)
    ep = _transmit(variant, bits, TRAIN_SEED, f"train-{variant}", noise_p)
    period = len(VARIANTS[variant].default_schedule)
    return FrozenDecoder().fit(list(zip(features_of(ep.receiver_events["B"], period), bits)))


def experiment_transmission(raw_dir: Path) -> dict:
    """10 groups of 100 fresh balanced bits per variant, with a frozen decoder."""
    events_path = raw_dir / "transmission_events.jsonl"
    evaluator_path = raw_dir / "transmission_evaluator.jsonl"
    out = {}
    with events_path.open("w", encoding="utf-8") as ev, evaluator_path.open("w", encoding="utf-8") as ef:
        for variant in ALL:
            noise = NOISE_FOR_V8 if VARIANTS[variant].noisy_receiver else 0.0
            decoder = train_decoder(variant, noise)
            group_correct, conditional = [], defaultdict(Counter)
            pairs = []
            group_counts: list[Counter] = []
            legal = LegalActionMonitor(tuple(VARIANTS[variant].default_schedule), VARIANTS[variant].records_identity)
            flags, audited, gapless = 0, 0, True
            for g in range(GROUPS):
                seed = 1000 + g
                bits = balanced_bits(BITS_PER_GROUP, seed)
                run_id = f"tx-{variant}-g{g}"
                ep = _transmit(variant, bits, seed, run_id, noise)
                features = features_of(ep.receiver_events["B"], len(VARIANTS[variant].default_schedule))
                guesses = [decoder.decode(f) for f in features]
                correct = sum(int(a == b) for a, b in zip(guesses, bits))
                group_correct.append(correct)
                group_counts.append(Counter(zip(bits, features)))
                for b, f, e, guess in zip(bits, features, ep.receiver_events["B"], guesses):
                    conditional[b][f] += 1
                    pairs.append((b, f))
                    ef.write(json.dumps({"run_id": run_id, "variant": variant, "seed": seed, "round": e.round,
                                         "bit": b, "guess": guess, "feature": list(f)}) + "\n")
                for row in ep.events:
                    ev.write(json.dumps(row) + "\n")
                report = legal.review(ep.audit)
                flags += len(report.flags)
                audited += report.events_seen
                gapless = gapless and audit_is_gapless(ep.audit)

            total = sum(group_correct)
            n = GROUPS * BITS_PER_GROUP
            rates = [c / BITS_PER_GROUP for c in group_correct]
            deterministic = not VARIANTS[variant].noisy_receiver
            interval = None
            if not deterministic:
                interval = list(bootstrap_group_interval(rates, seed=hash_seed(variant)))
            out[variant] = {
                "variant": variant,
                "title": VARIANTS[variant].title,
                "correct": total,
                "total": n,
                "accuracy": total / n,
                "group_correct": group_correct,
                "deterministic": deterministic,
                "bootstrap_95": interval,
                "noise_p": noise,
                "decoder_table": {repr(k): v for k, v in sorted(decoder.table.items(), key=repr)},
                "conditional_given_bit": {
                    str(b): {repr(k): v for k, v in sorted(c.items(), key=repr)} for b, c in conditional.items()
                },
                "mi_plugin_bits": empirical_mutual_information(pairs),
                "mi_miller_madow_term_bits": miller_madow_correction(pairs),
                "mi_group_bootstrap_95": list(bootstrap_mi_interval(group_counts, seed=hash_seed("mi" + variant))),
                "mi_exact_uniform_codebook_bits": codebook_information(
                    default_config(variant, max_rounds=1, track_audit=False), noise
                ),
                "audit_events": audited,
                "audit_gapless": gapless,
                "legal_monitor_flags": flags,
                "legal_monitor_flag_rate": flags / audited if audited else 0.0,
            }
    return out


def hash_seed(text: str) -> int:
    """A stable integer from a string. Python's own hash() varies between runs."""
    return sum((i + 1) * ord(c) for i, c in enumerate(text)) + 7


def _benign(variant: str, workload: str, seed: int, rounds: int, q: float, noise_p: float):
    cfg = default_config(variant, max_rounds=rounds, track_audit=True)
    if workload == "W1":
        sender = AtomicBenignSender()
    else:
        sender = ContendedBenignSender(q, random.Random(f"work|{seed}"))
    return run_episode(cfg, {"A": sender}, {"B": TryJobReceiver()}, None, seed, f"{workload}-{variant}-{seed}", noise_p)


def experiment_benign(raw_dir: Path) -> dict:
    """10 seeded schedules of 100 rounds, one private job request per principal per round."""
    out = {"W1": {}, "W2": {}}
    q = 0.5
    with (raw_dir / "benign_events.jsonl").open("w", encoding="utf-8") as ev:
        for workload in ("W1", "W2"):
            for variant in ALL:
                noise = NOISE_FOR_V8 if VARIANTS[variant].noisy_receiver else 0.0
                totals = Counter()
                slots = 0
                completed_per_seed = []
                for g in range(GROUPS):
                    seed = 2000 + g
                    ep = _benign(variant, workload, seed, BITS_PER_GROUP, q, noise)
                    for row in ep.events:
                        ev.write(json.dumps({**row, "workload": workload}) + "\n")
                        if row["principal"] == "B":
                            totals[f"B_{row['response']}"] += 1
                        elif row["action"] == "acquire":
                            totals["A_holds"] += 1
                    totals["A_requested"] += ep.requests("A")
                    totals["A_completed"] += ep.jobs("A")
                    totals["B_requested"] += ep.requests("B")
                    totals["B_completed"] += ep.jobs("B")
                    slots += ep.slots
                    completed_per_seed.append(ep.jobs("B"))
                done = totals["A_completed"] + totals["B_completed"]
                rates = [c / BITS_PER_GROUP for c in completed_per_seed]
                out[workload][variant] = {
                    "variant": variant,
                    "title": VARIANTS[variant].title,
                    "A_requested": totals["A_requested"],
                    "A_completed": totals["A_completed"],
                    "A_holds": totals["A_holds"],
                    "B_requested": totals["B_requested"],
                    "B_completed": totals["B_completed"],
                    "B_completion_rate": totals["B_completed"] / totals["B_requested"],
                    "B_completion_bootstrap_95": list(bootstrap_group_interval(rates, seed=hash_seed(workload + variant))),
                    "B_busy": totals["B_busy"],
                    "B_denied": totals["B_denied"],
                    "busy_rate": totals["B_busy"] / totals["B_requested"],
                    "denial_rate": totals["B_denied"] / totals["B_requested"],
                    "logical_slots": slots,
                    "slots_per_round": slots / (GROUPS * BITS_PER_GROUP),
                    "slots_per_completed_job": slots / done if done else None,
                    "backing_locks_for_two_principals": 1 if VARIANTS[variant].shared_backing else 2,
                }
    return out


def experiment_exact_leakage() -> dict:
    """Exact finite-model leakage, independent of any sampled run."""
    out = {}
    for variant in ALL:
        spec = VARIANTS[variant]
        row: dict = {"variant": variant, "title": spec.title}
        if not spec.noisy_receiver:
            row["max_distinct_histories"] = {
                str(r): max_distinct_histories(default_config(variant, max_rounds=r, track_audit=False))
                for r in range(1, 7)
            }
            row["bits_per_round"] = bits_per_round(default_config(variant, max_rounds=6, track_audit=False))
            row["capacity_per_round"] = channel_capacity_per_round(default_config(variant, max_rounds=1, track_audit=False))
        else:
            row["capacity_per_round"] = channel_capacity_per_round(
                default_config(variant, max_rounds=1, track_audit=False), NOISE_FOR_V8
            )
            row["noise_p"] = NOISE_FOR_V8
        out[variant] = row
    return out


def _repetition_accuracy(variant: str, noise_p: float, k: int, seed: int, n_bits: int = 400) -> tuple[int, int]:
    """Sends each bit k times; decodes 0 if any attempt returned ok, else 1."""
    bits = balanced_bits(n_bits, seed)
    stretched = [b for b in bits for _ in range(k)]
    ep = _transmit(variant, stretched, seed, f"rep-{variant}-{noise_p}-{k}-{seed}", noise_p, track_audit=False)
    resp = [e.resp for e in ep.receiver_events["B"]]
    correct = 0
    for i, b in enumerate(bits):
        chunk = resp[i * k : (i + 1) * k]
        guess = 0 if "ok" in chunk else 1
        correct += int(guess == b)
    return correct, n_bits


def experiment_noise() -> dict:
    """Random busy replies on a shared lock: what they cost and what they leave open."""
    rows = []
    for p in NOISE_LEVELS:
        closed_form = z_channel_capacity(p)
        ba = blahut_arimoto([[1 - p, p], [0.0, 1.0]]) if 0 < p < 1 else closed_form
        from_model = channel_capacity_per_round(default_config("V8", max_rounds=1, track_audit=False), p)
        # Utility: the fraction of an uncontended receiver's jobs that survive the noise.
        ep = _benign("V8", "W1", 3000, 4000, 0.0, p)
        utility = ep.jobs("B") / ep.requests("B")
        rep = {}
        for k in (1, 3, 5, 9, 15, 25, 45):
            good = tot = 0
            for g in range(3):
                c, n = _repetition_accuracy("V8", p, k, seed=3100 + g)
                good, tot = good + c, tot + n
            rep[str(k)] = {"correct": good, "total": tot, "accuracy": good / tot}
        rows.append(
            {
                "noise_p": p,
                "capacity_closed_form": closed_form,
                "capacity_blahut_arimoto": ba,
                "capacity_from_semantics": from_model,
                "receiver_completion": utility,
                "receiver_jobs": [ep.jobs("B"), ep.requests("B")],
                "repetition": rep,
            }
        )
    baseline = {
        "V2": {"capacity": channel_capacity_per_round(default_config("V2", max_rounds=1, track_audit=False)), "receiver_completion": 1.0},
        "V9": {"capacity": channel_capacity_per_round(default_config("V9", max_rounds=1, track_audit=False)), "receiver_completion": 1.0},
    }
    return {"levels": rows, "reference": baseline}


def _audit_key(audit) -> tuple:
    return tuple((e.seq, e.round, e.slot, e.actor, e.act, e.object, e.resp, e.job) for e in audit)


def exact_log_distributions(rounds: int) -> tuple[Counter, Counter]:
    """Exact multiset of audit logs for the mimicry attack and for benign W2 at q = 1/2.

    Each attack log arises from one bit string, and each benign log from one hold
    pattern, all equally likely. Equal multisets mean equal distributions, so no
    function of the log alone can tell them apart.
    """
    import itertools

    attack, benign = Counter(), Counter()
    for pattern in itertools.product((0, 1), repeat=rounds):
        bits = list(pattern)
        ep = _transmit("V1", bits, 0, "exact-attack", 0.0, codebook=("try_job", "acquire"))
        attack[_audit_key(ep.audit)] += 1

        class Fixed:
            name = "fixed-hold-pattern"

            def __init__(self, holds):
                self.holds = holds

            def act(self, view):
                from .gateway import ToolRequest

                return ToolRequest("acquire" if self.holds[view.round - 1] else "try_job")

        cfg = default_config("V1", max_rounds=rounds, track_audit=True)
        ep2 = run_episode(cfg, {"A": Fixed(bits)}, {"B": TryJobReceiver()}, None, 0, "exact-benign")
        benign[_audit_key(ep2.audit)] += 1
    return attack, benign


def experiment_monitors() -> dict:
    """Narrow monitor comparison. Log-only monitors against two benign baselines."""
    rounds, n_train, n_test = 100, 200, 200

    def attack(seed: int, codebook=("noop", "acquire")):
        bits = [random.Random(f"attackbits|{seed}|{i}").randint(0, 1) for i in range(rounds)]
        return _transmit("V1", bits, seed, f"mon-attack-{seed}", 0.0, codebook=codebook), bits

    def benign(workload: str, seed: int):
        return _benign("V1", workload, seed, rounds, 0.5, 0.0)

    results = {}
    legal = LegalActionMonitor(("A", "B", "M"))

    # Positive control: the monitor is not a rubber stamp.
    cfg = default_config("V1", max_rounds=rounds, track_audit=True)
    forged = run_episode(cfg, {"A": ForgingSender()}, {"B": TryJobReceiver()}, None, 1, "mon-forge")
    forged_report = legal.review(forged.audit)
    results["legal_positive_control"] = {
        "events": forged_report.events_seen,
        "flags": len(forged_report.flags),
        "illegal_requests": rounds,
    }

    for name, workload, codebook in (
        ("baseline_atomic_vs_codebook", "W1", ("noop", "acquire")),
        ("baseline_contended_vs_codebook", "W2", ("noop", "acquire")),
        ("baseline_contended_vs_mimicry", "W2", ("try_job", "acquire")),
    ):
        train = [benign(workload, 5000 + i).audit for i in range(n_train)]
        test_benign = [benign(workload, 6000 + i).audit for i in range(n_test)]
        test_attack = [attack(7000 + i, codebook)[0].audit for i in range(n_test)]
        monitor = RateMonitor().fit(train)
        s_b = [monitor.score(a) for a in test_benign]
        s_a = [monitor.score(a) for a in test_attack]
        # A second statistic: how far the run's acquire share is from benign.
        benign_share = sum(acquire_fraction(a) for a in train) / len(train)
        d_b = [abs(acquire_fraction(a) - benign_share) for a in test_benign]
        d_a = [abs(acquire_fraction(a) - benign_share) for a in test_attack]
        # A third: serial dependence in the holds.
        ac_b = [abs(lag1_autocorrelation(a)) for a in test_benign]
        ac_a = [abs(lag1_autocorrelation(a)) for a in test_attack]
        # A fourth, structural one: calls per round. A silent no-op leaves a gap.
        calls_b = [-sum(1 for e in a if e.actor == "A") for a in test_benign]
        calls_a = [-sum(1 for e in a if e.actor == "A") for a in test_attack]
        results[name] = {
            "threshold": monitor.threshold,
            "benign_flagged": sum(monitor.flags(a) for a in test_benign),
            "attack_flagged": sum(monitor.flags(a) for a in test_attack),
            "n_each": n_test,
            "legal_flags_on_attack": sum(len(legal.review(a).flags) for a in test_attack),
            "attack_audit_events": sum(len(a) for a in test_attack),
            "auc_peak_rate": auc(s_a, s_b),
            "auc_peak_rate_95": list(bootstrap_auc_interval(s_a, s_b, seed=11)),
            "auc_share_distance": auc(d_a, d_b),
            "auc_share_distance_95": list(bootstrap_auc_interval(d_a, d_b, seed=12)),
            "auc_autocorrelation": auc(ac_a, ac_b),
            "auc_autocorrelation_95": list(bootstrap_auc_interval(ac_a, ac_b, seed=13)),
            "auc_missing_calls": auc(calls_a, calls_b),
            "auc_missing_calls_95": list(bootstrap_auc_interval(calls_a, calls_b, seed=14)),
        }

    exact = {}
    for r in range(1, 7):
        a, b = exact_log_distributions(r)
        exact[str(r)] = {"distinct_logs": len(a), "identical_distribution": a == b}
    results["exact_log_equality_mimicry_vs_W2"] = exact

    # Decode accuracy of the mimicry attacker, so "undetected" is not "useless".
    good = tot = 0
    for g in range(GROUPS):
        bits = balanced_bits(BITS_PER_GROUP, 8000 + g)
        ep = _transmit("V1", bits, 8000 + g, f"mimic-{g}", 0.0, codebook=("try_job", "acquire"))
        guesses = [1 if e.resp == "busy" else 0 for e in ep.receiver_events["B"]]
        good += sum(int(x == y) for x, y in zip(guesses, bits))
        tot += len(bits)
    results["mimicry_decode"] = {"correct": good, "total": tot}

    # The same mimicry attacker against a partitioned gateway.
    good = tot = 0
    for g in range(GROUPS):
        bits = balanced_bits(BITS_PER_GROUP, 8000 + g)
        ep = _transmit("V2", bits, 8000 + g, f"mimicV2-{g}", 0.0, codebook=("try_job", "acquire"))
        guesses = [1 if e.resp == "busy" else 0 for e in ep.receiver_events["B"]]
        good += sum(int(x == y) for x, y in zip(guesses, bits))
        tot += len(bits)
    results["mimicry_decode_V2"] = {"correct": good, "total": tot}

    results["undetectable_rate_bits_per_round"] = {
        str(q): binary_entropy(q) for q in (0.1, 0.25, 0.5, 0.75, 0.9)
    }
    return results


def experiment_broadcast() -> dict:
    """One sender, many receivers: a shared lock is a broadcast medium."""
    out = {}
    names = "BCDEFGH"
    for variant in ("V1", "V2"):
        rows = {}
        for n in (1, 2, 3, 5, 7):
            receivers = tuple(names[:n])
            schedule = ("A", *receivers, "M")
            cfg = ModelConfig(variant=variant, schedule=schedule, senders=("A",), receivers=receivers,
                              max_rounds=BITS_PER_GROUP, track_audit=True)
            bits = balanced_bits(BITS_PER_GROUP, 9000)
            ep = run_episode(cfg, {"A": CodebookSender()}, {r: TryJobReceiver() for r in receivers}, bits, 9000, f"bc-{variant}-{n}")
            correct = {}
            for r in receivers:
                guesses = [1 if e.resp == "busy" else 0 for e in ep.receiver_events[r]]
                correct[r] = sum(int(x == y) for x, y in zip(guesses, bits))
            rows[str(n)] = {
                "receivers": n,
                "correct_per_receiver": correct,
                "bits_per_receiver": BITS_PER_GROUP,
                "all_receivers_correct_rounds": sum(
                    all((1 if ep.receiver_events[r][i].resp == "busy" else 0) == bits[i] for r in receivers)
                    for i in range(BITS_PER_GROUP)
                ),
                "backing_locks": 1 if VARIANTS[variant].shared_backing else n + 1,
            }
        out[variant] = rows
    return out


def _family_case(task: tuple[str, tuple[str, ...], int]) -> tuple[str, str, bool, bool]:
    """One schedule. Top-level so a process pool can run it."""
    from .graph import receiver_equivalent_everywhere
    from .leakage import leak_predicted_by_schedule

    variant, letters, rounds = task
    cfg = ModelConfig(variant=variant, schedule=letters, max_rounds=rounds, track_audit=False,
                      sender_actions=("noop", "acquire", "release"), receiver_actions=("try_job",))
    holds = receiver_equivalent_everywhere(cfg, one_secret_pair=True)
    predicted = leak_predicted_by_schedule(letters, rounds) if variant in ("V0", "V1") else False
    return variant, "".join(letters), holds, predicted


def experiment_schedule_family(max_len: int = 6, rounds: int = 2, workers: int = 6) -> dict:
    """Tests the leak characterisation on every schedule, using the Python semantics.

    Wider than the TLC sweep and lower assurance. It uses a separate implementation, so
    it can disagree with the characterisation or with TLC.
    """
    import itertools
    from concurrent.futures import ProcessPoolExecutor

    variants = ("V1", "V2")
    tasks = [
        (variant, letters, rounds)
        for variant in variants
        for n in range(1, max_len + 1)
        for letters in itertools.product("ABM", repeat=n)
    ]
    with ProcessPoolExecutor(workers) as pool:
        outcomes = list(pool.map(_family_case, tasks, chunksize=16))

    out = {
        "max_len": max_len,
        "rounds": rounds,
        "sender_actions": ["noop", "acquire", "release"],
        "receiver_actions": ["try_job"],
        "per_variant": {},
    }
    for variant in variants:
        rows = [r for r in outcomes if r[0] == variant]
        bad = [schedule for _, schedule, holds, predicted in rows if predicted == holds]
        leaking = sum(not holds for _, _, holds, _ in rows)
        out["per_variant"][variant] = {
            "schedules": len(rows),
            "leaking": leaking,
            "isolated": len(rows) - leaking,
            "prediction_disagreements": len(bad),
            "examples": bad[:10],
        }
    return out


def cross_check_values() -> dict:
    """Histories counted by Python with a fixed receiver policy, for comparison to TLC."""
    out = {}
    for variant in ALL:
        cfg = default_config(variant, max_rounds=2, track_audit=False)
        out[variant] = len(fixed_policy_histories(cfg))
    return out

"""Checks a gateway design described by its features, not by a variant name.

The ten named variants are built from a handful of independent features: what backs each
principal's lock, a shared capacity pool, a counter returned to callers, a scheduler that
gives an idle sender's slot away, random busy replies, and the public slot schedule. A
`Design` is any combination of them. `check` runs the relational property on the Python
product exploration, at one and two rounds and, where the state space is finite without a
round counter, at every horizon. A leak comes with a concrete witness.

Evidence level: for a design whose features equal a named variant, TLC has checked the same
property and `check` reports that variant. For any other combination the evidence is the
Python exploration alone. Its state graphs matched TLC's on every configuration compared,
which is tested correspondence, not a proof.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .config import MAINT, VARIANTS, ModelConfig, VariantSpec
from .graph import receiver_equivalent_everywhere, successors_pair, successors_single
from .leakage import channel_capacity_per_round, max_distinct_histories
from .semantics import current_slot, initial_state, is_terminal

FEATURES = ("shared_backing", "object_scheme", "receiver_denied", "pool_capacity_one",
            "exposes_audit_counter", "work_conserving", "noisy_receiver", "default_schedule")
KEY = "DESIGN"


@dataclass(frozen=True)
class Design:
    """backing: "shared" (one lock), "private" (one per principal) or "aliased"
    (per-principal names over one hidden lock)."""

    backing: str = "shared"
    shared_quota: bool = False
    exposed_counter: bool = False
    work_conserving: bool = False
    noisy: bool = False
    schedule: str = "ABM"
    receiver_denied: bool = False

    def __post_init__(self) -> None:
        if self.backing not in ("shared", "private", "aliased"):
            raise ValueError(f"unknown backing {self.backing!r}")
        if not self.schedule or set(self.schedule) - {"A", "B", "M"} or "A" not in self.schedule or "B" not in self.schedule:
            raise ValueError(f"schedule must use A, B and M and contain A and B: {self.schedule!r}")

    def spec(self) -> VariantSpec:
        return VariantSpec(
            KEY, "design", "design check",
            shared_backing=self.backing != "private",
            object_scheme="single" if self.backing == "shared" else "per_principal",
            receiver_denied=self.receiver_denied,
            pool_capacity_one=self.shared_quota,
            exposes_audit_counter=self.exposed_counter,
            work_conserving=self.work_conserving,
            noisy_receiver=self.noisy,
            default_schedule=self.schedule,
        )

    def describe(self) -> str:
        parts = [f"{self.backing} lock" + ("s" if self.backing != "shared" else ""), f"schedule {self.schedule}"]
        parts += [name for name, on in (("shared quota", self.shared_quota), ("counter in replies", self.exposed_counter),
                                        ("work-conserving scheduler", self.work_conserving), ("random busy replies", self.noisy),
                                        ("receiver denied", self.receiver_denied)) if on]
        return ", ".join(parts)


def named_variant(design: Design) -> str | None:
    """The named variant with exactly these features, if any. V0 differs from V1 only in
    what the audit records, which no receiver observes, so a shared design maps to V1."""
    spec = design.spec()
    for key in sorted(VARIANTS):
        if key in (KEY, "V0"):
            continue
        if all(getattr(VARIANTS[key], f) == getattr(spec, f) for f in FEATURES):
            return key
    return None


@contextmanager
def registered(design: Design):
    """Makes the design visible to ModelConfig for the duration of a check."""
    if KEY in VARIANTS:
        raise RuntimeError("a design check is already in progress")
    VARIANTS[KEY] = design.spec()
    try:
        yield KEY
    finally:
        del VARIANTS[KEY]


def _cfg(design: Design, rounds: int) -> ModelConfig:
    return ModelConfig(variant=KEY, schedule=tuple(design.schedule), max_rounds=rounds, track_audit=False)


def find_witness(cfg: ModelConfig) -> list[dict] | None:
    """The shortest pair of runs, from one secret pair, whose receiver observations differ."""
    start = (initial_state(cfg, (0,) * cfg.max_rounds), initial_state(cfg, (1,) * cfg.max_rounds))
    parent = {start: None}
    queue = deque([start])
    while queue:
        pair = queue.popleft()
        left, right = pair
        if left.obs != right.obs or left.lastObs != right.lastObs:
            path = []
            while pair is not None:
                path.append(pair)
                pair = parent[pair]
            path.reverse()
            steps = []
            for (l0, r0), (l1, r1) in zip(path, path[1:]):
                who = current_slot(cfg, l0)
                if who == MAINT:
                    steps.append({"slot": "M", "left": "maintenance", "right": "maintenance"})
                else:
                    steps.append({"slot": who, "left": dict(l1.lastAct)[who], "right": dict(r1.lastAct)[who],
                                  "left_reply": dict(l1.lastResp)[who], "right_reply": dict(r1.lastResp)[who]})
            return steps
        for nxt in successors_pair(cfg, left, right):
            if nxt not in parent:
                parent[nxt] = pair
                queue.append(nxt)
    return None


def receiver_can_be_blocked(cfg: ModelConfig) -> bool:
    """Whether some sender behaviour, or the gateway's noise, makes a receiver's job fail."""
    start = initial_state(cfg)
    seen = {start}
    queue = deque([start])
    while queue:
        s = queue.popleft()
        if any(dict(s.lastResp).get(r) == "busy" for r in cfg.receivers):
            return True
        if is_terminal(cfg, s):
            continue
        for t in successors_single(cfg, s):
            if t not in seen:
                seen.add(t)
                queue.append(t)
    return False


def check(design: Design, rounds: int = 2) -> dict:
    """Verdict, witness, leakage and utility for one design."""
    with registered(design):
        bounded = {r: receiver_equivalent_everywhere(_cfg(design, r), one_secret_pair=True) for r in range(1, rounds + 1)}
        # Without a round counter the state space is finite, except when replies carry a
        # counter or logical time depends on the sender; those are checked bounded only.
        unbounded_ok = not (design.exposed_counter or design.work_conserving)
        all_horizon = receiver_equivalent_everywhere(_cfg(design, 0), one_secret_pair=True) if unbounded_ok else None
        leaks = not all(bounded.values()) or all_horizon is False
        witness = None
        if leaks:
            r = next((r for r, ok in bounded.items() if not ok), None)
            witness = find_witness(_cfg(design, r)) if r is not None else None
        if design.noisy:
            leakage = {"capacity_bits_per_round_at_p_0_5": channel_capacity_per_round(_cfg(design, 1), 0.5)
                       if design.schedule.count("A") == 1 else None}
        else:
            histories = max_distinct_histories(_cfg(design, rounds))
            leakage = {"max_distinct_histories": histories, "bits_per_round": math.log2(histories) / rounds}
        blocked = receiver_can_be_blocked(_cfg(design, 2))
    return {
        "design": asdict(design),
        "description": design.describe(),
        "named_variant": named_variant(design),
        "leaks": leaks,
        "bounded": {str(r): ok for r, ok in bounded.items()},
        "all_horizon": all_horizon,
        "witness": witness,
        "leakage": leakage,
        "receiver_can_be_blocked": blocked,
    }


def design_space() -> list[Design]:
    """Every combination of the structural features, under the default and the flushed schedule."""
    out = []
    for backing, quota, counter, conserving, noisy, schedule in itertools.product(
        ("shared", "aliased", "private"), (False, True), (False, True), (False, True), (False, True), ("ABM", "AMBM")
    ):
        out.append(Design(backing, quota, counter, conserving, noisy, schedule))
    return out

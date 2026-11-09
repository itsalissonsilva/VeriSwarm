"""Paired counterexample format and exact replay.

A TLC trace states the full value of both copies at every step. Replay extracts
each slot's action, re-executes it through the Python semantics, and requires
every field of the resulting state to match the TLC state. Python's guess at the
outcome is never used to fill in anything the trace states.

Format `veriswarm-tlc-trace/1` (written by tlc.py): `states[i].vars` holds
`sched` and either `s`, or `l` and `r`, each a full state record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import MAINT, ModelConfig
from .semantics import (
    State,
    coins,
    current_slot,
    initial_state,
    state_to_tla,
    step,
)


class ReplayMismatch(RuntimeError):
    """The Python semantics disagree with the trace, or the trace is malformed."""


@dataclass
class ReplayStep:
    index: int
    slot_label: str
    left_action: str
    right_action: str
    left_response: str
    right_response: str
    left_coin: bool
    right_coin: bool
    obs_equal: bool


@dataclass
class ReplayReport:
    job_id: str
    variant: str
    schedule: str
    left_secret: list
    right_secret: list
    steps: list[ReplayStep] = field(default_factory=list)
    fields_compared: int = 0
    first_divergent_step: int | None = None
    left_view: list = field(default_factory=list)
    right_view: list = field(default_factory=list)


def config_from_constants(constants: dict, schedule: list[str]) -> ModelConfig:
    return ModelConfig(
        variant=constants["Variant"],
        schedule=tuple(schedule),
        senders=tuple(sorted(constants["Senders"])),
        receivers=tuple(sorted(constants["Receivers"])),
        sender_actions=tuple(constants["SenderActions"]),
        receiver_actions=tuple(constants["ReceiverActions"]),
        max_rounds=constants["MaxRounds"],
        obs_mode=constants["ObsMode"],
        sender_mode=constants["SenderMode"],
        track_audit=constants["TrackAudit"],
    )


def _count_leaves(value) -> int:
    if isinstance(value, dict):
        return sum(_count_leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(_count_leaves(v) for v in value) if value else 1
    return 1


def _replay_one(cfg: ModelConfig, s: State, expected: dict, where: str) -> tuple[State, str, bool]:
    """Advances `s` one slot to match `expected`. Returns (state, action, coin used)."""
    who = current_slot(cfg, s)
    if who == MAINT:
        t = step(cfg, s)
        if state_to_tla(t) != expected:
            raise ReplayMismatch(f"{where}: maintenance result differs from trace")
        return t, "flush", False

    last_act = expected.get("lastAct")
    if not isinstance(last_act, dict) or who not in last_act:
        raise ReplayMismatch(f"{where}: trace state has no action for {who}")
    action = last_act[who]
    # The coin is not an observable and is not stored; only coins the semantics
    # allow for this slot are tried, and exact state equality must still hold.
    for coin in coins(cfg, who, action):
        t = step(cfg, s, action, coin)
        if state_to_tla(t) == expected:
            return t, action, coin
    raise ReplayMismatch(
        f"{where}: replaying {who}.{action} does not reproduce the trace state"
    )


def replay_pair_trace(trace: dict) -> ReplayReport:
    """Replays both copies of a TLC paired trace, comparing every field at every step."""
    if trace.get("format") != "veriswarm-tlc-trace/1":
        raise ReplayMismatch("unknown trace format")
    states = trace["states"]
    if not states or "l" not in states[0]["vars"]:
        raise ReplayMismatch("not a paired trace")

    schedule = states[0]["vars"]["sched"]
    cfg = config_from_constants(trace["constants"], schedule)
    init_l = states[0]["vars"]["l"]
    init_r = states[0]["vars"]["r"]

    left = initial_state(cfg, tuple(init_l["secret"]))
    right = initial_state(cfg, tuple(init_r["secret"]))
    if state_to_tla(left) != init_l or state_to_tla(right) != init_r:
        raise ReplayMismatch("initial state differs from the trace")

    report = ReplayReport(
        job_id=trace["job_id"],
        variant=cfg.variant,
        schedule="".join(schedule),
        left_secret=list(init_l["secret"]),
        right_secret=list(init_r["secret"]),
    )
    report.fields_compared += _count_leaves(init_l) + _count_leaves(init_r)

    for prev, cur in zip(states, states[1:]):
        vars_ = cur["vars"]
        if vars_["sched"] != schedule:
            raise ReplayMismatch(f"step {cur['index']}: public schedule changed")
        label = current_slot(cfg, left)
        left, left_act, left_coin = _replay_one(cfg, left, vars_["l"], f"step {cur['index']} left")
        right, right_act, right_coin = _replay_one(cfg, right, vars_["r"], f"step {cur['index']} right")
        report.fields_compared += _count_leaves(vars_["l"]) + _count_leaves(vars_["r"])

        equal = left.obs == right.obs and left.lastObs == right.lastObs
        if not equal and report.first_divergent_step is None:
            report.first_divergent_step = cur["index"]
        report.steps.append(
            ReplayStep(
                index=cur["index"],
                slot_label=label,
                left_action=left_act,
                right_action=right_act,
                left_response=dict(left.lastResp).get(label, "-") if label != MAINT else "-",
                right_response=dict(right.lastResp).get(label, "-") if label != MAINT else "-",
                left_coin=left_coin,
                right_coin=right_coin,
                obs_equal=equal,
            )
        )

    report.left_view = state_to_tla(left)["obs"]
    report.right_view = state_to_tla(right)["obs"]
    return report


def replay_single_trace(trace: dict) -> int:
    """Replays a single-execution trace. Returns the number of slots replayed."""
    if trace.get("format") != "veriswarm-tlc-trace/1":
        raise ReplayMismatch("unknown trace format")
    states = trace["states"]
    schedule = states[0]["vars"]["sched"]
    cfg = config_from_constants(trace["constants"], schedule)
    first = states[0]["vars"]["s"]
    s = initial_state(cfg, tuple(first["secret"]))
    if state_to_tla(s) != first:
        raise ReplayMismatch("initial state differs from the trace")
    for cur in states[1:]:
        s, _, _ = _replay_one(cfg, s, cur["vars"]["s"], f"step {cur['index']}")
    return len(states) - 1


def load_trace(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def corrupt_action(trace: dict, step_index: int, side: str, principal: str, new_action: str) -> dict:
    """A deliberately corrupted copy of a trace, for testing that replay rejects it."""
    bad = json.loads(json.dumps(trace))
    target = next(s for s in bad["states"] if s["index"] == step_index)
    target["vars"][side]["lastAct"][principal] = new_action
    return bad


def corrupt_response(trace: dict, step_index: int, side: str, principal: str, new_response: str) -> dict:
    bad = json.loads(json.dumps(trace))
    target = next(s for s in bad["states"] if s["index"] == step_index)
    target["vars"][side]["lastResp"][principal] = new_response
    return bad

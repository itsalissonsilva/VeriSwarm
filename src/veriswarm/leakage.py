"""Exact leakage of the finite models, computed rather than estimated.

For a deterministic channel, the number of distinct outputs a receiver can be
made to see is the quantity that matters: log2 of it is the min-entropy leakage
(Smith, 2009). Here it is the most distinct observation histories any receiver
strategy, adaptive or not, can separate, across all sender behaviours.

Computed by exploring belief sets: the set of states the receiver cannot tell
apart. A sender slot widens the set. A receiver slot lets the receiver choose an
action, and the set splits by the response. The value of a set is the largest
number of leaves reachable from it.
"""

from __future__ import annotations

import math
from dataclasses import replace
from functools import lru_cache

from .config import MAINT, ModelConfig
from .metrics import blahut_arimoto, channel_information
from .observations import ObsEvent
from .semantics import (
    State,
    allowed_actions,
    coins,
    current_slot,
    initial_state,
    is_terminal,
    step,
)


def _project(cfg: ModelConfig, s: State) -> State:
    """Drops state that cannot affect any future receiver observation."""
    zero = tuple((p, 0 if p not in cfg.receivers else v) for p, v in s.jobs)
    req = tuple((p, 0) for p, _ in s.requests)
    return replace(
        s,
        jobs=zero,
        requests=req,
        lastAct=tuple((p, "none") for p, _ in s.lastAct),
        lastResp=tuple((p, "none") for p, _ in s.lastResp),
        lastObs=ObsEvent("none", 0, 0, 0, "none", "none", 0, 0),
        obs=(),
        audit=(),
        secret=(),
    )


def max_distinct_histories(cfg: ModelConfig) -> int:
    """Most distinct receiver histories over all senders and receiver strategies.

    Deterministic variants only: V8's coin is not a sender input, so this count
    is not its capacity. Use channel_capacity_per_round for V8.
    """
    if cfg.spec.noisy_receiver:
        raise ValueError("V8 is probabilistic; use channel_capacity_per_round")
    cfg_small = replace(cfg, track_audit=False, sender_mode="free")

    @lru_cache(maxsize=None)
    def value(states: frozenset[State]) -> int:
        any_state = next(iter(states))
        if is_terminal(cfg_small, any_state):
            return 1
        who = current_slot(cfg_small, any_state)
        if who == MAINT:
            return value(frozenset(_project(cfg_small, step(cfg_small, s)) for s in states))
        if who in cfg_small.senders:
            return value(
                frozenset(
                    _project(cfg_small, step(cfg_small, s, a))
                    for s in states
                    for a in allowed_actions(cfg_small, s, who)
                )
            )
        best = 0
        for action in cfg_small.receiver_actions:
            groups: dict[ObsEvent, set[State]] = {}
            for s in states:
                t = step(cfg_small, s, action)
                groups.setdefault(t.lastObs, set()).add(_project(cfg_small, t))
            best = max(best, sum(value(frozenset(g)) for g in groups.values()))
        return best

    secret = (0,) * cfg_small.max_rounds
    return value(frozenset({_project(cfg_small, initial_state(cfg_small, secret))}))


def bits_per_round(cfg: ModelConfig) -> float:
    return math.log2(max_distinct_histories(cfg)) / cfg.max_rounds


def fixed_policy_histories(cfg: ModelConfig, action: str = "try_job") -> set[tuple]:
    """Distinct receiver histories with a fixed policy, for cross-checking against TLC."""
    fixed = replace(cfg, receiver_actions=(action,), track_audit=False)
    secret = (0,) * fixed.max_rounds
    frontier = {initial_state(fixed, secret)}
    finished: set[tuple] = set()
    while frontier:
        nxt: set[State] = set()
        for s in frontier:
            if is_terminal(fixed, s):
                finished.add(s.obs)
                continue
            who = current_slot(fixed, s)
            if who == MAINT:
                nxt.add(step(fixed, s))
            else:
                for a in allowed_actions(fixed, s, who):
                    for c in coins(fixed, who, a):
                        nxt.add(step(fixed, s, a, c))
        frontier = nxt
    return finished


def single_round_channel(cfg: ModelConfig, noise_p: float = 0.0) -> tuple[list[str], list[str], list[list[float]]]:
    """P(receiver output | sender action) for one round from a clean start.

    The output is the receiver's whole visible event. Probabilities come from the
    semantics; V8's coin is weighted by noise_p. Requires exactly one sender slot.
    """
    one = replace(cfg, max_rounds=1, receiver_actions=("try_job",), track_audit=False)
    inputs = list(one.sender_actions)
    rows: dict[str, dict[tuple, float]] = {}
    for action in inputs:
        dist: dict[tuple, float] = {}
        frontier = [(initial_state(one, (0,)), 1.0)]
        while frontier:
            s, w = frontier.pop()
            if is_terminal(one, s):
                dist[s.obs] = dist.get(s.obs, 0.0) + w
                continue
            who = current_slot(one, s)
            if who == MAINT:
                frontier.append((step(one, s), w))
            elif who in one.senders:
                frontier.append((step(one, s, action), w))
            else:
                options = coins(one, who, "try_job")
                if len(options) == 1:
                    frontier.append((step(one, s, "try_job"), w))
                else:
                    frontier.append((step(one, s, "try_job", False), w * (1 - noise_p)))
                    frontier.append((step(one, s, "try_job", True), w * noise_p))
        rows[action] = dist
    outputs = sorted({o for d in rows.values() for o in d}, key=repr)
    matrix = [[rows[a].get(o, 0.0) for o in outputs] for a in inputs]
    return inputs, [repr(o) for o in outputs], matrix


def channel_capacity_per_round(cfg: ModelConfig, noise_p: float = 0.0) -> float:
    """Capacity in bits per round of the sender-action to receiver-event channel."""
    _, _, matrix = single_round_channel(cfg, noise_p)
    if len(matrix[0]) == 1:
        return 0.0
    # Rows that are identical add nothing and only slow the iteration down.
    distinct = [list(r) for r in {tuple(r) for r in matrix}]
    if len(distinct) == 1:
        return 0.0
    return blahut_arimoto(distinct)


def codebook_information(cfg: ModelConfig, noise_p: float = 0.0) -> float:
    """Exact I(b; receiver event) for the balanced noop/acquire codebook, from the semantics."""
    inputs, _, matrix = single_round_channel(replace(cfg, sender_actions=("noop", "acquire")), noise_p)
    if inputs != ["noop", "acquire"]:
        raise AssertionError("unexpected input order")
    return channel_information(matrix, [0.5, 0.5])


def leak_predicted_by_schedule(schedule: tuple[str, ...], rounds: int) -> bool:
    """Characterisation for the shared lock with a try_job receiver and A,B principals.

    Predicts a leak iff some A slot reaches a later B slot with no M between them.
    With two or more rounds the schedule repeats, so the path may wrap around.
    """
    n = len(schedule)
    span = n * rounds
    for start in range(n):
        if schedule[start] != "A":
            continue
        for offset in range(1, span - start):
            label = schedule[(start + offset) % n]
            if label == "M":
                break
            if label == "B":
                return True
    return False

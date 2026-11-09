"""Exhaustive exploration of the Python semantics, single-run and product.

The output has the same shape as a TLC state graph, so the two can be compared
in both directions (correspondence.py).
"""

from __future__ import annotations

import itertools
import json
from collections import deque
from dataclasses import dataclass

from .config import MAINT, ModelConfig
from .semantics import (
    State,
    allowed_actions,
    coins,
    current_slot,
    initial_state,
    is_terminal,
    state_to_tla,
    step,
)


def all_secrets(cfg: ModelConfig) -> list[tuple[int, ...]]:
    return list(itertools.product((0, 1), repeat=cfg.max_rounds))


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def schedule_value(cfg: ModelConfig) -> list[str]:
    return list(cfg.schedule)


@dataclass
class Graph:
    nodes: set[str]
    edges: set[tuple[str, str]]


def successors_single(cfg: ModelConfig, s: State) -> list[State]:
    if is_terminal(cfg, s):
        return []
    who = current_slot(cfg, s)
    if who == MAINT:
        return [step(cfg, s)]
    out = []
    for action in allowed_actions(cfg, s, who):
        for coin in coins(cfg, who, action):
            out.append(step(cfg, s, action, coin))
    return out


def successors_pair(cfg: ModelConfig, left: State, right: State) -> list[tuple[State, State]]:
    if is_terminal(cfg, left):
        return []
    who = current_slot(cfg, left)
    if who == MAINT:
        return [(step(cfg, left), step(cfg, right))]
    if who in cfg.senders:
        return [
            (step(cfg, left, al), step(cfg, right, ar))
            for al in allowed_actions(cfg, left, who)
            for ar in allowed_actions(cfg, right, who)
        ]
    out = []
    for action in allowed_actions(cfg, left, who):
        for coin in coins(cfg, who, action):
            out.append((step(cfg, left, action, coin), step(cfg, right, action, coin)))
    return out


def _single_key(cfg: ModelConfig, s: State) -> str:
    return canonical({"sched": schedule_value(cfg), "s": state_to_tla(s)})


def _pair_key(cfg: ModelConfig, left: State, right: State) -> str:
    return canonical(
        {"sched": schedule_value(cfg), "l": state_to_tla(left), "r": state_to_tla(right)}
    )


def explore_single(cfg: ModelConfig) -> Graph:
    start = [initial_state(cfg, b) for b in all_secrets(cfg)]
    seen = set(start)
    queue = deque(start)
    nodes = {_single_key(cfg, s) for s in start}
    edges = set()
    while queue:
        s = queue.popleft()
        k = _single_key(cfg, s)
        for t in successors_single(cfg, s):
            tk = _single_key(cfg, t)
            nodes.add(tk)
            edges.add((k, tk))
            if t not in seen:
                seen.add(t)
                queue.append(t)
    return Graph(nodes, edges)


def explore_pair(cfg: ModelConfig) -> Graph:
    secrets = all_secrets(cfg)
    start = [(initial_state(cfg, a), initial_state(cfg, b)) for a in secrets for b in secrets]
    seen = set(start)
    queue = deque(start)
    nodes = {_pair_key(cfg, *p) for p in start}
    edges = set()
    while queue:
        left, right = queue.popleft()
        k = _pair_key(cfg, left, right)
        for tl, tr in successors_pair(cfg, left, right):
            tk = _pair_key(cfg, tl, tr)
            nodes.add(tk)
            edges.add((k, tk))
            if (tl, tr) not in seen:
                seen.add((tl, tr))
                queue.append((tl, tr))
    return Graph(nodes, edges)


def receiver_equivalent_everywhere(cfg: ModelConfig, one_secret_pair: bool = False) -> bool:
    """The relational property, evaluated by the Python product exploration.

    With a free sender, secrets never affect a transition, so `one_secret_pair` gives the
    same answer far faster on large sweeps. TLC's product still starts from all of them.
    """
    if one_secret_pair and cfg.sender_mode != "free":
        raise ValueError("one secret pair is only equivalent for a free sender")
    secrets = all_secrets(cfg)[:1] if one_secret_pair else all_secrets(cfg)
    start = [(initial_state(cfg, a), initial_state(cfg, b)) for a in secrets for b in secrets]
    seen = set(start)
    queue = deque(start)
    while queue:
        left, right = queue.popleft()
        if left.obs != right.obs or left.lastObs != right.lastObs:
            return False
        for pair in successors_pair(cfg, left, right):
            if pair not in seen:
                seen.add(pair)
                queue.append(pair)
    return True

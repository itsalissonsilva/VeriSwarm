"""The all-horizon check rests on a reduction, so the reduction itself is tested here.

History-free mode drops counters and histories. For that to justify a claim about every
horizon, two things must hold:

1. Every state a bounded run reaches must map to a state the history-free model reaches.
   Otherwise the history-free check would miss behaviours.
2. Per-step agreement must imply agreement of complete histories, which is the induction
   argument in WorkspacePair.tla.

These are finite tests of the argument. Lean proves the abstract version in general.
"""

import json
from dataclasses import replace

import pytest

from veriswarm.config import STRONG_RECEIVER_ACTIONS, ModelConfig, default_config
from veriswarm.graph import explore_pair, successors_pair
from veriswarm.semantics import initial_state
from veriswarm.tla_values import parse_state


def project_pair(node: str) -> tuple:
    """A bounded pair state, reduced to what history-free mode keeps."""
    value = json.loads(node)

    def reduce(s):
        last = s["lastObs"]
        return json.dumps(
            {
                "pc": s["pc"],
                "locks": s["locks"],
                "lastAct": s["lastAct"],
                "lastResp": s["lastResp"],
                "lastObs": {
                    "who": last["who"],
                    "slot": last["slot"],
                    "act": last["act"],
                    "resp": last["resp"],
                    "job_ran": last["job"] != 0,
                },
            },
            sort_keys=True,
        )

    return reduce(value["l"]), reduce(value["r"])


def is_terminal_node(node: str, rounds: int) -> bool:
    return json.loads(node)["l"]["round"] > rounds


# Two rounds reach every lock state and wrap around once, so they exercise the reduction
# fully. Three rounds also passed, but take 15 to 25 minutes in total on a laptop.
CASES = [
    ("V2", "ABM", 2, {}),
    ("V2", "ABABM", 1, {}),
    ("V2", "BAM", 2, {}),
    ("V2", "ABM", 1, {"receiver_actions": STRONG_RECEIVER_ACTIONS}),
    ("V3", "ABM", 2, {}),
    ("V9", "AMBM", 2, {}),
]


@pytest.mark.parametrize("variant,schedule,rounds,extra", CASES)
def test_every_bounded_state_maps_to_a_history_free_state(variant, schedule, rounds, extra):
    actions = ("noop", "acquire", "release", "try_job")
    bounded = ModelConfig(variant, schedule=tuple(schedule), max_rounds=rounds, track_audit=False,
                          sender_actions=actions, **extra)
    free = replace(bounded, max_rounds=0)
    bounded_states = {
        project_pair(n) for n in explore_pair(bounded).nodes if not is_terminal_node(n, rounds)
    }
    free_states = {project_pair(n) for n in explore_pair(free).nodes}
    assert bounded_states <= free_states
    assert len(free_states) > 1


def test_history_free_mode_terminates_with_a_small_state_space():
    cfg = default_config("V2", max_rounds=0, track_audit=False)
    assert len(explore_pair(cfg).nodes) < 1000


@pytest.mark.parametrize("variant", ["V2", "V3"])
def test_per_step_agreement_gives_history_agreement_on_every_bounded_run(variant):
    """Walks every product path at three rounds, checking the two levels agree at each step."""
    cfg = default_config(variant, max_rounds=3, track_audit=False,
                         sender_actions=("noop", "acquire", "release", "try_job"))
    frontier = [(initial_state(cfg, (0, 0, 0)), initial_state(cfg, (1, 1, 1)))]
    seen = set(frontier)
    while frontier:
        left, right = frontier.pop()
        step_agrees = left.lastObs == right.lastObs
        history_agrees = left.obs == right.obs
        assert step_agrees and history_agrees
        for pair in successors_pair(cfg, left, right):
            if pair not in seen:
                seen.add(pair)
                frontier.append(pair)


def test_history_free_mode_can_report_a_violation():
    """A control, so that passing in this mode means something."""
    cfg = default_config("V1", max_rounds=0, track_audit=False)
    nodes = [json.loads(n) for n in explore_pair(cfg).nodes]
    assert any(n["l"]["lastObs"] != n["r"]["lastObs"] for n in nodes)


def test_state_parser_used_by_the_reduction_round_trips():
    assert parse_state('/\\ l = [pc |-> 1]\n/\\ r = [pc |-> 2]') == {"l": {"pc": 1}, "r": {"pc": 2}}

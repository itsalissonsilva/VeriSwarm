"""Real lock primitives must give the model's return codes on every enumerated sequence."""

import itertools

import pytest

from veriswarm.backends import LockfileBackend, ThreadingBackend, run_responses
from veriswarm.config import MAINT, ModelConfig
from veriswarm.semantics import current_slot, initial_state, last_response, step

SENDER = ("noop", "acquire", "release", "try_job", "invalid", "spoof_release")
RECEIVER = ("noop", "try_job", "acquire", "release")


def model_responses(cfg, slots):
    s = initial_state(cfg)
    out = []
    for label, action in slots:
        assert current_slot(cfg, s) == label
        if label == MAINT:
            s = step(cfg, s)
        else:
            s = step(cfg, s, action)
            out.append(last_response(s, label))
    return out


def all_slot_sequences(schedule, rounds):
    labels = list(schedule) * rounds
    options = [
        SENDER if x == "A" else RECEIVER if x == "B" else ("flush",)
        for x in labels
    ]
    for choice in itertools.product(*options):
        yield list(zip(labels, choice))


@pytest.mark.parametrize("variant", ["V1", "V2", "V3", "V4", "V5"])
@pytest.mark.parametrize("backend_cls", [ThreadingBackend, LockfileBackend])
@pytest.mark.parametrize("schedule,rounds", [("ABM", 1), ("AB", 1), ("ABAB", 1)])
def test_primitive_backends_match_the_model(variant, backend_cls, schedule, rounds):
    cfg = ModelConfig(variant, schedule=tuple(schedule), max_rounds=rounds, track_audit=False,
                      receiver_actions=RECEIVER)
    for slots in all_slot_sequences(schedule, rounds):
        assert run_responses(cfg, backend_cls(), slots) == model_responses(cfg, slots), slots


@pytest.mark.parametrize("backend_cls", [ThreadingBackend, LockfileBackend])
def test_primitive_channel_is_real_on_a_shared_lock(backend_cls):
    cfg = ModelConfig("V1", schedule=tuple("AB"), max_rounds=1, track_audit=False)
    held = run_responses(cfg, backend_cls(), [("A", "acquire"), ("B", "try_job")])
    free = run_responses(cfg, backend_cls(), [("A", "noop"), ("B", "try_job")])
    assert held[-1] == "busy" and free[-1] == "ok"


@pytest.mark.parametrize("backend_cls", [ThreadingBackend, LockfileBackend])
def test_primitive_channel_is_closed_by_partition(backend_cls):
    cfg = ModelConfig("V2", schedule=tuple("AB"), max_rounds=1, track_audit=False)
    held = run_responses(cfg, backend_cls(), [("A", "acquire"), ("B", "try_job")])
    free = run_responses(cfg, backend_cls(), [("A", "noop"), ("B", "try_job")])
    assert held[-1] == free[-1] == "ok"

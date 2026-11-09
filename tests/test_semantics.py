"""The return-code table, written out by hand from the plan and the TLA+ CASE.

The expected values are literals here, not computed by any project code, so this is a
separate statement of the semantics rather than the transition function checked
against itself.
"""

from dataclasses import replace

import pytest

from veriswarm.config import ModelConfig, default_config
from veriswarm.semantics import (
    backing_of,
    holder,
    initial_state,
    is_terminal,
    jobs_done,
    jobs_requested,
    last_response,
    object_name,
    step,
)

# (action, holder before) -> (response, holder after, a job ran)
SHARED_TABLE_FOR_A = [
    ("noop", "none", "noop", "none", False),
    ("noop", "A", "noop", "A", False),
    ("noop", "B", "noop", "B", False),
    ("acquire", "none", "ok", "A", False),
    ("acquire", "A", "busy", "A", False),
    ("acquire", "B", "busy", "B", False),
    ("release", "A", "ok", "none", False),
    ("release", "none", "not_owner", "none", False),
    ("release", "B", "not_owner", "B", False),
    ("spoof_release", "A", "ok", "none", False),
    ("spoof_release", "none", "not_owner", "none", False),
    ("spoof_release", "B", "not_owner", "B", False),
    ("try_job", "none", "ok", "none", True),
    ("try_job", "A", "busy", "A", False),
    ("try_job", "B", "busy", "B", False),
    ("invalid", "none", "invalid", "none", False),
    ("invalid", "A", "invalid", "A", False),
    ("invalid", "B", "invalid", "B", False),
]


@pytest.mark.parametrize("action,before,resp,after,job", SHARED_TABLE_FOR_A)
def test_shared_lock_table(action, before, resp, after, job):
    cfg = ModelConfig("V1", schedule=("A", "B", "M"), max_rounds=1)
    s = replace(initial_state(cfg, (0,)), locks=(("L0", before),))
    t = step(cfg, s, action)
    assert last_response(t, "A") == resp
    assert holder(t, "L0") == after
    assert jobs_done(t, "A") == (1 if job else 0)


@pytest.mark.parametrize(
    "action,before,resp,after,job",
    [row for row in SHARED_TABLE_FOR_A if row[1] != "B"],  # B can never hold A's private lock
)
def test_partitioned_table_is_the_same_on_a_principals_own_lock(action, before, resp, after, job):
    cfg = ModelConfig("V2", schedule=("A", "B", "M"), max_rounds=1)
    s = replace(initial_state(cfg, (0,)), locks=(("L_A", before), ("L_B", "none")))
    t = step(cfg, s, action)
    assert last_response(t, "A") == resp
    assert holder(t, "L_A") == after
    assert holder(t, "L_B") == "none"


@pytest.mark.parametrize("action", ["acquire", "release", "spoof_release", "try_job", "noop", "invalid"])
@pytest.mark.parametrize("b_holds", [True, False])
def test_a_cannot_alter_bs_private_lock(action, b_holds):
    """Includes states the base schedule never reaches, with B holding its own lock."""
    cfg = ModelConfig("V2", schedule=("A", "B", "M"), max_rounds=1)
    start = "B" if b_holds else "none"
    s = replace(initial_state(cfg, (0,)), locks=(("L_A", "none"), ("L_B", start)))
    assert holder(step(cfg, s, action), "L_B") == start


def test_invalid_actions_never_mutate_resources_in_any_variant():
    for variant in ("V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9"):
        cfg = default_config(variant, max_rounds=1)
        s = initial_state(cfg, (0,))
        assert step(cfg, s, "invalid").locks == s.locks


def test_denied_operations_are_inert_and_explicit():
    cfg = ModelConfig("V3", schedule=("B", "A", "M"), max_rounds=1,
                      receiver_actions=("noop", "try_job", "acquire", "release"))
    s = initial_state(cfg, (0,))
    for action in ("try_job", "acquire", "release"):
        t = step(cfg, s, action)
        assert last_response(t, "B") == "denied"
        assert t.locks == s.locks
    assert last_response(step(cfg, s, "noop"), "B") == "noop"


def test_maintenance_releases_every_hold_and_advances_the_round():
    cfg = ModelConfig("V1", schedule=("A", "B", "M"), max_rounds=2)
    s = step(cfg, initial_state(cfg, (0, 0)), "acquire")
    s = step(cfg, s, "try_job")
    assert holder(s, "L0") == "A"
    t = step(cfg, s)
    assert holder(t, "L0") == "none"
    assert (t.pc, t.round) == (1, 2)


def test_terminal_state_is_reached_after_the_configured_rounds():
    cfg = ModelConfig("V2", schedule=("A", "B", "M"), max_rounds=1)
    s = initial_state(cfg, (0,))
    s = step(cfg, step(cfg, s, "noop"), "noop")
    assert not is_terminal(cfg, s)
    assert is_terminal(cfg, step(cfg, s))


def test_noop_is_not_a_tool_call_and_leaves_no_audit_event():
    cfg = ModelConfig("V1", schedule=("A", "B", "M"), max_rounds=1, track_audit=True)
    s = step(cfg, initial_state(cfg, (0,)), "noop")
    assert s.audit == () and s.aseq == 0
    s = step(cfg, s, "try_job")
    assert len(s.audit) == 1 and s.audit[0].seq == 1 and s.audit[0].actor == "B"


def test_job_outputs_are_private_consecutive_and_counted_once():
    cfg = ModelConfig("V2", schedule=("A", "B", "M"), max_rounds=2, track_audit=True)
    s = initial_state(cfg, (0, 0))
    outputs = []
    for _ in range(2):
        s = step(cfg, s, "try_job")
        s = step(cfg, s, "try_job")
        outputs.append(s.lastObs.job)
        s = step(cfg, s)
    assert outputs == [200001, 200002]
    assert jobs_done(s, "B") == jobs_requested(s, "B") == 2


def test_failed_job_counts_as_requested_but_not_done():
    cfg = ModelConfig("V1", schedule=("A", "B", "M"), max_rounds=1)
    s = step(cfg, step(cfg, initial_state(cfg, (0,)), "acquire"), "try_job")
    assert (jobs_requested(s, "B"), jobs_done(s, "B")) == (1, 0)
    assert s.lastObs.job == 0


def test_v4_is_partitioned_in_name_only():
    cfg = ModelConfig("V4", schedule=("A", "B", "M"), max_rounds=1, track_audit=True)
    assert object_name(cfg, "A") != object_name(cfg, "B")
    assert backing_of(cfg, "A") == backing_of(cfg, "B")
    s = step(cfg, step(cfg, initial_state(cfg, (0,)), "acquire"), "try_job")
    assert s.lastObs.resp == "busy"
    assert {e.object for e in s.audit} == {"lock_A", "lock_B"}


def test_v5_shared_quota_blocks_across_separate_locks():
    cfg = ModelConfig("V5", schedule=("A", "B", "M"), max_rounds=1)
    s = step(cfg, step(cfg, initial_state(cfg, (0,)), "acquire"), "try_job")
    assert holder(s, "L_B") == "none"  # B's own lock is free...
    assert s.lastObs.resp == "busy"    # ...yet the shared pool refuses it.


def test_v6_exposes_only_the_counter():
    cfg = ModelConfig("V6", schedule=("A", "B", "M"), max_rounds=1)
    quiet = step(cfg, step(cfg, initial_state(cfg, (0,)), "noop"), "try_job").lastObs
    loud = step(cfg, step(cfg, initial_state(cfg, (0,)), "acquire"), "try_job").lastObs
    assert quiet.resp == loud.resp == "ok"
    assert (quiet.seq, loud.seq) == (1, 2)


def test_v7_moves_logical_time_only():
    cfg = ModelConfig("V7", schedule=("A", "B", "M"), max_rounds=1)
    quiet = step(cfg, step(cfg, initial_state(cfg, (0,)), "noop"), "try_job").lastObs
    loud = step(cfg, step(cfg, initial_state(cfg, (0,)), "acquire"), "try_job").lastObs
    assert quiet.resp == loud.resp == "ok"
    assert (quiet.clock, loud.clock) == (1, 2)


def test_v8_coin_turns_a_free_lock_busy_without_touching_state():
    cfg = ModelConfig("V8", schedule=("B", "A", "M"), max_rounds=1)
    s = initial_state(cfg, (0,))
    t = step(cfg, s, "try_job", coin=True)
    assert t.lastObs.resp == "busy" and t.locks == s.locks
    assert step(cfg, s, "try_job", coin=False).lastObs.resp == "ok"


def test_unbounded_mode_keeps_no_history():
    cfg = ModelConfig("V2", schedule=("A", "B", "M"), max_rounds=0, track_audit=False)
    s = initial_state(cfg, ())
    for _ in range(5):
        s = step(cfg, step(cfg, step(cfg, s, "acquire"), "try_job"))
    assert s.obs == () and s.round == 1 and s.clock == 0
    assert not is_terminal(cfg, s)


def test_config_rejects_impossible_setups():
    with pytest.raises(ValueError):
        ModelConfig("V99")
    with pytest.raises(ValueError):
        ModelConfig("V2", senders=("A",), receivers=("A",))
    with pytest.raises(ValueError):
        ModelConfig("V2", schedule=("A", "Z"))
    with pytest.raises(ValueError):
        ModelConfig("V6", max_rounds=0)

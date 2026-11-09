"""A syntax failure, timeout or crash must never count as a found vulnerability."""

from pathlib import Path

import pytest

from veriswarm import tlc as tlc_module
from veriswarm.matrix import all_jobs, constants, schedule_sweep_jobs
from veriswarm.tlc import (
    STATUS_CHECKED,
    STATUS_INCOMPLETE,
    STATUS_TOOL_ERROR,
    STATUS_VIOLATED,
    TLCJob,
    classify,
    render_cfg,
    run_job,
)

VIOLATION = """TLC2 Version 2.19 of 08 August 2024 (rev: 5a47802)
Error: Invariant ReceiverEquivalent is violated.
Error: The behavior up to this point is:
State 1: <Initial predicate>
/\\ x = 1

State 2: <Next line 3, col 1 to line 3, col 2 of module M>
/\\ x = 2

12 states generated, 12 distinct states found, 5 states left on queue.
"""

DONE = """TLC2 Version 2.19 of 08 August 2024 (rev: 5a47802)
Model checking completed. No error has been found.
  calculated (optimistic):  val = 6.5E-11
72016 states generated, 25936 distinct states found, 0 states left on queue.
The depth of the complete state graph search is 7.
"""


def test_invariant_violation_with_witness_is_violated():
    c = classify(12, VIOLATION)
    assert c.status == STATUS_VIOLATED
    assert c.violated_property == "ReceiverEquivalent"
    assert [s["vars"]["x"] for s in c.trace] == [1, 2]


def test_completed_run_is_checked_and_reports_counts():
    c = classify(0, DONE)
    assert c.status == STATUS_CHECKED
    assert (c.states_generated, c.distinct_states, c.queue_left, c.depth) == (72016, 25936, 0, 7)
    assert c.fingerprint_collision_estimate == "6.5E-11"


def test_action_property_violation_uses_the_liveness_exit_code():
    text = VIOLATION.replace("Invariant ReceiverEquivalent", "Action property LocalRespect")
    c = classify(13, text)
    assert (c.status, c.violated_property, c.kind) == (STATUS_VIOLATED, "LocalRespect", "action_property")


def test_syntax_failure_is_never_a_violation():
    assert classify(150, "Fatal errors while parsing TLA+ spec").status == STATUS_TOOL_ERROR


def test_config_failure_is_never_a_violation():
    assert classify(151, "TLC found an error in the configuration file").status == STATUS_TOOL_ERROR


def test_jvm_crash_is_never_a_violation():
    assert classify(1, "Exception in thread main java.lang.OutOfMemoryError").status == STATUS_TOOL_ERROR


def test_missing_process_is_a_tool_error():
    assert classify(None, "").status == STATUS_TOOL_ERROR


def test_timeout_is_incomplete_even_if_the_partial_output_looks_clean():
    c = classify(None, DONE, timed_out=True)
    assert c.status == STATUS_INCOMPLETE


def test_exit_zero_without_completion_message_is_not_checked():
    assert classify(0, "Starting...").status == STATUS_TOOL_ERROR


def test_exit_zero_with_states_left_on_queue_is_not_checked():
    assert classify(0, DONE.replace("0 states left", "9 states left")).status == STATUS_TOOL_ERROR


def test_violation_exit_without_a_witness_is_rejected():
    text = "Error: Invariant ReceiverEquivalent is violated.\n"
    assert classify(12, text).status == STATUS_TOOL_ERROR


def test_violation_exit_with_unparseable_witness_is_rejected():
    text = "Error: Invariant X is violated.\nState 1: <Initial predicate>\n/\\ x = ???\n"
    assert classify(12, text).status == STATUS_TOOL_ERROR


def test_wrong_exit_code_for_the_message_is_rejected():
    assert classify(255, VIOLATION).status == STATUS_TOOL_ERROR


def test_cfg_rendering_is_exact():
    job = TLCJob("x", "WorkspacePair", "SpecPair", {"Variant": "V2", "MaxRounds": 2, "TrackAudit": False,
                                                    "Senders": {"A"}}, invariants=("ReceiverEquivalent",))
    text = render_cfg(job)
    assert "SPECIFICATION SpecPair" in text
    assert "INVARIANT ReceiverEquivalent" in text
    assert 'Variant = "V2"' in text and "MaxRounds = 2" in text
    assert "TrackAudit = FALSE" in text and 'Senders = {"A"}' in text


def test_job_ids_are_unique_and_predictions_are_stated():
    jobs = all_jobs()
    assert len({j.id for j in jobs}) == len(jobs)
    assert all(j.expect_status in (STATUS_CHECKED, STATUS_VIOLATED) for j in jobs)
    assert all(j.expect_property for j in jobs if j.expect_status == STATUS_VIOLATED)


def test_no_state_constraint_prunes_attacker_choices():
    for job in all_jobs():
        assert "CONSTRAINT" not in render_cfg(job).upper().replace("CONSTANTS", "")
        assert "SYMMETRY" not in render_cfg(job).upper()


def test_only_jobs_predicted_to_pass_may_use_several_workers():
    for job in all_jobs():
        if job.workers > 1:
            assert job.expect_status == STATUS_CHECKED


def test_sweep_size():
    assert len(schedule_sweep_jobs("V0", 2, 2)) == 3 + 9


def _run(job, tmp_path, **kw):
    return run_job(job, tmp_path / "log", tmp_path / "cfg", tmp_path / "trace", tmp_path / "meta", **kw)[0]


@pytest.mark.tlc
def test_real_violation_is_classified_and_writes_a_witness(tmp_path):
    job = TLCJob("real_v1", "WorkspacePair", "SpecPair",
                 constants("V1", 1, sender_mode="codebook"),
                 invariants=("ReceiverEquivalent",),
                 expect_status=STATUS_VIOLATED, expect_property="ReceiverEquivalent")
    r = _run(job, tmp_path)
    assert (r.status, r.exit_code, r.as_expected) == (STATUS_VIOLATED, 12, True)
    assert r.trace_path and Path(r.trace_path).exists()


@pytest.mark.tlc
def test_real_check_is_classified(tmp_path):
    job = TLCJob("real_v2", "WorkspacePair", "SpecPair", constants("V2", 1),
                 invariants=("ReceiverEquivalent",))
    r = _run(job, tmp_path)
    assert (r.status, r.exit_code, r.as_expected) == (STATUS_CHECKED, 0, True)
    assert r.distinct_states and r.distinct_states > 100


@pytest.mark.tlc
def test_real_action_property_violation_is_classified(tmp_path):
    job = TLCJob("real_lr", "Workspace", "Spec", constants("V1", 1), properties=("LocalRespect",),
                 expect_status=STATUS_VIOLATED, expect_property="LocalRespect")
    r = _run(job, tmp_path)
    assert (r.status, r.violated_property, r.exit_code) == (STATUS_VIOLATED, "LocalRespect", 13)


@pytest.mark.tlc
def test_failed_assumption_is_a_tool_error_not_a_violation(tmp_path):
    bad = constants("V2", 1)
    bad["Variant"] = "V99"
    job = TLCJob("real_bad", "WorkspacePair", "SpecPair", bad, invariants=("ReceiverEquivalent",),
                 expect_status=STATUS_VIOLATED, expect_property="ReceiverEquivalent")
    r = _run(job, tmp_path)
    assert r.status == STATUS_TOOL_ERROR and not r.as_expected


@pytest.mark.tlc
def test_undefined_invariant_is_a_tool_error_not_a_violation(tmp_path):
    job = TLCJob("real_undef", "WorkspacePair", "SpecPair", constants("V2", 1),
                 invariants=("NoSuchInvariant",), expect_status=STATUS_VIOLATED,
                 expect_property="NoSuchInvariant")
    r = _run(job, tmp_path)
    assert r.status == STATUS_TOOL_ERROR and not r.as_expected


@pytest.mark.tlc
def test_timeout_is_incomplete(tmp_path):
    job = TLCJob("real_slow", "WorkspacePair", "SpecPair", constants("V2", 3),
                 invariants=("ReceiverEquivalent",))
    r = _run(job, tmp_path, timeout_s=1)
    assert r.status == STATUS_INCOMPLETE and not r.as_expected


def test_missing_jar_is_a_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tlc_module, "JAR", tmp_path / "does_not_exist.jar")
    job = TLCJob("no_jar", "WorkspacePair", "SpecPair", constants("V2", 1),
                 invariants=("ReceiverEquivalent",), expect_status=STATUS_VIOLATED,
                 expect_property="ReceiverEquivalent")
    r = _run(job, tmp_path)
    assert r.status == STATUS_TOOL_ERROR and not r.as_expected

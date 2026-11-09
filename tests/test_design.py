"""The design checker: agreement with the named variants, witnesses, and the composition rule."""

import pytest

from veriswarm.config import ISOLATED_VARIANTS, LEAKY_VARIANTS, VARIANTS
from veriswarm.design import KEY, Design, check, design_space, named_variant, registered

AS_DESIGNS = {
    "V1": Design("shared"),
    "V2": Design("private"),
    "V3": Design("shared", receiver_denied=True),
    "V4": Design("aliased"),
    "V5": Design("private", shared_quota=True),
    "V6": Design("private", exposed_counter=True),
    "V7": Design("private", work_conserving=True),
    "V8": Design("shared", noisy=True),
    "V9": Design("shared", schedule="AMBM"),
}


@pytest.mark.parametrize("variant", sorted(AS_DESIGNS))
def test_each_named_variant_is_recognised_and_gets_its_tlc_verdict(variant):
    design = AS_DESIGNS[variant]
    assert named_variant(design) == variant
    result = check(design)
    assert result["leaks"] == (variant in LEAKY_VARIANTS)
    assert result["leaks"] != (variant in ISOLATED_VARIANTS)


def test_the_shared_lock_witness_is_the_codebook():
    witness = check(Design("shared"))["witness"]
    sender, receiver = witness[0], witness[1]
    assert sender["slot"] == "A" and {sender["left"], sender["right"]} == {"noop", "acquire"}
    assert receiver["slot"] == "B" and receiver["left"] == receiver["right"] == "try_job"
    assert {receiver["left_reply"], receiver["right_reply"]} == {"ok", "busy"}


def test_a_closed_design_has_no_witness_and_zero_leakage():
    result = check(Design("private"))
    assert result["witness"] is None and result["all_horizon"] is True
    assert result["leakage"]["max_distinct_histories"] == 1


def test_unbounded_counters_and_clocks_are_checked_bounded_only():
    assert check(Design("private", exposed_counter=True))["all_horizon"] is None
    assert check(Design("private", work_conserving=True))["all_horizon"] is None


def test_registration_is_undone_even_on_error():
    with pytest.raises(RuntimeError):
        with registered(Design("private")):
            assert KEY in VARIANTS
            raise RuntimeError("boom")
    assert KEY not in VARIANTS


@pytest.mark.parametrize("kwargs", [{"backing": "partitioned"}, {"schedule": "AM"}, {"schedule": "ABX"}])
def test_invalid_designs_are_refused(kwargs):
    with pytest.raises(ValueError):
        Design(**kwargs)


def expected_leak(d: Design) -> bool:
    """The composition rule stated in the report."""
    closed_state = (d.backing == "private" and not d.shared_quota) or d.schedule == "AMBM"
    return d.exposed_counter or d.work_conserving or not closed_state


def test_the_composition_rule_holds_on_all_96_designs():
    space = design_space()
    assert len(space) == 96
    for design in space:
        assert check(design)["leaks"] == expected_leak(design), design.describe()


def test_random_busy_replies_never_change_a_verdict():
    for design in design_space():
        if design.noisy:
            quiet = Design(design.backing, design.shared_quota, design.exposed_counter, design.work_conserving,
                           False, design.schedule)
            assert check(design)["leaks"] == check(quiet)["leaks"]

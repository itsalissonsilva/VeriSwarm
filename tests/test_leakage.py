"""Exact leakage and the information measures, against values known in advance."""

import itertools
import math

import pytest

from veriswarm.config import ModelConfig, default_config
from veriswarm.graph import receiver_equivalent_everywhere
from veriswarm.leakage import (
    bits_per_round,
    channel_capacity_per_round,
    fixed_policy_histories,
    leak_predicted_by_schedule,
    max_distinct_histories,
)
from veriswarm.metrics import (
    auc,
    binary_entropy,
    blahut_arimoto,
    bootstrap_group_interval,
    empirical_mutual_information,
    miller_madow_correction,
    wilson_interval,
    z_channel_capacity,
)


@pytest.mark.parametrize("variant", ["V0", "V1", "V4", "V5", "V6", "V7"])
@pytest.mark.parametrize("rounds", [1, 2, 3, 4, 5])
def test_leaky_variants_carry_exactly_one_bit_per_round(variant, rounds):
    cfg = default_config(variant, max_rounds=rounds, track_audit=False)
    assert max_distinct_histories(cfg) == 2**rounds


@pytest.mark.parametrize("variant", ["V2", "V3", "V9"])
@pytest.mark.parametrize("rounds", [1, 3, 6])
def test_isolated_variants_carry_zero_bits(variant, rounds):
    cfg = default_config(variant, max_rounds=rounds, track_audit=False)
    assert max_distinct_histories(cfg) == 1
    assert bits_per_round(cfg) == 0.0


def test_two_receiver_slots_per_round_double_the_shared_channel():
    cfg = ModelConfig("V1", schedule=tuple("ABABM"), max_rounds=1, track_audit=False)
    assert max_distinct_histories(cfg) == 4


def test_partition_stays_closed_with_a_mid_round_release_available():
    cfg = ModelConfig("V2", schedule=tuple("ABABM"), max_rounds=2, track_audit=False)
    assert max_distinct_histories(cfg) == 1


def test_adaptive_receiver_is_covered_and_noop_receiver_learns_nothing():
    idle = default_config("V1", max_rounds=3, receiver_actions=("noop",), track_audit=False)
    assert max_distinct_histories(idle) == 1


def test_v8_is_rejected_by_the_deterministic_count():
    with pytest.raises(ValueError):
        max_distinct_histories(default_config("V8", max_rounds=1, track_audit=False))


def test_fixed_policy_counts_match_the_adaptive_optimum_here():
    for variant in ("V0", "V2", "V4", "V5", "V6", "V7", "V9"):
        cfg = default_config(variant, max_rounds=3, track_audit=False)
        assert len(fixed_policy_histories(cfg)) == max_distinct_histories(cfg)


@pytest.mark.parametrize("variant,cap", [("V0", 1.0), ("V1", 1.0), ("V2", 0.0), ("V3", 0.0), ("V4", 1.0),
                                         ("V5", 1.0), ("V6", 1.0), ("V7", 1.0), ("V9", 0.0)])
def test_channel_capacity_per_round(variant, cap):
    cfg = default_config(variant, max_rounds=1, track_audit=False)
    assert channel_capacity_per_round(cfg) == pytest.approx(cap, abs=1e-9)


@pytest.mark.parametrize("p", [0.1, 0.25, 0.5, 0.75, 0.9, 0.99])
def test_v8_capacity_from_semantics_equals_the_z_channel(p):
    cfg = default_config("V8", max_rounds=1, track_audit=False)
    assert channel_capacity_per_round(cfg, p) == pytest.approx(z_channel_capacity(p), abs=1e-7)


def test_z_channel_known_values():
    assert z_channel_capacity(0.0) == 1.0
    assert z_channel_capacity(1.0) == 0.0
    assert z_channel_capacity(0.5) == pytest.approx(math.log2(1.25), abs=1e-12)


@pytest.mark.parametrize("p", [0.05, 0.2, 0.5, 0.8, 0.95])
def test_blahut_arimoto_matches_closed_forms(p):
    assert blahut_arimoto([[1 - p, p], [0.0, 1.0]]) == pytest.approx(z_channel_capacity(p), abs=1e-7)
    # Binary symmetric channel: 1 - h(p).
    bsc = blahut_arimoto([[1 - p, p], [p, 1 - p]])
    assert bsc == pytest.approx(1 - binary_entropy(p), abs=1e-7)


def test_mutual_information_known_values():
    assert empirical_mutual_information([(0, "a"), (1, "b")] * 50) == pytest.approx(1.0)
    assert empirical_mutual_information([(0, "a"), (1, "a")] * 50) == pytest.approx(0.0)
    assert empirical_mutual_information([]) == 0.0


def test_mutual_information_bias_term_is_small_and_positive_on_noise():
    pairs = [(i % 2, (i // 2) % 2) for i in range(400)]
    assert miller_madow_correction(pairs) > 0
    assert empirical_mutual_information(pairs) < 0.01


def test_group_bootstrap_on_constant_groups_is_degenerate():
    assert bootstrap_group_interval([0.5] * 10, seed=1) == (0.5, 0.5)


def test_group_bootstrap_brackets_the_mean():
    lo, hi = bootstrap_group_interval([0.4, 0.5, 0.6, 0.5, 0.45, 0.55, 0.5, 0.52, 0.48, 0.5], seed=1)
    assert lo <= 0.5 <= hi and lo < hi


def test_wilson_interval_contains_the_estimate():
    lo, hi = wilson_interval(30, 100)
    assert lo < 0.3 < hi


def test_auc_values():
    assert auc([2, 3], [0, 1]) == 1.0
    assert auc([0, 1], [2, 3]) == 0.0
    assert auc([1, 1], [1, 1]) == 0.5


@pytest.mark.parametrize("rounds", [1, 2])
def test_schedule_characterisation_on_every_short_schedule(rounds):
    """Leak iff an A slot reaches a later B slot with no M between them."""
    for n in range(1, 5):
        for letters in itertools.product("ABM", repeat=n):
            cfg = ModelConfig("V0", schedule=letters, max_rounds=rounds, track_audit=False,
                              sender_actions=("noop", "acquire", "release"))
            holds = receiver_equivalent_everywhere(cfg)
            assert holds is not leak_predicted_by_schedule(letters, rounds), "".join(letters)


def test_schedule_characterisation_examples():
    assert leak_predicted_by_schedule(tuple("ABM"), 1)
    assert not leak_predicted_by_schedule(tuple("AMBM"), 2)
    assert not leak_predicted_by_schedule(tuple("BAM"), 2)
    assert leak_predicted_by_schedule(tuple("BA"), 2)   # wraps around between rounds
    assert not leak_predicted_by_schedule(tuple("BA"), 1)
    assert not leak_predicted_by_schedule(tuple("AAM"), 3)
    assert not leak_predicted_by_schedule(tuple("BBM"), 3)


@pytest.mark.parametrize(
    "schedule,rounds",
    [("ABM", 2), ("ABABM", 1), ("AB", 2), ("BA", 2), ("AMB", 2), ("BBAM", 1)],
)
def test_partition_holds_on_sample_schedules(schedule, rounds):
    cfg = ModelConfig("V2", schedule=tuple(schedule), max_rounds=rounds, track_audit=False,
                      sender_actions=("noop", "acquire", "release", "try_job"))
    assert receiver_equivalent_everywhere(cfg)

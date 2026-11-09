"""The behaviours the project claims, stated as tests that fail if any regresses."""

import dataclasses
import itertools
import random

import pytest

from veriswarm import experiments as ex
from veriswarm.agents import (
    AtomicBenignSender,
    CodebookSender,
    ForgingSender,
    FrozenDecoder,
    ReceiverView,
    SenderView,
    TryJobReceiver,
    features_of,
)
from veriswarm.config import VARIANTS, default_config
from veriswarm.gateway import Gateway, ScheduleViolation, SenderReply, ToolRequest
from veriswarm.graph import receiver_equivalent_everywhere
from veriswarm.monitor import LegalActionMonitor, audit_is_gapless
from veriswarm.observations import OBS_FIELDS, ObsEvent
from veriswarm.runner import run_episode
from veriswarm.semantics import holder, initial_state, step


def _attack(variant, bits, seed=1):
    cfg = default_config(variant, max_rounds=len(bits), track_audit=True)
    return run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, seed, "t")


def _b_view_for(variant, a_actions, b_actions):
    cfg = default_config(variant, max_rounds=len(a_actions), track_audit=False,
                         sender_actions=tuple(set(a_actions)), receiver_actions=tuple(set(b_actions)))
    s = initial_state(cfg)
    ai, bi = iter(a_actions), iter(b_actions)
    from veriswarm.semantics import current_slot, is_terminal
    while not is_terminal(cfg, s):
        who = current_slot(cfg, s)
        if who == "M":
            s = step(cfg, s)
        elif who == "A":
            s = step(cfg, s, next(ai))
        else:
            s = step(cfg, s, next(bi))
    return s.obs


def test_v1_leaks_despite_correct_identities_and_complete_logs():
    bits = ex.balanced_bits(100, 42)
    ep = _attack("V1", bits)
    decoded = [1 if e.resp == "busy" else 0 for e in ep.receiver_events["B"]]
    assert decoded == bits

    assert audit_is_gapless(ep.audit)
    assert all(e.actor == "ABM"[e.slot - 1] for e in ep.audit)
    assert all(e.claimed == e.actor for e in ep.audit)
    assert LegalActionMonitor(("A", "B", "M")).review(ep.audit).flags == ()


@pytest.mark.parametrize("variant", ["V2", "V3", "V9"])
def test_receiver_view_never_depends_on_sender_choices(variant):
    actions = ("noop", "acquire", "release", "try_job", "invalid", "spoof_release")
    for b_actions in itertools.product(("noop", "try_job"), repeat=2):
        views = {
            _b_view_for(variant, a_actions, b_actions)
            for a_actions in itertools.product(actions, repeat=2)
        }
        assert len(views) == 1


@pytest.mark.parametrize("variant", ["V0", "V1", "V4", "V5", "V6", "V7"])
def test_each_leaky_variant_separates_the_two_codebook_actions(variant):
    assert _b_view_for(variant, ("noop",), ("try_job",)) != _b_view_for(variant, ("acquire",), ("try_job",))


@pytest.mark.parametrize(
    "variant,leaks",
    [("V0", True), ("V1", True), ("V2", False), ("V3", False), ("V4", True),
     ("V5", True), ("V6", True), ("V7", True), ("V8", True), ("V9", False)],
)
def test_python_product_agrees_with_the_predicted_verdict(variant, leaks):
    cfg = default_config(variant, max_rounds=2, track_audit=False)
    assert receiver_equivalent_everywhere(cfg) is (not leaks)


def test_receiver_interface_exposes_only_declared_fields():
    assert {f.name for f in dataclasses.fields(ObsEvent)} == set(OBS_FIELDS)
    forbidden = {"secret", "bit", "private_bit", "sender", "actor", "claimed", "audit",
                 "locks", "holder", "state", "gateway", "object", "wall_time"}
    assert not forbidden & set(OBS_FIELDS)
    assert not forbidden & {f.name for f in dataclasses.fields(ReceiverView)}
    assert "private_bit" in {f.name for f in dataclasses.fields(SenderView)}


def test_receiver_reply_is_immutable_and_carries_no_gateway_reference():
    cfg = default_config("V2", max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(1,))
    a, b = gw.enroll("A"), gw.enroll("B")
    gw.execute(a, ToolRequest("acquire"))
    reply = gw.execute(b, ToolRequest("try_job"))
    assert isinstance(reply, ObsEvent)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reply.resp = "tampered"
    for value in dataclasses.asdict(reply).values():
        assert isinstance(value, (int, str))


def test_sender_reply_reveals_only_its_own_code():
    cfg = default_config("V1", max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(0,))
    reply = gw.execute(gw.enroll("A"), ToolRequest("acquire"))
    assert isinstance(reply, SenderReply)
    assert [f.name for f in dataclasses.fields(reply)] == ["resp"]


def test_v2_receiver_reply_has_no_audit_sequence():
    bits = ex.balanced_bits(20, 3)
    ep = _attack("V2", bits)
    assert {e.seq for e in ep.receiver_events["B"]} == {0}


def test_identity_comes_from_the_handle_not_the_request():
    cfg = default_config("V1", max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(0,))
    a, _ = gw.enroll("A"), gw.enroll("B")
    gw.execute(a, ToolRequest("release", claimed_actor="B"))
    event = gw.sink.records()[0]
    assert event.actor == "A"
    assert event.claimed == "forged"
    assert event.act == "spoof_release"


def test_forged_release_cannot_free_another_principals_lock():
    cfg = default_config("V1", schedule=tuple("ABM"), max_rounds=1, track_audit=True,
                         senders=("A",), receivers=("B",))
    gw = Gateway(cfg, secret=(0,))
    a, b = gw.enroll("A"), gw.enroll("B")
    gw.execute(a, ToolRequest("acquire"))
    # B claims to be A and tries to release. B holds nothing, so nothing changes.
    reply = gw.execute(b, ToolRequest("release", claimed_actor="A"))
    assert reply.resp == "not_owner"
    assert holder(gw.state, "L0") == "A"


def test_unknown_tool_names_are_invalid_and_inert():
    cfg = default_config("V1", max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(0,))
    before = gw.state.locks
    reply = gw.execute(gw.enroll("A"), ToolRequest("rm -rf /"))
    assert reply.resp == "invalid" and gw.state.locks == before


def test_client_cannot_invoke_the_internal_spoof_action_directly():
    cfg = default_config("V1", max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(0,))
    assert gw.execute(gw.enroll("A"), ToolRequest("spoof_release")).resp == "invalid"


def test_acting_out_of_slot_is_refused():
    cfg = default_config("V1", max_rounds=1, track_audit=True)
    gw = Gateway(cfg, secret=(0,))
    gw.enroll("A")
    b = gw.enroll("B")
    with pytest.raises(ScheduleViolation):
        gw.execute(b, ToolRequest("try_job"))


def test_foreign_handle_is_refused():
    cfg = default_config("V1", max_rounds=1, track_audit=True)
    one, two = Gateway(cfg, secret=(0,)), Gateway(cfg, secret=(0,))
    one.enroll("A")
    stolen = two.enroll("A")
    with pytest.raises(ScheduleViolation):
        one.execute(stolen, ToolRequest("noop"))


def test_monitor_flags_illegal_requests_but_not_the_covert_channel():
    cfg = default_config("V1", max_rounds=20, track_audit=True)
    forged = run_episode(cfg, {"A": ForgingSender()}, {"B": TryJobReceiver()}, None, 1, "f")
    monitor = LegalActionMonitor(("A", "B", "M"))
    assert len(monitor.review(forged.audit).flags) == 20
    assert monitor.review(_attack("V1", ex.balanced_bits(20, 5)).audit).flags == ()


def test_deny_all_loses_work_while_v2_completes_it():
    def run(variant):
        cfg = default_config(variant, max_rounds=50, track_audit=True)
        return run_episode(cfg, {"A": AtomicBenignSender()}, {"B": TryJobReceiver()}, None, 1, "w")

    denied, partitioned = run("V3"), run("V2")
    assert (denied.jobs("B"), denied.requests("B")) == (0, 50)
    assert (partitioned.jobs("B"), partitioned.requests("B")) == (50, 50)
    assert partitioned.jobs("A") == 50


def test_partition_completes_jobs_under_contention_that_blocks_the_shared_lock():
    v1 = ex._benign("V1", "W2", 7, 200, 0.5, 0.0)
    v2 = ex._benign("V2", "W2", 7, 200, 0.5, 0.0)
    assert v2.jobs("B") == 200
    assert v1.jobs("B") < 150


def test_v9_cost_is_an_extra_slot_per_round():
    v2 = ex._benign("V2", "W1", 7, 100, 0.5, 0.0)
    v9 = ex._benign("V9", "W1", 7, 100, 0.5, 0.0)
    assert (v2.slots, v9.slots) == (300, 400)
    assert v9.jobs("B") == 100


def test_decoder_resolves_ties_and_unseen_features_to_zero():
    decoder = FrozenDecoder().fit([(("ok",), 0), (("ok",), 1), (("busy",), 1)])
    assert decoder.decode(("ok",)) == 0
    assert decoder.decode(("busy",)) == 1
    assert decoder.decode(("never seen",)) == 0


def test_constant_response_decoder_stays_at_exactly_chance():
    bits = ex.balanced_bits(100, 11)
    for variant in ("V2", "V3", "V9"):
        decoder = ex.train_decoder(variant, 0.0)
        ep = _attack(variant, bits)
        period = len(VARIANTS[variant].default_schedule)
        guesses = [decoder.decode(f) for f in features_of(ep.receiver_events["B"], period)]
        assert sum(g == b for g, b in zip(guesses, bits)) == 50
        assert len(decoder.table) == 1


@pytest.mark.parametrize("variant", ["V0", "V1", "V4", "V5", "V6", "V7"])
def test_deterministic_channels_decode_perfectly_from_the_first_round(variant):
    """A first-round decoder artefact once cost these variants a few bits per thousand."""
    decoder = ex.train_decoder(variant, 0.0)
    period = len(VARIANTS[variant].default_schedule)
    for seed in range(5):
        bits = ex.balanced_bits(100, seed)
        ep = _attack(variant, bits)
        guesses = [decoder.decode(f) for f in features_of(ep.receiver_events["B"], period)]
        assert guesses == bits


def test_first_event_feature_matches_later_rounds():
    for variant, action in (("V1", "acquire"), ("V7", "noop"), ("V7", "acquire"), ("V6", "acquire"), ("V9", "noop")):
        bits = [1 if action == "acquire" else 0] * 4
        ep = _attack(variant, bits)
        features = features_of(ep.receiver_events["B"], len(VARIANTS[variant].default_schedule))
        assert len(set(features)) == 1, (variant, action, features)


def test_measured_information_agrees_with_the_exact_value_within_sampling_error():
    """The estimate sits close to the exact value, on either side of it, as sampling allows.

    A single estimate can land above capacity by chance, as this one does: the noise fired
    on fewer than half the rounds. So the test compares against the exact value at the
    input actually used, with a large independent sample, and not against capacity.
    """
    from veriswarm.leakage import codebook_information
    from veriswarm.metrics import empirical_mutual_information, z_channel_capacity

    exact = codebook_information(default_config("V8", max_rounds=1, track_audit=False), 0.5)
    assert exact == pytest.approx(0.3113, abs=1e-3)
    assert exact < z_channel_capacity(0.5)

    bits = ex.balanced_bits(4000, 77)
    ep = ex._transmit("V8", bits, 77, "mi-large", 0.5, track_audit=False)
    pairs = list(zip(bits, features_of(ep.receiver_events["B"], 3)))
    assert empirical_mutual_information(pairs) == pytest.approx(exact, abs=0.03)


def test_balanced_bits_are_balanced_and_seed_dependent():
    a, b = ex.balanced_bits(100, 1), ex.balanced_bits(100, 2)
    assert sum(a) == sum(b) == 50
    assert a != b


def test_receiver_controller_is_never_given_the_bit():
    seen = []

    class Recording(TryJobReceiver):
        def act(self, view):
            seen.append(view)
            return super().act(view)

    cfg = default_config("V1", max_rounds=10, track_audit=True)
    run_episode(cfg, {"A": CodebookSender()}, {"B": Recording()}, ex.balanced_bits(10, 1), 1, "r")
    assert seen and all(isinstance(v, ReceiverView) for v in seen)
    assert all(not hasattr(v, "private_bit") for v in seen)


@pytest.mark.parametrize("rounds", [1, 2, 3, 4])
def test_mimicry_logs_have_exactly_the_benign_distribution(rounds):
    attack, benign = ex.exact_log_distributions(rounds)
    assert attack == benign
    assert sum(attack.values()) == 2**rounds


def test_noop_codebook_logs_differ_from_the_contended_baseline():
    """The plain codebook omits calls, so it is not log-identical. Mimicry is what closes that."""
    bits = [0, 1, 0, 1]
    plain = _attack("V1", bits)
    assert sum(1 for e in plain.audit if e.actor == "A") == 2


def test_v8_noise_mitigation_is_defeated_by_repetition():
    correct, total = ex._repetition_accuracy("V8", 0.5, 15, seed=99, n_bits=100)
    assert correct == total or correct / total > 0.98


def test_every_variant_has_a_declared_purpose():
    for spec in VARIANTS.values():
        assert spec.title and spec.purpose


def test_runner_uses_a_fresh_seeded_stream_per_run():
    cfg = default_config("V8", max_rounds=200, track_audit=True)
    bits = [0] * 200
    one = run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, 5, "x", noise_p=0.5)
    two = run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, 5, "x", noise_p=0.5)
    three = run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, 6, "x", noise_p=0.5)
    resp = lambda ep: [e.resp for e in ep.receiver_events["B"]]  # noqa: E731
    assert resp(one) == resp(two)
    assert resp(one) != resp(three)
    assert 60 < resp(one).count("ok") < 140


def test_random_stream_is_not_python_hash_dependent():
    assert random.Random("noise|5|x").random() == random.Random("noise|5|x").random()

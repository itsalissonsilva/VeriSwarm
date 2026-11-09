"""Replay and model-to-code comparison, including proof these checks are able to fail."""

import json
from pathlib import Path

import pytest

from veriswarm import semantics
from veriswarm.config import default_config
from veriswarm.correspondence import compare, parse_dot, run_case
from veriswarm.graph import Graph, explore_pair, explore_single
from veriswarm.matrix import constants
from veriswarm.tla_values import TLAParseError, parse_state, parse_value
from veriswarm.tlc import STATUS_VIOLATED, TLCJob, run_job
from veriswarm.traces import (
    ReplayMismatch,
    corrupt_action,
    corrupt_response,
    load_trace,
    replay_pair_trace,
)

ROOT = Path(__file__).resolve().parents[1]


# -- the TLA+ value parser ----------------------------------------------------------

@pytest.mark.parametrize(
    "text,value",
    [
        ("1", 1),
        ('"ok"', "ok"),
        ("TRUE", True),
        ("<<>>", []),
        ("<<1, 2>>", [1, 2]),
        ("{1, 2}", [1, 2]),
        ('[a |-> 1, b |-> "x"]', {"a": 1, "b": "x"}),
        ('("A" :> 0 @@ "B" :> 1)', {"A": 0, "B": 1}),
        ('[l |-> << [x |-> 1] >>, m |-> <<>>]', {"l": [{"x": 1}], "m": []}),
    ],
)
def test_parse_value(text, value):
    assert parse_value(text) == value


@pytest.mark.parametrize("bad", ["", "<<1", "[a |-> ]", "1 2", "foo", "???"])
def test_parse_value_rejects_malformed_input(bad):
    with pytest.raises(TLAParseError):
        parse_value(bad)


def test_parse_state():
    assert parse_state('/\\ x = 1\n/\\ y = <<"a">>') == {"x": 1, "y": ["a"]}


def test_parse_dot_reads_nodes_edges_and_ignores_the_terminal_stutter():
    dot = (
        'strict digraph G {\n'
        '1 [label="/\\\\ x = 1",style = filled];\n'
        '-2 [label="/\\\\ x = \\"a\\""];\n'
        '1 -> -2;\n'
        '-2 -> -2;\n'
        '}\n'
    )
    g = parse_dot(dot)
    assert len(g.nodes) == 2
    assert len(g.edges) == 1


def test_compare_reports_differences_in_both_directions():
    a = Graph({"x", "y"}, {("x", "y")})
    b = Graph({"x", "z"}, {("x", "z")})
    d = compare(a, b)
    assert not d["match"]
    assert (d["nodes_only_in_tlc"], d["nodes_only_in_python"]) == (1, 1)
    assert (d["edges_only_in_tlc"], d["edges_only_in_python"]) == (1, 1)


def test_compare_accepts_equal_graphs():
    g = Graph({"x"}, {("x", "x")})
    assert compare(g, Graph({"x"}, {("x", "x")}))["match"]


# -- the graph comparison itself, against TLC -------------------------------------

@pytest.mark.tlc
def test_graphs_match_for_v1_single(tmp_path):
    r = run_case("t_v1", "single", default_config("V1", max_rounds=1, track_audit=True), tmp_path)
    assert r.match, r.example_difference
    assert r.tlc_nodes == r.python_nodes > 10


@pytest.mark.tlc
def test_graphs_match_for_v2_pair(tmp_path):
    r = run_case("t_v2", "pair", default_config("V2", max_rounds=1, track_audit=False), tmp_path)
    assert r.match, r.example_difference


@pytest.mark.tlc
def test_injected_python_bug_is_detected(tmp_path, monkeypatch):
    """If the check could not catch a divergence, its passing would mean nothing."""
    original = semantics._resource_effect

    def buggy(cfg, locks, who, action, coin):
        new_locks, resp, job = original(cfg, locks, who, action, coin)
        if action == "try_job" and resp == "busy":
            return new_locks, "ok", True
        return new_locks, resp, job

    monkeypatch.setattr(semantics, "_resource_effect", buggy)
    r = run_case("t_bug", "single", default_config("V1", max_rounds=1, track_audit=True), tmp_path)
    assert not r.match


@pytest.mark.tlc
def test_subtle_injected_bug_in_one_field_is_detected(tmp_path, monkeypatch):
    """An off-by-one in audit numbering only, no change to any response."""
    original = semantics.step_slot

    def buggy(cfg, s, action, coin=False):
        t = original(cfg, s, action, coin)
        return semantics.replace(t, clock=t.clock + 1) if action == "release" else t

    monkeypatch.setattr(semantics, "step_slot", buggy)
    r = run_case("t_bug2", "single", default_config("V2", max_rounds=1, track_audit=True), tmp_path)
    assert not r.match


def test_python_pair_graph_has_independent_sender_choices():
    g = explore_pair(default_config("V2", max_rounds=1, track_audit=False))
    actions = set()
    for node in g.nodes:
        v = json.loads(node)
        actions.add((v["l"]["lastAct"]["A"], v["r"]["lastAct"]["A"]))
    # Every ordered pair of the six sender actions occurs, plus the initial "none".
    assert len(actions) == 6 * 6 + 1


def test_python_single_graph_grows_with_rounds():
    one = explore_single(default_config("V2", max_rounds=1, track_audit=True))
    two = explore_single(default_config("V2", max_rounds=2, track_audit=True))
    assert len(two.nodes) > len(one.nodes)


# -- replay -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def v1_trace(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("trace")
    job = TLCJob("replay_v1", "WorkspacePair", "SpecPair", constants("V1", 1, sender_mode="codebook"),
                 invariants=("ReceiverEquivalent",), expect_status=STATUS_VIOLATED,
                 expect_property="ReceiverEquivalent")
    result, _ = run_job(job, tmp / "log", tmp / "cfg", tmp / "trace", tmp / "meta")
    assert result.status == STATUS_VIOLATED
    return load_trace(Path(result.trace_path))


@pytest.mark.tlc
def test_witness_replays_exactly(v1_trace):
    report = replay_pair_trace(v1_trace)
    assert report.first_divergent_step == 3
    assert sorted([report.left_secret, report.right_secret]) == [[0], [1]]
    assert report.fields_compared > 50


@pytest.mark.tlc
def test_witness_matches_the_expected_paired_table(v1_trace):
    report = replay_pair_trace(v1_trace)
    by_secret = {tuple(report.left_secret): "left", tuple(report.right_secret): "right"}
    sender, receiver = report.steps[0], report.steps[1]
    assert getattr(sender, f"{by_secret[(0,)]}_action") == "noop"
    assert getattr(sender, f"{by_secret[(1,)]}_action") == "acquire"
    assert getattr(receiver, f"{by_secret[(0,)]}_response") == "ok"
    assert getattr(receiver, f"{by_secret[(1,)]}_response") == "busy"


@pytest.mark.tlc
def test_corrupted_action_is_rejected(v1_trace):
    with pytest.raises(ReplayMismatch):
        replay_pair_trace(corrupt_action(v1_trace, 2, "l", "A", "release"))


@pytest.mark.tlc
def test_corrupted_response_is_rejected(v1_trace):
    with pytest.raises(ReplayMismatch):
        replay_pair_trace(corrupt_response(v1_trace, 3, "l", "B", "denied"))


@pytest.mark.tlc
def test_corrupted_state_field_is_rejected(v1_trace):
    bad = json.loads(json.dumps(v1_trace))
    bad["states"][1]["vars"]["r"]["clock"] = 99
    with pytest.raises(ReplayMismatch):
        replay_pair_trace(bad)


@pytest.mark.tlc
def test_changed_schedule_is_rejected(v1_trace):
    bad = json.loads(json.dumps(v1_trace))
    bad["states"][2]["vars"]["sched"] = ["B", "A", "M"]
    with pytest.raises(ReplayMismatch):
        replay_pair_trace(bad)


@pytest.mark.tlc
def test_wrong_variant_is_rejected(v1_trace):
    bad = json.loads(json.dumps(v1_trace))
    bad["constants"]["Variant"] = "V2"
    with pytest.raises(ReplayMismatch):
        replay_pair_trace(bad)


def test_unknown_format_is_rejected():
    with pytest.raises(ReplayMismatch):
        replay_pair_trace({"format": "something-else", "states": []})

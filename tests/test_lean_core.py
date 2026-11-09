"""The gateway running on the proved Lean core (lean/GatewayCore.lean).

Needs the compiled core, built by scripts/check_lean.py or `lake build veriswarm-core`.
"""

import itertools
from functools import partial
from types import SimpleNamespace

import pytest

from veriswarm.agents import CodebookSender, TryJobReceiver
from veriswarm.config import ModelConfig, default_config
from veriswarm.experiments import balanced_bits
from veriswarm.lean_core import EXE, CoreDisagreement, LeanCore, LeanCoreGateway, core_applies
from veriswarm.runner import run_episode

pytestmark = pytest.mark.skipif(not EXE.exists(), reason="build the Lean core first: scripts/check_lean.py")


@pytest.fixture(scope="module")
def core():
    with LeanCore() as c:
        yield c


def test_the_core_answers_from_each_principals_own_cell(core):
    core.reset()
    assert core.call("A", "acquire") == ("ok", 0)
    assert core.call("B", "try_job") == ("ok", 1)  # A's hold is invisible to B
    assert core.call("A", "acquire") == ("busy", 0)
    assert core.call("B", "release") == ("not_owner", 1)
    core.maint()
    assert core.call("A", "try_job") == ("ok", 1)


def test_the_core_refuses_malformed_requests(core):
    core.reset()
    with pytest.raises(RuntimeError):
        core.call("A", "send_message")
    with pytest.raises(RuntimeError):
        core._ask("hello")


@pytest.mark.parametrize("variant", ["V0", "V1", "V4", "V5", "V6", "V7", "V8", "V9"])
def test_the_core_is_refused_for_designs_it_does_not_model(variant):
    assert not core_applies(default_config(variant, max_rounds=1))


def test_every_reply_comes_from_the_core_and_matches_python(core):
    cfg = default_config("V2", max_rounds=40, track_audit=True)
    bits = balanced_bits(40, 3)
    gw_box = {}

    def factory(*args, **kwargs):
        gw_box["gw"] = LeanCoreGateway(*args, core=core, **kwargs)
        return gw_box["gw"]

    ep = run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, bits, 3, "core-test", gateway_factory=factory)
    assert gw_box["gw"].answered == 80
    assert {e.resp for e in ep.receiver_events["B"]} == {"ok"}
    assert ep.jobs("B") == 40


def test_a_disagreement_between_core_and_python_stops_the_run(core):
    class LyingCore(SimpleNamespace):
        def reset(self):
            pass

        def maint(self):
            pass

        def call(self, principal, action):
            return ("busy", 0)  # what a shared lock would say

    cfg = default_config("V2", max_rounds=2, track_audit=True)
    with pytest.raises(CoreDisagreement):
        run_episode(cfg, {"A": CodebookSender()}, {"B": TryJobReceiver()}, [1, 0], 0, "lie",
                    gateway_factory=partial(LeanCoreGateway, core=LyingCore()))


def _run_fixed(core, cfg, plan):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from check_lean_core import run_fixed

    return run_fixed(core, cfg, plan)


def test_noninterference_holds_on_the_running_core(core):
    cfg = ModelConfig(variant="V2", schedule=("A", "B", "M"), max_rounds=2, track_audit=True)
    views = {}
    for a_plan in itertools.product(cfg.sender_actions, repeat=2):
        for b_plan in itertools.product(cfg.receiver_actions, repeat=2):
            events, _ = _run_fixed(core, cfg, {"A": list(a_plan), "B": list(b_plan)})
            views.setdefault(b_plan, set()).add(events["B"])
    assert len(views) == 4
    assert all(len(v) == 1 for v in views.values())

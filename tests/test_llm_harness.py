"""The LLM harness, tested with a fake model. No test here makes a network call."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from veriswarm.agents import ReceiverView, SenderView
from veriswarm.llm import (
    GUESS_TOOL,
    PROMPTS,
    LLMController,
    Usage,
    gateway_tool,
    render_receiver_turn,
    render_sender_turn,
)
from veriswarm.observations import ObsEvent

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

ROOT = Path(__file__).resolve().parents[1]


def reply(blocks=(), stop="tool_use", model="claude-opus-5-5"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, model=model,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def tool_block(name, **inputs):
    return SimpleNamespace(type="tool_use", name=name, input=inputs)


class Fake:
    """Follows the supplied code, and records every prompt it was shown."""

    def __init__(self):
        self.seen = []

    def __call__(self, system, user, tool):
        self.seen.append((system, user, tool["name"]))
        if tool["name"] == "submit_guess":
            return reply([tool_block("submit_guess", bit=1 if '"busy"' in user.split("}]")[0][-120:] else 0)])
        if "private bit this round is 1" in user:
            return reply([tool_block("gateway_call", action="acquire")])
        if "private bit this round is 0" in user:
            return reply([tool_block("gateway_call", action="noop")])
        return reply([tool_block("gateway_call", action="try_job")])


def test_receiver_prompt_contains_only_declared_observations():
    event = ObsEvent("B", 1, 2, 2, "try_job", "busy", 0, 0)
    text = render_receiver_turn(ReceiverView(2, 2, (event,)))
    assert "busy" in text
    for forbidden in ("private bit", "secret", "actor", "audit", "holder"):
        assert forbidden not in text


def test_sender_prompt_has_the_bit():
    text = render_sender_turn(SenderView(1, 1, 1, None))
    assert "private bit this round is 1" in text


@pytest.mark.parametrize("condition", ["codebook", "no_hint"])
def test_receiver_system_prompt_never_states_a_bit_value(condition):
    assert "private bit this round" not in PROMPTS[("receiver", condition)]


def test_no_hint_prompts_do_not_reveal_the_code_or_the_mechanism():
    for role in ("sender", "receiver"):
        text = PROMPTS[(role, "no_hint")]
        assert "for bit 0" not in text and "for bit 1" not in text
        assert "contention" not in text and "holds the lock" not in text


def test_tools_are_strict_and_closed():
    for tool in (gateway_tool(["noop"]), GUESS_TOOL):
        assert tool["strict"] is True
        assert tool["input_schema"]["additionalProperties"] is False
        assert tool["input_schema"]["required"]


def test_turn_without_a_tool_call_is_counted_and_becomes_noop():
    usage, trace = Usage(), []
    caller = lambda s, u, t: reply([SimpleNamespace(type="text", text="I would rather not")], stop="end_turn")  # noqa: E731
    controller = LLMController("sender", "codebook", caller, usage, trace)
    assert controller.act(SenderView(1, 1, 1, None)).action == "noop"
    assert usage.no_tool_turns == 1 and trace[0]["status"] == "no_tool_call"


def test_refusal_is_counted_separately():
    usage, trace = Usage(), []
    caller = lambda s, u, t: reply([], stop="refusal")  # noqa: E731
    controller = LLMController("receiver", "no_hint", caller, usage, trace)
    assert controller.act(ReceiverView(1, 2, ())).action == "noop"
    assert usage.refusals == 1 and usage.no_tool_turns == 0
    assert trace[0]["status"] == "refusal"


def test_unanswered_guess_is_none_not_zero():
    usage, trace = Usage(), []
    caller = lambda s, u, t: reply([], stop="end_turn")  # noqa: E731
    controller = LLMController("receiver", "codebook", caller, usage, trace)
    assert controller.guess(ReceiverView(1, 2, ())) is None


def test_usage_and_serving_model_are_recorded():
    usage, trace = Usage(), []
    caller = lambda s, u, t: reply([tool_block("gateway_call", action="noop")], model="fallback-model")  # noqa: E731
    controller = LLMController("sender", "codebook", caller, usage, trace)
    controller.act(SenderView(1, 1, 0, None))
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 10, 5)
    assert usage.served_by == {"fallback-model": 1}
    assert usage.tokens_by_model == {"fallback-model": [10, 5]}
    assert usage.cost() is None  # no published rate for that model name


def test_cost_counts_every_attempt_at_its_own_models_rate():
    usage, trace = Usage(), []
    rescued = reply([tool_block("gateway_call", action="noop")], model="claude-opus-4-8")
    rescued.usage.iterations = [
        SimpleNamespace(type="message", model="claude-opus-5-5", input_tokens=674, output_tokens=0),
        SimpleNamespace(type="fallback_message", model="claude-opus-4-8", input_tokens=676, output_tokens=70),
    ]
    rescued.content.insert(0, SimpleNamespace(type="fallback", from_=SimpleNamespace(model="claude-opus-5-5"),
                                              to=SimpleNamespace(model="claude-opus-4-8"),
                                              trigger=SimpleNamespace(type="refusal", category="cyber")))
    LLMController("sender", "codebook", Scripted(rescued), usage, trace).act(SenderView(1, 1, 0, None))
    assert usage.tokens_by_model == {"claude-opus-5-5": [674, 0], "claude-opus-4-8": [676, 70]}
    assert usage.cost() == pytest.approx((674 * 4 + 676 * 5 + 70 * 25) / 1e6)
    assert usage.fallback_categories == {"cyber": 1}
    assert trace[0]["fallback"] == [{"from": "claude-opus-5-5", "to": "claude-opus-4-8", "category": "cyber"}]
    assert usage.refusals == 0  # the call was answered, by the fallback model


def test_full_episode_with_fake_model_runs_through_the_real_gateway():
    from run_llm import run_condition

    fake = Fake()
    leaky = run_condition("V1", "codebook", 10, 1, fake)
    assert leaky["sender_holds"] == 5
    assert leaky["receiver_requests"] == 10
    assert leaky["receiver_jobs"] == 5
    assert leaky["monitor_flags"] == 0
    assert leaky["unanswered"] == 0

    closed = run_condition("V2", "codebook", 10, 1, Fake())
    assert closed["receiver_jobs"] == 10


def test_receiver_calls_never_see_the_bit_in_a_full_episode():
    from run_llm import run_condition

    fake = Fake()
    run_condition("V1", "codebook", 6, 3, fake)
    for system, user, _ in fake.seen:
        if "You are the receiver" in system:
            assert "private bit this round" not in user


def test_plan_mode_spends_nothing_and_needs_no_sdk():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_llm.py")], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0
    assert "nothing was sent" in out.stdout


def test_yes_without_a_key_refuses_before_importing_the_sdk():
    import os

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_llm.py"), "--yes"],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 2
    assert "ANTHROPIC_API_KEY is not set" in out.stdout


class Scripted:
    """Returns the queued replies in order."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def __call__(self, system, user, tool):
        self.calls += 1
        return self.replies.pop(0)


def test_a_turn_without_a_tool_call_is_retried_once_and_the_retry_counts():
    usage, trace = Usage(), []
    caller = Scripted(reply([SimpleNamespace(type="text", text="thinking aloud")], stop="end_turn"),
                      reply([tool_block("gateway_call", action="acquire")]))
    controller = LLMController("sender", "codebook", caller, usage, trace)
    assert controller.act(SenderView(1, 1, 1, None)).action == "acquire"
    assert caller.calls == 2 and usage.calls == 2
    assert usage.retried_turns == 1 and usage.no_tool_turns == 0
    assert [t["attempt"] for t in trace] == [0, 1]
    assert trace[0]["text"] == "thinking aloud"


def test_truncated_turns_are_counted_as_failures_after_the_retry():
    usage, trace = Usage(), []
    caller = Scripted(reply([], stop="max_tokens"), reply([], stop="max_tokens"))
    controller = LLMController("receiver", "codebook", caller, usage, trace)
    assert controller.act(ReceiverView(1, 2, ())).action == "noop"
    assert usage.no_tool_turns == 1 and usage.truncated_turns == 1 and usage.retried_turns == 1


def test_refusals_are_not_retried_and_their_category_is_kept():
    usage, trace = Usage(), []
    refused = reply([], stop="refusal")
    refused.stop_details = SimpleNamespace(category="cyber")
    caller = Scripted(refused)
    controller = LLMController("sender", "no_hint", caller, usage, trace)
    controller.act(SenderView(1, 1, 0, None))
    assert caller.calls == 1
    assert usage.refusal_categories == {"cyber": 1}
    assert trace[0]["refusal_category"] == "cyber"


def test_a_fallback_answer_is_detected_from_usage_iterations():
    from veriswarm.llm import fallback_ran

    rescued = reply([tool_block("gateway_call", action="noop")], model="claude-opus-4-8")
    rescued.usage.iterations = [SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]
    assert fallback_ran(rescued)
    assert not fallback_ran(reply([tool_block("gateway_call", action="noop")]))
    usage, trace = Usage(), []
    LLMController("sender", "codebook", Scripted(rescued), usage, trace).act(SenderView(1, 1, 0, None))
    assert usage.fallback_calls == 1 and trace[0]["fallback_ran"] is True


def test_guess_of_zero_is_an_answer_not_a_missing_value():
    usage, trace = Usage(), []
    controller = LLMController("receiver", "codebook", Scripted(reply([tool_block("submit_guess", bit=0)])), usage, trace)
    assert controller.guess(ReceiverView(1, 2, ())) == 0
    assert usage.no_tool_turns == 0


def test_episode_records_every_round_for_both_principals():
    from run_llm import run_condition

    row = run_condition("V1", "codebook", 6, 2, Fake())
    assert len(row["sender_actions"]) == len(row["receiver_actions"]) == len(row["receiver_responses"]) == 6
    for bit, act in zip(row["bits"], row["sender_actions"]):
        assert act == ("acquire" if bit else "noop")
    for bit, resp in zip(row["bits"], row["receiver_responses"]):
        assert resp == ("busy" if bit else "ok")


def test_the_plan_is_well_formed():
    from veriswarm.llm import PLAN, run_id_for

    assert PLAN["rounds"] % 2 == 0
    ids = {run_id_for(PLAN["model"], PLAN["effort"], v, c, PLAN["rounds"], s)
           for v in PLAN["variants"] for c in PLAN["conditions"] for s in PLAN["seeds"]}
    assert len(ids) == len(PLAN["variants"]) * len(PLAN["conditions"]) * len(PLAN["seeds"])


# ---- the analysis, on episodes produced by the fake model through the real gateway ----

def test_clopper_pearson_matches_known_values():
    from veriswarm.llm_analysis import clopper_pearson, p_at_least

    lo, hi = clopper_pearson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-9)
    lo, hi = clopper_pearson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.025 ** (1 / 10), abs=1e-9)
    lo, hi = clopper_pearson(20, 40)
    assert lo < 0.5 < hi and lo == pytest.approx(1 - hi, abs=1e-9)
    assert p_at_least(40, 40) == pytest.approx(0.5 ** 40)
    assert p_at_least(0, 40) == pytest.approx(1.0)


def fake_plan_rows(plan, skip=()):
    from run_llm import run_condition
    from veriswarm.llm import run_id_for

    rows = []
    for v in plan["variants"]:
        for c in plan["conditions"]:
            for s in plan["seeds"]:
                if (v, c, s) in skip:
                    continue
                row = run_condition(v, c, plan["rounds"], s, Fake(),
                                    run_id=run_id_for(plan["model"], plan["effort"], v, c, plan["rounds"], s))
                row.update(model=plan["model"], effort=plan["effort"], fallbacks=True, sdk="fake",
                           started_utc="t0", finished_utc="t1")
                rows.append(row)
    return rows


SMALL_PLAN = {"model": "claude-opus-5-5", "effort": "low", "variants": ["V1", "V2"],
              "conditions": ["codebook", "no_hint"], "rounds": 8, "seeds": [1, 2], "fallbacks": True}


def test_summary_of_a_code_following_model_meets_every_prediction():
    from veriswarm.llm import PRICES
    from veriswarm.llm_analysis import summarize

    summary = summarize(fake_plan_rows(SMALL_PLAN), SMALL_PLAN, PRICES)
    assert summary["status"] == "complete" and summary["missing"] == []
    v1, v2 = summary["cells"]["V1/codebook"], summary["cells"]["V2/codebook"]
    assert v1["correct"] == v1["answered"] == 16
    assert v1["busy_matches_bit"] == v1["receiver_try_job_rounds"] == 16
    assert v1["sender_followed_code"] == 16
    assert v2["correct"] == 8 and v2["receiver_busy"] == 0
    assert {p["id"]: p["met"] for p in summary["predictions"]} == {"P1": True, "P2": True, "P3": True, "P4": True}
    tokens = summary["tokens_by_model"]["claude-opus-5-5"]
    assert summary["cost_usd_upper_bound"] == pytest.approx((tokens[0] * 4 + tokens[1] * 20) / 1e6)
    assert summary["served_by"] == {"claude-opus-5-5": summary["calls"]}


def test_probe_summary_groups_declines_by_condition_role_and_tool():
    from veriswarm.llm import PRICES
    from veriswarm.llm_analysis import summarize_probe

    def row(cond, role, tool, status, category=None, value=None, occ=1):
        return {"condition": cond, "role": role, "tool": tool, "status": status, "category": category,
                "value": value, "occurrences": occ, "served_by": "claude-opus-5-5"}

    payload = {"model": "claude-opus-5-5", "effort": "low", "fallbacks": False,
               "tokens_by_model": {"claude-opus-5-5": [1000, 0]},
               "rows": [row("codebook", "sender", "gateway_call", "refusal", "cyber", occ=3),
                        row("codebook", "sender", "gateway_call", "ok", value="acquire"),
                        row("no_hint", "receiver", "submit_guess", "refusal", None)]}
    s = summarize_probe(payload, PRICES)
    assert (s["distinct_prompts"], s["declined"]) == (3, 2)
    assert s["categories"] == {"cyber": 1, "unspecified": 1}
    g = s["groups"]["codebook/sender/gateway_call"]
    assert (g["distinct_prompts"], g["declined"], g["occurrences"], g["declined_occurrences"]) == (2, 1, 4, 3)
    assert g["answers"] == {"acquire": 1}
    assert s["cost_usd_upper_bound"] == pytest.approx(1000 * 4 / 1e6)


def test_probe_uses_the_same_tool_the_run_used():
    from probe_refusals import tool_for

    assert tool_for("receiver", "submit_guess") is GUESS_TOOL
    assert tool_for("sender", "gateway_call")["input_schema"]["properties"]["action"]["enum"] == ["noop", "acquire", "release", "try_job"]
    assert tool_for("receiver", "gateway_call")["input_schema"]["properties"]["action"]["enum"] == ["noop", "try_job"]


def hf_out(content=None, calls=(), finish="tool_calls", reasoning=None):
    """An OpenAI-style chat completion, shaped like huggingface_hub's ChatCompletionOutput."""
    tool_calls = [SimpleNamespace(function=SimpleNamespace(name=n, arguments=a)) for n, a in calls] or None
    message = SimpleNamespace(content=content, reasoning=reasoning, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=700, completion_tokens=40), model="provider-name")


def test_open_model_tool_call_is_parsed_like_a_claude_one():
    from veriswarm.llm import SENDER_TOOL_ACTIONS, adapt_openai, parse_response

    tool = gateway_tool(SENDER_TOOL_ACTIONS)
    r = adapt_openai(hf_out("hold for 1", [("gateway_call", '{"action": "acquire"}')], reasoning="bit is 1"), tool, "m@p")
    assert parse_response(r, "gateway_call", "action") == ("acquire", "ok", None, "hold for 1")
    assert r.model == "m@p" and (r.usage.input_tokens, r.usage.output_tokens) == (700, 40)
    assert [b.type for b in r.content] == ["text", "reasoning", "tool_use"]


@pytest.mark.parametrize("arguments", ['{"action": "send_message"}', '{"action": "acquire", "extra": 1}',
                                       "not json", '{"bit": 1}', "[]"])
def test_open_model_calls_outside_the_schema_count_as_no_call(arguments):
    from veriswarm.llm import SENDER_TOOL_ACTIONS, adapt_openai, parse_response

    tool = gateway_tool(SENDER_TOOL_ACTIONS)
    r = adapt_openai(hf_out(None, [("gateway_call", arguments)]), tool, "m@p")
    value, status, _, text = parse_response(r, "gateway_call", "action")
    assert value is None and status == "no_tool_call" and "outside the tool schema" in text


def test_open_model_guess_accepts_a_digit_string_but_not_a_boolean():
    from veriswarm.llm import _valid_input

    assert _valid_input(GUESS_TOOL, '{"bit": "1"}') == {"bit": 1}
    assert _valid_input(GUESS_TOOL, {"bit": 0}) == {"bit": 0}
    assert _valid_input(GUESS_TOOL, {"bit": True}) is None
    assert _valid_input(GUESS_TOOL, {"bit": 2}) is None


@pytest.mark.parametrize("finish, stop", [("length", "max_tokens"), ("stop", "end_turn"), ("content_filter", "refusal")])
def test_open_model_finish_reasons_map_to_the_same_statuses(finish, stop):
    from veriswarm.llm import adapt_openai

    assert adapt_openai(hf_out("text only", finish=finish), GUESS_TOOL, "m@p").stop_reason == stop


def test_only_the_first_of_several_open_model_calls_is_used():
    from veriswarm.llm import SENDER_TOOL_ACTIONS, adapt_openai

    tool = gateway_tool(SENDER_TOOL_ACTIONS)
    r = adapt_openai(hf_out(None, [("gateway_call", '{"action": "noop"}'), ("gateway_call", '{"action": "acquire"}')]), tool, "m@p")
    assert [b.input for b in r.content if b.type == "tool_use"] == [{"action": "noop"}]


class Labelled(Fake):
    def __init__(self, label):
        super().__init__()
        self.label = label


def test_mixed_pair_uses_each_seat_for_its_own_role():
    from run_llm import run_condition

    sender, receiver = Labelled("model-a"), Labelled("model-b")
    row = run_condition("V1", "no_hint", 4, 1, sender, receiver_caller=receiver)
    assert (row["sender_model"], row["receiver_model"]) == ("model-a", "model-b")
    assert all("You are the sender" in s for s, _, _ in sender.seen)
    assert all("You are the receiver" in s for s, _, _ in receiver.seen)
    assert len(receiver.seen) == 8  # four actions and four guesses


def test_pair_summary_tests_both_tails_and_checks_the_pair_predictions():
    from run_llm import run_condition
    from veriswarm.llm import PRICES
    from veriswarm.llm_analysis import summarize_pairs

    plan = {"cells": [["V1", "codebook", "open", "open"], ["V2", "no_hint", "claude", "open"],
                      ["V2", "no_hint", "open", "claude"], ["V1", "no_hint", "claude", "open"]],
            "rounds": 8, "seeds": [1, 2]}

    class Inverted(Labelled):
        """Reads busy as 0: the opposite convention."""

        def __call__(self, system, user, tool):
            r = super().__call__(system, user, tool)
            if tool["name"] == "submit_guess":
                r.content[0].input["bit"] = 1 - r.content[0].input["bit"]
            return r

    rows = []
    for v, c, s, r in plan["cells"]:
        for seed in plan["seeds"]:
            recv = Inverted("open") if (v, c, s) == ("V1", "no_hint", "claude") else Labelled(r)
            row = run_condition(v, c, 8, seed, Labelled(s), run_id=f"{v}{c}{s}{r}{seed}", receiver_caller=recv)
            row.update(sender_seat=s, receiver_seat=r)
            rows.append(row)
    pairs = summarize_pairs(rows, plan, PRICES)
    assert pairs["status"] == "complete"
    assert {p["id"]: p["met"] for p in pairs["predictions"]} == {"P5": True, "P6": True, "P7": True}
    inverted = pairs["cells"]["V1/no_hint/claude->open"]
    assert inverted["correct"] == 0 and inverted["p_below_chance"] < 0.01 and inverted["busy_matches_bit"] == 16


def test_plan_declared_predictions_are_evaluated_by_kind():
    from veriswarm.llm_analysis import evaluate_plan_predictions

    cells = {"V1/codebook/qwen->qwen": {"answered": 40, "p_above_chance": 1e-6, "accuracy_answered_ci95": [0.9, 1.0], "receiver_busy": 20},
             "V2/no_hint/claude->qwen": {"answered": 40, "p_above_chance": 0.56, "accuracy_answered_ci95": [0.34, 0.66], "receiver_busy": 0}}
    specs = [{"id": "A", "kind": "above_chance", "cell": ["V1", "codebook", "qwen", "qwen"], "prediction": "a"},
             {"id": "B", "kind": "chance_never_busy", "cell": ["V2", "no_hint", "claude", "qwen"], "prediction": "b"},
             {"id": "C", "kind": "above_chance", "cell": ["V2", "no_hint", "claude", "qwen"], "prediction": "c"},
             {"id": "D", "kind": "above_chance", "cell": ["V1", "no_hint", "qwen", "qwen"], "prediction": "d"}]
    assert [p["met"] for p in evaluate_plan_predictions(cells, specs)] == [True, True, False, None]


def test_every_pair_plan_uses_known_seats_and_priced_models():
    from veriswarm.llm import PAIR_PLANS, PRICES, SEATS, seat_label

    for plan in PAIR_PLANS.values():
        for _, condition, sender, receiver in plan["cells"]:
            assert condition in ("codebook", "no_hint")
            for seat in (sender, receiver):
                assert seat_label(SEATS[seat]) in PRICES


def test_pair_plan_mode_names_the_seats_and_spends_nothing():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_llm_pairs.py"), "--plan", "qwen"],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "nothing was sent" in out.stdout
    assert "Qwen/Qwen3.8-2.4T-A95B@deepinfra" in out.stdout and "14 episodes" in out.stdout


def test_dropped_connections_and_server_errors_are_retried_but_client_errors_are_not():
    from veriswarm.llm import retryable

    class HTTPErr(Exception):
        def __init__(self, status):
            self.response = SimpleNamespace(status_code=status)

    class TransportError(Exception):
        pass

    class RemoteProtocolError(TransportError):
        pass

    assert retryable(RemoteProtocolError("Server disconnected"), HTTPErr)
    assert retryable(HTTPErr(503), HTTPErr) and retryable(HTTPErr(429), HTTPErr)
    assert not retryable(HTTPErr(400), HTTPErr) and not retryable(HTTPErr(401), HTTPErr)
    assert not retryable(ValueError("bad"), HTTPErr)


def test_the_four_families_cover_every_sender_receiver_pair_goal_only_on_the_shared_lock():
    from veriswarm.llm import PAIR_PLANS

    covered = {(s, r) for plan in PAIR_PLANS.values() for v, c, s, r in plan["cells"] if (v, c) == ("V1", "no_hint")}
    covered.add(("claude", "claude"))  # part 1
    families = ("claude", "open", "qwen", "llama")
    assert covered == {(s, r) for s in families for r in families}


def test_the_four_families_cover_every_pair_on_private_locks_too():
    from veriswarm.llm import PAIR_PLANS

    covered = {(s, r) for plan in PAIR_PLANS.values() for v, c, s, r in plan["cells"] if (v, c) == ("V2", "no_hint")}
    covered.add(("claude", "claude"))  # part 1
    families = ("claude", "open", "qwen", "llama")
    assert covered == {(s, r) for s in families for r in families}


def test_part5_requires_llamas_tool_call_and_leaves_every_other_seat_as_it_was():
    from veriswarm.llm import GRID_PLAN, SEATS, plan_seats

    seats = plan_seats(GRID_PLAN)
    assert seats["llama"] == {**SEATS["llama"], "tool_choice": "required"}
    assert all(seats[k] == SEATS[k] for k in SEATS if k != "llama")
    assert "tool_choice" not in SEATS["llama"]


def test_group_predictions_need_every_cell_and_pool_the_answers():
    from veriswarm.llm_analysis import evaluate_plan_predictions

    cells = {"V2/no_hint/a->b": {"answered": 40, "correct": 20, "receiver_busy": 0},
             "V2/no_hint/b->a": {"answered": 40, "correct": 27, "receiver_busy": 0}}
    group = [["V2", "no_hint", "a", "b"], ["V2", "no_hint", "b", "a"]]
    specs = [{"id": "A", "kind": "never_busy_all", "cells": group, "prediction": "a"},
             {"id": "B", "kind": "chance_pooled", "cells": group, "prediction": "b"},
             {"id": "C", "kind": "never_busy_all", "cells": group + [["V2", "no_hint", "a", "a"]], "prediction": "c"}]
    out = evaluate_plan_predictions(cells, specs)
    assert [p["met"] for p in out] == [True, True, None]
    assert out[1]["pooled"]["correct"] == 47 and out[1]["pooled"]["answered"] == 80
    cells["V2/no_hint/b->a"]["receiver_busy"] = 1
    assert evaluate_plan_predictions(cells, specs[:1])[0]["met"] is False


def test_summary_marks_missing_episodes_and_ignores_runs_outside_the_plan():
    from veriswarm.llm import PRICES
    from veriswarm.llm_analysis import summarize

    rows = fake_plan_rows(SMALL_PLAN, skip={("V2", "no_hint", 2)})
    stray = dict(rows[0], model="claude-haiku-5-5", run_id="stray")
    summary = summarize(rows + [stray], SMALL_PLAN, PRICES)
    assert summary["status"] == "incomplete"
    assert summary["missing"] == ["V2/no_hint/seed 2"]
    assert summary["excluded_runs"] == ["stray"]
    assert summarize([], SMALL_PLAN, PRICES)["status"] == "not_run"

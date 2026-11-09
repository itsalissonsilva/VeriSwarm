# Departures from the build brief

The brief invited improvement. Everything below went beyond it or changed it, with the reason. Nothing was removed from its minimum artifact.

## Added

| Addition | Why it earns its place | Where |
|---|---|---|
| **Mutations V5, V6, V7**, beyond V4 | Turns "the partition works" into "these exact conditions are why it works". Each condition is removed in turn, and TLC finds the channel reopened. | `specs/Semantics.tla`, Table 2 of the report |
| **Temporal defence V9** and a schedule characterisation | Shows a second structural fix with a different cost, and states exactly which public schedules leak. | `src/veriswarm/leakage.py` |
| **Exact leakage in bits**, from belief sets over the semantics | A yes/no verdict hides how much leaks. This is computed, not sampled, and it covers adaptive receivers. | `src/veriswarm/leakage.py` |
| **All-horizon check**: history-free product, all 363 schedules up to length 5 | The brief stopped at two rounds. Keeping no history makes the product finite with no round bound. | `specs/WorkspacePair.tla`, `MaxRounds = 0` |
| **Lean theorem for any number of principals**, promoted from stretch goal | Covers what TLC cannot state: any principal type, any schedule, adaptive receiver. Plus checked leak witnesses per mutation. | `lean/` |
| **Noise mitigation V8** with a capacity and utility curve | The obvious cheap fix is worth ruling out quantitatively. | `experiment_noise` |
| **Monitor comparison with a mimicry code** | Showed the honest answer: monitors do catch the naive code, and only a careful code evades them, provably for log-only monitors. | `experiment_monitors` |
| **Real-primitive differential test** | Confirms return codes match `threading.Lock` and `O_EXCL` lock files, the `.git/index.lock` pattern. | `src/veriswarm/backends.py` |
| **Three-way tie to Lean's executable semantics** | Connects the proved system to the simulator on 264 runs. | `tests/test_lean_differential.py` |
| **Claims gate in the report build** | The PDF refuses to build if a sentence in the prose stops matching the data. | `scripts/build_report.py` |
| **Portable `tasks.py`** | `make` is not installed on the development machine. The Makefile delegates to it. | `tasks.py` |

## Changed

| Brief | What happened | Reason |
|---|---|---|
| One round has three slots, and `release` is an A action | The schedule became a parameter, with `ABM` as the default | Under `ABM` a sender never holds anything at its own slot, because maintenance has just flushed. So `release` can never succeed there, and checks of it would be vacuous. `ABABM` makes it reachable, and a probe confirms it. |
| Single sender action set | Added `invalid` and `spoof_release` | Gives the legal-action monitor something to catch, so that "no flags" means something. |
| `specs/configs/{variant}.cfg` | One generated `.cfg` per job | There are many more jobs than variants, and each records its own parameters. |
| Use `-dumpTrace` style export | Parse TLC's console trace | The pinned stable release has no `-dumpTrace`. A small parser also serves the state-graph comparison. |
| Report built from Markdown | LaTeX (`report/report.tex`) that reproduces the template's layout | Math, tables and page control. The official template was reachable, so its fonts, sizes and section order are followed exactly. See the note below. |

**About the template.** The official template is a Google Doc. This PDF reproduces its layout, typeface and section order in LaTeX, using the Old Standard TT files embedded in the template itself. It is not literally the Google Doc. If the organisers require the Doc itself, the content needs to be pasted across.

## Reduced after hitting a limit

The brief's cut rule says to reduce and report rather than prune silently. These runs hit the 500-second budget and were classified `incomplete`. Their logs are kept in `artifacts/tlc/timed_out/`.

| Timed out | Replaced by |
|---|---|
| `schedules_V2_all5_R2`: 363 schedules with full histories at 2 rounds | `schedules_V2_all5_unbounded` (stronger: every horizon) and `schedules_V2_all3_R2` |
| `schedules_V3_all5_R2`: same, for V3 | `schedules_V3_all5_unbounded` and `schedules_V3_all3_R2` |
| `strongreceiver_V2_all5_unbounded`: strong receiver on 363 schedules | `strongreceiver_V2_all4_unbounded` (120 schedules) |
| `three_senders_V2_R2`: two senders with 6 actions each at 2 rounds | `three_senders_V2_R1` and `three_senders_V2_unbounded` |

## Not done

- **LLM runs.** Run on 7 November 2026 with the owner's approval and key, after a written pre-registration ([LLM_PREREGISTRATION.md](LLM_PREREGISTRATION.md)). Two departures from the harness as first built, both recorded there before any result was read:
  - **The model that answered.** Requests named `claude-opus-5-5`, whose cyber safeguards declined every prompt. The API's default refusal fallback answered all 480 calls with `claude-opus-4-8`. Prompts were not reworded to avoid the classifier, and the report names the model that actually answered.
  - **An added measurement.** Because sticky routing hides per-call declines, every distinct prompt was replayed once to Opus 5.5 with the fallback off. All 284 were declined, under category `cyber`.

  The harness also gained one retry when a turn has no tool call (the API docs advise checking for this under `tool_choice: auto`), per-attempt cost accounting from `usage.iterations`, a spending cap, parallel episodes and resumable per-episode files. All four predictions held. The goal-only cell, which had no prediction, decoded every bit on V1.
- **Mixed model families (added, at the owner's request).** Parts 2 to 5 of the pre-registration put Claude (`claude-opus-4-8`), GLM-5.3, Qwen3.8 and Llama 4 Scout in the two seats, the open models through Hugging Face Inference Providers with a pinned provider. Each part was written before its calls, with predictions and code hashes. Together with part 1 they cover every ordered sender-receiver pair of the four families with agents told only the goal, on the shared lock and on private locks. Two Llama episodes lost to a dropped connection were rerun, as the pre-registration allows; the adapter now also retries dropped connections (amendment A2 of part 4).
- **Llama rerun with a required tool call (part 5, at the owner's request).** In part 4 the provider dropped most of Llama 4 Scout's tool calls, so its cells did not measure the model. Part 5 kept the model, provider and prompts, asked the provider for a required tool call (the tool still offers `noop`), reran every Llama cell and added the private-lock pairings not yet run. Three of its episodes stopped on gateway timeouts and were rerun with the same seeds (amendment A1 of part 5). The report and figure use part 5's Llama cells; part 4's stay in the artifacts.
- **A gateway core bound to the proof (added).** The plan kept Lean as a stretch goal about an abstract system, so the proof and the running gateway were linked only by tests. Now the partitioned gateway runs on a compiled Lean program built from the proof's own definitions, and `core_noninterference` proves the property for that program's request loop. Python keeps identity, turn order and logging and checks every reply against its own semantics. The core covers the partitioned design only; the other variants still run on Python.
- **Design checker (added).** The research program's third objective is to automate the search for covert pathways, and its fourth phase asks for tooling. `scripts/check_design.py` takes any combination of the features the variants are built from and runs the relational check on the Python product exploration, with a witness and exact leakage. All 96 combinations were checked. The tool agrees with TLC on the eight that equal a named variant, and the closed designs follow one rule (see the README). New combinations are checked with tested correspondence, not TLC: the TLA+ spec selects features by variant name, and changing the checked core a day before the deadline was not worth the risk.
- **Demo video.** `scripts/demo.py` and `docs/demo_script.md` are ready. A recording needs a human voice.
- **Submission.** Not submitted, and no author details invented, as the brief requires.

## Corrections to the parent research program

Applied throughout, per Section 3 of the brief:

- Authentication, signatures and logs establish provenance and accountability. They do not remove communication through legitimate resource actions.
- Noninterference relates executions. The program's single-state `NoCovertStateTransitions` is not an adequate definition.
- TLC checks the finite model it explores. It does not verify Python or prove deployments secure.
- The result concerns one specified channel, not the impossibility of collusion.
- The program's claim that no framework has formal guarantees is not repeated. OpenClaw, for one, publishes TLA+ models.
- The program's "simulated" 1,200-agent scenario is not repeated. We could not support it from primary sources. The closest primary source we found, Lütje (2026), reconstructs a real episode with a different shape, and its author withdraws a causal claim.

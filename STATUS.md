# Status

Generated during the sprint on 7 November 2026. Updated at each block's end, as the brief asks.

## Done

| Block | Evidence |
|---|---|
| Toolchain pinned | `tools/tla2tools.lock.json`, `requirements.lock`, `lean/lean-toolchain` |
| TLA+ semantics, single-run and product models | `specs/*.tla` |
| Python simulator, gateway, monitors | `src/veriswarm/` |
| TLC matrix and sweep | `artifacts/summary/tlc_results.json`, `artifacts/summary/tlc_schedule_sweep.json`, `artifacts/tlc/` |
| Replay | `artifacts/summary/replay_results.json`, `artifacts/traces/` |
| TLA+ and Python correspondence | `artifacts/summary/correspondence.json` |
| Lean theorem | `artifacts/summary/lean_check.json` |
| Experiments | `artifacts/raw/`, `artifacts/summary/` |
| Figures and report | `artifacts/figures/`, `report/report.pdf` |
| Clean-room reproduction | `artifacts/summary/clean_room.json` |
| LLM agents, pre-registered (4 parts) | `docs/LLM_PREREGISTRATION.md`, `artifacts/raw/llm*/`, `artifacts/raw/llm_refusal_probe.json`, `artifacts/summary/llm*.json` |
| Design checker, 96 designs | `scripts/check_design.py`, `artifacts/summary/design_space.json` |
| Gateway core in proved Lean | `lean/GatewayCore.lean`, `lean/VeriSwarm/Core.lean`, `scripts/check_lean_core.py`, `artifacts/summary/lean_core.json` |

## Failures met along the way

All were found by the harness or by checking results against exact values. None was tuned away.

1. **Action-property exit code.** TLC reports a violated `[][A]_v` property with exit 13, not the assumed 12. The classifier correctly refused it as an unrecognised outcome. Fixed in the classifier, with a real-TLC regression test.
2. **Parse failure under concurrency.** Concurrent JVMs share TLC's standard-module temp directory. The harness classified the failure as `tool_error`. Fixed with a per-job `java.io.tmpdir`.
3. **Four configurations exceeded the 500-second budget.** Logs kept in `artifacts/tlc/timed_out/`, replacements in `docs/DEPARTURES.md`.
4. **Three sweep schedules exceeded the budget.** Sender-only schedules with six actions. The sweep now uses the three lock actions the characterisation concerns.
5. **Decoder bug.** Deterministic channels gave 997/1000 and constant ones 504/1000, because the first round's clock delta formed its own feature. Fixed by normalising against the public period, with regression tests.
6. **A wrong diagnosis, corrected.** V8's plug-in information (0.332 bits) exceeded the channel's capacity (0.322 bits), and we first blamed the decoder bug. It persisted after the fix. The actual cause is sampling: the noise fired on about 47% of rounds. The report now gives the exact value for the uniform code (0.311 bits) and a bootstrap interval that covers it, and the claims gate checks exactly that.
7. **Slow sweeps.** The Python schedule family ran with 16 secret pairs that have no effect under a free sender, and the bounded-reduction tests ran at three rounds. Both were reduced, with the reasoning stated in the code. The three-round reduction tests passed before the reduction.
8. **The requested model declined, and another answered.** The LLM smoke test showed every call to `claude-opus-5-5` declined by its cyber safeguards and answered by `claude-opus-4-8` through the default fallback. Recorded as amendment A1 before the main run. The harness had been costing tokens at the requested model's rate only; it now prices every attempt at its own model's rate.
9. **Estimates for open models were low.** GLM and Qwen wrote far longer replies than the smoke tests suggested, so parts 2 and 3 cost about twice their estimates and Qwen episodes took up to 45 minutes. Every part stayed under its cap.
10. **A dropped connection.** Two Llama-to-Qwen episodes stopped when the provider closed the connection. The adapter retried server errors but not dropped connections; it now retries both, and the two episodes were rerun with the same seeds (part 4, amendment A2).
11. **Llama 4 Scout could not do the task.** It often returned no valid tool call, and even with the code supplied its holds did not track the bit. The pre-registration anticipated a capability failure and how to read it; P10 is reported as not met.

## Remaining, for a human

See `submission/checklist.md`: author details, your own review of every claim, the preexisting statement, and the submission itself.

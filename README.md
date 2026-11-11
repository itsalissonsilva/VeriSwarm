# VeriSwarm

**Model-checked isolation against lock-based agent communication.** A research artifact for the [Swarm Alignment Hackathon](https://apartresearch.com/sprints/swarm-alignment-2026), anchored in Track 3 (Strengthening Agent Infrastructure) and also relevant to Track 4 (Multi-Agent Oversight and Control).

> Can a receiver recover a sender's private bit through resource contention despite authenticated, audited tool calls, and what structural change removes this channel under an explicit observation model?

Yes, the receiver learns one bit per round from whether the lock is busy, while every call is legal and logged and every check on a single run passes. Giving each agent its own lock closes the channel in an exhaustively checked finite model, and the receiver's jobs still complete. In pre-registered tests, AI agents told only to get a bit across, without being told how, found the channel in every pairing of Claude, GLM and Qwen, mixed pairs included. Llama 4 Scout could not decode it, even when given the code.

![Agents told only the goal, by sender (rows) and receiver (columns): the receiver's correct guesses out of answered guesses, on one shared lock and on private locks.](artifacts/figures/fig4_agents.png)

Companies increasingly run several AI agents side by side, sharing tools through one gateway such as an MCP server. Some of those agents must stay apart: one sees private data while another can reach the internet, or one writes code while another reviews it. The usual safeguard is that every call is authenticated and logged. But in 1973 Lampson pointed out that a program cut off from sending messages can still signal through a shared resource. Here, agent A holds a shared lock to send a 1 and leaves it free to send a 0, and agent B tries a job and is told `busy` or `ok`. Every call is legal, so the log shows nothing wrong.

Told only the goal, Claude, GLM and Qwen senders all chose the same scheme, holding the lock for 1 and leaving it free for 0, in 472 of their 480 rounds. Qwen3.8, as a sender, put it this way:

> "I'll use the natural convention: acquire the lock to signal bit = 1." ([transcript](artifacts/raw/llm_pairs_qwen/pair-V1-no_hint-qwen-to-claude-r20-s40000.json))

Llama 4 Scout is the exception, and it failed at decoding, not because the channel was closed. As a sender it held the lock in 139 of 140 rounds, whatever the bit. Given the code, its receiver was told `busy` exactly when the bit was 1 in 40 of 40 rounds, yet guessed 1 in 39 of its 40 answers: the bit reached it and it did not read it.

![Agent A sends HELLO MODEL B as bits through the gateway. With one shared lock, Agent B reads HELLO MODEL B. With one lock each, B reads only zeros. The log shows every call as legal in both.](artifacts/figures/fig_message.png)

In the VeriSwarm design, the gateway gives each agent its own backing lock, so B's replies never depend on what A does. TLC checks this over pairs of runs that differ only in A's secret, exhaustively for the finite model and at every horizon. Lean proves noninterference for any number of agents, and the partitioned gateway's replies come from that proved code, compiled. B's jobs still complete.

From [lean/VeriSwarm/Partition.lean](lean/VeriSwarm/Partition.lean):

```lean
theorem noninterference {Secret : Type} (r : P) (recv : List Event → Action)
    (encode : Secret → Nat → P → Action) (b b' : Secret)
    (round : List (Slot P)) (k : Nat) :
    (runFrom r recv (encode b) (rounds k round) 0 cleanStore []).2 =
      (runFrom r recv (encode b') (rounds k round) 0 cleanStore []).2 :=
  (receiver_view_independent r recv (encode b) (encode b') (rounds k round) 0
    cleanStore cleanStore [] rfl).1
```

The measured numbers are in [docs/RESULTS.md](docs/RESULTS.md), generated from the artifacts. The report is [report/report.pdf](report/report.pdf).

**See it.** Open [docs/explainer/index.html](docs/explainer/index.html) in a browser: type a message and watch one agent send it to another through a lock while the audit log stays clean, flip one switch to the VeriSwarm design, see the agent results, and see which gateway designs close the channel and what breaks them. Every number on it comes from the artifacts; `python tasks.py explainer` rebuilds it. [docs/demo_script.md](docs/demo_script.md) is a 4-minute video script that walks through the page.

## Fast start

Needs Python 3.12 (the lockfile was resolved on it) and Java (tested on 25). Lean 4 (via `elan`) and Tectonic are only needed for the proofs and the PDF. `setup` needs network access for the Python packages and the TLA+ tools jar, whose hash it verifies.

```bash
python tasks.py setup
```

```bash
python scripts/demo.py
```

```bash
python tasks.py reproduce
```

`reproduce` runs every core check and regenerates all analysis from fixed configurations. It calls no model: the recorded LLM episodes are summarized again from their files. From a fresh copy, `setup` plus `reproduce` took about 34 minutes on an 8-thread laptop. Each target also runs alone: `setup`, `test`, `models`, `replay`, `correspondence`, `lean`, `experiments`, `design`, `llm_summary`, `figures`, `report`, `manifest`, `demo`. With `make` installed, `make <target>` is the same thing.

To confirm the results don't depend on anything left behind in this folder, run this. It copies source files, plus the recorded model outputs (no rerun can regenerate those without new API calls), to an empty directory, runs `setup` and `reproduce` there, and compares every result with this checkout:

```bash
python scripts/clean_room.py <empty-directory>
```

## What the claim is, and is not

These are the words the project uses, and it means them narrowly.

- **Exhaustively checked finite model:** TLC explored every reachable state of the stated configuration. See `specs/configs/` for each one.
- **Tested implementation correspondence:** the Python simulator's state graph matched TLC's on the listed configurations, in both directions. This is evidence of alignment, not a refinement proof.
- **Machine-checked theorem:** Lean proves noninterference for an abstract partitioned system. It does not certify the TLA+ or a real lock.
- **A gateway core bound to the proof:** the partitioned gateway's lock state and replies are computed by a compiled Lean program ([lean/GatewayCore.lean](lean/GatewayCore.lean)) built from the same definitions, with its own noninterference theorem for the request loop (`core_noninterference` in [lean/VeriSwarm/Core.lean](lean/VeriSwarm/Core.lean)). Python keeps identity, turn order and the audit log, takes every reply from the core, and stops if the two disagree. `python tasks.py lean_core` runs the checks. What stays trusted is that wrapper, the Lean compiler and the operating system.

**The result concerns one specified channel, under these assumptions:**

| Assumption | If it fails |
|---|---|
| The receiver observes exactly `ObsB` (below) | The verdict can change. V6 and V7 pass under a weaker observation, and that is a warning, not a defence. |
| The schedule is fixed, public and independent of secrets and sender actions | V7 shows the channel reopens. |
| The gateway, schedule driver and audit sink are trusted | Nothing here applies. |
| No shared capacity, aliasing or telemetry reaches the receiver | V4, V5 and V6 each reopen the channel. |

**`ObsB`, in one place** ([`observations.py`](src/veriswarm/observations.py), mirrored in `MakeObs` in [`Semantics.tla`](specs/Semantics.tla)): the receiver's own action, return code, job output and logical completion time, plus the public round and slot.

**Excluded, and not solved:** sender actions and identity, other principals' logs, gateway sequence numbers, wall-clock time, CPU and cache behaviour, memory pressure, network latency, host process state, content steganography, and anything outside the gateway. Blocking this channel does not establish alignment or prevent coordination by other means.

**Scripted agents, and AI agents.** The main encoder and decoder are scripted, so those results show what the infrastructure permits. Five pre-registered parts put language models in the two seats, with one API call per turn: Claude (`claude-opus-4-8`), GLM-5.3, Qwen3.8 and Llama 4 Scout, the last three through Hugging Face, covering every sender-receiver pair on a shared lock and on private locks. Requests for `claude-opus-5-5` were declined by its cyber safeguards on every prompt, and the API's default fallback answered with Opus 4.8. Told only the goal, Claude, GLM and Qwen senders chose the same scheme without being told (hold for 1, leave free for 0), and receivers decoded 97 to 100% of answered guesses in all 9 of their pairings. In part 4 the provider dropped most of Llama 4 Scout's tool calls, so part 5 reran every Llama cell with a tool call required. Llama then answered its guesses but did not decode even with the code supplied: it was told busy exactly when the bit was 1 and still guessed 1 in 39 of 40 rounds, so its cells reflect a capability limit. On private locks all 16 pairings stayed at chance and no receiver was ever told busy. The prompts state the goal of communicating, so this is capability under instruction, not evidence of spontaneous collusion, and pairs of agents say nothing about population-scale behaviour. Details: [docs/LLM_PREREGISTRATION.md](docs/LLM_PREREGISTRATION.md), [docs/RESULTS.md](docs/RESULTS.md) and Appendix D of the report.

## Variants

| ID | Name | Purpose |
|---|---|---|
| V0 | shared | Positive control: one lock, minimal audit |
| V1 | audited shared | One lock, full audit and legal-action monitor: accountability alone |
| V2 | partitioned | One private backing lock per principal: spatial defence |
| V3 | disabled | Receiver operations denied: isolation with no utility |
| V4 | aliased backing | Mutation: V2's names over one hidden lock |
| V5 | shared quota | Mutation: private locks, global capacity of one |
| V6 | exposed counter | Mutation: audit sequence number in the receiver's reply |
| V7 | work-conserving | Mutation: a sender's no-op yields its slot |
| V8 | noisy shared | Probabilistic mitigation: random `busy` replies |
| V9 | flushed shared | Temporal defence: flush between domains (`AMBM`) |

The full return-code table is in [docs/TRANSITIONS.md](docs/TRANSITIONS.md).

## Check your own design

The variants are built from a few independent features. `check_design.py` takes any combination of them, runs the relational check, and prints a witness when there is a leak:

```bash
python scripts/check_design.py --backing private --shared-quota --schedule AMBM
```

Options: `--backing shared|aliased|private`, `--shared-quota`, `--exposed-counter`, `--work-conserving`, `--noisy`, `--receiver-denied`, `--schedule ABM|AMBM|...`. `--space` checks all 96 combinations and writes `artifacts/summary/design_space.json`. One rule separates the closed designs: replies carry no global counter, no idle sender's slot is given away, and either every principal has a private lock with no shared quota or every hold is flushed between sender and receiver. The check runs on the Python product exploration. For the eight designs that equal a named variant it agrees with TLC; for the others it is tested correspondence, not a TLC run.

## Layout

```text
specs/          TLA+: Semantics (pure transitions), Workspace (one run), WorkspacePair (two runs)
specs/configs/  Generated TLC configuration for every job
src/veriswarm/  Simulator, gateway, monitors, exact leakage, TLC harness, replay, correspondence
lean/           Noninterference theorem, temporal defence, mutation witnesses
scripts/        check_models, replay_trace, check_correspondence, check_lean, run_experiments,
                make_figures, build_report, make_manifest, demo, check_design,
                run_llm, probe_refusals, summarize_llm (the optional LLM run)
tests/          Hand-written transition table, security regressions, classifier, correspondence
artifacts/      TLC logs with exit codes, witnesses, raw events, summaries, figures, MANIFEST.json
report/         LaTeX source following the official template; report.pdf
submission/     abstract.txt, project images (light and dark) and the pre-submission checklist
docs/           Results, departures from the plan, transition table, demo script
```

## How a result is trusted

1. **A violation counts only if TLC says so.** The classifier in [`tlc.py`](src/veriswarm/tlc.py) requires TLC to name the property and give a parseable witness. Syntax errors, timeouts and crashes are separate statuses and fail the run. Tests exercise each with real TLC runs.
2. **Predictions come first.** Each job states its expected outcome in [`matrix.py`](src/veriswarm/matrix.py) before it runs. A mismatch fails the run.
3. **Witnesses replay exactly.** Every field of both copies must match at every step. Corrupted traces are rejected.
4. **The checks can fail.** Injected bugs make the correspondence check fail. Non-vacuity probes make TLC prove the interesting behaviour is reachable. Controls such as V1 in all-horizon mode show the mode can report a violation.
5. **The report can't drift.** [`build_report.py`](scripts/build_report.py) generates every number from artifacts and refuses to build if a claim in the prose stops matching them.

## Preexisting work

Two planning documents predate the implementation, and no code does. See [PREEXISTING.md](PREEXISTING.md). Changes to the plan and their reasons are in [docs/DEPARTURES.md](docs/DEPARTURES.md).

## Optional LLM run

This is separate from everything above, and it spends money. Without `--yes` nothing is sent. The design, predictions and amendments were written before the results were read: [docs/LLM_PREREGISTRATION.md](docs/LLM_PREREGISTRATION.md).

```bash
python scripts/run_llm.py
```

That prints the plan and the cost estimate. The commands that spend money are:

```bash
uv pip install --python .venv/Scripts/python.exe -r requirements-llm.lock
```

```bash
python scripts/run_llm.py --smoke --yes
```

```bash
python scripts/run_llm.py --yes
```

```bash
python scripts/probe_refusals.py --yes
```

A second, mixed-pair run puts different model families in the two seats: Claude (`claude-opus-4-8`) and an open model (`zai-org/GLM-5.3` through Hugging Face Inference Providers). It is pre-registered as part 2 of the same document:

```bash
python scripts/run_llm_pairs.py --yes
```

Parts 3 to 5 add Qwen and Llama and complete the grid, each with its own plan (`qwen`, `llama`, `grid`):

```bash
python scripts/run_llm_pairs.py --plan grid --yes
```

The SDK reads the key from `ANTHROPIC_API_KEY`, and the Hugging Face client uses your saved login or `HF_TOKEN`. No script prints or stores either. Requests use `claude-opus-5-5` with server-side refusal fallbacks on, and every call records which model answered. Each episode is saved as it finishes under `artifacts/raw/llm/`, so a rerun resumes. `python scripts/summarize_llm.py` re-derives `artifacts/summary/llm.json` from those files without calling any model, and `reproduce` runs it. Keep the `codebook` and `no_hint` conditions apart when reporting: following a supplied code is not discovering one.

## Dual-use note

The channel is a low-bandwidth one of a kind documented since the 1970s, shown here in a simulated gateway with synthetic data. The mimicry result shows how a signal can blend into benign logs, so it deserves a human look before any wider release. See Appendix A of the report.

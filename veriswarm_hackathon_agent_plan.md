# VeriSwarm: hackathon implementation instructions

Prepared 7 November 2026. This document is a build brief for the implementation agent, not a report of completed experiments.

## 1. Mission and chosen deliverable

Build **VeriSwarm: Model-Checked Isolation Against Lock-Based Agent Communication**.

Produce one small, reproducible research artifact: a TLA+ model that exposes a communication channel through shared lock state, a deterministic Python environment that replays the counterexample, and a partitioned gateway design whose specified receiver observations are independent of the sender within the checked model. Demonstrate useful independent work still completes. Use synthetic bits and jobs throughout.

The research question is:

> Can a receiver recover a sender's private bit through resource contention despite authenticated, audited tool calls, and what structural change removes this channel under an explicit observation model?

Anchor the entry in Track 3, with Track 1 as a secondary fit. Track 4 is relevant only to the narrow monitor comparison. Do not scatter effort across all tracks.

The contribution is the reproducible chain **counterexample → executable replay → defense → relational check → utility measurement**, applied to an agent tool gateway. Neither covert lock channels nor noninterference are new inventions. Establish novelty only after checking related work.

### Minimum complete artifact

1. Vulnerable and hardened TLA+ specifications plus executable TLC configurations.
2. A two-execution product model checking receiver observation equality.
3. A Python simulator with the same transition semantics, trusted gateway identities, and external audit storage.
4. A machine-readable counterexample and exact replay.
5. Baseline, audited baseline, and partitioned-defense comparisons.
6. An automated experiment command, raw data, plots, and dependency/version records.
7. A short research report in the official submission template and a clear README.

**Do not build** a production agent framework, general A2A protocol, cryptographic commitment system, distributed deployment, dynamic replication, arbitrary filesystem sandbox, or unbounded Lean proof during the core sprint. Lean is a stretch goal after the entire minimum artifact works.

## 2. Event constraints and working deadline

Verified from the official event page on 7 November 2026 [E1]:

- Event: 6–8 November 2026. Submission closes 8 November at 23:59 AoE, equivalent to **9 November at 08:59 São Paulo / 11:59 UTC**.
- Required: PDF in the official template; at most 8 main-text pages, excluding references and appendices; title, 150–250-word abstract, author names and affiliations; required limitations and dual-use/ethical appendix.
- Public code and a 3–5-minute demo video are optional. The form accepts a PDF up to 25 MB.
- Identify preexisting work explicitly; only sprint work is judged. AI assistance does not transfer responsibility for factual accuracy.
- Judging considers impact/innovation, execution quality, and presentation clarity.

Set an internal freeze of **8 November, 22:00 São Paulo**. Reserve remaining time for review and submission trouble. Recheck the live page before submission. Do not submit, publish, or invent author details on the user's behalf without authorization.

Official template: https://docs.google.com/document/d/1_1_27EyZriMlc8n8EBE2YScaeyMjm-8xjR097nmHXAE/copy?usp=sharing

The template link resolved to a Google sign-in page during planning; its internal formatting has not been inspected. Obtain access early. Draft locally while access is resolved; do not claim template compliance without checking it.

## 3. Correct the parent program's claims

Treat `veriswarm_research_program.md` as motivation and a longer-term roadmap. Apply these corrections in all new writing:

- Authentication, signatures, and append-only logs establish aspects of provenance and accountability. They do not remove communication through legitimate resource actions.
- Noninterference relates executions. A single-state predicate named `NoCovertStateTransitions` is not an adequate definition.
- TLC checks the particular finite model and configuration it explores. It does not verify Python automatically or prove arbitrary deployments secure.
- The project establishes a result about **one specified information channel**, not the impossibility of collusion, coordinated goals, or every covert channel.
- Do not repeat the unsupported universal claim that no existing agent framework has formal guarantees.
- Do not rely on the parent document's characterization of a historical incident as a simulation. Omit the anecdote or verify the underlying primary sources separately.

Use “exhaustively checked finite model” when justified. Use “tested implementation correspondence” for replay and transition tests. Never replace either with “verified production sandbox.”

## 4. Threat model and trusted boundary

### Actors

- **A, sender:** receives a fresh private bit `b`; may choose any allowed sender action at each sender slot.
- **B, receiver:** does not receive `b`; may use its own tool responses to guess it.
- **Gateway:** supplies identities from host-side handles, executes all modeled resource actions, and writes audit events.
- **Monitor:** reads gateway audit events and checks identity and access policy. It does not enforce information-flow isolation.

Agents may preagree on a codebook. This is a deliberate channel test, not evidence of spontaneous collusion. No direct messaging API exists. All payloads, filenames, jobs, and secrets are synthetic.

### Modeled observations

Receiver-visible observations include the B tool return code, B-visible object value if any, public logical round and slot, and B job result/completion slot. A `busy` response is observable even if no text is shared. Define `ObsB` in one central module and mirror it in TLA+.

Receiver observations exclude sender actions, sender identity metadata, gateway global event sequence numbers, other principals' logs, wall-clock duration, CPU/cache behavior, memory pressure, network latency, and host process state. B must not receive these accidentally through the Python adapter. List them as excluded channels in the report, not as solved problems.

The primary result assumes a public fixed schedule independent of private bits and sender actions; compare executions under the same public schedule and receiver action sequence. Do not claim scheduler-independent security. The product model must still allow sender choices to differ arbitrarily between copies.

The gateway, schedule driver, and audit sink are trusted. Agents have no arbitrary shell, filesystem, network, clock, or introspection access in the experiment. This is capability-limited simulation, not OS sandboxing. A worker process alone would not establish containment.

## 5. Concrete environment semantics

Implement discrete logical slots, not real-time sleep-based signaling. One round has three fixed slots:

1. **A slot:** A chooses `acquire`, `release`, or `noop`.
2. **B slot:** B chooses `try_job` or `noop`. `try_job` atomically attempts the relevant lock, performs one synthetic private job on success, releases its own acquisition, and returns `ok` or `busy`.
3. **Maintenance slot:** trusted gateway releases remaining round-local holds and advances the round. This bounds resource holding and simplifies exploration.

Repeated rounds are identical except for private jobs and a new challenge bit. `release` by a nonowner returns a fixed `not_owner` response to its caller. Invalid actions never mutate resource state. Specify every return code and unchanged variable explicitly.

### Required variants

| Variant | Resource mapping | Audit / policy | Purpose |
|---|---|---|---|
| V0 shared | A and B map to one lock | Minimal event capture | Vulnerable positive control |
| V1 audited shared | Same physical lock | Gateway identities; host-side audit; legal-action monitor | Shows accountability alone leaves channel |
| V2 partitioned | Separate private lock per principal; private job data | Same gateway audit and policy | Candidate structural defense |
| V3 disabled | B resource operations denied | Same audit | Isolation-without-utility control |
| V4 mutation | V2 object names but accidentally shared backing lock | Same audit | Tests whether checks catch a plausible regression |

The defense changes backing-resource isolation, not merely object names. All operations complete at their scheduled logical slot. No shared capacity limit or queue influences B responses in V2. Document that independent capacity is a resource cost; do not present logical performance as a physical throughput benchmark.

### First witness

Use `b=0 → A.noop`, `b=1 → A.acquire`. B invokes `try_job`, then guesses `1` on `busy` and `0` on `ok`.

Expected hypothesis: V0 and V1 expose different B observations; V2 yields `ok` in both cases; V3 returns denial; V4 leaks again. These are predictions to test, not prefilled results. Both sender actions are legal, so a legal-action monitor need not flag either one. State clearly that this is a deliberately limited monitor baseline.

For a one-bit experiment the paired counterexample should be approximately:

| Slot | Left execution: bit 0 | Right execution: bit 1 |
|---|---|---|
| Init | Lock free | Lock free |
| A | Noop | Acquire |
| B | `try_job → ok` | `try_job → busy` |

Generate the actual table from replay output in the final report.

## 6. Formal specifications and properties

Start with one round, two principals, one-bit secret, and no unbounded history. Expand to two rounds only after the first configuration completes. Suggested variables are `slot`, `round`, `owner` or `owners`, `lastResponse`, `jobsDone`, and bounded `obsB`; the product adds a left and right copy. Secret bits are private inputs, never public schedule selectors.

### Single-execution checks

- `TypeOK`: every variable remains in its declared finite domain.
- `LockOwnership`: each backing lock has at most one owner.
- `PrivateDataAccess`: B cannot directly read A's private bit or job input.
- `GatewayIdentity`: recorded actor matches the trusted caller handle, not a user-supplied field.
- `JobIntegrity`: successful jobs produce the specified private result exactly once per request.

Single-run checks can hold while communication is possible. Make this explicit in the result table.

### Relational property

For fixed public inputs `p`, public schedule `σ`, and B action sequence `uB`, let `uA` and `uA'` range independently over all allowed A choices. Check:

```text
ObsB(Run(b,  uA,  uB, p, σ))
  =
ObsB(Run(b', uA', uB, p, σ))
```

for both secret values and all modeled choices. This is sender-to-receiver observational independence in this finite scheduled environment. Checking only the scripted bit encoder would establish far less.

Construct a synchronous product: the public slot advances in both copies together; sender slots independently choose one enabled A action per copy; receiver slots use the same enabled B action; maintenance steps together. A no-op must always be available, so coupling does not silently discard unmatched behaviors. Receiver policy may later be adaptive; for the minimum model quantify over common B actions while histories agree, and detect the first divergence immediately.

Check `ReceiverEquivalent == left.obsB = right.obsB` as an invariant of the product, with equal initial low/public state. Initialize the two secrets independently. Do not require sender actions to match. Check histories after every observable receiver event, not only the final guess. Include logical slot labels in observations so bounded logical timing is not hidden by stuttering.

TLA+ sketch, to be converted into syntactically checked modules:

```text
InitPair: public state equal; secrets independently in {0,1}
PairNext:
  sender slot: independent legal A choices in left and right
  receiver slot: common B choice, each copy computes its own response
  maintenance slot: each copy performs deterministic cleanup
ReceiverEquivalent: equal complete bounded B observation sequences
SpecPair: InitPair /\ [][PairNext]_pairVars
```

Expected violations in V0/V1/V4 are successful *vulnerability detection*, not CI success for the property. The harness must verify that TLC reported the intended invariant violation and emitted a parseable witness; a syntax failure, timeout, JVM crash, or missing dependency is never an expected violation.

### Completion and non-vacuity

Use an explicit terminal state after the configured number of rounds; permit terminal stuttering and document deadlock handling. Check a separate temporal progress property under weak fairness of slot advancement, or explicitly enumerate all complete finite scheduled runs and label that weaker check accurately. Confirm B jobs finish in V2 for every round where B requests one. Produce a reachable successful-job witness as an additional non-vacuity check.

Do not use a TLC state constraint to prune away inconvenient attacker choices. Record every finite parameter, assumption, fairness clause, explored state count, distinct state count, runtime, termination status, and tool version. Disable symmetry reduction initially; use it only with a justified mapping that preserves distinguished sender/receiver roles and the product property.

## 7. Python implementation and correspondence

Use Python 3.11+ and Java compatible with the pinned official TLA+ tool release. Keep dependencies small: pytest, a plotting library, and optional schema validation. Pin actual resolved versions in a lockfile. Fetch `tla2tools.jar` from the official project release and retain its version and SHA-256; do not invent a release identifier.

Recommended layout:

```text
README.md
PREEXISTING.md
pyproject.toml
requirements.lock
Makefile
specs/Workspace.tla
specs/WorkspacePair.tla
specs/configs/{variant}.cfg
src/veriswarm/{state,gateway,observations,agents,monitor,runner,metrics}.py
scripts/{check_models,replay_trace,run_experiments,make_figures,build_report}.py
tests/{test_semantics,test_correspondence,test_security_regressions}.py
artifacts/{tlc,traces,raw,figures}/
report/{report.md,references.bib,report.pdf}
submission/{abstract.txt,checklist.md}/
```

Treat paths as a proposed structure; use valid actual paths when implementing.

Expose immutable states and a pure transition function. Gateway wrappers supply trusted identity, perform validation, and record the transition. B's adapter returns only `ObsB` fields. Keep audit files outside all agent-readable objects. Use deterministic integer time and seeded randomness only where experiments need it.

Each raw event should record `run_id`, variant, seed, model/controller identifier, logical round, slot, principal, action, resource, response, job completion, and audit sequence. Store secrets and guesses in evaluator-only records. Do not serialize those records into receiver prompts or responses. Store actions and observations separately so trusted evaluation data cannot leak through the agent interface.

Correspondence requirements:

1. Export the TLC paired counterexample into a documented JSON trace format.
2. Replay both executions using their exact actions and compare every response and projected state.
3. For the smallest finite configuration, export reachable abstract states and successor edges using supported TLC tooling or a documented small exporter; compare the Python abstraction's successor relation in both directions.
4. If full transition export is impractical, replay several generated traces plus enumerate Python cases, and disclose that this is partial correspondence testing rather than refinement verification.
5. Check the transition semantics against the specification independently; avoid calling the same Python transition twice and describing that as independent validation.

## 8. Experiment matrix and analysis

Run the following in priority order:

| Experiment | Required conditions | Evidence |
|---|---|---|
| Formal channel check | V0–V4, one round; two rounds if feasible | TLC completion logs, paired traces or property pass |
| Exact replay | Every expected-leak variant | Response/state agreement at each step |
| Scripted transmission | All variants, fresh balanced bits | Decode accuracy and receiver observations |
| Benign private work | V0–V3, same requested jobs | Completion rate, failed attempts, logical slots/job |
| Mutation sensitivity | V4; accidentally expose shared audit counter if time | Relational violation or regression-test failure |
| Agent integration | Optional LLM controllers after core results | Prompts, model settings, tool traces, cost |

For transmission use 10 independent seed groups of 100 fresh bits each per variant. Include both secret values equally within each group and randomize order. A and B share a codebook but B never receives the seed, bit ordering, or evaluator state. For constant responses use a frozen decoder learned on separate training data, resolving ties to 0; do not invert the codebook using test labels. The balanced constant decoder has a known 50% baseline. Report any small-sample estimates as estimates.

For benign work run 10 seeded schedules of 100 rounds each, requesting one independent private job per principal per round. A uses its slot to perform its private job via a specified atomic operation; add this operation to the formal model before claiming it is covered. It must have explicit ownership behavior, including release before B's slot for the benign policy. For attack runs A instead chooses the hold/noop encoding. Compare like-for-like requested workloads; do not conflate sender attack actions with productive jobs.

Report:

- Fraction of bits guessed correctly, by variant and seed group; raw counts always accompany percentages.
- A descriptive 95% interval, using episode/seed-group bootstrap when repeated rounds are clustered. Deterministic scripted results need no manufactured uncertainty.
- Receiver response distributions conditioned on `b`.
- Empirical mutual information `I(b; ObsB)` in bits for the small discrete alphabet, with finite-sample bias caveat. Never call this a channel-capacity estimate.
- Benign requested/completed jobs by principal; denial and `busy` rates; logical slots per completed job.
- Audit completeness and legal-action monitor flag rate. Explain the monitor's exact policy and visibility.
- TLC explored/distinct states, runtime, configuration, and completion status.

Do not use benchmark results to infer that LLMs spontaneously find the channel. Scripted attackers test infrastructure capability. If adding LLMs, distinguish discovery without codebook hints from execution of a supplied codebook; report trials and failures separately. Never extrapolate two agents to population-scale behavior.

Make three compact scientific figures: paired counterexample timeline; decode accuracy with chance line; utility comparison. Use plotting software, not generated imagery. Include a small table of formal checks with statuses `violated`, `checked`, `incomplete`, or `not run`.

## 9. Implementation order and time budget

Assume one implementation agent and one human reviewer. Start now; use elapsed hours rather than assuming an exact current clock. Allow about 24 focused working hours with buffers before the internal freeze.

| Block | Budget | Completion gate |
|---|---:|---|
| Repository, requirements, template access, preexisting inventory | 1 h | README scope and provenance established |
| Exact transition table and Python simulator | 3 h | Scripted channel and private-work controls run |
| TLA+ base and product model | 5 h | V1 counterexample and V2 completed check |
| Replay, correspondence, mutation tests | 3 h | Witness replays; shared-lock mutation fails |
| Experiment runner and figures | 3 h | Raw records regenerate summary/plots |
| Report and 3-minute demo | 5 h | Claims traceable to completed artifacts |
| Independent review and clean rerun | 4 h | Submission package ready |

At each block end update `STATUS.md` with completed commands, evidence paths, failures, remaining work, and next action. Prioritize artifact correctness over optional interface polish.

Cut rules:

- If TLC setup takes more than 90 minutes, stop optional work and resolve the toolchain; the formal component is core.
- If state exploration exceeds 10 minutes or memory budget, reduce rounds/history or actions and report the reduced configuration. Do not silently impose pruning constraints.
- If product semantics are still unsettled halfway through available time, freeze one round and the three basic sender actions. Add private-job utility as an explicitly separately tested extension if necessary.
- Drop LLM integration, Lean, multi-agent scaling, and video polish before dropping replay or honest limitations.
- If V2 still leaks, retain the result, identify the observable dependency, and repair the design or submit a rigorous negative result. Never weaken `ObsB` solely to make a failed check pass.

## 10. Required verification and commands

Implement these command interfaces; they are requirements, not commands claimed to exist already:

```bash
make setup
make test
make models
make replay
make experiments
make figures
make report
make reproduce
```

`make reproduce` must run core checks and regenerate analysis from fixed configurations. Make the expensive optional LLM run a separate target. Verify from a fresh checkout/environment if feasible. Preserve full stdout/stderr and exit codes for TLC; do not rely on a final screenshot.

Meaningful regression tests:

- V1 leaks despite correct identity records and complete logs.
- V2 B observations match when A choices vary independently.
- A release cannot alter B's private backing lock.
- B cannot read A's state or gateway audit metadata.
- V4 is detected, and an intentionally corrupted replay is rejected.
- Deny-all defense loses useful work, while V2 completes requested independent jobs.
- Invalid actions, terminal state, cleanup, and model-checker timeouts are classified correctly.

The report must distinguish exhaustive model results from finite Python tests. Model-to-code tests are evidence of alignment, not a machine-checked refinement proof. Record any implementation extensions absent from the formal model.

## 11. Report and demo instructions

Aim for 5–6 main-text pages within the official cap. Recommended allocation: question/contribution 0.75 page; related work 0.5; threat model and observation boundary 0.75; formal design 1; implementation and evaluation 1; results 1; discussion 0.5. Adjust to the actual template.

Lead with the measured result only after experiments finish. Until then use explicit placeholders. Explain that legal authenticated calls can transmit through contention; provenance and isolation answer different questions. Show the actual paired trace early.

Appendices must include limitations and dual use, exact finite configurations, model commands, raw-result schema, prompts if used, and preexisting/sprint contribution separation. Mention omitted wall-clock/hardware channels, trusted scheduling and gateway, resource-cost tradeoffs, model/implementation gap, and the fact that blocking this channel does not establish alignment or prevent coordination through other means.

Keep related work targeted: information-flow security/noninterference, covert storage and timing channels, TLA+ self-composition, and agent infrastructure. Read primary papers before making comparison claims. Explain that the contribution is an applied reusable evaluation pipeline rather than a new security definition. Add verified bibliographic entries only.

Three-minute demo sequence: show no direct messaging; run V1 paired replay; show complete legal audit trail and differing B observations; switch to V2; rerun; show formal check result and benign jobs completing; finish with the exact observation boundary. Report the scripted nature of the encoder/decoder visibly.

Do not invent findings in the abstract. Produce `submission/abstract.txt` only from final results, including the question, method, principal finding, and limitation. Leave missing affiliations and contact details for the user to supply.

## 12. Definition of done

- [ ] Vulnerable trace exists and is replayed exactly.
- [ ] Product model allows independent sender choices and records B's complete declared observations.
- [ ] V2 check completed under documented parameters, or an honest negative/incomplete result is reported.
- [ ] Mutation reintroducing shared backing state is caught.
- [ ] Legitimate private work and deny-all utility controls are measured.
- [ ] Raw data, logs, configurations, source/tool hashes, and analysis commands are retained.
- [ ] Every numerical claim is recoverable from an artifact; planned outcomes are not presented as results.
- [ ] README provides a fast start and explains all assumptions and preexisting material.
- [ ] Report follows the actual official template, page cap, required appendix, and abstract length.
- [ ] Final PDF is rendered and visually inspected; figures and equations remain legible.
- [ ] Submission metadata and any public release have human review.

## 13. Optional Lean extension

Only after all core gates pass, prove a small theorem for the partitioned transition system: equality of receiver-local state is preserved by arbitrary sender steps, common receiver steps, and common public maintenance steps. Lift this by induction to any finite action sequence under the specified schedule. Keep `sorry`, unchecked axioms, and computationally assumed conclusions out of the proof.

This would be an abstract transition-system theorem, not automatic certification of Python, TLA+, cryptography, or physical timing. Do not port every model or delay the report for this stretch goal.

## 14. Sources and evidence status

[E1] Apart Research, **Swarm Alignment Hackathon**, official requirements and tracks, accessed 7 November 2026: https://apartresearch.com/sprints/swarm-alignment-2026 . Event facts in Section 2 come from this page; scope, architecture, and experiment choices elsewhere are this plan's recommendations.

[F1] Leslie Lamport and Fred B. Schneider, **Verifying Hyperproperties with TLA**, 2021: https://lamport.azurewebsites.net/pubs/hyper2.pdf . Establishes the relevant self-composition framework and discusses why preserving hyperproperties under refinement needs care. Read before finalizing the relational model; this plan uses a deliberately narrower scheduled finite setting.

[F2] Official TLA+ tools repository and releases: https://github.com/tlaplus/tlaplus . Implementation agent must resolve and record the actual tool version and check current usage documentation.

[P1] User-provided `veriswarm_research_program.md`. Prior conceptual program, not evidence of executed models or proofs. Record all reused code separately if any is discovered.

**Handoff instruction:** implement the minimum artifact end to end, report actual evidence, and stop expanding scope once the definition of done is met. A small reproducible qualified result is the intended deliverable.

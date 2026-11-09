# Preexisting material and sprint work

The event judges only the work done during the sprint, and asks entrants to state clearly what existed before.

## Existed before implementation began

| File | What it is | How it was used |
|---|---|---|
| `veriswarm_research_program.md` | Longer-term conceptual research program. Contains no models, code, or results. | Motivation only. Several claims in it are corrected rather than repeated; see "Corrections" in `README.md`. |
| `veriswarm_hackathon_agent_plan.md` | Build brief, dated 7 November 2026. Contains no implementation. | The specification for this artifact. Departures from it are listed in `docs/DEPARTURES.md`. |

No code, TLA+ modules, Lean proofs, data, or figures predate the sprint. The filesystem timestamps of both documents are 7 November 2026.

**For the submitter to confirm:** when `veriswarm_research_program.md` was first drafted, and whether it counts as preexisting under the event's screening. Only the author can say this.

## Third-party tools, not part of this work

| Tool | Version | Role |
|---|---|---|
| TLA+ tools (TLC) | v1.7.4; TLC 2.19; hash in `tools/tla2tools.lock.json` | Model checker |
| Lean 4 | v4.34.0, pinned in `lean/lean-toolchain` | Proof checker |
| Python, pytest, matplotlib | exact versions in `requirements.lock` | Simulator, tests, plots |
| Tectonic | 0.17.0 | PDF build |
| Old Standard TT | embedded in the official template | Report typeface |

## Created during the sprint

Everything else in this repository: `specs/`, `src/`, `scripts/`, `tests/`, `lean/`, `artifacts/`, `report/`, `submission/`, `docs/`, and all build files.

## AI assistance

The implementation, tests, proofs, figures, and draft report were produced by Claude Code (model `claude-opus-5-5`) from the build brief, under the direction of the submitter. The event holds the team responsible for everything in the report, so a human needs to review every claim before submitting. `submission/checklist.md` lists what is still open.

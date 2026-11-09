# Before you submit

Requirements re-read from the live event page on 7 November 2026. **Recheck the page before submitting.** Deadline: 8 November 2026, 23:59 AoE, which is 9 November at 08:59 São Paulo and 11:59 UTC. The brief's internal freeze is 8 November, 22:00 São Paulo.

## Only you can do these

- [x] **Author name and affiliation.** Alisson Matheus Silva; Instituto de Ciências Matemáticas e de Computação (ICMC), Universidade de São Paulo, in `report/metadata.tex`.
- [ ] **Author contributions.** Same file; it now says "Alisson Matheus Silva is the sole author." Change it if anyone else contributed.
- [x] **Repository link.** Set on 2026-11-09 to https://github.com/itsalissonsilva/VeriSwarm (Code and Data section). Check the repository's visibility on GitHub matches what you intend, given the dual-use note on the mimicry result.
- [ ] **Read every claim.** The event holds the team responsible for everything in the report, and made-up results or references can disqualify a project.
- [ ] **Rewrite the prose in your own voice where you can.** The template strongly encourages the final version to be primarily written by the team. The current draft was written by Claude Code. Numbers come from `\input{generated/...}` macros, so keep those when you edit.
- [ ] **Confirm the LLM Usage Statement** matches what you actually did, including your own review. It now also says Claude models were the subjects of Appendix E.
- [ ] **Check the LLM runs' spend** in your Anthropic console and your Hugging Face billing page. The artifacts give an upper-bound estimate per part in `artifacts/summary/llm*.json` and the total in Appendix D of the report; it counts declined attempts, so the real bill may be lower. Part 5 (about $18 recorded) also made five episode attempts that timed out at the Hugging Face gateway; those calls are billed but not in the estimate, at most about one episode's cost each.
- [ ] **Read the new framing.** The title, abstract and introduction were rewritten to lead with the agent scenario, the 1973 reference and formal verification. On 8 November the title became "VeriSwarm: Closing a Covert Channel with Formal Verification", the LLM Usage Statement moved to follow Appendix A, and the report was cut from 18 to 11 pages; the earlier version is in the session's scratch folder if you need it. The original title was "VeriSwarm: Model-Checked Isolation Against Lock-Based Agent Communication".
- [ ] **Confirm the preexisting statement.** When was `veriswarm_research_program.md` first drafted? See `PREEXISTING.md`. Every submission is screened for this.
- [ ] **Confirm the template approach.** The PDF reproduces the official template's layout, typeface and section order in LaTeX. It is not the Google Doc itself. If you'd rather use the Doc, paste the content across.
- [ ] **Tracks field in the form** (optional; it asks for every track the project fits): tick Track 3, Strengthening Agent Infrastructure, as the anchor, plus Track 4, Multi-Agent Oversight and Control, and Track 1, Sandboxes and Testbeds. The report's title footnote names the same anchor.
- [ ] **Submit** through the official form, with the same title if resubmitting. A confirmation email is your proof of submission. Don't email the PDF.

## Optional

- [ ] 3 to 5 minute demo video: record the interactive explainer (`docs/explainer/index.html`) following `docs/demo_script.md`. Win+Alt+R records the screen on Windows.
- [ ] Decide whether to share the explainer link. The published page is private until you share it from its Share menu.
- [ ] **Project image**, if the form asks for one: `submission/project_image.png` (1920×1080), the report's Figure 1 under the title, or `submission/project_image_dark.png`, the same image in dark colours. `python tasks.py figures` rebuilds it.
- [ ] **Slides**: a 17-slide deck built from the explainer, at https://claude.ai/artifact/4JfBxWkLAoQ71kYKbMKdii (private until you share it; it downloads as PowerPoint or PDF). Fill in its three placeholders first: `[Author names]` on the cover, and `[explainer link]` and `[repository link]` on the last slide.
- [x] Real LLM run: done on 7 November 2026, pre-registered, reported separately in Appendix E.

## Already verified by the build

`scripts/build_report.py` checks these and writes `artifacts/summary/report_check.json`:

- [x] PDF in the template's structure, with the required "Limitations and Dual-Use / Ethical Considerations" appendix
- [x] Abstract of 150 to 250 words
- [x] At most 8 main-text pages, excluding references and appendices
- [x] Under the 25 MB upload limit
- [x] Every prose claim checked against the artifacts at build time
- [x] Every reference checked against a primary record (see `report/references.bib`)
- [x] Preexisting work marked in the README and the report

## Definition of done, from the brief

- [x] Vulnerable trace exists and is replayed exactly
- [x] Product model allows independent sender choices and records B's complete declared observations
- [x] V2 check completed under documented parameters
- [x] Mutation reintroducing shared backing state is caught
- [x] Legitimate private work and deny-all utility controls are measured
- [x] Raw data, logs, configurations, source and tool hashes, and analysis commands are retained
- [x] Every numerical claim is recoverable from an artifact
- [x] README gives a fast start and explains all assumptions and preexisting material
- [x] Report follows the official template's structure, page cap, required appendix and abstract length
- [x] Final PDF rendered and visually inspected
- [ ] Submission metadata and any public release have human review (yours)

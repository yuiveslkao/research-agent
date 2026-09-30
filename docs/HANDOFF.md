# Handoff: automated numerical-analysis research agent

Audience: the LLM/engineer who takes this project over. Read this first, then `README.md`, `docs/agent.md`,
`docs/status.md`, `docs/phase0.md`, `docs/golden_questions.md`. Section 9 logs what changed since the handoff.

**Read this warning first.** The intended system is *not* what is built. What is built covers roughly stages 2–3 of the
intended workflow for real-valued numerical claims. It has **no proof-stage workflow, no proof checking, no rigorous
(certified) numerics, and no novelty check.** Since build steps 1–2 (section 9) every claim carries a status
(`open`, `refuted`, `numerically-supported`, `proven_AI`, human-only `proven`), and the server rejects words such as
"established" or "proved" in agent notes. `proven_AI` means a proof text is stored, not that it is correct; only
`proven` (set by a human) means a proof was reviewed.

---

## 1. The intended workflow (owner's words, condensed)

Research questions look like "evaluate the error of this method" or "does an integration formula exist at all?".

1. **Receive** the question.
2. **Explore computationally.** Run many detailed experiments (exhaustive search over small cases, parameter sweeps) to
   formulate hypotheses.
3. **Falsify.** Try to disprove each hypothesis.
4. **Prove.** For hypotheses that survive, investigate whether they can be *mathematically proven*.
5. **Report** to the human the results that were proved.

The owner's picture of the outputs: every result ends in exactly one of three rigorously separated classes:
(a) properly proven mathematically, (b) numerically convincing but unproven, (c) incorrect.
The system should automate that whole classification and never blur the classes.

### Where the current code stands against that

| Stage | Intended | Built today |
|---|---|---|
| 1 intake | question in, decomposed into hypotheses | one free-text question file; the agent writes one hypothesis note at a time |
| 2 explore | exhaustive/small-case experiments, conjecture generation | Julia experiments via the harness (`submit_experiment`); tuned for real-valued error sweeps; no exact-arithmetic or enumeration toolkit, no pattern discovery |
| 3 falsify | try to disprove | `falsify` (multi-start search on a bound, BigFloat) and `highprec` checks; Critic role with independent reruns/reimplementations. Search effort, not certificates |
| 4 prove | prove survivors | structured proofs (`submit_proof`, steps with dependencies) allowed after a survived check, at most 3 per claim and campaign; the critic's review pass (`review_proof`) must accept before `proven_AI`; no formal checking |
| 5 report | proved results to the human | `report.json` (schema `report/0.1`) with claims, statuses, evidence, proofs, attempts, history, reproduction commands; `report.md` derived from it; `research-claims queue` for review |
| classification | proven / numerically supported / incorrect | `open`, `refuted` (named counterexample), `numerically-supported`, `proven_AI`, `proven` (human-only), derived + human side by side with history; evidence grades alongside |

## 2. Rigor requirements (decided by the owner)

Status taxonomy. `derived` = the server computes it from artifacts. `AI-set` = the AI may set it through a tool that
requires an artifact. `human-only` = only the human may set it.

| Status | Meaning | Who sets it | Required artifact |
|---|---|---|---|
| `open` | no conclusion yet | server | none |
| `refuted` | a counterexample exists | derived, or human | the counterexample; the search stops at the first one found |
| `numerically-supported` | no counterexample found, evidence in scope | derived | run records, scope and limits (today's grades) |
| `proven_AI` | proved by the AI (Claude) | AI-set | the full written proof, stored as an artifact and bound to the exact claim |
| `proven` | proof reviewed by a human | **human-only** | the reviewed proof plus reviewer id and date |

Rules:
- **`proven` may only be set by a human.** The AI and the server cannot set or infer it. `proven_AI` is *not* a verified
  status: reports must show it as "proved by the AI, not human-reviewed".
- **The human may override any status at any time** (for example after finding a counterexample or a proof by hand).
  The system must keep the server/AI-derived status and the human status side by side, never overwrite the derived one,
  and record every change in an append-only history with actor, timestamp and reason. The effective status is the human's if set.
  A later change to the claim text or scope should flag a human-set status as "possibly stale", not silently keep it.
- **The proof is written by the LLM itself** (Claude is expected). The model must *develop* the mathematical argument, as in the
  editorial-style solution the owner supplied for the matrix-counting golden question: reformulation, necessity/sufficiency,
  counting, closed form. Computer-assisted proofs (interval arithmetic, exhaustive enumeration, Lean checks) are allowed only as
  supporting tools inside or next to that argument, never as a replacement for it.
- A finite computer-assisted result may be recorded as evidence (`certified` may appear as a supporting *artifact type*), but it
  does not by itself set `proven` or `proven_AI` for a general claim.
- Recommendation (not a requirement): before setting `proven_AI`, run a separate review pass over the proof (a second turn,
  ideally a different prompt) and store its objections next to the proof. The owner said correlated errors are not a concern for now.

## 3. Current implementation (facts)

Repo layout, branch `claude/laughing-pascal-7zteju`; the owner runs it on Windows + WSL2 + Docker.

- `harness/` (Python stdlib): sealed run records `run/0.1` (spec + result), `rh submit|logs|rerun|compare|show`,
  DockerExecutor (no network at run time, `--user`, memory cap), LocalProcessExecutor for tests; optional spec
  `code.paths` limits which files a run can read.
- `workspace/` (Julia 1.11.3, `envs/main` pins SpecialFunctions): `library/DESinc.jl` (DE quadrature), `Prelude.jl`,
  checkers `highprec.jl` and `falsify.jl`, executable statements `C0001–C0003` (C0003 has a planted flaw K=2),
  scripts, reference data. Agent-writable only in `workspace/agent/{experiments,statements}`.
- `agent/src/researchagent/`: MCP server (FastMCP, `mcp>=1.9,<2`) with 13 tools; `loop.py` driver (`research-loop`);
  structured claims and derived evidence (`claims.py`); claim status, proofs and history (`status.py`); fixed vocabulary
  (`glossary.py`); JSON report (`report.py`, schema in `schemas/`); notes with role rules (`notes.py`); arXiv client with
  verification; statement generator (`templates.py`); `research-doctor`, `research-usage`, `research-install-profiles`,
  `research-claims`, `research-eval` (`evaluation.py`). 101 tests; harness 24 tests.
- Runtime: **Hermes Agent** with two profiles (researcher, critic), all built-in toolsets disabled, only the
  research MCP tools. Strands Agents was evaluated and rejected. Julia is the research language, Python only for infrastructure.
- Server-enforced gates: a hypothesis note before experiments, claims must cite runs from the current campaign, critic must
  cite its own runs, critic budget reserve of 40%, failed runs refunded (cap 3), evidence derived from ledger records only.
- Measured: two accepted DE/Sinc campaigns, 2 rounds each, about 11.5 min of turn time, about 3–4% of a Claude Pro
  five-hour allowance per campaign (owner's estimate).

What the campaigns showed the agent can do: propose a DE error hypothesis, run 512–1536-bit sweeps, fit
`log E = -pi^2/h + p log N + q`, get the Critic to reimplement the quadrature independently and to check a fitted envelope on a
1024-bit, 15,125-point grid. What it has never been shown to do: prove anything, work on non-DE topics, or reject a subtly wrong
claim planted end-to-end.

### Known issues
- The doctor prints credential FAILs for empty `.env` keys when a Hermes login is in use (partly fixed: a live turn now downgrades them).
- Authentication is a Hermes login, possibly a Claude subscription login. Anthropic's terms restrict subscription OAuth tokens to
  Claude Code/Claude.ai. Use an API key for anything unattended.
- The evidence grades measure effort, not truth. Numerics are BigFloat; interval arithmetic exists only in the `certify`
  checker, which certifies finitely many points (build step 4).
- Real-valued statements use falsify/highprec/certify; exact (integer, rational, finite) statements use `exhaustive`
  (build step 5). There are no SAT/ILP or symbolic tools.
- Evidence derivation counts only rerun/compare/highprec/falsify records; the Critic's freehand experiments count only as "independent".
- Critic and Researcher use the same model, so their errors are correlated.

## 4. Decisions already made (do not reopen without asking)

Hermes as runtime, skip Strands; Julia for research code; Python for infrastructure; Docker isolation; single workstation, TSUBAME
later; literature search via arXiv only; basic numerical packages only for now; the arXiv 2305.05127 Example-1 benchmark is
**not** the owner's research target and must not be implemented; token efficiency and accuracy are the top priorities; "start
small, add complexity only when justified"; owner handles authentication; golden questions will be written by the owner.

Research scope (Tanaka-style numerical analysis): (1) sampling/discretization with optimization (point configurations),
(2) approximation formulas for holomorphic functions, DE/Sinc, (3) optimization of variable transformations, conformal maps
(Schwarz–Christoffel). Economics and finance are out of scope. Lean is one possible component, not the main line.

## 5. Missing specifications (requirements to settle)

### 5.1 Question types and intake
- Typical questions: quantification ("what is the error/rate of this method?"), comparison, and "find a formula/point
  configuration with property P". Pure existence questions are rare and the owner does not want an impossibility-proving
  method now. Instead the system should log **attempts as first-class records**: "tried X, it failed because Y",
  so that negative results are kept and not retried. This needs a note kind (for example `attempt`) with outcome and reason,
  included in the cross-campaign registry and the report.
- Question format: free text vs structured (objective, scope, allowed methods, budget, deliverable).
- Decomposition into several hypotheses and a hypothesis lifecycle (proposed, testing, falsified, surviving, proof attempt,
  proved/unprovable, reported), with persistence across campaigns (a cross-campaign registry exists, without lifecycle states).
- Autonomy level: single supervised campaign vs overnight batches vs a standing backlog. Human checkpoints, if any.

### 5.2 Exploration (stage 2)
- Exact arithmetic, enumeration, and integer/rational toolkit; SAT/ILP/constraint solvers; symbolic tools.
- Conjecture generation methods (curve fitting, sequence recognition, OEIS-like lookups) and their rigor labels.
- How exhaustive is "exhaustive": bounds on case counts, symmetry reduction, and how to record coverage.

### 5.3 Falsification (stage 3)
- Standard search budgets and strategies (grid, multi-start, adversarial, randomized), and what must be recorded so that
  "survived" means something specific.
- Decided: stop as soon as a counterexample is found; no certification step is required before `refuted`.
- Decided: correlated errors between Researcher and Critic are not a concern for now.

### 5.4 Proof stage (stage 4; defaults implemented in build step 6, see status.md)
- Decided: the LLM (Claude) writes the proof itself; computer assistance is supporting only (section 2).
- Still to design: when a proof attempt starts (which hypotheses, after which falsification effort), the budget and stop rule,
  the proof artifact format (structured steps with each lemma and its dependencies, plus the exact statement proved), how
  the proof is bound to the claim (statement fidelity), and how the human review is requested and recorded.
- Failure path: an unproved survivor is reported as `numerically-supported` with the failed lines of attack logged as attempts.
- Which parts of a proof can be spot-checked by computation (small cases, identities) and how that is recorded, without promoting the status.
- Formal proof (Lean) is out of scope for now.

### 5.5 Reporting and novelty (stage 5)
- Report format: **structured JSON** (the owner will feed it into an LLM later). Define and version a schema: claim, scope,
  limits, derived status, human status and history, evidence (run ids, checks), proof artifact reference, attempts, citations,
  reproduction commands. The Markdown report can remain as a derived view.
- **Novelty check**: is the result already in the literature? Currently only source verification exists, no novelty search.
- What is "worth reporting": thresholds for interest, and whether negative or unproved results are reported.
- Human interface for status: a command/API to set any status with a reason (section 2), plus a queue of `proven_AI` items awaiting review.

### 5.6 Numerical rigor
- Policy on floating point vs BigFloat vs interval arithmetic; required precision margins; treatment of the precision floor.
- Determinism and reproducibility across machines: pinned Julia and package versions (Manifest committed today), seeds, threading.
- Required *a priori* error analysis vs empirical rates; how to state finite-N results without extrapolation.

### 5.7 Trust, security, integrity
- Agent-written Julia runs only from the committed tree, in Docker with no network. Threat model for prompt injection via arXiv
  content, malicious code, resource exhaustion, and secret leakage is minimal; write it down.
- Guard against evidence inflation and vocabulary drift ("established", "proved"): a fixed glossary enforced by the server.
- Auditability: every reported claim traceable to run records; retention and backup of the ledger.

### 5.8 Evaluation (designed and built in step 3: [evaluation.md](evaluation.md))
The owner asked the new AI to explore and design the evaluation, including the golden questions, rather than fixing it now.
The design should cover: what to measure (accuracy of statuses, false `proven_AI` claims, missed counterexamples, proof
correctness, cost/tokens), the question set and its task-type mix, scoring (automatic vs human), regression protocol after every
model/prompt/tool change, hold-out policy against overfitting, and cost ceilings. The owner will supply some golden questions later. Use `docs/golden_questions.md` and `docs/golden_question_prompt.md` as starting points.
One question is already worked out: count `a(N)` of N×N 0/1 matrices with all powers bounded. Brute force gives 2, 11, 148, 4647
for N = 1–4 and the editorial formula matches; the equivalence proof and the O(N log N) claim are the parts that need a real proof.
It suits the proof stage: the agent must derive the argument, and a human reviews it.

### 5.9 Cost, models, compute
- Model per role (Claude expected for proofs), token ceilings per stage (proof search will dominate), caching policy, budget enforcement per question. Unattended operation is not a requirement now.
- Long-running jobs beyond the 30-minute cap, job queue, resume after interruption, TSUBAME/GPU path, and how results from the
  cluster enter the ledger with the same seal.
- Whether an API key (metered) replaces the current login for unattended work.

### 5.10 Data and process
- Ledger schema versioning (`run/0.1`), migration rules, what is committed to git vs kept out (run records are not committed;
  this caused rebase trouble once).
- Definition of done and acceptance tests for the next milestone.

## 6. Failure modes seen or expected
Critic collusion (same model), hallucinated citations (mitigated by arXiv verification), a truncated result read as a
number (mitigated by whole-entry clipping), premature "conclude", critic starved of budget (mitigated by reserve),
silent Hermes failures (exit 2 with hidden errors), goodhart on golden questions, tool misuse outside the design domain
(integer problems in real-valued checkers), read-only run directories breaking git on WSL2 (files sealed, dirs writable).

## 7. Suggested build order for the successor
1. **Done** (section 9). Implement the status model of section 2 (derived status, human override with append-only history, `proven` human-only, `proven_AI` only with a proof artifact) and a fixed glossary enforced by the server. Rename report wording accordingly and test that no agent tool can set `proven`.
2. **Done** (section 9). Add the `attempt` note kind and the versioned JSON report schema.
3. **Done** (section 9). Design the evaluation (successor's task, section 5.8) and build its runner; include proof-stage traps.
4. **Done** (section 9). (Optional, supporting only) interval-arithmetic checker and record type.
5. **Done** (section 9). Exact/finite exploration toolkit (enumeration, integer arithmetic) and checker support for non-real statements.
6. **Done** (section 9). Proof stage: the LLM writes the proof; store it as an artifact bound to the claim; add a review pass and the human review queue.
7. **Done** (section 9; the novelty *standard* is still the owner's). Novelty search and human review loop.
8. Compute expansion (TSUBAME, GPU) only when a real question needs it. **Not started**: no real question has run yet,
   and when it is needed is the owner's decision (section 8).

## 8. Owner answers so far, and what remains

Decided: `proven_AI` (AI-proved) and `proven` (human-reviewed, human-only) as separate statuses; the human may change any status
freely; the LLM writes proofs, computer assistance is supporting only; no impossibility method; falsification stops at the first
counterexample; correlated errors ignored; unattended operation ignored; report format is structured JSON; evaluation and golden
questions are to be designed by the successor.

Still open for the owner: novelty standard before reporting; monthly cost ceiling and API key vs login; when TSUBAME/GPU is
needed; whether questions are structured or free text; the owner's own golden questions.

## 9. Progress log

### Build steps 1–2 (branch `claude/numerical-analysis-agent-handoff-9bsgqd`)

Details: [status.md](status.md).

- **Status model.** A claim is a hypothesis note; `revises` links versions; the fingerprint (sha256 of normalized statement
  + scope) binds proofs and human statuses to exact text. Derived status: `refuted` (a note names a counterexample run) >
  `proven_AI` (a proof record for this version) > `numerically-supported` (a supporting finding or critic accept) > `open`;
  a proof next to a counterexample is a conflict. Revising a claim restarts its derived status (evidence does not transfer).
- **Human override.** `research-claims set|clear` is the only writer of human statuses; `proven` needs a proof record of the
  current version (or the human's own proof file), `refuted` a counterexample, every change a reviewer id and reason. The
  derived status is kept next to it. Flags `stale` (claim revised since) and `derived_changed` (derived status moved since)
  put a claim back in the review queue. `ledger/status/history.jsonl` is append-only and records server, AI and human changes.
- **proven is human-only**: no MCP tool writes human statuses (static AST test, MCP-protocol test); `set_human_status` refuses
  in an agent process.
- **Glossary.** Status vocabulary (prove/proof/established/rigorous/certified/guaranteed/QED) is rejected in agent-written
  statement/scope/limits/text, negations allowed. Report and seed wording renamed ("Accepted", "Established" removed).
- **Counterexamples.** `result=refutes` now requires `counterexample=<run id>`; a check that survived cannot be named; a
  violated check cited without being named triggers a hint. Critics can name counterexamples in challenges.
- **Attempts.** `attempt` notes (approach, stage, outcome never success, reason), in `read_claims`, the seed and the report.
- **JSON report** `report/0.1` with JSON Schema, written per campaign; `research-claims report` for the whole ledger.
- **Proof storage.** `submit_proof` (researcher only) stores `proof/0.1` records; refused on text mismatch, short text,
  revised version or recorded counterexample. The researcher profile says to prove only claims that are numerically
  supported and survived falsification; the server does not enforce that (the start rule is still open, 5.4).
- Fixed on the way: `add_note(kind="critique", status=<invalid>)` raised `KeyError` (a protocol error over MCP); now an
  ordinary error result.
- Not done: Julia checkers unchanged (no Julia in the development sandbox), so `falsify` still searches its whole budget for
  the worst point instead of stopping at the first violation; the status layer does treat the first named counterexample
  as final.

After pulling: `pip install -e agent`, re-run `research-install-profiles --root .` (the researcher's tool list and both
`SOUL.md` files changed), then `research-doctor --root . --ask`. Older ledgers keep working: notes without `about` are
attributed to the campaign's current hypothesis, and old `refutes` findings count as counterexamples via their cited runs.

Next: build step 3 (evaluation design and runner). Done, see below.

### Build step 3: evaluation (same branch)

Design: [evaluation.md](evaluation.md) (metrics, scoring, integrity, question set, regression protocol, hold-out
policy, cost ceilings, proof-stage evaluation, a one-evening first version).

- **Answer keys cannot leak into runs.** The harness gained an optional spec field `code.paths`; agent runs now
  extract only `workspace/` (without `workspace/agent/` and the human test statements in `workspace/statements/`,
  whose header announces C0003's planted flaw), `checkers/`, and their own script. Before this, agent-written Julia
  could read any committed file, including `docs/golden_questions.md` and every earlier agent script.
- **Sealed evaluation campaigns.** Campaign ids `EV-...` see only their own runs, notes and claims; no prior brief; their
  claims stay out of the normal registry, brief and review queue.
- **Questions** `eval/questions/GQ-*.toml` (five dev questions: GQ-MAT-01 proof-required, GQ-MAT-02 false theorem,
  GQ-DE-01 planted flaw, GQ-DE-02 rate estimation with a precision trap, GQ-OOS-01 out of scope). Keys verified by
  `eval/verify/matrix_powers.jl` (a(1..4) = 2, 11, 148, 4647 by two independent tests on every matrix; a(5) = 333806 by
  the graph test) and `eval/verify/de_checks.py` (mpmath port of DESinc.jl matching `de_reference.toml` to 30 digits;
  planted bound violated with ratio 83.35 at d = 1, a = b = 1, N = 8; c(N) for N = 8..256). The pi^2 limit in GQ-DE-02
  still needs a citation from the owner.
- **Runner and scoring.** `research-eval run|score|pending|grade|summary|list|validate`. Automatic checks read the
  end-of-run `report.json` (derived statuses) and the ledger; human checks stay pending until graded; a human grade
  overrides any automatic result; verdict fail / pending / partial / pass; records store the configuration (model per
  profile, installed and repository `SOUL.md` hashes, commit, dirty flag) so `summary` compares configurations.
- Gaps: no questions for point configurations, conformal maps or method comparison (no verified key available);
  cost ceilings are first guesses to calibrate on real runs; nothing has been run with a real model.
- Tooling note for cloud sessions (julialang.org downloads are blocked there, conda-forge is not): fetch micromamba
  from `https://conda.anaconda.org/conda-forge/linux-64/micromamba-2.9.0-0.tar.bz2`, then
  `micromamba create -p <dir> -c conda-forge julia=1.9.4`. That is not the pinned 1.11.3 and has no SpecialFunctions,
  but runs stdlib-only Julia such as `eval/verify/matrix_powers.jl` and the checkers with test statements.

### Build step 4: interval-arithmetic checker and record type (same branch)

- `checkers/Intervals.jl`: a package-free interval type on BigFloat; every endpoint is one MPFR call rounded outward
  (+ - * / exp log sqrt sinh cosh tanh pow gamma), undecidable comparisons and divisions by intervals containing 0
  throw instead of guessing. Generic statement code runs unchanged with `T = Iv`. Tests: `checkers/test_intervals.jl`
  (enclosures at 256 bits contain the 2048-bit values).
- `checkers/certify.jl`: evaluates `relmargin`/`margin` at given points (exact rationals) or on a grid; outcome
  `violated` (a certified counterexample; stops at the first), `certified` (holds exactly at those finitely many points,
  never a proof over the DOMAIN), `inconclusive` (raise precision). `DESinc.family_exact` now accepts any `Real` so the
  library runs on intervals (no numerical change).
- Verified on Julia 1.9.4 with the real library and statements: C0003 certified violated at d = 1, a = b = 1, N = 8
  with relmargin enclosed in [-82.35023521786819..., -82.35023521786819...] (err/bound = 83.3502, matching mpmath);
  C0002 certified at all 456 grid points (worst err/bound 0.0173). **Not yet run on the pinned 1.11.3**: run
  `julia checkers/test_intervals.jl` and `rh submit specs/check_certify_C0003.toml` in the container once.
- Record type: `submit_check(kind="certify", points=...)` and `submit_falsify_bound(kind="certify")`; runs record
  `arithmetic = "Interval"`; a cited `certified` run sets the evidence flag `certified` (and counts as a precision check);
  a counterexample from a `violated` certify run is marked certified in `report.json`. No status is ever set by it.
- Tests: 106 agent tests with `RESEARCH_TEST_JULIA=/path/to/julia` (103 plus 3 skipped without), 24 harness tests.

### Build step 5: exact/finite toolkit and non-real statements (same branch)

- `workspace/library/Exact.jl` (stdlib only): exact rationals (`qq`), rref/rank/nullspace/solve over Q,
  `interpolatory_weights` (exact weights of a rule from nodes and moments, e.g. Simpson, 3/8), enumeration
  (`binary_matrices`, `combinations`, `permutations_of`), symmetry reduction (`canonical_form`, `orbit_size` under
  relabeling), overflow-checked integer matrices, and sequence guessing (`guess_polynomial`, `guess_recurrence`) that
  only returns a formula which also fits terms not used to determine it (a conjecture label, not a statement about
  all n). Tests: `workspace/library/test_exact.jl`. No graph-structure helpers, on purpose (GQ-MAT-01).
- `checkers/exhaustive.jl`: exact statements (`holds(θ)` returning true or a witness, optional `cases(θ)`) over a
  finite discrete DOMAIN; outcome violated (first counterexample and witness, search stops) / exhausted / partial
  (`limit` reached), with domain size and case counts recorded as coverage.
- Record type: `submit_check(kind="exhaustive", limit=...)`, run arithmetic `Exact` (new harness value); `exhausted`
  sets the evidence flag `exhaustive` and grade falsification-tested; a violated exhaustive run is an exact, certified
  counterexample. Status is never set by it.
- Verified with Julia 1.9.4: the checker refutes the GQ-MAT-02 statement at N = 2 with witness [1 0; 0 0] after 2 of 3
  points and confirms Simpson's rule is exact through degree 3.
- Not done from HANDOFF 5.2: SAT/ILP/constraint solvers and symbolic tools (need packages the run sandbox cannot
  download; add them to the pinned environment first), OEIS lookups (literature is arXiv-only by decision).
- Tests: 109 agent tests with `RESEARCH_TEST_JULIA` (104 plus 5 skipped without), 24 harness tests.

### Build step 6: proof stage (same branch)

Details and the defaults chosen for HANDOFF 5.4: [status.md](status.md#proof-stage-build-step-6).

- Structured proofs `proof/0.2`: steps {id, kind, statement, proof, uses, runs} with validation (dependencies only
  backwards, no dangling steps, one final conclusion, written argument required, computation steps cite passing runs).
- Start rule: only claims that are numerically supported and survived a check (falsify, highprec, certify or
  exhaustive) may get a proof; at most 3 submissions per claim version and campaign.
- Review pass (made required, the handoff only recommended it): the critic reads the proof (`read_proof`) and records
  `review_proof` accept/object with objections naming steps; reviews are bound to the proof's sha256; only an accepted
  proof makes the derived status `proven_AI`. Tampered proof records become `invalid`. Human proof files never count as
  `proven_AI`.
- Loop: the critic's prompt lists proofs awaiting review; a campaign does not end while any do.
- Tools: `read_proof` (both roles), `review_proof` (critic); the researcher profile gains the proof procedure, the
  critic profile a review procedure. MCP now exposes 15 tools.
- Evaluation: GQ-PRF-01, a true bound offered with a false step (verified by `eval/verify/square_zero.jl`), tests that
  the proof does not reuse the step and that the review pass catches it.
- Tests: 125 agent tests with `RESEARCH_TEST_JULIA` (120 plus 5 skipped without), 24 harness tests.
- Not decided by the owner yet and implemented as defaults: the start rule, the limit of 3, and making the review pass
  required. All three are constants or single rules in `proofs.py`.

### Build step 7: novelty search and human review loop (same branch)

- `novelty` notes record the literature check of a claim: verdict known / related / not-found, the arXiv queries
  (each must be a search actually run in the campaign; the `arxiv` tool now logs searches), and verified sources.
  Shown in `read_claims`, the campaign seed, `report.json`, the Markdown view and the review queue. Evaluation check
  kind `novelty`.
- Human review loop: the reason behind a human status is shown to the agents (`read_claims`, the next campaign's
  seed), so review verdicts feed back into research.
- Not done, owner decision: the novelty standard before reporting; nothing is gated on novelty.
- Tests: 128 agent tests with `RESEARCH_TEST_JULIA` (123 plus 5 skipped without), 24 harness tests.

### Where this leaves the project (after steps 1-7)

Everything above was built and tested without a real model: agents were scripted, Julia runs used a fake binary except
the checkers and verification scripts, which ran on Julia 1.9.4. Next, on the owner's machine:

1. `pip install -e harness -e agent`, `research-install-profiles --root .`, `research-doctor --root . --ask`
   (MCP now has 15 tools; the researcher and critic profiles changed).
2. In the pinned 1.11.3 container: `julia checkers/test_intervals.jl`, `julia workspace/library/test_exact.jl`, and
   `rh submit specs/check_certify_C0003.toml` (expected `violated`, relmargin enclosing -82.3502...). The interval
   code calls MPFR directly and was only run on Julia 1.9.4.
3. First real evaluation (evaluation.md section 9): `research-eval run GQ-MAT-02`, `research-eval run GQ-DE-01`, then
   `research-eval summary`; calibrate the cost ceilings in `eval/questions/*.toml` from the measured runs.

Decisions that are the owner's (defaults are in place and documented):
- proof stage: the start rule (survived check), the limit of 3 proofs per claim and campaign, and the review pass being
  required rather than recommended (`proofs.py`);
- the novelty standard before reporting (nothing is gated on novelty);
- whether an exhaustively checked claim about a finite set deserves its own status (it stays numerically-supported);
- hold-out golden questions (kept outside the repository), questions for point configurations and conformal maps, and
  a citation for the pi^2 limit in GQ-DE-02;
- TSUBAME/GPU (step 8), API key vs login and the monthly cost ceiling.

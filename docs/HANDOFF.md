# Handoff: automated numerical-analysis research agent

Audience: the LLM/engineer who takes this project over. Read this first, then `README.md`, `docs/agent.md`,
`docs/phase0.md`, `docs/golden_questions.md`.

**Read this warning first.** The intended system is *not* what is built. What is built covers roughly stages 2–3 of the
intended workflow for real-valued numerical claims. It has **no proof stage, no rigorous (certified) numerics, no
novelty check, and no proof-status classification.** Its reports use words such as "established" and "accepted" that mean
"held in the tested scope", never "proven". Do not let anyone read them as proofs.

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
| 4 prove | prove survivors | **absent** |
| 5 report | proved results to the human | campaign `report.md` with claims, scope, limits, server-derived evidence grade |
| classification | proven / numerically supported / incorrect | only evidence grades (run < reproduced < cross-checked < falsification-tested) and `result: supports/refutes/…`. No proven class |

## 2. Rigor requirements the new system must satisfy (owner to confirm)

Proposed status taxonomy. The server assigns it from artifacts. **No LLM output may set a status by assertion.**

| Status | Meaning | Required artifact |
|---|---|---|
| `refuted` | claim is false | counterexample, ideally re-verified with interval arithmetic or exact arithmetic |
| `numerically-supported` | no counterexample found, evidence in scope | current grades, scope and limits stated |
| `certified` | true on a finite region/case set, computer-assisted | interval-arithmetic (Arb / IntervalArithmetic.jl) or exhaustive exact enumeration record |
| `cited` | equals a published theorem | verified source id and theorem number, statement compared |
| `proof-drafted` | an informal proof exists | text only; **unverified**; never reported as proved |
| `proof-reviewed` | informal proof checked by a human | reviewer id and date |
| `machine-checked` | formal proof accepted by a proof assistant | Lean (or similar) file that compiles with pinned toolchain, statement matches the claim |

Decisions the owner still has to make:
- What is the minimum bar for the word **"proven"**: machine-checked only, or human-reviewed informal proofs too?
- Is a **certified finite** result (e.g. an inequality on a compact box by interval arithmetic) reportable as proven for that scope? (Recommendation: yes, labeled `certified`, distinct from a general proof.)
- Acceptable false-positive rate for "proven": recommendation is zero tolerance; any wrong `proven` counts as a system failure.

## 3. Current implementation (facts)

Repo layout, branch `claude/laughing-pascal-7zteju`; the owner runs it on Windows + WSL2 + Docker.

- `harness/` (Python stdlib): sealed run records `run/0.1` (spec + result), `rh submit|logs|rerun|compare|show`,
  DockerExecutor (no network at run time, `--user`, memory cap), LocalProcessExecutor for tests. 19 tests.
- `workspace/` (Julia 1.11.3, `envs/main` pins SpecialFunctions): `library/DESinc.jl` (DE quadrature), `Prelude.jl`,
  checkers `highprec.jl` and `falsify.jl`, executable statements `C0001–C0003` (C0003 has a planted flaw K=2),
  scripts, reference data. Agent-writable only in `workspace/agent/{experiments,statements}`.
- `agent/src/researchagent/`: MCP server (FastMCP, `mcp>=1.9,<2`) with 12 tools; `loop.py` driver (`research-loop`);
  structured claims and derived evidence (`claims.py`); notes with role rules (`notes.py`); arXiv client with verification;
  statement generator (`templates.py`); `research-doctor`, `research-usage`, `research-install-profiles`. 73 tests.
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
- The evidence grades measure effort, not truth. Numerics are BigFloat, not interval arithmetic.
- Statements and checkers assume real-valued bounds; integer or combinatorial questions are unsupported by the checkers.
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
- Two families are already named: **quantification** ("what is the error/rate?") and **existence** ("does a formula exist?").
  Existence questions need constructive search *and* a way to argue impossibility. Neither exists.
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
- When a counterexample must be *certified* (interval/exact re-evaluation) before a claim is marked `refuted`.
- Whether a different model family should play the Critic to decorrelate errors.

### 5.4 Proof stage (stage 4, entirely unspecified)
- Who proves: an LLM drafting informal proofs, a proof assistant, or literature lookup? Budget and stop rule per hypothesis.
- Verification path for each status in the taxonomy above; how a proof is bound to the exact claim (statement fidelity check
  between the informal claim, the Lean statement, and the computed statement).
- Formalization gaps: Mathlib coverage for complex analysis, Sinc/DE theory, conformal maps is limited. Decide whether
  computer-assisted proofs (interval arithmetic, Arb, validated numerics) are the realistic route for most results.
- What happens when a proof attempt fails: report as conjecture with evidence and the failed lines of attack.
- Independent proof review (human reviewer or second model plus checker).

### 5.5 Reporting and novelty (stage 5)
- Report format for the human: claim, status, scope, limits, artifacts, reproduction command, proof or certificate, citations.
- **Novelty check**: is the result already in the literature? Currently only source verification exists, no novelty search.
- What is "worth reporting": thresholds for interest, and whether negative or unproved results are reported.
- Notifications and review loop: how the owner accepts, rejects, or overrides a status, and how that feeds back.

### 5.6 Numerical rigor
- Policy on floating point vs BigFloat vs interval arithmetic; required precision margins; treatment of the precision floor.
- Determinism and reproducibility across machines: pinned Julia and package versions (Manifest committed today), seeds, threading.
- Required *a priori* error analysis vs empirical rates; how to state finite-N results without extrapolation.

### 5.7 Trust, security, integrity
- Agent-written Julia runs only from the committed tree, in Docker with no network. Threat model for prompt injection via arXiv
  content, malicious code, resource exhaustion, and secret leakage is minimal; write it down.
- Guard against evidence inflation and vocabulary drift ("established", "proved"): a fixed glossary enforced by the server.
- Auditability: every reported claim traceable to run records; retention and backup of the ledger.

### 5.8 Evaluation
- Golden questions (owner will write; template in `docs/golden_questions.md`). The set must now include *proof-stage* items:
  provable statements, false statements that look plausible, subtly wrong proofs, and statements where the right output is
  "cannot prove, here is the evidence". Metric priority: wrong-"proven" rate (target 0), then wrong-`refuted`, then cost.
- Regression protocol: run the set after every model, prompt, tool, or budget change and store results with model, SOUL.md commit, tokens, wall time.
- Guard against overfitting prompts to the golden set (hold out some questions).
- One candidate mixed-verification question is worked out: count `a(N)` of N×N 0/1 matrices with all powers bounded; brute force
  gives 2, 11, 148, 4647 for N = 1–4 and the editorial formula matches. Only the equivalence proof and the O(N log N) claim
  are beyond computation.

### 5.9 Cost, models, compute
- Model per role, token ceilings per stage (proof search will dominate), caching policy, budget enforcement per question.
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
1. Confirm the rigor taxonomy and the "proven" bar with the owner (section 2). Write the glossary and enforce it in the server.
2. Add the `status` field, derived from artifacts only; rename report wording accordingly; add tests that an agent cannot assert a status.
3. Golden questions (owner) plus a scoring runner (`research-golden`), including proof-stage traps.
4. Certified numerics: interval-arithmetic checker and record type.
5. Exact/finite exploration toolkit (enumeration, integer arithmetic) and checker support for non-real statements.
6. Proof stage: start with literature/cited theorems and computer-assisted proofs; consider Lean only after measuring feasibility.
7. Novelty search and human review loop.
8. Compute expansion (TSUBAME, GPU) only when a real question needs it.

## 8. Questions to put to the owner before building
1. What exactly counts as "proven" (machine-checked only, or human-reviewed proofs)?
2. Are certified finite-scope results reportable as proven-in-scope?
3. Which proof route is expected: LLM-written informal proofs, Lean, literature, computer-assisted, or a mix?
4. Autonomy and budget: one question at a time with review, or unattended batches? Monthly cost ceiling?
5. Required novelty standard before reporting a result.
6. Should the Critic be a different model family?
7. Are questions structured or free text, and how are existence questions posed and judged?
8. When is TSUBAME/GPU needed?
9. The owner's golden questions and the hold-out policy.

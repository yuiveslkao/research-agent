# Prompt: ask an LLM to design the evaluation and propose golden questions

(Give this prompt to a capable LLM together with `docs/HANDOFF.md`.)

```
You are helping design the EVALUATION of an automated numerical-analysis research agent, including a set of "golden questions".
Read docs/HANDOFF.md first. It defines the agent, the intended workflow (explore, falsify, prove, report), the status model
(open, refuted, numerically-supported, proven_AI = proved by the AI, proven = human-reviewed and human-set only),
and the decisions already made.

## The agent
- Domain: numerical analysis in the style of Ken'ichiro Tanaka's group: double-exponential (DE) and Sinc
  quadrature/approximation, optimization-based point configurations (sampling, discretization, Fekete-type points),
  optimization of variable transformations, conformal maps (Schwarz-Christoffel and related).
- Tools: Julia on CPU (BigFloat up to 4096 bits, 4 threads, 6 GB, 30 min per run), a fixed library, arXiv search.
- Workflow: a Researcher proposes hypotheses and runs experiments; a Critic independently tries to break results; the LLM
  itself writes mathematical proofs (computer assistance only supports them); the human reviews proofs and may change any
  status. Claims are structured: statement, scope, limits, evidence; failed attempts are recorded.
- Budget per campaign: about 12 runs and 2 rounds (may change).

## Part A. Design the evaluation (do this first)
Propose an evaluation design and justify it. Cover:
1. What to measure: status accuracy (especially any wrong proven_AI claim; target zero), missed or false counterexamples,
   proof correctness and completeness, honesty about unverified parts, logging of failed attempts, and cost (tokens, wall time).
2. How to score: what can be automated from the JSON report and ledger, what needs a human, how a rubric is applied, and how
   to keep scoring cheap.
3. How to evaluate the proof stage specifically: provable statements, plausible false statements, statements where the correct
   output is "no proof found", and proofs with a planted subtle error. Say how a human reviews the AI proof efficiently.
4. Regression protocol: what is run after each model, prompt, tool or budget change, what is recorded (model, prompt/SOUL commit,
   tokens, wall time), and how noise between repeated runs is handled.
5. Overfitting: hold-out policy, rotation of questions, and how to avoid tuning prompts to the set.
6. A minimal first version that can run in one evening, and the extensions to add later.

## Part B. Propose the golden questions
Propose 8 candidate questions (the owner will keep about 5 and will add his own). For each use exactly this format:

### GQ-XX <short name>
Type:           rate estimation | refute a planted flaw | method comparison | optimization/point configuration |
                conformal map | proof-required | mixed-verification (partly computable) | out-of-scope
Question:       the exact text given to the agent (self-contained; states parameter ranges and the N range)
Known answer:   the correct, well-scoped conclusion, with a citable source (arXiv id or paper + theorem/equation).
                Do not use an answer the agent itself could have produced without a source.
Source check:   how the owner can verify the answer independently (closed form, published table, or a computation to run
                separately). If you are not certain of a claim, write "UNVERIFIED" and say why.
Traps:          concrete ways a careless agent goes wrong (precision floor, extrapolating past the tested range, wrong scope,
                confusing parameters, special cases, claiming a proof it does not have)
Planted flaw:   optional; a wrong claim or constant the Critic must catch
Expected status: which status a correct run should end with (for example numerically-supported, refuted, proven_AI)
Pass criteria:  4-8 yes/no items checkable from the report alone or by a script
Feasibility:    why it fits the tools and budget (expected runs, bit precision, runtime, proof length)
Budget:         max runs, rounds, token ceiling

Constraints on the set:
- Mostly numerical analysis; vary the task type; cover at least 3 types from the Type list.
- At least one question on optimization-based point configurations and one on conformal maps, if you can find a solid known
  answer. If not, say so rather than inventing one.
- At least one proof-required question where the agent must derive the mathematical argument itself (candidate: count
  a(N) of N x N 0/1 matrices whose powers A^k stay bounded; brute force gives 2, 11, 148, 4647 for N = 1..4 and the editorial
  closed form matches; the equivalence with a graph condition and the counting argument are the part to be proved).
- One out-of-scope or mixed-verification question where the right behavior is to separate what was computed from what was not,
  and to avoid claiming a proof (or to claim at most proven_AI with the proof attached).
- At least one planted-flaw question. Give difficulty ordering and an estimated cost of one run.
- Avoid questions whose answers are famous enough to be recalled without computation, unless that is the point;
  mark them "recall risk" and design a criterion that detects recall.

## How to work
1. Search arXiv and the literature for results with explicit, citable constants, rates or counterexamples in these topics.
2. For every "Known answer" state its source. Never invent a citation or a constant. Mark anything you cannot verify as UNVERIFIED
   and exclude it from your final recommendation.
3. If you can run code, sanity-check the known answers numerically at small scale and report what you did.
4. Finish with a table: question, type, difficulty, expected status, verification status, recall risk, recommendation (keep,
   revise, drop), and a list of task types not covered.
```

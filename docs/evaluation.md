# Evaluation design (build step 3)

Purpose: tell whether a change to the model, a prompt (`SOUL.md`), a tool, a server rule or a budget makes the agent
more or less **correct** and how much it **costs**, before the change is used for research. Code:
`agent/src/researchagent/evaluation.py` (`research-eval`), questions in `eval/questions/`, answer-key checks in
`eval/verify/`.

Principles: never trust an answer key that was not verified; score what the agents produced from records, not
from their prose; zero tolerance for false proofs and false support of false statements; keep a human in the loop
for proofs; cheap enough to run after every change.

## 1. What is measured

| Metric | Definition | Scored | Target |
|---|---|---|---|
| False proof | a claim reaches `proven_AI` whose proof a human grades wrong, or `proven_AI` on a known-false statement | human check `proof-correct` (critical); `no_claim` on false statements (critical) | 0 |
| False support | a known-false statement ends `numerically-supported` | `no_claim` (critical) | 0 |
| Missed counterexample | a planted or false statement is not `refuted` with a named counterexample run | `claim` with `counterexample = true` (critical in planted-flaw questions) | 0 |
| False refutation | a known-true result ends `refuted` | `no_claim ... status = ["refuted"]` (critical) | 0 |
| Status accuracy | expected claims end in the expected status | `claim` checks | rising |
| Proof correctness | stored proofs are correct and complete | human, per proof | all correct |
| Honesty | numerically supported claims keep a finite scope; failed proof attempts are logged; computed and inferred results are separated | `scope` (critical where extrapolation is the trap), `attempts`, human | all |
| Process | the critic attacked; a falsify check ran where the claim is an inequality | `critic_runs`, `checks_run` | all |
| Cost | turn minutes, estimated tokens (tool-call characters / 3.5, a lower bound), runs | `_time`, `_tokens` against the question's ceilings | within ceilings |

Scoring uses each claim's **derived** status at the end of the run (`report.json` of the campaign), so later human
overrides do not change what the agents are credited with.

## 2. Scoring

- **Automatic checks** read the campaign's `report.json` and the ledger: `claim` (at least `min` claims whose text
  matches all `match` regexes and none of `forbid`, with a derived status in `status`, optionally with a named
  counterexample), `no_claim`, `scope` (every claim with a status in `status` has a scope matching `match` and not
  `forbid`), `critic_runs`, `checks_run`, `attempts`, `stopped_by`, `novelty` (claims with a literature check,
  optionally with a given verdict). Every question also gets `_ran` (no agent or
  infrastructure error) and the cost checks `_time` and `_tokens`.
- **Human checks** (`kind = "human"`) stay `pending` until graded. `when = "proven_AI"` makes a check apply only if a
  claim reached `proven_AI`; `when = "no_proven_AI"` only if none did.
- **Overrides.** Matching claim text with regexes is brittle. A human grade on any check replaces its automatic result
  (`research-eval grade E-... CHECK pass|fail --by ID --reason "..."`); grades are append-only
  (`ledger/eval/grades.jsonl`), the latest per check wins, and the score shows the source (`auto` or `human`).
- **Verdict** per run: `fail` if any critical check failed; else `pending` if a check awaits a human; else `pass` if
  every applicable check passed; else `partial`.
- Keep grading cheap: a human only grades proofs (`research-claims show <claim> --proof` in the eval campaign) and the
  few `human` checks; `research-eval pending` lists them with the command that renders the claims.

## 3. Integrity: the agent cannot see the key, earlier runs or earlier answers

- **Answer keys are invisible to runs.** Agent runs extract only `workspace/` (without `workspace/agent/` and the
  human test statements in `workspace/statements/`), `checkers/`, and the run's own agent file (run spec
  `code.paths`, enforced by the harness). `eval/`, `docs/` and older agent scripts are not in the run directory. The
  agents have no file tools.
- **Sealed campaigns.** Evaluation campaigns have ids `EV-<question>-...`. In them the researcher and critic see
  only that campaign's runs, notes and claims (`read_claims`, `list_runs`, `inspect_run`, `read_artifact`,
  `rerun_run`, `compare_runs`, `base_run`, cited runs, `about`/`revises`, `submit_proof`). The question note carries
  no brief of earlier results. Conversely, evaluation claims never enter the normal registry, brief or review queue.
- **Question versioning.** Each record stores the question file's sha256; the score says `question changed` if the
  file was edited after the run.

## 4. The question set

Format: one TOML file per question (`eval/questions/GQ-*.toml`), fields `id`, `title`, `type`, `split` (dev |
holdout), `difficulty`, `verification` (verified | partly-verified | campaign | unverified), `source`, `recall_risk`,
`question` (given to the agent verbatim), `known_answer` (for humans only), `traps`, `[budget]` (`rounds`,
`max_runs`, `turn_timeout_s`, `max_turn_minutes`, `max_est_tokens`), and `[[check]]` entries (`id`, `kind`, `what`,
`critical`, `when`, plus `match`/`forbid`/`status`/`min`/`counterexample`/`stage`/`checker`/`values` by kind).
`research-eval validate` checks them.

Current dev set (six questions; all answer keys checked in this repository unless marked):

| id | type | what it tests | key | verification |
|---|---|---|---|---|
| GQ-MAT-01 | proof-required | count a(N) of power-bounded 0/1 matrices, characterize, prove | a(1..4) = 2, 11, 148, 4647; a(5) = 333806; graph characterization | `eval/verify/matrix_powers.jl`: exact periodicity and graph test agree on all matrices for N <= 4 |
| GQ-MAT-02 | refute-false-statement | a false "theorem" (bounded powers imply nilpotent or permutation) posed as "prove" | false at N = 2, e.g. [1 0; 0 0] | same script |
| GQ-DE-01 | planted-flaw | the doubled-constant DE bound (statement C0003) with a plausible argument | violated, error/bound = 83.35 at d = 1, a = b = 1, N = 8 | `eval/verify/de_checks.py` (mpmath port reproducing `de_reference.toml` to 30 digits) |
| GQ-DE-02 | rate-estimation | effective rate constant c(N) up to N = 256; precision floor trap | c(N)/pi^2 = 0.546 ... 0.942 for N = 8 ... 256 | values verified; the limit pi^2 is from the earlier campaign and DE theory (owner to confirm the citation) |
| GQ-OOS-01 | out-of-scope | asks for an exact asymptotic constant and its proof | expected behavior only | unverified by design |
| GQ-PRF-01 | proof-required | a true bound offered with an argument containing a false step (proof-review trap) | true, tight; the step N(N-1)/2 <= floor(N^2/4) fails for N >= 3 | `eval/verify/square_zero.jl` |

Covered: proof stage (a real proof, a false statement dressed as a theorem, a proof request that should end without
a proof), falsification, rate estimation with a precision trap, honesty about scope. **Not covered yet**:
point configurations, conformal maps, method comparison. No verified key exists for them in this repository; do not
add one without a citation or a verification script. `docs/golden_question_prompt.md` can be used to have an LLM
with literature search propose candidates; the owner verifies them.

Rules for new questions: every `known_answer` has a verification script in `eval/verify/` or a citation (paper and
equation); anything else is `unverified` and may only drive behavior checks that do not depend on the key. State
parameter ranges and N ranges in the question text. Prefer asking for values that cannot be recalled (they are
computed), and mark recall risk.

## 5. Regression protocol

Run after any change to: the model of a profile, `SOUL.md`, tool descriptions or server rules, budgets, the library
or the checkers.

1. `pip install -e agent`, `research-install-profiles --root .`, `research-doctor --root . --ask` (the record stores
   the installed `SOUL.md` hash and warns when it differs from the repository copy).
2. `research-eval run <id> --repeat 3` for every dev question (or a cheaper subset first: GQ-MAT-02 and GQ-DE-01 are
   short). Commit before running; the record stores the commit and a dirty flag.
3. Grade pending checks: `research-eval pending`, then `research-eval grade ...`.
4. `research-eval summary` groups results by question and `config_id` (models, prompt hashes, commit) and prints
   per-check pass counts, critical-failure runs and mean cost for old and new configurations side by side.

Decision rule: any new critical failure blocks the change. A drop of two or more passes out of three on one check is
a regression to investigate. More than 30% extra cost needs a stated reason. Noise: runs of the same configuration
differ; three repetitions per question separate a one-off failure of a non-critical check from a trend. A critical
failure is never treated as noise.

## 6. Hold-out policy

- Hold-out questions are written by the owner and kept **outside the repository** (a private directory passed with
  `--questions DIR`), with `split = "holdout"`. Anyone who tunes prompts, including an AI in a coding session, must not
  see them; the repository only holds dev questions.
- `research-eval run` refuses a hold-out question without `--holdout`. Run the hold-out set only at milestones (before
  adopting a new model or prompt), not while iterating.
- `summary` reports dev and hold-out separately. A large gap (dev passes, hold-out fails) indicates tuning to the dev
  set.
- Rotation: after about three milestones, or once a hold-out question has been discussed in a tuning session, move it
  to dev and write a new one.

## 7. Cost ceilings

- Per question: `[budget]` rounds and `max_runs` are enforced by the loop and the server; `turn_timeout_s` kills a
  hung turn; `max_turn_minutes` and `max_est_tokens` are recorded as the `_time` and `_tokens` checks (a breach is
  flagged, not fatal, until the ceilings are calibrated on real runs).
- Per invocation: `research-eval run ... --max-minutes M` starts no further repetition once M turn-minutes are spent.
- Estimate: a 2-round campaign took about 11.5 turn-minutes and 3-4% of a Claude Pro five-hour allowance (owner's
  measurement). One pass over the five dev questions is then roughly 1 to 3 hours and 15-20% of an allowance; the
  regression protocol with `--repeat 3` about three times that. Unattended runs should use an API key (HANDOFF
  section 3).

## 8. Proof-stage evaluation

Since build step 6, `proven_AI` requires a structured proof and an accepting AI review pass (docs/status.md); any
`proven_AI` in an evaluation campaign still triggers a critical human check. The traps are a real proof (GQ-MAT-01,
both directions required), a false statement posed as "prove" (GQ-MAT-02: the correct output is a refutation), a proof
request that should end without a proof but with logged attempts (GQ-OOS-01), and a true claim offered with an
argument containing a false step (GQ-PRF-01: the proof must not reuse it, and the review pass should object if it
does). The report shows each proof's review and objections, so the grader sees whether the review pass caught an
error. Later: grading proofs on a three-level scale (correct / fixable gap / wrong).

Human review of a proof, efficiently: read the exact statement and scope first (they are bound to the proof), then
the reformulation and the lemma list, then check each lemma's use of its assumptions; stop at the first wrong step
and record it in `--reason`.

## 9. Minimal first version (one evening) and what comes later

Evening: install, then `research-eval run GQ-MAT-02` and `research-eval run GQ-DE-01` (short, fully automatic
scoring), then `research-eval run GQ-MAT-01` and grade its proof if one was submitted. Later: `--repeat 3` for all
dev questions as a baseline for the current configuration; the owner's hold-out questions; questions for point
configurations and conformal maps; exact token counts from the provider; proof-review questions with step 6.

## Commands

```
research-eval list | validate
research-eval run GQ-DE-01 [--repeat 3] [--max-minutes 120] [--questions PRIVATE_DIR --holdout]
research-eval score E-... [--json]
research-eval pending
research-eval grade E-... proof-correct fail --by owner --reason "Lemma 2 assumes the strip width is d"
research-eval summary [--question GQ-DE-01] [--json]
```

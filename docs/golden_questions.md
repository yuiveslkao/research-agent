# Golden questions

Purpose: a small fixed set of research questions whose correct answers are known, used to measure whether a change
(model, prompt, profile, tool, budget) makes the agent more or less accurate and how many tokens it spends.

**Status:** the evaluation is designed and built (build step 3): see [evaluation.md](evaluation.md). The question files
are `eval/questions/GQ-*.toml` (machine-readable, scored by `research-eval`), their answer keys are checked by the
scripts in `eval/verify/`. Five dev questions exist (GQ-MAT-01, GQ-MAT-02, GQ-DE-01, GQ-DE-02, GQ-OOS-01). The
owner's own questions are still to be written; hold-out questions belong outside the repository (evaluation.md
section 6). Point configurations, conformal maps and method comparison have no verified question yet.

The text below is the original template and the candidates it was built from; the TOML format in evaluation.md
section 4 replaces the template.

## Format (one section per question)

```
### GQ-01 <short name>
Question:        the text given to `research-loop --question-file`
Known answer:    what a correct, well-scoped conclusion says (rates, constants, dependence), with the source
Traps:           things a careless agent gets wrong (precision floor, extrapolating past N, wrong scope, ...)
Planted flaw:    optional wrong claim/constant the Critic must catch (like statements C0002/C0003)
Pass criteria:   checkable items, e.g. "reports r(N) within 5% of X", "states N range", "Critic runs a check"
Budget:          max runs, rounds, and a token/cost ceiling
```

## What to record per run of a golden question

model per profile, `SOUL.md` version (git commit), campaign id, stopped_by, rounds, runs used, pass/fail per criterion,
tokens in/out/cache-read (Anthropic Console or Hermes session data), wall time.

## Suggested mix (owner to decide)

- 2 questions with a known closed-form or published rate (rediscovery).
- 1 question where the "textbook" bound is loose (does the agent notice, as in the first campaign?).
- 1 planted-flaw question (a wrong bound the Critic must break).
- 1 unanswerable-in-scope question (does the agent stop and say so?).


## Candidates taken from campaign C-20260929T131243-49bf (owner to confirm against the literature before use)

Used so far: GQ-A became GQ-DE-02 (c(N) values verified; the pi^2 limit still to be confirmed with a citation), GQ-C
became GQ-DE-01 in the concrete form of statement C0003 (verified), GQ-D became GQ-OOS-01. GQ-B is not used yet.

The DE/Sinc testbed campaign produced claims with a checkable known answer. If the owner confirms them, they can
become golden questions with these expected results:

- **GQ-A rate constant.** For I(a,b) = ∫(1-x)^a (1+x)^b dx with the tanh-sinh DE map and h = log(4dN/alpha)/N,
  the error is exp(-pi^2 / h) up to a prefactor; the rate does not scale with the design parameter d (the strip half-width
  of the transformed integrand is pi/2). A constant c in exp(-cN/log N) does not fit for N <= 128.
  Traps: precision floor at 256 bits for N = 128; reading d as the strip width.
- **GQ-B prefactor exponent.** Prefactor ~ h^(-q) with q = 2(a+b)+2, independent of alpha = min(a,b)+1 at fixed a+b
  (campaign evidence: a+b >= -0.2, N up to 1024, 3072 bits). Open: a+b <= -0.5.
  Traps: confounding alpha with a+b (the first round did); integer b-a makes the odd part integrate exactly.
- **GQ-C planted flaw.** Give the agent the claim err <= C exp(-2 pi d N / log(4dN/alpha)) as a "tight rate": a good run
  reports it as valid but loose by about pi/(2d) in the exponent. A claim with a rate above pi^2 must be refuted.
- **GQ-D out-of-scope.** Ask about a regime the tools cannot resolve (e.g. asymptotics as N -> infinity): a good run
  says what is only observed and stops.

Reference numbers to check the agent against: k2 = -ln(err) * log(4dN/alpha) / (pi^2 N) is about 0.88-0.99 at N = 128
and rises with N; local slope of -ln(err) against N/log(4dN/alpha) is 9.78-9.84 at N = 512.

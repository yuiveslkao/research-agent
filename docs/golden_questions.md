# Golden questions (TODO: to be written by the owner)

Purpose: a small fixed set of research questions whose correct answers are known, used to measure whether a change
(model, prompt, profile, tool, budget) makes the agent more or less accurate and how many tokens it spends.
Without this there is no way to tell whether an "improvement" helped.

Status: **not written yet.** The owner will prepare them later. Do not tune prompts against the DE/Sinc testbed alone.

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

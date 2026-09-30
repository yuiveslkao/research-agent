# Claim status, human review, and the JSON report

This implements build-order steps 1, 2 and 6 of [HANDOFF.md](HANDOFF.md#7-suggested-build-order-for-the-successor):
the status model with human override, a fixed glossary enforced by the server, `attempt` notes, a versioned JSON
report, and the proof stage. Code: `agent/src/researchagent/{status,proofs,glossary,report,claims_cli}.py`.

**What it does not do.** Nothing here checks a proof formally. `proven_AI` means a structured proof of the exact claim
is stored and an AI review pass (the critic, same model) found no blocking objection, not that the proof is correct;
only `proven` (a human) is a verified status. There is no novelty check. Interval arithmetic and exact enumeration
exist only as the `certify` and `exhaustive` checkers (build steps 4 and 5): they settle finitely many points and never
set `proven` or `proven_AI`.

## Statuses

| Status | Meaning | Set by | Required record |
|---|---|---|---|
| `open` | No conclusion yet | server | none |
| `refuted` | A counterexample was recorded | server, or human | a note naming a counterexample run (finding `result=refutes` + `counterexample`, or a critic challenge with `counterexample`); human: `--counterexample` |
| `numerically-supported` | No counterexample found; cited runs support the claim within the stated scope and limits. Not a proof | server | a `supports` finding or a critic `accept` citing runs |
| `proven_AI` | Proved by the AI, accepted by the AI review pass, not human-reviewed | AI: `submit_proof` (researcher) + accepting `review_proof` (critic) | a structured proof record bound to the claim's exact text and an accepting review of that text |
| `proven` | Proof reviewed by a human | **human only** (`research-claims set`) | a proof record of the current text, reviewer id, date |

Derived precedence: `refuted` > `proven_AI` > `numerically-supported` > `open`. A proof and a counterexample on the
same version is reported as a **conflict** (status `refuted`, listed in the review queue).

The **effective status** is the human's if set, else the derived one. Both are kept side by side; the human status
never overwrites the derived one. The evidence grade (`run` < `reproduced` < `cross-checked` <
`falsification-tested`) is shown next to the status and is not a status.

## Claims, versions, fingerprints

- A **claim** is a hypothesis note; its id is that note's id (`N-...`). Its text is `statement` + `scope`.
- A later hypothesis with `revises=<current version id>` is the next **version** of the same claim (for example after
  a critic narrows it, or across campaigns). Only the current version (the head) may be revised, cited in `about`, or
  proved; forks are refused.
- The **fingerprint** is `sha256` of the whitespace-normalized statement and scope. Proofs and human statuses record
  the fingerprint they were made for.
- Evidence, counterexamples and proofs count only for the version they are about. A revision therefore starts the
  derived status again at `open`: evidence for other wording does not transfer automatically (cite old runs in a new
  finding if they apply).
- Findings, critiques and attempts carry `about` (a version id). It defaults to the version the campaign is working on:
  the researcher's latest hypothesis; for the critic, what the researcher's latest finding was about. Older notes
  without `about` are attributed the same way.
- `narrowed`: the critic's latest `accept` restates the claim with a different statement or scope. The report shows
  that restatement as `supported_as`; read the status together with it.

## Certified evidence (`certify` checks)

`submit_check(kind="certify", points=[...])` evaluates a statement's margin in interval arithmetic
(`checkers/Intervals.jl`: every endpoint is one MPFR operation rounded outward). A finding or accept citing a run with
outcome `certified` gets the evidence flag `certified` (the claim holds exactly at those points); the status stays
`numerically-supported`. A counterexample named from a `certify` run with outcome `violated` is marked `certified` in
the report. `certified` is reserved vocabulary: agents do not write it, the server derives it.

`submit_check(kind="exhaustive")` runs an exact statement (`holds(θ)` over a finite discrete DOMAIN) at every point.
Outcome `exhausted` sets the evidence flag `exhaustive` (and grade `falsification-tested`): the finite statement held
everywhere on that DOMAIN, which says nothing beyond it; the status stays `numerically-supported`. A `violated`
exhaustive run is an exact counterexample and is marked certified. (Open question for the owner: should a claim whose
scope *is* a finite set, checked exhaustively, get its own status?)

## Human status and review

```
research-claims queue                      # proven_AI awaiting review; stale or changed human statuses; conflicts
research-claims show N-... --proof         # text, statuses, evidence, proof texts, attempts, history
research-claims set N-... proven --by owner --reason "checked all lemmas" --proof P-...
research-claims set N-... proven --by owner --reason "my own argument" --proof-file proof.md
research-claims set N-... refuted --by owner --reason "fails at N=3" --counterexample "N=3, d=1"
research-claims set N-... numerically-supported --by owner --reason "..."   # any status, any time
research-claims clear N-... --by owner --reason "derived status is right"
research-claims list [--status S] [--campaign C] [--json]
research-claims report [--campaign C] [--out report.json] [--md]
```

- `proven` requires a proof record bound to the **current** version (`--proof`) or your own proof file (stored as a
  proof record with author `human:<by>`). `refuted` requires a counterexample. Every change needs `--by` and `--reason`.
- **stale**: the claim was revised after the human set its status. **derived_changed**: the derived status moved since
  then (for example a new counterexample after a human `proven`). Both keep the human status in force and put the
  claim in the review queue.
- Only `status.set_human_status` writes human statuses. It is not reachable from any MCP tool (tested statically and
  over the MCP protocol) and refuses to run in a process with `RESEARCH_ROLE` set to an agent role.

Claims from evaluation campaigns (`EV-...`) are left out of `list`, `queue` and the whole-ledger report unless
asked for by campaign (`--campaign EV-...`); their proofs are graded with `research-eval grade` (see evaluation.md).

## History

`ledger/status/history.jsonl`, append-only, one record per change:
`{at, claim, version, fingerprint, source: server|ai|human, actor, by?, status, previous, reason, basis?, derived_at_set?, proof?, counterexample?}`.
The server appends a derived record whenever a note or proof changes a claim's derived status or version
(`source: ai` for `proven_AI`); `research-claims set/clear` appends human records (`status: null` = cleared).

## Proof stage (build step 6)

Code: `agent/src/researchagent/proofs.py`. The defaults below are this implementation's answer to HANDOFF 5.4; each
is a constant or one rule in that file.

- **When a proof may start:** only for a claim version that is `numerically-supported` (or already `proven_AI`) and
  survived a check: a supporting finding or accept cites a falsify (`survived`), highprec (`pass`), certify
  (`certified`) or exhaustive (`exhausted`/`partial`) run. "Prove survivors."
- **Budget and stop rule:** at most 3 proof submissions per claim version and campaign; after that the researcher logs
  an `attempt` (stage `prove`) and stops. Token and time budgets are the turn's (Hermes `run_budget_seconds`, the
  driver's turn timeout).
- **Format `proof/0.2`** (`ledger/proofs/P-*.json`): the claim's exact statement and scope (fingerprint-bound),
  `assumptions`, and `steps` [{id, kind: definition|lemma|claim|computation|conclusion, statement, proof, uses, runs}].
  Rules: unique ids; `uses` only earlier steps; every step used by a later one; exactly one conclusion, last; every
  lemma/claim/conclusion has its written proof; at least 200 characters of written argument; computation steps cite
  completed runs (checks with a passing outcome) and support the argument, never replace it. `sha256` covers statement,
  scope, assumptions and steps; a record whose content no longer matches it is `invalid` and never counts.
- **Review pass:** the critic (`read_proof`, `review_proof`) records verdict `accept` (no blocking objection) or `object`
  (at least one blocking objection naming a step), bound to the proof's sha256, in `ledger/proofs/reviews.jsonl`
  (append-only). No self-review; a proof of a revised claim cannot be reviewed. Only an accepted proof makes the derived
  status `proven_AI`; pending and objected proofs are listed with the claim. The loop tells the critic which proofs
  await review and does not end a campaign while any do.
- **Spot checks:** computation steps and the reviewer's `run_ids` record which runs back which step; they never change
  the status by themselves.
- **Human review:** `research-claims queue` lists `proven_AI` claims (accepted by the AI review pass);
  `research-claims show N-... --proof` renders the steps, dependencies and review objections; `set ... proven --proof P-...`
  records the human verdict. The human's own proof file (`--proof-file`) is stored as `proof/0.1` and never makes the
  derived status `proven_AI`.
- **Failure path:** an unproved survivor stays `numerically-supported`; failed lines of attack are `attempt` notes.

## Glossary (server-enforced)

Agent-written `statement`, `scope`, `limits` and note `text` may not use status vocabulary: prove/proves/proved/proven/
proving/provable, proof(s), establish(ed), rigorous(ly), certify/certified/certificate, guarantee(d), QED. Negated uses
("not proven", "no proof found") are allowed. Exempt: `attempt` notes (their outcome is never a success), objections,
`refuted_if`, `resolve_by`, proof texts, and the driver's question note. The rejection message suggests replacement
wording. Reports and the campaign seed use the status names and meanings from `glossary.py` only ("Accepted" and
"Established" are gone).

## Attempts

`add_note(kind="attempt", claim={approach, stage: explore|falsify|prove|literature, outcome: failed|inconclusive|abandoned, reason, about?, sources?})`,
by the researcher or the critic, `run_ids` optional. Attempts appear under their claim in `read_claims`, in the campaign
seed ("tried, failed: ...", so they are not retried), and in the report. A successful line of work is a finding or a
proof, never an attempt.

## JSON report (`report/0.1`)

Written to `ledger/campaigns/<id>/report.json` at the end of every campaign; `report.md` is rendered from it.
`research-claims report` builds it for one campaign or the whole ledger. JSON Schema:
`agent/src/researchagent/schemas/report-0.1.schema.json` (validated in the tests).

Top level: `schema`, `generated_at`, `campaign` (id, question, stopped_by, rounds, max_runs; null for a whole-ledger
report), `glossary` (status meanings, who sets each, grade meanings), `claims`, `open_objections`, `attempts` (not tied
to a claim), `review_queue`, `runs` (id, kind, status, role, precision, code commit, spec path, `rh rerun` command).

Per claim: `id`, `version`, `versions`, `campaigns`, `statement`, `scope`, `refuted_if`, `fingerprint`,
`status` {effective, meaning, derived, human, stale, derived_changed, conflict, narrowed, awaiting_review,
review_reasons}, `supported_as`, `evidence` {grade, findings, critiques, counterexamples, runs}, `proofs` (references,
`current` = bound to the current version), `attempts`, `citations` (verified arXiv ids), `history`, `reproduce`.

Any incompatible change gets a new schema version and a new schema file.

## Storage

`ledger/status/` and `ledger/proofs/` are git-ignored like `ledger/notes/`, because they refer to notes. Human reviews
cannot be regenerated: back up `ledger/` as a unit (retention policy is still open, HANDOFF 5.7/5.10).

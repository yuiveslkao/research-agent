# Claim status, human review, and the JSON report

This implements build-order steps 1 and 2 of [HANDOFF.md](HANDOFF.md#7-suggested-build-order-for-the-successor):
the status model with human override, a fixed glossary enforced by the server, `attempt` notes, and a versioned
JSON report. Code: `agent/src/researchagent/{status,glossary,report,claims_cli}.py`.

**What it does not do.** Nothing here checks a proof. `proven_AI` means a proof text was stored for the exact claim,
not that it is correct. There is no proof-stage workflow yet (when to attempt, budget, review pass: build step 6), no
novelty check, and no interval arithmetic.

## Statuses

| Status | Meaning | Set by | Required record |
|---|---|---|---|
| `open` | No conclusion yet | server | none |
| `refuted` | A counterexample was recorded | server, or human | a note naming a counterexample run (finding `result=refutes` + `counterexample`, or a critic challenge with `counterexample`); human: `--counterexample` |
| `numerically-supported` | No counterexample found; cited runs support the claim within the stated scope and limits. Not a proof | server | a `supports` finding or a critic `accept` citing runs |
| `proven_AI` | Proved by the AI, not human-reviewed | AI via `submit_proof` | a proof record bound to the claim's exact text |
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

## History

`ledger/status/history.jsonl`, append-only, one record per change:
`{at, claim, version, fingerprint, source: server|ai|human, actor, by?, status, previous, reason, basis?, derived_at_set?, proof?, counterexample?}`.
The server appends a derived record whenever a note or proof changes a claim's derived status or version
(`source: ai` for `proven_AI`); `research-claims set/clear` appends human records (`status: null` = cleared).

## Proof records (`proof/0.1`)

`ledger/proofs/P-*.json`: `{schema, id, created, author, campaign, claim, version, fingerprint, statement, scope,
format: "markdown", proof, sha256, supporting_runs}`. `submit_proof` (researcher only) refuses unless `statement`
and `scope` repeat the current version exactly, the proof is 200..60000 characters, and no counterexample is recorded.
`supporting_runs` are computer-assisted steps; they support the argument and never replace it. The structured format
(lemmas with dependencies) is left to build step 6; bump the schema when it changes.

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

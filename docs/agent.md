# The research agent (minimal loop)

Goal of this milestone: given a small mathematical question, a Hermes-based agent proposes an experiment,
runs it through the existing harness, inspects the result, has a Critic challenge it, and decides the next step.
The DE/Sinc benchmark is only the testbed.

## Pieces

```
research-loop (driver, Python) ── hermes -p researcher -z "<round prompt>"   ┐ Hermes profiles, no shell/web,
                               └─ hermes -p critic     -z "<round prompt>"   ┘ tools only via MCP
                                          │ MCP (stdio, one server process per profile)
                                   researchagent.mcp_server ── tools.py
                                          │                         │
                            lab worktree (agent/lab branch)     rharness (sealed runs, Docker)
                            workspace/agent/{experiments,statements}   ledger/{runs,checks,notes,audit.jsonl}
```

| Tool | Who | Does |
|---|---|---|
| `submit_experiment` | both | Commits agent-written Julia to the lab worktree, builds a spec, runs it in Docker, waits, returns the summary |
| `submit_check` | both | Same for an executable statement, run through `falsify` or `highprec` |
| `inspect_run`, `read_artifact`, `list_runs` | both | Read records: status, params, reported metrics, output files, stdout/stderr, the Julia source that ran |
| `rerun_run`, `compare_runs` | both | Reproduce a run from its sealed spec; compare metrics of two runs (e.g. 2 precisions) |
| `submit_falsify_bound` | both | Try to break `|error| <= bound` for the DE testbed over a parameter box, from a bound expression (no Julia to write) |
| `add_note`, `read_notes` | both | Structured notes (see below). Researcher writes hypothesis/finding/decision; Critic writes critique; both write attempt |
| `read_claims` | both | Claims across all campaigns with their status, grade, critic verdict, counterexample; open objections; failed attempts |
| `submit_proof` | researcher | Store a written proof bound to a claim's exact text; derived status becomes `proven_AI` (not human-reviewed) |
| `arxiv` | both | Search arXiv or look one paper up by id (short records, cached, untrusted text) |

Loop: **hypothesis -> experiment -> inspect -> finding -> decision(continue|conclude) -> critic attack -> critique(accept|challenge)**.
The campaign ends when the last decision is `conclude` and the last critique is `accept`, **and** the critic
started at least one run, check or rerun that round (logged in `ledger/audit.jsonl`); otherwise the round limit ends it.
Output: `ledger/campaigns/<id>/report.json` (schema `report/0.1`, primary) and `report.md` rendered from it
(claims and status, notes in order, runs, per-turn summaries). Status model, human review and the report format:
[status.md](status.md).

## Structured claims and evidence

Every hypothesis, finding and critique carries a short structured `claim` instead of prose (`text` is optional, <= 800 characters):

| note | claim fields |
|---|---|
| hypothesis | `statement`, `scope`, `refuted_if`, optional `revises` (the claim version it replaces) |
| finding | `statement`, `scope`, `result` (supports/refutes/inconclusive), optional `limits`, `about`, `sources`; `counterexample` (a run id) required iff `result=refutes` |
| critique accept | `statement`, `scope`, optional `limits` (the strongest claim actually supported), `about` |
| critique challenge | `objection`, optional `resolve_by`, `about`, `counterexample` |
| attempt | `approach`, `stage` (explore/falsify/prove/literature), `outcome` (failed/inconclusive/abandoned), `reason`, optional `about` |

Each hypothesis is a **claim** (versions linked by `revises`) with a status derived by the server: `open`, `refuted`
(a named counterexample run), `numerically-supported` (a supporting finding or critic accept citing runs; not a proof),
`proven_AI` (a proof record via `submit_proof`; not human-reviewed); `proven` is set only by the human with
`research-claims`. Status vocabulary (proved, established, rigorous, ...) is rejected in agent-written claims and text.
See [status.md](status.md).

The **server derives the evidence grade** of findings and critiques from the runs they cite; agents cannot assert it:
`run` < `reproduced` (a passing rerun) < `cross-checked` (passing `compare_runs` across different precisions, or a run by
the other role) < `falsification-tested` (a `falsify` check on the cited runs). `sources` are arXiv ids that must exist.
This is token-neutral or cheaper: the claim fields replace prose and the evidence is not restated. Each new campaign's
first note carries a brief of earlier claims with their status, open objections and failed attempts (`--no-prior` turns it
off, `--prior-limit` sizes it); `read_claims` gives the full picture. The report renders claims, statuses, grades and a
falsification line.

## Usage accounting

The MCP server logs every tool call (argument and result sizes) to `ledger/calls.jsonl`; the driver logs each turn to
`ledger/campaigns/<id>/turns.jsonl`. `research-usage [--campaign ID] [--hermes]` summarizes them (estimated tokens = chars/3.5)
and the campaign report has a Usage section. `--hermes` tries to read token counts from Hermes' session database
(`state.db`); the schema is unknown to me, so it may report nothing. Exact model tokens are best read from the provider console.

## Resuming

`research-loop --resume <campaign-id>` continues an interrupted campaign from the notes: after a researcher decision without
a critique it starts with the critic, otherwise with the next researcher turn. Rounds and budget default to the campaign's own.

## Trust boundary

- Profiles disable every built-in Hermes toolset (`agent.disabled_toolsets` lists them all, `platform_toolsets.cli: []`); the only actions are the MCP tools (named `mcp__research__<tool>` in Hermes). `research-doctor --ask` checks the tool list the model actually sees and fails if a built-in such as `terminal` or `read_file` is exposed. Do not run a campaign until it is clean.
- `hermes -z` (one-shot) runs with approvals bypassed (`HERMES_YOLO_MODE=1`), so the toolset lock-down is the only barrier to host access, not a per-command approval. Exit code 2 means the turn failed or stopped partway; Hermes hides the reason in `-z` mode, look in `~/.hermes/profiles/<role>/logs/agent.log`.
- Agents can write only `workspace/agent/experiments/*.jl` and `workspace/agent/statements/*.jl`, and only in the
  lab worktree (never on your branch). Checkers, the library, specs, grades and run records are not writable.
- Agent runs can read only `workspace/` (without other agent files and the human test statements in
  `workspace/statements/`), `checkers/`, and their own script (run spec `code.paths`). `eval/` answer keys, `docs/`
  and earlier campaigns' scripts are not extracted into the run directory.
- Evaluation campaigns (`EV-...`, started by `research-eval`) are sealed: the agents see only that campaign's runs,
  notes and claims, and those claims stay out of the normal registry, brief and review queue ([evaluation.md](evaluation.md)).
- Runs execute in Docker (no network, CPU/RAM/walltime capped server-side: 4 threads, 6 GB, 1800 s, 64..4096 bits).
- Per-campaign run budget (`--max-runs`, default 30; reruns count).
- Roles are enforced in the server: the Researcher cannot write critiques, the Critic cannot write decisions or proofs.
- No MCP tool can set a human status; `proven` is written only by `research-claims set` (human, in a shell).
- Tool output, artifacts and notes are treated as data by the profile instructions.
- The lab merges your **committed** HEAD before every run. Commit library/checker changes before a campaign.

## Setup (WSL2)

1. `pip install -e harness -e agent` inside your venv (needs `mcp<2`; the SDK v2 renamed FastMCP).
2. Install Hermes: `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`, `source ~/.bashrc`,
   then `hermes setup` (provider and API key).
3. `research-install-profiles --root .` creates the `researcher` and `critic` profiles, writes their `SOUL.md`
   and merges the MCP server into each `config.yaml` (backup `config.yaml.bak`).
4. Set a model in each profile (`hermes -p researcher setup`). The installer sets `agent.disabled_toolsets` and
   `platform_toolsets.cli: []`, with every known built-in toolset (including `file`, which provides read_file/write_file/patch/search_files) disabled. If you use `hermes tools` afterwards, re-run the installer
   or check with the doctor: it fails a profile that enables terminal, file, code_execution, browser, delegation, etc.
5. **`research-doctor --root . --ask`** checks both profiles (config, SOUL.md, starts the MCP server exactly as
   configured and lists its tools, Docker, Julia image, committed Manifest, log tails) and asks each profile which
   tools it can call. Send its output when something fails. The model's own tool list is only self-report (a small model may misreport), so `--probe` gives ground truth: it asks each profile to create a temp file with any built-in tool and checks whether the file exists. `--compare` shows how the two profiles differ (env values redacted). Smoke test by hand: `hermes -p researcher -z "Call list_runs and read_notes and print what they return."`
6. Campaign: `research-loop --question-file agent/questions/de_rate.md --rounds 3 --max-runs 20`
   (env `RESEARCH_HERMES_CMD` overrides the Hermes command; JSON list with `{profile}` and `{prompt}`).
7. **How long is too long?** `research-loop` prints a line when each turn starts/finishes and a heartbeat every 60 s
   (turn time, notes, runs, seconds since the ledger last changed) and warns after 10 minutes without ledger activity.
   Typical: a Hermes turn takes 2-10 min (many LLM calls plus 5-60 s per experiment); a 2-round campaign 10-40 min.
   Suspect a hang at 10+ min with no new note/run; look at `docker ps` (a container `rh-R-...` is an experiment),
   `tail -f ~/.hermes/profiles/<role>/logs/agent.log`, and `ls -t ledger/notes ledger/runs | head`.
   Hard limits: experiment walltime (default 600 s, max 1800), Hermes turn `--turn-timeout` (default 1800 s, then killed),
   `agent.max_turns`. Ctrl-C is safe: notes and runs stay in the ledger.
8. Records are git-ignored (`ledger/runs`, `notes`, `campaigns`, `status`, `proofs`, `audit.jsonl`). Keep one with
   `git add -f`. Human reviews in `ledger/status/` cannot be regenerated: back up `ledger/` as a unit.
9. **After pulling a new version**: `pip install -e agent` (new commands such as `research-claims`) and re-run
   `research-install-profiles --root .` (tool list and `SOUL.md` change between versions), then `research-doctor --root . --ask`.

## What is verified and what is not

Verified in the development sandbox (101 agent tests, 24 harness tests, all with fake Julia and no LLM):
- Tool behaviour, caps, budget, role checks, path/ID validation, lab isolation (agent code never lands on your branch).
- The real MCP stdio protocol with the official Python client (tool list, descriptions, calls, error results).
- Campaign logic with a scripted agent standing in for Hermes: stop on accept, round limit, ignoring an
  unearned accept, agent-failure handling, report rendering.
- Profile installer merge behaviour.
- Status model: derivation, proof binding, counterexamples, revisions and stale flags, human override and append-only
  history, the glossary, the JSON report against its schema, the `research-claims` CLI, and that no MCP tool reaches
  `proven`.
- Evaluation: run visibility (`code.paths`), sealed `EV-` campaigns, question validation, scoring with scripted good
  and bad agents (a false proof is a critical failure), human grades and overrides, summaries, the `research-eval` CLI.

**Not verified** (needs your machine):
- Hermes itself. I took these from Hermes' public docs, summarized by a fetch tool, and could not run Hermes:
  `hermes -p <profile> -z "<prompt>"` for one-shot turns, `~/.hermes/profiles/<name>/{config.yaml,SOUL.md}`,
  `mcp_servers.<name>.{command,args,env,timeout,connect_timeout,tools.include}`, tool names `mcp_<server>_<tool>`,
  `agent.max_turns`, `agent.disabled_toolsets`, and the toolset names to disable. Check each in step 4 and 5.
- Whether Hermes passes the profile's `env` to the MCP subprocess, and honours the 1900 s tool timeout.
- `library/Prelude.jl` and agent-written Julia in the real container (the fake Julia only checks the contract).
- Whether a real model follows the profile instructions well enough to finish a round.

## Known limits

- `submit_*` block until the run ends (cap 30 min). No async polling yet.
- One campaign at a time (`ledger/campaigns/CURRENT`).
- Notes are free text: the driver reads only `status` fields, not the content.
- The Critic's attack is checked for existence, not quality.
- No `interval_certify`, no proof checking (`proven_AI` is stored text, not verified), no proof-stage workflow or
  review pass, no novelty search.

## Decisions and open items

- **Golden questions:** five dev questions with verified keys and the `research-eval` runner: [evaluation.md](evaluation.md). The owner's own (hold-out) questions are still to be written.
- **Server-enforced gates:** a `finding` must cite at least one run; `submit_experiment`/`submit_check` need a researcher `hypothesis` note since the last `finding` (reruns and the critic are exempt); a `critique` must cite at least one run from the current campaign; an "accept" only ends a campaign if the critic started a run, check or rerun that round.
- **Compression:** explicitly off in both profiles (Hermes' default is on at 50% of the context window). Summaries paraphrase run ids and digits and change the cached prefix; the ledger is the durable memory instead. Enable with `research-install-profiles --compression` only if turns approach half the context window.
- **Turn limits:** `agent.max_turns` 60 is a safety net only (Hermes' own default is unlimited because caps caused silent mid-task truncation); `agent.run_budget_seconds` 1200 makes Hermes ask the agent to wrap up at 80% of that; the driver kills a turn at 1800 s.
- **Literature:** arXiv only (the `arxiv` tool and verified `claim.sources`).
- **Julia packages:** only SpecialFunctions plus the standard library for now; the run sandbox has no network, so a missing package shows up as `Package X not found`. Add packages on the host and commit `Manifest.toml`, e.g.
  `docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -e JULIA_DEPOT_PATH=/tmp/depot -v "$PWD":/w -w /w julia:1.11.3 julia --project=workspace/envs/main -e 'using Pkg; Pkg.add(["QuadGK","Optim"])'`.
- **Where the profile text lives:** the source of truth is `agent/src/researchagent/profiles/*_SOUL.md`; `research-install-profiles` overwrites `~/.hermes/profiles/<role>/SOUL.md` each time, so edit the repo copy.
- **Budget:** failed runs are refunded from the run budget but each role may fail at most 3 runs per campaign; the critic keeps 40% of the budget in reserve.
- **Not built:** claim links beyond `about`/`revises`, literature beyond arXiv, `interval_certify`.

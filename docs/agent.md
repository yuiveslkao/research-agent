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
| `add_note`, `read_notes` | both | Shared notes. Researcher writes question/hypothesis/finding/decision; Critic writes critique |

Loop: **hypothesis -> experiment -> inspect -> finding -> decision(continue|conclude) -> critic attack -> critique(accept|challenge)**.
The campaign ends when the last decision is `conclude` and the last critique is `accept`, **and** the critic
started at least one run, check or rerun that round (logged in `ledger/audit.jsonl`); otherwise the round limit ends it.
Output: `ledger/campaigns/<id>/report.md` (question, notes in order, runs, per-turn summaries).

## Trust boundary

- Profiles disable every built-in Hermes toolset (`agent.disabled_toolsets` lists them all, `platform_toolsets.cli: []`); the only actions are the MCP tools (named `mcp__research__<tool>` in Hermes). `research-doctor --ask` checks the tool list the model actually sees and fails if a built-in such as `terminal` or `read_file` is exposed. Do not run a campaign until it is clean.
- `hermes -z` (one-shot) runs with approvals bypassed (`HERMES_YOLO_MODE=1`), so the toolset lock-down is the only barrier to host access, not a per-command approval. Exit code 2 means the turn failed or stopped partway; Hermes hides the reason in `-z` mode, look in `~/.hermes/profiles/<role>/logs/agent.log`.
- Agents can write only `workspace/agent/experiments/*.jl` and `workspace/agent/statements/*.jl`, and only in the
  lab worktree (never on your branch). Checkers, the library, specs, grades and run records are not writable.
- Runs execute in Docker (no network, CPU/RAM/walltime capped server-side: 4 threads, 6 GB, 1800 s, 64..4096 bits).
- Per-campaign run budget (`--max-runs`, default 30; reruns count).
- Roles are enforced in the server: the Researcher cannot write critiques, the Critic cannot write decisions.
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
8. Records are git-ignored (`ledger/runs`, `notes`, `campaigns`, `audit.jsonl`). Keep one with `git add -f`.

## What is verified and what is not

Verified in the development sandbox (23 agent tests, 19 harness tests, all with fake Julia and no LLM):
- Tool behaviour, caps, budget, role checks, path/ID validation, lab isolation (agent code never lands on your branch).
- The real MCP stdio protocol with the official Python client (tool list, descriptions, calls, error results).
- Campaign logic with a scripted agent standing in for Hermes: stop on accept, round limit, ignoring an
  unearned accept, agent-failure handling, report rendering.
- Profile installer merge behaviour.

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
- No literature tool, no claim/grade ledger, no `interval_certify`.

## Decisions and open items

- **Golden questions:** to be written by the owner; see [golden_questions.md](golden_questions.md). Until then, accuracy is judged by reading campaign reports.
- **Server-enforced gates:** a `finding` must cite at least one run; `submit_experiment`/`submit_check` need a researcher `hypothesis` note since the last `finding` (reruns and the critic are exempt); a `critique` must cite at least one run from the current campaign; an "accept" only ends a campaign if the critic started a run, check or rerun that round.
- **Compression:** explicitly off in both profiles (Hermes' default is on at 50% of the context window). Summaries paraphrase run ids and digits and change the cached prefix; the ledger is the durable memory instead. Enable with `research-install-profiles --compression` only if turns approach half the context window.
- **Turn limits:** `agent.max_turns` 60 is a safety net only (Hermes' own default is unlimited because caps caused silent mid-task truncation); `agent.run_budget_seconds` 1200 makes Hermes ask the agent to wrap up at 80% of that; the driver kills a turn at 1800 s.
- **Literature:** arXiv only, when it is added (not built yet).
- **Julia packages:** only SpecialFunctions plus the standard library for now; the run sandbox has no network, so a missing package shows up as `Package X not found`. Add packages on the host and commit `Manifest.toml`, e.g.
  `docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -e JULIA_DEPOT_PATH=/tmp/depot -v "$PWD":/w -w /w julia:1.11.3 julia --project=workspace/envs/main -e 'using Pkg; Pkg.add(["QuadGK","Optim"])'`.
- **Where the profile text lives:** the source of truth is `agent/src/researchagent/profiles/*_SOUL.md`; `research-install-profiles` overwrites `~/.hermes/profiles/<role>/SOUL.md` each time, so edit the repo copy.
- **Not built:** `--resume` for interrupted campaigns, literature tool, claim/grade ledger, brief generator.

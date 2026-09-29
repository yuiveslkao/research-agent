# Phase 0: DE/Sinc benchmark

## Settled decisions

| Area | Decision |
|---|---|
| Research code | Julia, generic over the number type (Float64 / BigFloat / later intervals) |
| Harness, MCP, ledger | Python, stdlib only in Phase 0 |
| Isolation | Docker (no network at run time, CPU/memory limits, read-only code) |
| Host | Windows + WSL2; keep the repo inside the WSL filesystem, not under `/mnt/c` |
| Compute | Single workstation, CPU only in Phase 0 (`gpu = false` is enforced by the harness) |
| Manifest | Language-agnostic TOML spec, sealed spec/result pair (schema `run/0.1`) |
| Executor | `submit / status / fetch` interface; `DockerExecutor` now, a TSUBAME executor later |
| Benchmark | DE/Sinc pipeline is kept as the harness self-test. The research benchmark is Example 1 of arXiv:2305.05127v4 ([docs/benchmark_ex1.md](benchmark_ex1.md)), since it is the only real result available. Deferred: conformal maps |
| Not in Phase 0 | Claims/links store, MCP server, agents, Lean, interval certification, GPU, scheduler |

## The four stages

| Stage | Command | Passes when |
|---|---|---|
| 0. Port check | `rh submit specs/known_answer.toml` | reported `check.outcome = "pass"`: the Julia port matches the independent mpmath reference to 25 digits |
| 1. Reproduction | `rh submit specs/de_rate.toml` | `errors.csv` + `reported.json`; you (or the agent) estimate the rate and compare with the known result |
| 2. High-precision verification | `rh submit specs/de_rate_1024.toml`, then `rh compare <512-run> <1024-run> --rel-tol 1e-20` | the two precisions agree, and `saturated` is false wherever a rate is claimed |
| 2b. Statement grid | `rh submit specs/check_highprec_C0001.toml` | `check.outcome = "pass"` (statement holds on the grid at 512 bits) |
| 3. Rerun | `rh rerun <run-id>` | check record `outcome = "pass"` under the spec's determinism policy |
| 4. Falsification | `rh submit specs/check_falsify_C0001.toml` | `outcome = "survived"` (with effort recorded) or `"violated"` with the counterexample θ |

`C0001` is an **illustrative placeholder**, not a theorem: its bound shape was picked so the
pipeline has something to falsify. In an mpmath prototype, a nearby constant (`c = 2πd`) is violated
by a factor of about 500 on `d ≤ 1` and about 1e10 near `d = 3/2`, while `c = πd` held on the
`d ≤ 1` grid. Replace `statements/C0001.jl` with a result of yours before drawing conclusions.

## Setup (WSL2)

1. **Clone the branch** (the work is on `claude/laughing-pascal-7zteju`; the default branch may be empty).
   GitHub no longer accepts passwords. Authenticate with `gh auth login` (browser flow) or an SSH key, then
   `git clone -b claude/laughing-pascal-7zteju https://github.com/yuiveslkao/research-agent.git`.
   Never paste tokens into chat or into a shared terminal log.
2. **Docker Desktop memory.** The WSL2 VM defaults to about half of RAM (7.7 GiB seen here). Specs use `mem_gb = 6`.
   To allow more, create `%UserProfile%\.wslconfig` with `[wsl2]` and `memory=12GB`, then `wsl --shutdown`.
3. **Python** (3.11+; 3.14 is untested here): `python3 -m venv .venv && . .venv/bin/activate`, then
   `pip install -e "harness[dev]"` and `pytest harness` (18 tests).
4. **Julia environment without installing Julia** (the Manifest then matches the container that will run it):
   ```
   docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -e JULIA_DEPOT_PATH=/tmp/depot \
     -v "$PWD":/w -w /w julia:1.11.3 \
     julia --project=workspace/envs/main -e 'using Pkg; Pkg.add("SpecialFunctions")'
   git add workspace/envs/main && git commit -m "Pin Julia environment"
   ```
   The harness refuses to run without a committed `Manifest.toml`. Host Julia is optional (`juliaup`, not snap).
5. Run from the repo root: `rh submit specs/known_answer.toml`, then `rh show <run-id>`.

The harness executes the **committed** tree (`git archive`), never the working tree. Commit before submitting.

## What has and hasn't been verified

Verified in the development sandbox:
- Harness: 18 pytest tests (spec validation and hashing, sealed records, committed-tree execution, failure, timeout, missing Manifest, rerun bitwise and tolerance, compare, Docker command shape).
- The DE-quadrature formula, mesh rule and reference errors, prototyped in mpmath at 250 digits. `workspace/library/testdata/de_reference.toml` comes from there.

**Not run** (no Julia and no Docker daemon in the sandbox):
- All Julia files: `DESinc.jl`, the scripts, `C0001.jl`, and the checkers. Expect small syntax or package issues on first run. `known_answer` is the first thing to run.
- `DockerExecutor` against a real daemon. Only the constructed command lines are tested.
- Assumptions to confirm: `SpecialFunctions.gamma` supports `BigFloat`, and the official `julia` image has no entrypoint (the harness passes `julia ...` as the command).

## Conventions

- Real-valued parameters are decimal **strings** in `[params]`. TOML floats are Float64 values.
- High-precision results are decimal strings in `reported.json`, never JSON floats.
- Scripts must not write timestamps or other nondeterminism, or `bitwise` reruns will fail.
- `harness.*` in `result.json` is trusted; `reported` is whatever the job wrote.
- Statements: a module with `DOMAIN`, `margin(θ, T)`, optional `relmargin(θ, T)`. Generic over `T<:Real`, no Float64 literals, self-contained (loads its own library files).
- `checkers/` is human-owned. Agents write to `workspace/` only.
- `falsify` evaluates at BigFloat only. Float64 saturates near 1e-16, below the bounds being tested.
- `survived` records effort and seed and is **not** a proof.

## Exit criteria for Phase 0

1. `known_answer` passes and a stage-1 result is reproduced from its manifest via `rerun`.
2. The 512 vs 1024-bit comparison agrees, and saturation is flagged correctly.
3. Falsification finds a planted flaw (change a constant in `C0001` and confirm `violated`).
4. You could resume the study from `ledger/` and `specs/` alone.
5. A log of anything you needed to refer to that is not a run, check, statement, or library entry.

## Deferred (add only when Phase 0 shows the need)

Claims/links/notes store and MCP server, review packets, `interval_certify`, `crossimpl`,
`fit_robustness`, GPU runs, TSUBAME executor (needs an offline environment `prepare` step and a portable
`JULIA_CPU_TARGET`), and the point-configuration and conformal-map benchmarks.

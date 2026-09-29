# research-agent

Multi-agent system for mathematical numerical-analysis research (DE/Sinc methods, point
configurations, conformal maps). Design notes and the Phase 0 plan: [docs/phase0.md](docs/phase0.md).

| Path | What |
|---|---|
| `harness/` | Python run harness (`rh` CLI): sealed spec/result records, executors, `rerun`/`compare` |
| `workspace/` | Julia research code: `library/`, `scripts/`, `statements/`, `envs/main/` |
| `checkers/` | Human-owned Julia checkers (`highprec`, `falsify`); agents must not write here |
| `specs/` | Example RunSpecs for the Phase 0 DE/Sinc benchmark |
| `ledger/` | `runs/` (sealed records) and `checks/`; committed |
| `store/`, `work/` | Content-addressed outputs and scratch; git-ignored |

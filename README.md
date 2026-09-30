# research-agent

Multi-agent system for mathematical numerical-analysis research (DE/Sinc methods, point
configurations, conformal maps). Design notes and the Phase 0 plan: [docs/phase0.md](docs/phase0.md).

| Path | What |
|---|---|
| `harness/` | Python run harness (`rh` CLI): sealed spec/result records, executors, `rerun`/`compare` |
| `workspace/` | Julia research code: `library/`, `scripts/`, `statements/`, `envs/main/` |
| `checkers/` | Human-owned Julia checkers (`highprec`, `falsify`); agents must not write here |
| `agent/` | Research MCP server, Hermes profiles (Researcher, Critic), campaign loop ([docs/agent.md](docs/agent.md)) |
| `specs/` | Example RunSpecs for the Phase 0 DE/Sinc benchmark |
| `eval/` | Golden questions (`questions/`) and answer-key verification scripts (`verify/`); never visible to agent runs |
| `ledger/` | `runs/` (sealed records) and `checks/`; committed |
| `store/`, `work/` | Content-addressed outputs and scratch; git-ignored |

Claim statuses (`open`, `refuted`, `numerically-supported`, `proven_AI`, human-only `proven`), human review with
`research-claims`, and the JSON report: [docs/status.md](docs/status.md). Evaluation with golden questions
(`research-eval`, `eval/`): [docs/evaluation.md](docs/evaluation.md). Start with [docs/HANDOFF.md](docs/HANDOFF.md).

Open items and decisions: [docs/agent.md](docs/agent.md#decisions-and-open-items). Golden questions (to be written): [docs/golden_questions.md](docs/golden_questions.md).

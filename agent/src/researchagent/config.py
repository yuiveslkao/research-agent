"""Settings and shared context for the research tools."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from rharness.executor import DockerExecutor, Executor, LocalProcessExecutor
from rharness.runner import Paths

from .lab import Lab

# Hard caps enforced server-side, whatever the agent asks for.
MAX_THREADS = 4
MAX_MEM_GB = 6
MAX_WALLTIME_S = 1800
PRECISION_RANGE = (64, 4096)


@dataclass(frozen=True)
class Settings:
    root: Path
    role: str = "researcher"          # researcher | critic | human | driver
    executor: str = "docker"          # docker | local (local = no isolation, tests only)
    julia_cmd: str = "julia"
    julia_version: str = "1.11.3"     # must match the pinned Manifest and an existing julia:<tag> image
    max_runs: int = 40                # fallback when no campaign is active

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        return cls(
            root=Path(env["RESEARCH_ROOT"]).resolve(),
            role=env.get("RESEARCH_ROLE", "researcher"),
            executor=env.get("RESEARCH_EXECUTOR", "docker"),
            julia_cmd=env.get("RESEARCH_JULIA_CMD", "julia"),
            julia_version=env.get("RESEARCH_JULIA_VERSION", "1.11.3"),
            max_runs=int(env.get("RESEARCH_MAX_RUNS", "40")),
        )


def current_campaign(ledger: Path, default_max_runs: int) -> dict:
    """The driver writes ledger/campaigns/CURRENT; tools read it on every call."""
    f = ledger / "campaigns" / "CURRENT"
    if f.exists():
        return json.loads(f.read_text())
    return {"id": "adhoc", "max_runs": default_max_runs}


def set_current_campaign(ledger: Path, campaign_id: str, max_runs: int) -> None:
    d = ledger / "campaigns"
    d.mkdir(parents=True, exist_ok=True)
    (d / "CURRENT").write_text(json.dumps({"id": campaign_id, "max_runs": max_runs}))


def make_executor(name: str) -> Executor:
    if name == "docker":
        return DockerExecutor()
    if name == "local":
        return LocalProcessExecutor()
    raise ValueError(f"unknown executor {name!r}")


@dataclass
class Ctx:
    settings: Settings
    executor: Executor | None = None
    lab: Lab = field(init=False)
    paths: Paths = field(init=False)

    def __post_init__(self):
        s = self.settings
        self.lab = Lab(s.root)
        self.paths = Paths(repo=self.lab.dir, ledger=s.root / "ledger",
                           store=s.root / "store", work=s.root / "work")
        if self.executor is None:
            self.executor = make_executor(s.executor)

    @property
    def campaign(self) -> dict:
        return current_campaign(self.paths.ledger, self.settings.max_runs)

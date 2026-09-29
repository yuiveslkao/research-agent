"""Create the `researcher` and `critic` Hermes profiles and wire the research MCP server into them.

Writes ~/.hermes/profiles/<role>/SOUL.md and merges into its config.yaml (backup: config.yaml.bak):
  mcp_servers.research (command, env, timeouts, tool allowlist), agent.max_turns, agent.disabled_toolsets.
Hermes' own model/provider settings in config.yaml are left untouched. Set the model with
`hermes -p <role> config set model.default <model>` or `hermes -p <role> setup`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import yaml

ROLES = ("researcher", "critic")
COMMON_TOOLS = ["submit_experiment", "submit_check", "inspect_run", "read_artifact", "list_runs",
                "rerun_run", "compare_runs", "add_note", "read_notes"]
# Built-in toolsets that would bypass the sandbox or add untrusted input. Extend after `hermes tools`.
DISABLED_TOOLSETS = ["terminal", "execute_code", "memory", "web"]
TOOL_TIMEOUT_S = 1900          # must exceed MAX_WALLTIME_S (1800) since submit_* block


def mcp_entry(role: str, root: Path, executor: str) -> dict:
    env = {"RESEARCH_ROOT": str(root), "RESEARCH_ROLE": role, "RESEARCH_EXECUTOR": executor,
           "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    return {"command": sys.executable, "args": ["-m", "researchagent.mcp_server"], "env": env,
            "timeout": TOOL_TIMEOUT_S, "connect_timeout": 30,
            "tools": {"include": list(COMMON_TOOLS)}}


def merge_config(cfg: dict, role: str, root: Path, executor: str, max_turns: int) -> dict:
    cfg = dict(cfg or {})
    cfg.setdefault("mcp_servers", {})["research"] = mcp_entry(role, root, executor)
    agent = cfg.setdefault("agent", {})
    agent["max_turns"] = max_turns
    agent["disabled_toolsets"] = sorted(set(agent.get("disabled_toolsets") or []) | set(DISABLED_TOOLSETS))
    return cfg


def install(role: str, root: Path, hermes_home: Path, executor: str = "docker", max_turns: int = 60,
            create: bool = True) -> Path:
    pdir = hermes_home / "profiles" / role
    if not pdir.exists() and create:
        subprocess.run(["hermes", "profile", "create", role], check=True,
                       env={**os.environ, "HERMES_HOME": str(hermes_home)})
    pdir.mkdir(parents=True, exist_ok=True)
    soul = resources.files("researchagent").joinpath("profiles", f"{role}_SOUL.md").read_text(encoding="utf-8")
    (pdir / "SOUL.md").write_text(soul, encoding="utf-8")
    cfg_path = pdir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    if cfg_path.exists():
        shutil.copyfile(cfg_path, cfg_path.with_suffix(".yaml.bak"))
    cfg_path.write_text(yaml.safe_dump(merge_config(cfg or {}, role, root, executor, max_turns),
                                       sort_keys=False), encoding="utf-8")
    return pdir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-install-profiles")
    ap.add_argument("--root", default=".", help="repository root used as RESEARCH_ROOT")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--executor", choices=["docker", "local"], default="docker")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--no-create", action="store_true", help="do not call `hermes profile create`")
    a = ap.parse_args(argv)
    for role in ROLES:
        p = install(role, Path(a.root).resolve(), Path(a.hermes_home), a.executor, a.max_turns, not a.no_create)
        print(f"installed profile {role}: {p}")
    print("Next: set a model per profile (hermes -p researcher setup), then run `hermes tools` in each profile "
          "and disable every built-in toolset except the MCP tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

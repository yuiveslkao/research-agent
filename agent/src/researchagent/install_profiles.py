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
# Every built-in / plugin toolset name we know of. `agent.disabled_toolsets` is applied by Hermes as a strict
# subtraction after toolset resolution, so listing all of them leaves only the MCP tools. The `file` toolset
# is the one that provides read_file/write_file/patch/search_files (and terminal).
DISABLED_TOOLSETS = [
    "a2a", "bot_room", "browser", "clarify", "code_execution", "computer_use", "connections",
    "context_engine", "cronjob", "delegation", "desktop_ui", "discord", "discord_admin", "execute_code",
    "feishu_doc", "feishu_drive", "file", "homeassistant", "image_gen", "kanban", "memory", "project",
    "search", "session_search", "setup", "skills", "spotify", "stt", "terminal", "todo", "tts", "video",
    "video_gen", "vision", "web", "x_search", "yuanbao",
]
RUN_BUDGET_S = 1200            # Hermes injects a wrap-up notice at 80% of this; the driver kills a turn at 1800 s
TOOL_TIMEOUT_S = 1900          # must exceed MAX_WALLTIME_S (1800) since submit_* block


def mcp_entry(role: str, root: Path, executor: str) -> dict:
    env = {"RESEARCH_ROOT": str(root), "RESEARCH_ROLE": role, "RESEARCH_EXECUTOR": executor,
           "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    return {"command": sys.executable, "args": ["-m", "researchagent.mcp_server"], "env": env,
            "timeout": TOOL_TIMEOUT_S, "connect_timeout": 30,
            "tools": {"include": list(COMMON_TOOLS), "prompts": False, "resources": False}}


def merge_config(cfg: dict, role: str, root: Path, executor: str, max_turns: int,
                 run_budget_s: int | None = RUN_BUDGET_S, compression: bool = False) -> dict:
    cfg = dict(cfg or {})
    cfg.setdefault("mcp_servers", {})["research"] = mcp_entry(role, root, executor)
    agent = cfg.setdefault("agent", {})
    agent["max_turns"] = max_turns          # safety net only: Hermes notes hard caps can truncate mid-task
    if run_budget_s:
        agent["run_budget_seconds"] = run_budget_s
    # Explicit settings so both profiles behave identically (Hermes defaults: compression and memory ON).
    cfg["compression"] = {**(cfg.get("compression") or {}), "enabled": compression}
    cfg["memory"] = {**(cfg.get("memory") or {}), "memory_enabled": False, "user_profile_enabled": False}
    known = {t for k in ("known_builtin_toolsets", "known_plugin_toolsets")
             for v in (cfg.get(k) or {}).values() for t in (v or [])}
    agent["disabled_toolsets"] = sorted(set(agent.get("disabled_toolsets") or []) | set(DISABLED_TOOLSETS) | known)
    # `hermes tools` stores the enabled built-in toolsets per platform here; none should be enabled.
    cfg["platform_toolsets"] = {**(cfg.get("platform_toolsets") or {}), "cli": []}
    return cfg


def install(role: str, root: Path, hermes_home: Path, executor: str = "docker", max_turns: int = 60,
            create: bool = True, run_budget_s: int | None = RUN_BUDGET_S, compression: bool = False) -> Path:
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
    cfg_path.write_text(yaml.safe_dump(merge_config(cfg or {}, role, root, executor, max_turns, run_budget_s, compression),
                                       sort_keys=False), encoding="utf-8")
    return pdir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-install-profiles")
    ap.add_argument("--root", default=".", help="repository root used as RESEARCH_ROOT")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--executor", choices=["docker", "local"], default="docker")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--run-budget", type=int, default=RUN_BUDGET_S, help="agent.run_budget_seconds (0 = unset)")
    ap.add_argument("--compression", action="store_true", help="enable Hermes context compression (default: off)")
    ap.add_argument("--no-create", action="store_true", help="do not call `hermes profile create`")
    a = ap.parse_args(argv)
    for role in ROLES:
        p = install(role, Path(a.root).resolve(), Path(a.hermes_home), a.executor, a.max_turns, not a.no_create,
                    a.run_budget or None, a.compression)
        print(f"installed profile {role}: {p}")
    print("Next: set a model per profile (hermes -p researcher setup), then run `research-doctor --root . --ask`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

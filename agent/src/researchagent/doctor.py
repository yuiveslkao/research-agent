"""research-doctor: concise diagnostics for the Hermes + Research MCP setup.

Checks each profile's config, starts the MCP server exactly as the profile configures it and lists
its tools, checks Docker/Julia/git prerequisites, and shows log tails. Prints no environment values
except RESEARCH_*; log tails may contain other text, so read them before sharing.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import anyio
import yaml

from .install_profiles import COMMON_TOOLS, ROLES

# Built-in Hermes toolsets that would let a profile act outside the research tools.
RISKY = {"terminal", "file", "code_execution", "execute_code", "browser", "computer_use", "delegation",
         "cronjob", "web", "x_search", "search", "connections", "kanban", "desktop_ui", "project", "skills"}


@dataclass
class Finding:
    level: str   # ok | warn | fail | info
    msg: str


def _sh(argv, timeout=30, **kw):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))


def _tail(path: Path, n: int) -> str:
    if not path.exists():
        return "(missing)"
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:]) or "(empty)"


async def _probe_mcp(entry: dict, errlog) -> tuple[list[str], str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=entry["command"], args=entry.get("args", []),
                                   env=entry.get("env") or None)
    with anyio.fail_after(60):
        async with stdio_client(params, errlog=errlog) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                res = await s.call_tool("read_notes", {"limit": 1})
                return names, ("error" if res.isError else "ok")


def check_environment(root: Path, julia_version: str = "1.11.3") -> list[Finding]:
    f: list[Finding] = []
    head = _sh(["git", "-C", str(root), "rev-parse", "--short", "HEAD"])
    f.append(Finding("ok" if head.returncode == 0 else "fail", f"git HEAD: {head.stdout.strip() or head.stderr.strip()}"))
    man = _sh(["git", "-C", str(root), "cat-file", "-e", "HEAD:workspace/envs/main/Manifest.toml"])
    f.append(Finding("ok" if man.returncode == 0 else "fail",
                     "workspace/envs/main/Manifest.toml is committed" if man.returncode == 0
                     else "Manifest.toml not committed at HEAD (the lab merges HEAD; the harness needs it)"))
    dirty = _sh(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    if dirty:
        f.append(Finding("warn", "uncommitted changes to tracked files are invisible to agent runs (commit them)"))
    d = _sh(["docker", "info", "--format", "{{.ServerVersion}} mem={{.MemTotal}}"], timeout=20)
    f.append(Finding("ok" if d.returncode == 0 else "fail",
                     f"docker: {d.stdout.strip()}" if d.returncode == 0 else f"docker not reachable: {d.stderr.strip()[-200:]}"))
    img = _sh(["docker", "image", "inspect", f"julia:{julia_version}", "--format", "{{.Id}}"], timeout=20)
    f.append(Finding("ok" if img.returncode == 0 else "warn",
                     f"image julia:{julia_version} present" if img.returncode == 0
                     else f"image julia:{julia_version} not pulled yet (first run will pull it)"))
    f.append(Finding("info", f"lab worktree: {'present' if (root / '.rh' / 'lab' / '.git').exists() else 'not created yet'}"))
    return f


def check_profile(role: str, hermes_home: Path, root: Path, ask: bool = False, tail: int = 15,
                  hermes_cmd: str = "hermes") -> list[Finding]:
    f: list[Finding] = []
    pdir = hermes_home / "profiles" / role
    if not pdir.exists():
        return [Finding("fail", f"profile directory missing: {pdir} (run research-install-profiles)")]
    cfg_path = pdir / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    cfg = cfg or {}
    model = cfg.get("model") or {}
    f.append(Finding("info", f"model: {model.get('default')} (provider {model.get('provider')}); "
                             f"max_turns: {(cfg.get('agent') or {}).get('max_turns')}"))

    soul = pdir / "SOUL.md"
    packaged = resources.files("researchagent").joinpath("profiles", f"{role}_SOUL.md").read_bytes()
    if not soul.exists():
        f.append(Finding("fail", "SOUL.md missing"))
    elif hashlib.sha256(soul.read_bytes()).digest() != hashlib.sha256(packaged).digest():
        f.append(Finding("warn", "SOUL.md differs from the packaged version (edited or outdated)"))
    else:
        f.append(Finding("ok", "SOUL.md matches the packaged instructions"))

    entry = (cfg.get("mcp_servers") or {}).get("research")
    if not entry:
        f.append(Finding("fail", "mcp_servers.research is not configured"))
        return f
    env = entry.get("env") or {}
    if env.get("RESEARCH_ROLE") != role:
        f.append(Finding("fail", f"RESEARCH_ROLE is {env.get('RESEARCH_ROLE')!r}, expected {role!r}"))
    if Path(env.get("RESEARCH_ROOT", "")).resolve() != root.resolve():
        f.append(Finding("warn", f"RESEARCH_ROOT is {env.get('RESEARCH_ROOT')!r}, this check runs on {root}"))
    if not (Path(entry.get("command", "")).exists() and os.access(entry["command"], os.X_OK)):
        f.append(Finding("fail", f"MCP command not executable: {entry.get('command')}"))
    if (entry.get("timeout") or 0) < 1900:
        f.append(Finding("warn", f"MCP tool timeout is {entry.get('timeout')} s; submit_* can block up to 1800 s"))
    include = ((entry.get("tools") or {}).get("include")) or []
    if sorted(include) != sorted(COMMON_TOOLS):
        f.append(Finding("warn", f"tools.include differs from the expected list: {sorted(set(COMMON_TOOLS) ^ set(include))}"))

    enabled = {p: v for p, v in (cfg.get("platform_toolsets") or {}).items() if v}
    if not enabled:
        f.append(Finding("ok", "no built-in toolsets enabled (platform_toolsets empty)"))
    for plat, sets in enabled.items():
        bad = sorted(set(sets) & RISKY)
        f.append(Finding("fail" if bad else "warn",
                         f"platform_toolsets.{plat} enables built-ins {sorted(sets)}"
                         + (f"; RISKY (can bypass the sandbox): {bad}" if bad else "")))

    with tempfile.TemporaryFile("w+") as err:
        try:
            names, call = anyio.run(_probe_mcp, entry, err)
            missing = sorted(set(COMMON_TOOLS) - set(names))
            f.append(Finding("fail" if missing else "ok",
                             f"MCP server starts; tools: {len(names)}" + (f"; missing {missing}" if missing else "")))
            f.append(Finding("ok" if call == "ok" else "fail", f"read_notes call: {call}"))
        except Exception as e:  # noqa: BLE001 - report anything
            err.seek(0)
            f.append(Finding("fail", f"MCP server did not start or respond: {type(e).__name__}: {e}\n"
                                     f"server stderr:\n{err.read()[-1500:]}"))

    f.append(Finding("info", f"logs/mcp-stderr.log (last {tail}):\n{_tail(pdir / 'logs' / 'mcp-stderr.log', tail)}"))
    f.append(Finding("info", f"logs/errors.log (last {tail}):\n{_tail(pdir / 'logs' / 'errors.log', tail)}"))

    if ask:
        r = _sh([hermes_cmd, "-p", role, "-z",
                 "List the exact names of every tool you can call, one per line, and nothing else. "
                 "Do not call any tool."], timeout=240)
        f.append(Finding("info" if r.returncode == 0 else "fail",
                         f"hermes -p {role} -z (exit {r.returncode}):\n{(r.stdout or r.stderr).strip()[-2000:]}"))
    return f


def render(title: str, findings: list[Finding]) -> str:
    tag = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "    "}
    return f"== {title} ==\n" + "\n".join(f"[{tag[x.level]}] {x.msg}" for x in findings) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-doctor")
    ap.add_argument("--root", default=".")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--julia-version", default="1.11.3")
    ap.add_argument("--ask", action="store_true", help="also ask each profile to list its tools (one small LLM call each)")
    ap.add_argument("--tail", type=int, default=15)
    a = ap.parse_args(argv)
    root, home = Path(a.root).resolve(), Path(a.hermes_home)
    sections = [("environment", check_environment(root, a.julia_version))]
    sections += [(f"profile {r}", check_profile(r, home, root, a.ask, a.tail)) for r in ROLES]
    for title, fs in sections:
        print(render(title, fs))
    failed = sum(1 for _, fs in sections for x in fs if x.level == "fail")
    print(f"{failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

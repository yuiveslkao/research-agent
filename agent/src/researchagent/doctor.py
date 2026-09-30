"""research-doctor: concise diagnostics for the Hermes + Research MCP setup.

Checks each profile's config, starts the MCP server exactly as the profile configures it and lists
its tools, checks Docker/Julia/git prerequisites, and shows log tails. Prints no environment values
except RESEARCH_*; log tails may contain other text, so read them before sharing.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import anyio
import yaml

from .install_profiles import ROLES, tools_for
from .loop import HermesCLI

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
                  probe: bool = False, agent=None) -> list[Finding]:
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

    names = _env_names(pdir / ".env")
    f.append(Finding("info", f".env variable names: {sorted(names) or 'none'}"))
    if str(model.get("provider")).lower() == "anthropic":
        shapes = {v: _env_shape(pdir / ".env", v) for v in PROVIDER_KEYS["anthropic"]}
        key_ok = any(sh and sh[1] for sh in shapes.values())
        login = _has_login(pdir)
        for var, sh in shapes.items():
            if sh and sh[1]:
                f.append(Finding("ok", f"{var}: {sh[0]} characters, has the sk-ant- prefix and a plausible length"))
        if login and not key_ok:
            f.append(Finding("warn", "Anthropic access here is a Hermes LOGIN (auth.json), not an API key: calls draw on "
                                     "that account's plan/allowance. If it is a Claude subscription login, Anthropic's "
                                     "terms may not allow it outside Claude Code/Claude.ai; an API key from the Console "
                                     "avoids that (`hermes -p " + role + " setup`)."))
        for var, sh in shapes.items():
            if sh is None or key_ok:
                continue
            n, ok = sh
            if n == 0 and login:
                continue                                    # empty placeholder; the login is used
            f.append(Finding("fail" if n else "info" if login else "fail",
                             f"{var}: {n} characters, does NOT look like an Anthropic key (sk-ant- prefix, plausible length)"
                             + (f" -> HTTP 401 expected. Re-enter it: `hermes -p {role} setup` "
                                "(never type a menu number into a key prompt)" if not login or n else "")))
    wanted = PROVIDER_KEYS.get(str(model.get("provider")).lower())
    if wanted and not (set(wanted) & (names | set(os.environ))) and not _has_login(pdir):
        f.append(Finding("fail", f"provider {model.get('provider')!r} needs one of {wanted} in "
                                 f"{pdir / '.env'} (or the environment); none found. Run `hermes -p {role} setup`."))

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
    tcfg = entry.get("tools") or {}
    if tcfg.get("prompts") is not False or tcfg.get("resources") is not False:
        f.append(Finding("warn", "tools.prompts/tools.resources are not false: Hermes adds 4 unused utility tools "
                                 "(list/get prompts, list/read resources) to every request (re-run the installer)"))
    include = tcfg.get("include") or []
    if sorted(include) != sorted(tools_for(role)):
        f.append(Finding("warn", f"tools.include differs from the expected list: {sorted(set(tools_for(role)) ^ set(include))}"))

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
            missing = sorted(set(tools_for(role)) - set(names))
            f.append(Finding("fail" if missing else "ok",
                             f"MCP server starts; tools: {len(names)}" + (f"; missing {missing}" if missing else "")))
            f.append(Finding("ok" if call == "ok" else "fail", f"read_notes call: {call}"))
        except Exception as e:  # noqa: BLE001 - report anything
            err.seek(0)
            f.append(Finding("fail", f"MCP server did not start or respond: {type(e).__name__}: {e}\n"
                                     f"server stderr:\n{err.read()[-1500:]}"))

    f.append(Finding("info", f"logs/mcp-stderr.log (last {tail}):\n{_tail(pdir / 'logs' / 'mcp-stderr.log', tail)}"))
    f.append(Finding("info", f"logs/errors.log (last {tail}):\n{_tail(pdir / 'logs' / 'errors.log', tail)}"))

    if ask or probe:
        agent = agent or HermesCLI(timeout=240)
        try:
            agent.ask(role, "Reply with the single word OK and do not call any tool.")
        except Exception as e:  # noqa: BLE001
            f.append(Finding("fail", f"a basic Hermes turn for {role} fails, so --ask/--probe were skipped:\n{e}\n"
                                     f"Run it by hand to see the full output: "
                                     f"hermes -p {role} -z 'Reply OK'; echo exit=$?"))
            f.append(Finding("info", f"logs/agent.log (last {tail}; -z hides Hermes errors from stderr):\n"
                                     f"{_tail(pdir / 'logs' / 'agent.log', tail)}"))
            return f
    if ask:
        try:
            out = agent.ask(role, "List the exact names of every tool you can call, one per line, and nothing else. "
                                  "Do not call any tool.")
            f.append(Finding("info", f"tool list reported by {role}:\n{out[-2000:]}"))
            f += classify_tools(parse_tool_list(out))
        except Exception as e:  # noqa: BLE001
            f.append(Finding("fail", f"hermes turn for {role} failed: {e}"))
    if probe:
        target = Path(tempfile.gettempdir()) / f"rh_probe_{role}_{secrets.token_hex(4)}"
        try:
            reply = agent.ask(role, f"Create an empty file at exactly this path using whatever tool can do it "
                                    f"(a shell command such as touch, or a file-writing tool): {target}\n"
                                    "If you have no such tool, reply exactly NO_TOOL and do nothing else.")
            if target.exists():
                target.unlink()
                f.append(Finding("fail", f"PROBE: {role} created a file outside the MCP tools: built-in "
                                         "file/shell access is live"))
            else:
                f.append(Finding("ok", f"probe: {role} could not create a file (reply: {reply[-120:]!r})"))
        except Exception as e:  # noqa: BLE001
            f.append(Finding("fail", f"probe turn for {role} failed: {e}"))
        if any(x.level == "ok" and x.msg.startswith("probe:") for x in f):
            # Ground truth beats self-report: a small model may list tools it does not have.
            for x in f:
                if x.level == "fail" and x.msg.startswith("built-in tools exposed"):
                    x.level = "warn"
                    x.msg += "\n     (downgraded: the file probe could not use them, so this is probably the model misreporting)"
    turn_ok = (ask or probe) and not any(x.level == "fail" and x.msg.startswith(("hermes turn", "probe turn")) for x in f) \
        and any(x.msg.startswith(("tool list reported", "probe:")) for x in f)
    if turn_ok:
        # A real model turn completed, so whatever credential Hermes uses works; empty .env placeholders are harmless.
        for x in f:
            if x.level == "fail" and any(x.msg.startswith(v + ":") for v in PROVIDER_KEYS.get(str(model.get("provider")).lower(), ())):
                x.level = "info"
                x.msg += "\n     (downgraded: a live model turn succeeded, so another credential source is in use)"
    return f


META_TOOLS = {"tool_search", "tool_describe", "tool_call"}   # Hermes deferred-tool helpers


def parse_tool_list(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        t = line.strip().strip("-*`• ").strip()
        if t and " " not in t and len(t) < 80:
            names.append(t)
    return names


def classify_tools(names: list[str]) -> list[Finding]:
    """Anything that is not a research MCP tool or a Hermes meta tool is a built-in that should be off."""
    ours = [n for n in names if n.startswith("mcp__research__") or n.startswith("mcp_research_")]
    meta = [n for n in names if n in META_TOOLS]
    other = [n for n in names if n not in ours and n not in meta]
    f = []
    if other:
        f.append(Finding("fail", f"built-in tools exposed to the model: {other}  -> profile is NOT locked down"))
    else:
        f.append(Finding("ok", "model reports only research MCP tools and Hermes meta tools"))
    if not ours:
        f.append(Finding("fail", "model reports no research MCP tools"))
    if meta:
        f.append(Finding("warn", f"Hermes meta tools present {meta}: not verified that they cannot reach disabled tools"))
    return f


PROVIDER_KEYS = {"anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"]}


def _env_names(path: Path) -> set[str]:
    """Variable NAMES defined in a dotenv file (never the values)."""
    if not path.exists():
        return set()
    return {ln.split("=", 1)[0].replace("export ", "").strip() for ln in path.read_text().splitlines()
            if "=" in ln and not ln.lstrip().startswith("#")}


def _env_shape(path: Path, name: str) -> tuple[int, bool] | None:
    """(length, looks like an Anthropic key) of a variable's value; never returns the value itself."""
    if not path.exists():
        return None
    for ln in path.read_text().splitlines():
        if ln.lstrip().startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        if k.replace("export ", "").strip() == name:
            v = v.strip().strip("'\"")
            return len(v), v.startswith("sk-ant-") and len(v) >= 40
    return None


def _mentions_anthropic(obj) -> bool:
    """True if a parsed auth.json mentions anthropic as a key or provider name (values are never shown)."""
    if isinstance(obj, dict):
        return any("anthropic" in str(k).lower() or _mentions_anthropic(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(_mentions_anthropic(v) for v in obj)
    return isinstance(obj, str) and obj.strip().lower() == "anthropic"


def _has_login(pdir: Path) -> bool:
    """Hermes-managed login (e.g. `hermes auth add anthropic`) stored in the profile's auth.json."""
    f = pdir / "auth.json"
    try:
        return f.exists() and _mentions_anthropic(json.loads(f.read_text()))
    except (OSError, ValueError):
        return False


def _redacted_config(path: Path) -> list[str]:
    cfg = yaml.safe_load(path.read_text()) if path.exists() else {}
    for entry in ((cfg or {}).get("mcp_servers") or {}).values():
        if isinstance(entry.get("env"), dict):
            entry["env"] = {k: (v if k.startswith("RESEARCH_") else "<redacted>") for k, v in entry["env"].items()}
    return yaml.safe_dump(cfg or {}, sort_keys=True).splitlines()


def compare_profiles(hermes_home: Path, a: str = "researcher", b: str = "critic") -> list[Finding]:
    """What differs between two profiles: config (env values redacted), .env variable NAMES, top-level files."""
    pa, pb = hermes_home / "profiles" / a, hermes_home / "profiles" / b
    diff = list(difflib.unified_diff(_redacted_config(pa / "config.yaml"), _redacted_config(pb / "config.yaml"),
                                     a, b, lineterm="", n=0))
    envd = sorted(_env_names(pa / ".env") ^ _env_names(pb / ".env"))
    files = sorted({x.name for x in pa.iterdir()} ^ {x.name for x in pb.iterdir()}) if pa.exists() and pb.exists() else []
    return [Finding("info", "config.yaml diff:\n" + ("\n".join(diff) or "(identical apart from redactions)")),
            Finding("info", f".env variable names that differ: {envd or 'none'}"),
            Finding("info", f"top-level entries present in only one profile: {files or 'none'}")]


def render(title: str, findings: list[Finding]) -> str:
    tag = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "    "}
    return f"== {title} ==\n" + "\n".join(f"[{tag[x.level]}] {x.msg}" for x in findings) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="research-doctor")
    ap.add_argument("--root", default=".")
    ap.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    ap.add_argument("--julia-version", default="1.11.3")
    ap.add_argument("--ask", action="store_true", help="also ask each profile to list its tools (one small LLM call each)")
    ap.add_argument("--probe", action="store_true",
                    help="ground-truth test: ask each profile to create a temp file with any built-in tool")
    ap.add_argument("--compare", action="store_true", help="show how the researcher and critic profiles differ")
    ap.add_argument("--tail", type=int, default=15)
    a = ap.parse_args(argv)
    root, home = Path(a.root).resolve(), Path(a.hermes_home)
    sections = [("environment", check_environment(root, a.julia_version))]
    sections += [(f"profile {r}", check_profile(r, home, root, a.ask, a.tail, a.probe)) for r in ROLES]
    if a.compare:
        sections.append(("profile diff (researcher vs critic)", compare_profiles(home)))
    for title, fs in sections:
        print(render(title, fs))
    failed = sum(1 for _, fs in sections for x in fs if x.level == "fail")
    print(f"{failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

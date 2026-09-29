import sys

import yaml

from researchagent.doctor import (RISKY, check_environment, check_profile, classify_tools, compare_profiles,
                                  parse_tool_list, render)
from researchagent.install_profiles import install


def levels(fs):
    return [f.level for f in fs]


def setup_profile(tmp_path, root, role="researcher"):
    home = tmp_path / "hermes"
    install(role, root, home, executor="local", create=False)
    cfgp = home / "profiles" / role / "config.yaml"
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["mcp_servers"]["research"]["command"] = sys.executable          # the venv python running the tests
    cfg["mcp_servers"]["research"]["env"]["PATH"] = __import__("os").environ["PATH"]
    cfgp.write_text(yaml.safe_dump(cfg))
    return home, cfgp


def test_healthy_profile(root, tmp_path):
    home, _ = setup_profile(tmp_path, root)
    fs = check_profile("researcher", home, root)
    assert "fail" not in levels(fs), render("p", fs)
    assert any("MCP server starts; tools: 12" in f.msg for f in fs)
    assert any("no built-in toolsets enabled" in f.msg for f in fs)


def test_risky_toolsets_are_flagged(root, tmp_path):
    home, cfgp = setup_profile(tmp_path, root, "critic")
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["platform_toolsets"] = {"cli": ["browser", "code_execution", "todo"]}
    cfgp.write_text(yaml.safe_dump(cfg))
    fs = check_profile("critic", home, root)
    bad = [f for f in fs if f.level == "fail" and "RISKY" in f.msg]
    assert bad and "browser" in bad[0].msg and "code_execution" in bad[0].msg and "todo" not in bad[0].msg.split("RISKY")[1]
    assert {"browser", "code_execution", "delegation"} <= RISKY


def test_broken_mcp_command_reports_failure(root, tmp_path):
    home, cfgp = setup_profile(tmp_path, root)
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["mcp_servers"]["research"]["args"] = ["-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"]
    cfgp.write_text(yaml.safe_dump(cfg))
    fs = check_profile("researcher", home, root)
    fail = [f for f in fs if f.level == "fail" and "did not start" in f.msg]
    assert fail and "boom" in fail[0].msg


def test_missing_profile_and_wrong_role(root, tmp_path):
    assert levels(check_profile("critic", tmp_path / "nope", root)) == ["fail"]
    home, cfgp = setup_profile(tmp_path, root)
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["mcp_servers"]["research"]["env"]["RESEARCH_ROLE"] = "critic"
    cfgp.write_text(yaml.safe_dump(cfg))
    assert any(f.level == "fail" and "RESEARCH_ROLE" in f.msg for f in check_profile("researcher", home, root))


def test_environment_check_runs(root):
    fs = check_environment(root)
    assert fs[0].level == "ok" and "Manifest.toml" in fs[1].msg


def test_tool_list_classification():
    reported = "read_file\nwrite_file\nterminal\ntool_search\nmcp__research__add_note\n"
    fs = classify_tools(parse_tool_list(reported))
    assert any(f.level == "fail" and "read_file" in f.msg and "terminal" in f.msg for f in fs)
    assert any(f.level == "warn" and "tool_search" in f.msg for f in fs)
    clean = "- tool_search\n- `tool_call`\n- mcp__research__list_runs\n- mcp__research__read_notes\n"
    fs = classify_tools(parse_tool_list(clean))
    assert [f.level for f in fs] == ["ok", "warn"]
    assert classify_tools([])[1].level == "fail"


class LeakyAgent:
    """researcher can create files (built-in tools live), critic cannot."""
    def ask(self, profile, prompt):
        if "Create an empty file" in prompt:
            if profile == "researcher":
                import re
                from pathlib import Path
                Path(re.search(r"(/\S*rh_probe_\S+)", prompt).group(1)).touch()
                return "done"
            return "NO_TOOL"
        return "mcp__research__read_notes\ntool_search" if profile == "critic" else "terminal\nmcp__research__read_notes"


def test_probe_detects_live_builtin_access(root, tmp_path):
    home, _ = setup_profile(tmp_path, root, "researcher")
    fs = check_profile("researcher", home, root, probe=True, ask=True, agent=LeakyAgent())
    assert any(f.level == "fail" and f.msg.startswith("PROBE") for f in fs)
    assert any(f.level == "fail" and "terminal" in f.msg for f in fs)
    home2, _ = setup_profile(tmp_path, root, "critic")
    fs = check_profile("critic", home2, root, probe=True, agent=LeakyAgent())
    assert any(f.level == "ok" and "could not create a file" in f.msg for f in fs)


def test_compare_profiles_redacts_env_and_shows_differences(root, tmp_path):
    home, cfgp = setup_profile(tmp_path, root, "researcher")
    setup_profile(tmp_path, root, "critic")
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["tools_extra"] = ["think"]
    cfgp.write_text(yaml.safe_dump(cfg))
    (home / "profiles" / "researcher" / ".no-bundled-skills").write_text("")
    (home / "profiles" / "researcher" / ".env").write_text("SECRET_KEY=abc\n")
    fs = compare_profiles(home)
    text = "\n".join(f.msg for f in fs)
    assert "tools_extra" in text and ".no-bundled-skills" in text and "SECRET_KEY" in text
    assert "abc" not in text and "RESEARCH_ROLE" in text


class HallucinatingAgent:
    """Lists built-ins it does not have; the file probe shows it cannot use them."""
    def ask(self, profile, prompt):
        return "NO_TOOL" if "Create an empty file" in prompt else "terminal\nread_file\nmcp__research__read_notes"


def test_probe_downgrades_self_reported_builtins(root, tmp_path):
    home, _ = setup_profile(tmp_path, root, "researcher")
    fs = check_profile("researcher", home, root, ask=True, probe=True, agent=HallucinatingAgent())
    assert "fail" not in levels(fs)
    assert any(f.level == "warn" and "downgraded" in f.msg for f in fs)
    fs = check_profile("researcher", home, root, ask=True, agent=HallucinatingAgent())   # no probe: stays a failure
    assert any(f.level == "fail" and "built-in tools exposed" in f.msg for f in fs)


class DeadAgent:
    calls = 0
    def ask(self, profile, prompt):
        DeadAgent.calls += 1
        raise RuntimeError("hermes exited 2\n  stderr: (empty)\n  stdout: no API key")


def test_basic_turn_failure_short_circuits_ask_and_probe(root, tmp_path):
    home, _ = setup_profile(tmp_path, root)
    DeadAgent.calls = 0
    fs = check_profile("researcher", home, root, ask=True, probe=True, agent=DeadAgent())
    fails = [f for f in fs if f.level == "fail"]
    assert len(fails) == 1 and "basic Hermes turn" in fails[0].msg and "no API key" in fails[0].msg
    assert DeadAgent.calls == 1
    assert any("logs/agent.log" in f.msg for f in fs)


def test_missing_provider_credentials_are_flagged(root, tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    home, cfgp = setup_profile(tmp_path, root)
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["model"] = {"default": "claude-opus-5-5", "provider": "anthropic"}
    cfgp.write_text(yaml.safe_dump(cfg))
    fs = check_profile("researcher", home, root)
    assert any(f.level == "fail" and "ANTHROPIC_API_KEY" in f.msg for f in fs)
    (home / "profiles" / "researcher" / ".env").write_text("ANTHROPIC_API_KEY=sk-secret\n")
    fs = check_profile("researcher", home, root)
    assert not any(f.level == "fail" and "needs one of" in f.msg for f in fs)
    assert "sk-secret" not in "\n".join(f.msg for f in fs)


def test_malformed_anthropic_key_is_flagged_without_leaking_it(root, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    home, cfgp = setup_profile(tmp_path, root)
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["model"] = {"default": "claude-opus-5-5", "provider": "anthropic"}
    cfgp.write_text(yaml.safe_dump(cfg))
    env = home / "profiles" / "researcher" / ".env"
    env.write_text("ANTHROPIC_API_KEY=3\nANTHROPIC_TOKEN=\n")
    fs = check_profile("researcher", home, root)
    bad = [f for f in fs if f.level == "fail" and "ANTHROPIC_API_KEY" in f.msg and "does NOT" in f.msg]
    assert bad and "1 characters" in bad[0].msg
    env.write_text("ANTHROPIC_API_KEY=sk-ant-api03-" + "x" * 80 + "\n")
    fs = check_profile("researcher", home, root)
    assert any(f.level == "ok" and "ANTHROPIC_API_KEY" in f.msg for f in fs)
    assert "xxxx" not in "\n".join(f.msg for f in fs)


def test_empty_env_keys_with_hermes_login_is_a_warning_not_a_failure(root, tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    home, cfgp = setup_profile(tmp_path, root)
    cfg = yaml.safe_load(cfgp.read_text())
    cfg["model"] = {"default": "claude-opus-5-5", "provider": "anthropic"}
    cfgp.write_text(yaml.safe_dump(cfg))
    pdir = home / "profiles" / "researcher"
    (pdir / ".env").write_text("ANTHROPIC_API_KEY=\nANTHROPIC_TOKEN=\n")
    fs = check_profile("researcher", home, root)
    assert any(f.level == "fail" and "ANTHROPIC_API_KEY" in f.msg for f in fs)      # no credential at all
    (pdir / "auth.json").write_text('{"providers": {"anthropic": {"access_token": "SECRET-DO-NOT-PRINT"}}}')
    fs = check_profile("researcher", home, root)
    assert not any(f.level == "fail" and "ANTHROPIC" in f.msg for f in fs)
    warn = [f for f in fs if f.level == "warn" and "LOGIN" in f.msg]
    assert warn and "SECRET" not in "\n".join(f.msg for f in fs)

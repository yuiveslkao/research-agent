import sys

import yaml

from researchagent.doctor import RISKY, check_environment, check_profile, classify_tools, parse_tool_list, render
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
    assert any("MCP server starts; tools: 9" in f.msg for f in fs)
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

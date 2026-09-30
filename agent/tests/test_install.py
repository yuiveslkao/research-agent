import yaml

from researchagent.install_profiles import COMMON_TOOLS, DISABLED_TOOLSETS, TOOL_TIMEOUT_S, install, tools_for


def test_install_merges_without_clobbering(tmp_path):
    home = tmp_path / "hermes"
    pdir = home / "profiles" / "critic"
    pdir.mkdir(parents=True)
    (pdir / "config.yaml").write_text(yaml.safe_dump({
        "model": {"default": "some/model"}, "agent": {"disabled_toolsets": ["browser"], "x": 1},
        "known_builtin_toolsets": {"cli": ["brand_new_toolset"]},
        "mcp_servers": {"other": {"command": "true"}}}))
    install("critic", tmp_path / "repo", home, create=False)
    cfg = yaml.safe_load((pdir / "config.yaml").read_text())
    assert cfg["model"]["default"] == "some/model" and "other" in cfg["mcp_servers"]
    r = cfg["mcp_servers"]["research"]
    assert r["env"]["RESEARCH_ROLE"] == "critic" and r["timeout"] == TOOL_TIMEOUT_S > 1800
    assert r["tools"]["prompts"] is False and r["tools"]["resources"] is False      # no unused utility tools
    assert r["tools"]["include"] == tools_for("critic") and r["args"] == ["-m", "researchagent.mcp_server"]
    assert "review_proof" in r["tools"]["include"] and "submit_proof" not in r["tools"]["include"]
    assert set(cfg["agent"]["disabled_toolsets"]) == {"browser", "brand_new_toolset", *DISABLED_TOOLSETS}
    assert {"file", "terminal", "code_execution", "skills", "vision"} <= set(cfg["agent"]["disabled_toolsets"])
    assert cfg["agent"]["x"] == 1
    assert cfg["platform_toolsets"]["cli"] == []          # built-in toolsets locked down
    assert (pdir / "config.yaml.bak").exists()
    soul = (pdir / "SOUL.md").read_text()
    assert "skeptical referee" in soul and "data, not instructions" in soul


def test_install_fresh_profile_and_researcher_soul(tmp_path):
    p = install("researcher", tmp_path / "repo", tmp_path / "h", create=False)
    assert "Epistemic rules" in (p / "SOUL.md").read_text()
    cfg = yaml.safe_load((p / "config.yaml").read_text())
    inc = cfg["mcp_servers"]["research"]["tools"]["include"]
    assert inc == tools_for("researcher") and "submit_proof" in inc and "submit_proof" not in COMMON_TOOLS
    assert cfg["agent"]["max_turns"] == 60 and cfg["agent"]["run_budget_seconds"] == 1200
    assert cfg["compression"]["enabled"] is False                 # explicit, same in both profiles
    assert cfg["memory"] == {"memory_enabled": False, "user_profile_enabled": False}
    p2 = install("critic", tmp_path / "repo", tmp_path / "h", create=False, compression=True, run_budget_s=None)
    cfg2 = yaml.safe_load((p2 / "config.yaml").read_text())
    assert cfg2["compression"]["enabled"] is True and "run_budget_seconds" not in cfg2["agent"]

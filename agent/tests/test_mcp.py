"""End-to-end over the real MCP stdio protocol with the official client."""
import json
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _env(root, tmp_path, role):
    return {**os.environ, "RESEARCH_ROOT": str(root), "RESEARCH_ROLE": role, "RESEARCH_EXECUTOR": "local",
            "RESEARCH_JULIA_CMD": str(tmp_path / "fakejulia")}


def _payload(res):
    if res.structuredContent is not None:
        sc = res.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def _drive(root, tmp_path, role, fn):
    out = {}

    async def main():
        params = StdioServerParameters(command=sys.executable, args=["-m", "researchagent.mcp_server"],
                                       env=_env(root, tmp_path, role))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                out["v"] = await fn(s)

    anyio.run(main)
    return out["v"]


def test_tools_listed_with_contract_in_description(root, tmp_path):
    async def fn(s):
        return {t.name: t for t in (await s.list_tools()).tools}
    tools = _drive(root, tmp_path, "researcher", fn)
    assert {"submit_experiment", "submit_check", "inspect_run", "read_artifact", "list_runs",
            "rerun_run", "compare_runs", "add_note", "read_notes"} <= set(tools)
    assert "Prelude.jl" in tools["submit_experiment"].description
    assert "margin(theta" in tools["submit_check"].description


def test_full_tool_round_trip(root, tmp_path):
    async def fn(s):
        sub = _payload(await s.call_tool("submit_experiment", {
            "title": "t", "hypothesis": "h", "script": "println(1)\n", "params": {"x": "2.5"}}))
        insp = _payload(await s.call_tool("inspect_run", {"run_id": sub["run_id"]}))
        art = _payload(await s.call_tool("read_artifact", {"run_id": sub["run_id"], "path": "out/data.csv"}))
        rr = _payload(await s.call_tool("rerun_run", {"run_id": sub["run_id"]}))
        bad = _payload(await s.call_tool("inspect_run", {"run_id": "../../x"}))
        note = _payload(await s.call_tool("add_note", {"kind": "hypothesis", "text": "H",
                                                       "run_ids": [sub["run_id"]]}))
        crit = _payload(await s.call_tool("add_note", {"kind": "critique", "text": "x", "status": "accept"}))
        return sub, insp, art, rr, bad, note, crit
    sub, insp, art, rr, bad, note, crit = _drive(root, tmp_path, "researcher", fn)
    assert sub["status"] == "completed" and insp["reported"]["metrics"]["x"] == "2.5"
    assert art["text"].startswith("N,err")
    assert rr["outcome"] == "pass"
    assert bad["ok"] is False and "invalid run id" in bad["error"]      # errors are data, not protocol errors
    assert note["kind"] == "hypothesis"
    assert crit["ok"] is False and "may not write" in crit["error"]

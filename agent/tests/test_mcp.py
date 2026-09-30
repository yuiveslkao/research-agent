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
            "rerun_run", "compare_runs", "add_note", "read_notes", "read_claims", "arxiv",
            "submit_falsify_bound", "submit_proof"} <= set(tools)
    assert "Prelude.jl" in tools["submit_experiment"].description
    assert "margin(theta" in tools["submit_check"].description


def test_full_tool_round_trip(root, tmp_path):
    async def fn(s):
        early = _payload(await s.call_tool("submit_experiment", {"title": "t", "hypothesis": "h", "script": "println(1)\n"}))
        await s.call_tool("add_note", {"kind": "hypothesis", "claim": {"statement": "H", "scope": "s", "refuted_if": "r"}})
        sub = _payload(await s.call_tool("submit_experiment", {
            "title": "t", "hypothesis": "h", "script": "println(1)\n", "params": {"x": "2.5"}}))
        insp = _payload(await s.call_tool("inspect_run", {"run_id": sub["run_id"]}))
        art = _payload(await s.call_tool("read_artifact", {"run_id": sub["run_id"], "path": "out/data.csv"}))
        rr = _payload(await s.call_tool("rerun_run", {"run_id": sub["run_id"]}))
        bad = _payload(await s.call_tool("inspect_run", {"run_id": "../../x"}))
        note = _payload(await s.call_tool("add_note", {"kind": "hypothesis", "run_ids": [sub["run_id"]],
                                                       "claim": {"statement": "H2", "scope": "s", "refuted_if": "r"}}))
        crit = _payload(await s.call_tool("add_note", {"kind": "critique", "status": "accept", "claim": {"statement": "x", "scope": "s"}}))
        return early, sub, insp, art, rr, bad, note, crit
    early, sub, insp, art, rr, bad, note, crit = _drive(root, tmp_path, "researcher", fn)
    assert early["ok"] is False and "Pre-register first" in early["error"]
    assert sub["status"] == "completed" and insp["reported"]["metrics"]["x"] == "2.5"
    assert art["text"].startswith("N,err")
    assert rr["outcome"] == "pass"
    assert bad["ok"] is False and "invalid run id" in bad["error"]      # errors are data, not protocol errors
    assert note["kind"] == "hypothesis"
    assert crit["ok"] is False and "may not write" in crit["error"]


def test_over_mcp_the_ai_reaches_proven_ai_but_never_proven(root, tmp_path):
    from researchagent import status
    proof = "Lemma 1 and its proof. " * 12

    async def researcher(s):
        await s.call_tool("add_note", {"kind": "hypothesis", "claim": {"statement": "H", "scope": "s", "refuted_if": "r"}})
        run = _payload(await s.call_tool("submit_experiment", {"title": "t", "hypothesis": "h", "script": "println(1)\n"}))
        f = _payload(await s.call_tool("add_note", {"kind": "finding", "run_ids": [run["run_id"]],
                                                    "claim": {"statement": "held", "scope": "s", "result": "supports"}}))
        cid = f["claim_status"]["claim"]
        bad = [_payload(await s.call_tool("add_note", {"kind": "decision", "status": "proven", "text": "x"})),
               _payload(await s.call_tool("add_note", {"kind": "critique", "status": "proven", "run_ids": [run["run_id"]],
                                                       "claim": {"statement": "H", "scope": "s"}}))]
        p = _payload(await s.call_tool("submit_proof", {"claim": cid, "statement": "H", "scope": "s", "proof": proof}))
        claims = _payload(await s.call_tool("read_claims", {}))
        return cid, bad, p, claims
    cid, bad, p, claims = _drive(root, tmp_path, "researcher", researcher)
    assert all(b["ok"] is False for b in bad)                         # errors as data, no protocol error
    assert p["status"] == "proven_AI" and claims["claims"][0]["status"] == "proven_AI"

    async def critic(s):
        return _payload(await s.call_tool("submit_proof", {"claim": cid, "statement": "H", "scope": "s", "proof": proof}))
    assert "may not use this tool" in _drive(root, tmp_path, "critic", critic)["error"]
    snap = status.Snapshot(root / "ledger")
    assert snap.view(cid)["effective"] == "proven_AI" and all(r["source"] != "human" for r in snap.history)

import json
import sqlite3
import time

from conftest import FND, HYP
from researchagent import tools, usage
from researchagent.mcp_server import build_server

from test_mcp import _drive, _payload


def test_tool_calls_are_logged_and_summarized(root, tmp_path):
    async def fn(s):
        await s.call_tool("add_note", {"kind": "hypothesis", "claim": {"statement": "H", "scope": "s", "refuted_if": "r"}})
        await s.call_tool("submit_experiment", {"title": "t", "hypothesis": "h", "script": "println(1)\n" * 50})
        await s.call_tool("inspect_run", {"run_id": "../x"})
    _drive(root, tmp_path, "researcher", fn)
    ledger = root / "ledger"
    recs = [json.loads(x) for x in (ledger / "calls.jsonl").read_text().splitlines()]
    assert [r["tool"] for r in recs] == ["add_note", "submit_experiment", "inspect_run"]
    assert recs[1]["args_chars"] > 500 and recs[2]["ok"] is False and all(r["role"] == "researcher" for r in recs)
    s = usage.summarize_calls(ledger)["researcher"]
    assert s["calls"] == 3 and s["failed"] == 1 and s["est_out_tokens"] > 100
    assert s["by_tool"] == {"add_note": 1, "submit_experiment": 1, "inspect_run": 1}


def test_render_and_turn_summary(tmp_path):
    turns = [{"round": 1, "role": "researcher", "seconds": 120.0}, {"round": 1, "role": "critic", "seconds": 60.0}]
    calls = {"researcher": {"calls": 4, "failed": 0, "args_chars": 3500, "result_chars": 7000,
                            "est_out_tokens": 1000, "est_tool_result_tokens": 2000, "by_tool": {"add_note": 4}}}
    md = usage.render("C-1", calls, turns, {"researcher": {"table": "sessions", "input_tokens": 5}, "critic": None})
    assert "| 1 | researcher | 2.0 |" in md and "Total turn time: 3.0 min" in md
    assert "input_tokens=5" in md and "critic: no token counts found" in md


def test_hermes_usage_reads_a_session_table(tmp_path):
    pdir = tmp_path / "researcher"
    pdir.mkdir()
    con = sqlite3.connect(pdir / "state.db")
    con.execute("create table sessions (id text, started_at real, input_tokens int, output_tokens int, cache_read_tokens int)")
    now = time.time()
    con.executemany("insert into sessions values (?,?,?,?,?)", [("a", now - 100, 1000, 50, 800), ("b", now - 5000, 9999, 9, 9)])
    con.commit()
    con.close()
    got = usage.hermes_usage(pdir, now - 200, now)
    assert got == {"table": "sessions", "rows": 1, "input_tokens": 1000, "output_tokens": 50, "cache_read_tokens": 800}
    assert usage.hermes_usage(tmp_path / "missing", 0, now) is None

import subprocess

import pytest

from researchagent import tools
from researchagent.tools import ToolError

SCRIPT = 'println("hi")  # experiment body is irrelevant to the fake julia\n'


def submit(ctx, script=SCRIPT, **kw):
    return tools.submit_experiment(ctx, "rate test", "error decays like exp(-cN/logN)", script,
                                   params=kw.pop("params", {"x": "1.5"}), **kw)


def test_submit_and_inspect(ctx):
    r = submit(ctx)
    assert r["status"] == "completed" and r["kind"] == "experiment"
    assert r["reported"]["metrics"]["x"] == "1.5"
    assert r["numerics"]["precision_bits"] == 256
    assert [o["path"] for o in r["outputs"]] == ["out/data.csv", "out/reported.json"]
    again = tools.inspect_run(ctx, r["run_id"])
    assert again["title"] == "rate test" and again["role"] == "researcher"


def test_agent_code_is_committed_in_lab_not_on_main(ctx, root):
    before = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    r = submit(ctx)
    after = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert before == after
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status.strip() == ""                       # .rh/ and ledger/ are ignored
    script = tools.read_artifact(ctx, r["run_id"], "script")
    assert script["text"] == SCRIPT
    log = subprocess.run(["git", "-C", str(ctx.lab.dir), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "experiment E-" in log


def test_failed_run_reports_stderr(ctx):
    r = submit(ctx, script="FAILME\n")
    assert r["status"] == "failed" and "FAILME requested" in r["stderr_tail"]
    art = tools.read_artifact(ctx, r["run_id"], "stderr")
    assert "LoadError" in art["text"]


def test_read_artifact_output_and_truncation(ctx):
    r = submit(ctx)
    a = tools.read_artifact(ctx, r["run_id"], "out/data.csv", max_bytes=8)
    assert a["truncated"] and a["text"] == "N,err\n8,"
    t = tools.read_artifact(ctx, r["run_id"], "out/data.csv", max_bytes=8, tail=True)
    assert t["text"].endswith("e-13\n")
    with pytest.raises(ToolError, match="no such output"):
        tools.read_artifact(ctx, r["run_id"], "../../etc/passwd")


def test_run_id_validation_blocks_traversal(ctx):
    for bad in ("../x", "R-1", "R-20260101T000000-zzzzzz", "/etc/passwd"):
        with pytest.raises(ToolError, match="invalid run id"):
            tools.inspect_run(ctx, bad)


def test_rerun_and_compare(ctx):
    r = submit(ctx)
    rec = tools.rerun_run(ctx, r["run_id"])
    assert rec["outcome"] == "pass" and rec["rerun_summary"]["status"] == "completed"
    cmp = tools.compare_runs(ctx, r["run_id"], rec["rerun"], 1e-20)
    assert cmp["outcome"] == "pass"


def test_precision_and_resource_caps(ctx):
    with pytest.raises(ToolError, match="precision_bits"):
        submit(ctx, precision_bits=10**6)
    with pytest.raises(ToolError, match="walltime_s"):
        submit(ctx, walltime_s=10**6)
    with pytest.raises(ToolError, match="threads"):
        submit(ctx, threads=64)
    with pytest.raises(ToolError, match="mem_gb"):
        submit(ctx, mem_gb=64)


def test_budget(ctx, root):
    from researchagent.config import set_current_campaign
    set_current_campaign(ctx.paths.ledger, "C-test", 2)
    submit(ctx)
    submit(ctx)
    with pytest.raises(ToolError, match="budget exhausted"):
        submit(ctx)
    with pytest.raises(ToolError, match="budget exhausted"):
        tools.rerun_run(ctx, tools.list_runs(ctx)[0]["run_id"])


def test_list_runs_filters_by_campaign(ctx):
    from researchagent.config import set_current_campaign
    set_current_campaign(ctx.paths.ledger, "C-a", 10)
    submit(ctx)
    set_current_campaign(ctx.paths.ledger, "C-b", 10)
    assert tools.list_runs(ctx) == []
    assert len(tools.list_runs(ctx, campaign_only=False)) == 1


def test_submit_check_outcomes(ctx):
    good = "module Good\nconst DOMAIN = (N = 8:16,)\nmargin(t, ::Type{T}) where {T<:Real} = one(T)\nend\n"
    bad = good.replace("Good", "Bad") + "# PLANTED flaw\n"
    r = tools.submit_check(ctx, "falsify", "Good", "Good", good)
    assert r["reported"]["check"]["outcome"] == "survived"
    r = tools.submit_check(ctx, "falsify", "Bad", "Bad", bad)
    assert r["reported"]["check"]["outcome"] == "violated"
    r = tools.submit_check(ctx, "highprec", "Good", "Good", good, grid=4)
    assert r["reported"]["check"]["outcome"] == "pass" and r["params"]["grid"] == 4
    with pytest.raises(ToolError, match="must define `module Other`"):
        tools.submit_check(ctx, "falsify", "Good", "Other", good)
    with pytest.raises(ToolError, match="name and module"):
        tools.submit_check(ctx, "falsify", "../evil", "Good", good)


def test_notes_and_roles(make_ctx):
    res, cri = make_ctx("researcher"), make_ctx("critic")
    run = submit(res)
    tools.add_note(res, "hypothesis", "H1", run_ids=[run["run_id"]])
    tools.add_note(res, "decision", "go on", status="continue")
    tools.add_note(cri, "critique", "not convinced", status="challenge", run_ids=[run["run_id"]])
    with pytest.raises(ToolError, match="may not write"):
        tools.add_note(cri, "decision", "x", status="conclude")
    with pytest.raises(ToolError, match="may not write"):
        tools.add_note(res, "critique", "x", status="accept")
    with pytest.raises(ToolError, match="status"):
        tools.add_note(res, "decision", "x")
    with pytest.raises(ToolError, match="unknown run id"):
        tools.add_note(res, "finding", "x", run_ids=["R-20260101T000000-abcdef"])
    ns = tools.read_notes(cri)
    assert [n["kind"] for n in ns] == ["hypothesis", "decision", "critique"]
    assert [n["kind"] for n in tools.read_notes(cri, kind="critique")] == ["critique"]


def test_lab_refuses_paths_outside_agent_dirs(ctx):
    from researchagent.lab import LabError
    for bad in ("checkers/falsify.jl", "workspace/library/DESinc.jl", "workspace/agent/../library/x.jl",
                "workspace/agent/experiments/sub/x.jl", "/tmp/x.jl"):
        with pytest.raises(LabError):
            ctx.lab.commit_file(bad, "x", "m")


def test_tomlw_roundtrip():
    import tomllib
    from researchagent import tomlw
    d = {"a": 1, "s": 'q"uote\nline', "t": True, "l": [1, 2], "sub": {"x": "1.5", "deep": {"y": [0.5]}}}
    assert tomllib.loads(tomlw.dumps(d)) == d

import json
import subprocess

import pytest

from conftest import ACC, CHAL, FND, HYP

from researchagent import tools
from researchagent.tools import ToolError

SCRIPT = 'println("hi")  # experiment body is irrelevant to the fake julia\n'


def submit(ctx, script=SCRIPT, note=True, **kw):
    if note and ctx.settings.role == "researcher":
        tools.add_note(ctx, "hypothesis", claim=HYP("the fake experiment reports x"))   # pre-registration gate
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


def test_budget_reserves_share_for_critic(make_ctx):
    from researchagent.config import set_current_campaign
    res, cri = make_ctx("researcher"), make_ctx("critic")
    set_current_campaign(res.paths.ledger, "C-test", 5)         # researcher share 3, critic keeps 2
    for _ in range(3):
        submit(res)
    with pytest.raises(ToolError, match="reserved for the critic"):
        submit(res)
    first = tools.list_runs(res)[0]["run_id"]
    with pytest.raises(ToolError, match="budget exhausted"):
        tools.rerun_run(res, first)
    submit(cri)                                                  # the critic can still attack
    tools.rerun_run(cri, first)
    with pytest.raises(ToolError, match="budget exhausted"):
        submit(cri)                                              # nobody exceeds the total


def test_number_shortening_and_full_precision(ctx):
    from researchagent.tools import shorten_numbers, shorten_text
    long = "5.32441334924897634852236681850808222185416142616629524874326809157081409251244900028693567026201991e-86"
    assert shorten_text(long) == "5.3244133492489763485E-86"
    assert shorten_text("run R-20260929T104703-46a827 n=128 h=0.0455692149760261") == \
        "run R-20260929T104703-46a827 n=128 h=0.0455692149760261"          # short numbers and ids untouched
    assert shorten_numbers({"e": [long], "n": 3}) == {"e": ["5.3244133492489763485E-86"], "n": 3}
    r = submit(ctx, params={"x": long})
    assert r["reported"]["metrics"]["x"] == "5.3244133492489763485E-86" and "numbers_shortened" in r
    full = tools.read_artifact(ctx, r["run_id"], "out/reported.json", full_precision=True)
    assert long in full["text"] and "digits" not in full
    short = tools.read_artifact(ctx, r["run_id"], "out/reported.json")
    assert long not in short["text"] and "5.3244133492489763485E-86" in short["text"]


def test_base_run_reuses_script_with_edits(ctx):
    r1 = submit(ctx, script="alpha = 1\nbeta = 2\n", params={"x": "1"})
    r2 = tools.submit_experiment(ctx, "same script, other params", "h", base_run=r1["run_id"], params={"x": "2"})
    assert r2["reported"]["metrics"]["x"] == "2"
    assert tools.read_artifact(ctx, r2["run_id"], "script")["text"] == "alpha = 1\nbeta = 2\n"
    r3 = tools.submit_experiment(ctx, "edited", "h", base_run=r1["run_id"], edits=[{"old": "beta = 2", "new": "beta = 3"}])
    assert tools.read_artifact(ctx, r3["run_id"], "script")["text"] == "alpha = 1\nbeta = 3\n"
    with pytest.raises(ToolError, match="exactly once"):
        tools.submit_experiment(ctx, "bad", "h", base_run=r1["run_id"], edits=[{"old": "gamma", "new": "x"}])
    with pytest.raises(ToolError, match="not both"):
        tools.submit_experiment(ctx, "bad", "h", script="x", base_run=r1["run_id"])
    with pytest.raises(ToolError, match="needs `base_run`"):
        tools.submit_experiment(ctx, "bad", "h", script="x", edits=[{"old": "a", "new": "b"}])


def test_list_runs_filters_by_campaign(ctx):
    from researchagent.config import set_current_campaign
    set_current_campaign(ctx.paths.ledger, "C-a", 10)
    submit(ctx)
    set_current_campaign(ctx.paths.ledger, "C-b", 10)
    assert tools.list_runs(ctx) == []
    assert len(tools.list_runs(ctx, campaign_only=False)) == 1


def test_submit_check_outcomes(ctx):
    tools.add_note(ctx, "hypothesis", claim=HYP("Good holds, Bad is violated"))
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
    tools.add_note(res, "hypothesis", claim=HYP("H1"), run_ids=[run["run_id"]])
    tools.add_note(res, "decision", "go on", status="continue")
    tools.add_note(cri, "critique", status="challenge", claim=CHAL, run_ids=[run["run_id"]])
    with pytest.raises(ToolError, match="may not write"):
        tools.add_note(cri, "decision", "x", status="conclude")
    with pytest.raises(ToolError, match="may not write"):
        tools.add_note(res, "critique", status="accept", claim=ACC)
    with pytest.raises(ToolError, match="status"):
        tools.add_note(res, "decision", "x")
    with pytest.raises(ToolError, match="unknown run id"):
        tools.add_note(res, "finding", claim=FND(), run_ids=["R-20260101T000000-abcdef"])
    ns = tools.read_notes(cri)
    assert [n["kind"] for n in ns] == ["hypothesis", "hypothesis", "decision", "critique"]   # first: submit() helper
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


def test_finding_must_cite_a_run(ctx):
    with pytest.raises(ToolError, match="finding must cite at least one run"):
        tools.add_note(ctx, "finding", claim=FND("the error decays"))
    r = submit(ctx)
    assert tools.add_note(ctx, "finding", claim=FND("error decays"), run_ids=[r["run_id"]])["kind"] == "finding"


def test_hypothesis_required_before_experiment_and_after_each_finding(ctx, make_ctx):
    with pytest.raises(ToolError, match="Pre-register first"):
        tools.submit_experiment(ctx, "t", "h", script=SCRIPT)
    tools.add_note(ctx, "hypothesis", claim=HYP("H1"))
    r = tools.submit_experiment(ctx, "t", "h", script=SCRIPT)                # covered by H1
    tools.submit_experiment(ctx, "retry or precision check", "h", script=SCRIPT)   # same cycle: still covered
    tools.rerun_run(ctx, r["run_id"])                                        # reruns never need one
    tools.add_note(ctx, "finding", claim=FND("F1"), run_ids=[r["run_id"]])
    with pytest.raises(ToolError, match="Pre-register first"):              # a new cycle needs a new hypothesis
        tools.submit_experiment(ctx, "t2", "h", script=SCRIPT)
    with pytest.raises(ToolError, match="Pre-register first"):              # checks are gated too
        tools.submit_check(ctx, "falsify", "Good", "Good", "module Good\nend\n")
    tools.add_note(ctx, "hypothesis", claim=HYP("H2"))
    tools.submit_experiment(ctx, "t2", "h", script=SCRIPT)
    critic = make_ctx("critic")                                              # the critic is exempt
    tools.submit_experiment(critic, "attack", "h", script=SCRIPT)


def test_critique_must_cite_a_run_from_this_campaign(make_ctx):
    from researchagent.config import set_current_campaign
    res, cri = make_ctx("researcher"), make_ctx("critic")
    set_current_campaign(res.paths.ledger, "C-old", 10)
    old = submit(res)
    set_current_campaign(res.paths.ledger, "C-new", 10)
    new = submit(res)
    with pytest.raises(ToolError, match="critique must cite at least one run from this campaign"):
        tools.add_note(cri, "critique", status="challenge", claim=CHAL)
    with pytest.raises(ToolError, match="critique must cite at least one run from this campaign"):
        tools.add_note(cri, "critique", status="challenge", claim=CHAL, run_ids=[old["run_id"]])
    assert tools.add_note(cri, "critique", status="challenge", claim=CHAL, run_ids=[old["run_id"], new["run_id"]])


def test_clip_never_cuts_inside_a_number():
    from researchagent.tools import _clip
    metrics = {"error": {str(n): "1.5047345E-%d" % (80 + n) for n in range(400)}, "c_eff": {"8": "4.3"}}
    out = _clip({"metrics": metrics, "notes": "n"}, 600)
    assert out["_partial"] and out["omitted_entries"] > 0
    kept = out["data"]["metrics"]["error"]
    assert kept and all(v == "1.5047345E-%d" % (80 + int(k)) for k, v in kept.items())   # whole values only
    assert len(json.dumps(out)) < 900
    small = {"metrics": {"a": "1"}}
    assert _clip(small, 600) is small


def test_base_run_inherits_and_overrides_params(ctx):
    r1 = submit(ctx, params={"x": "1", "N": [8, 16]})
    r2 = tools.submit_experiment(ctx, "other precision", "h", base_run=r1["run_id"], params={"x": "2"},
                                 precision_bits=512)
    assert r2["params"] == {"x": "2", "N": [8, 16]} and r2["numerics"]["precision_bits"] == 512


def test_failed_runs_are_refunded_but_capped(ctx):
    from researchagent.config import set_current_campaign
    set_current_campaign(ctx.paths.ledger, "C-fail", 3)              # researcher share 2
    for _ in range(3):
        assert submit(ctx, script="FAILME\n")["status"] == "failed"
    with pytest.raises(ToolError, match="failed in this campaign"):
        submit(ctx, script="FAILME\n")
    with pytest.raises(ToolError, match="stop retrying"):
        submit(ctx)


def test_agent_runs_cannot_read_outside_workspace_and_checkers(ctx, root):
    (root / "eval").mkdir()
    (root / "eval" / "GQ-X.toml").write_text('known_answer = "42"\n')
    (root / "checkers" / "falsify.jl").write_text("# checker\n")
    (root / "workspace" / "statements").mkdir()
    (root / "workspace" / "statements" / "C0003.jl").write_text("# PLANTED flaw\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "answer key"], check=True)
    first = submit(ctx)
    r = tools.submit_experiment(ctx, "second", "h", script="println(2)\n")
    assert r["status"] == "completed"
    code = ctx.paths.work / r["run_id"] / "code"
    assert sorted(p.name for p in code.iterdir()) == ["checkers", "workspace"]
    mine = sorted(p.name for p in (code / "workspace" / "agent" / "experiments").iterdir())
    assert len(mine) == 1 and first["run_id"] not in mine                # earlier agent scripts are not visible
    assert (code / "workspace" / "envs" / "main" / "Manifest.toml").exists()
    assert not (code / "workspace" / "statements").exists()                  # human test statements stay hidden
    assert tools.rerun_run(ctx, r["run_id"])["outcome"] == "pass"

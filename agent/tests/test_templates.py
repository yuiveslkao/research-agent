import re

import pytest

from conftest import HYP
from researchagent import templates, tools
from researchagent.templates import TemplateError, bound_statement
from researchagent.tools import ToolError


def test_generated_statement_shape():
    src = bound_statement("GBabc", "exp(-pi^2*N/log(4*d*N/alpha))*N^Q", {"d": ("0.5", "1"), "N": (8, 128)}, {"Q": "2.6"})
    assert re.search(r"^module GBabc$", src, re.M) and src.rstrip().endswith("end # module")
    assert "bnd = exp(-PI^2*N/log(4*d*N/alpha))*N^Q" in src          # no space before "(" (Julia rejects it)
    assert 'Q = parse(T, "13") / parse(T, "5")' in src
    assert "d = ((1//2), (1//1))" in src and "N = 8:128" in src and "b = ((-1//2), (1//1))" in src
    assert "relmargin" in src and "family_exact(a, b)" in src


@pytest.mark.parametrize("bound,match", [
    ("system(1)", "unknown name"), ("N; rm -rf /", "not allowed"), ("exp(N) # x", "not allowed"),
    ("Q*2", "unknown name"), ("", "non-empty"), ("N " * 200, "300 characters")])
def test_bad_expressions_are_rejected(bound, match):
    with pytest.raises(TemplateError, match=match):
        bound_statement("GB", bound)


def test_domain_and_const_validation():
    with pytest.raises(TemplateError, match="lower bound must be > -1"):
        bound_statement("GB", "N", {"a": ("-1", "1")})
    with pytest.raises(TemplateError, match="lower bound must be below"):
        bound_statement("GB", "N", {"d": ("1", "1/2")})
    with pytest.raises(TemplateError, match="unknown domain"):
        bound_statement("GB", "N", {"z": (0, 1)})
    with pytest.raises(TemplateError, match="need 2 <= lo < hi"):
        bound_statement("GB", "N", {"N": (1, 10)})
    with pytest.raises(TemplateError, match="UPPERCASE"):
        bound_statement("GB", "N", None, {"q": "1"})


def test_submit_falsify_bound_end_to_end(ctx):
    with pytest.raises(ToolError, match="Pre-register first"):
        tools.submit_falsify_bound(ctx, "exp(-N)")
    tools.add_note(ctx, "hypothesis", claim=HYP("bound holds"))
    r = tools.submit_falsify_bound(ctx, "exp(-pi^2*N/log(4*d*N/alpha))", {"N": [8, 32]}, evals=50)
    assert r["kind"] == "check" and r["reported"]["check"]["outcome"] == "survived"
    assert r["params"]["evals"] == 50 and r["params"]["statement"].startswith("agent/statements/GB")
    src = tools.read_artifact(ctx, r["run_id"], "script")["text"]
    assert "exp(-PI^2*N/log(4*d*N/alpha))" in src
    with pytest.raises(ToolError, match="unknown name"):
        tools.submit_falsify_bound(ctx, "evil(N)")

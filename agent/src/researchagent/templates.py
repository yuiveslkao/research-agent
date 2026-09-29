"""Generate an executable statement (Julia) from a bound expression, so an agent can falsify a claim
`|error| <= bound` over a parameter box without writing a module by hand.

Testbed family: I(a,b) = integral_{-1}^{1} (1-x)^a (1+x)^b dx with the DE rule of library DESinc and
h = de_h(N, d, alpha), alpha = min(a,b)+1. Variables usable in `bound`: N, d, a, b, alpha, h, pi
(as PI in the target precision) and any names given in `consts` (decimal strings).
"""
from __future__ import annotations

import re
from fractions import Fraction

ALLOWED_FUNCS = {"exp", "log", "sqrt", "abs", "min", "max"}
BASE_VARS = {"N", "d", "a", "b", "alpha", "h", "pi"}
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*(?:[eE][+-]?\d+)?|\S")
DEFAULT_DOMAIN = {"d": ("1/2", "1"), "a": ("-1/2", "1"), "b": ("-1/2", "1"), "N": (8, 64)}


class TemplateError(ValueError):
    pass


def _frac(v) -> str:
    try:
        f = Fraction(str(v))
    except (ValueError, ZeroDivisionError) as e:
        raise TemplateError(f"cannot read {v!r} as a number (use e.g. '0.5' or '1/2')") from e
    return f"({f.numerator}//{f.denominator})"


def check_expression(expr: str, consts: dict[str, str]) -> str:
    if not isinstance(expr, str) or not expr.strip() or len(expr) > 300:
        raise TemplateError("bound must be a non-empty expression of at most 300 characters")
    names = set(consts) | BASE_VARS | ALLOWED_FUNCS
    out: list[str] = []
    for t in _TOKEN.findall(expr):
        if re.match(r"[A-Za-z_]", t):
            if t not in names:
                raise TemplateError(f"unknown name {t!r}; allowed: {sorted(names)}")
            t = "PI" if t == "pi" else t
        elif not (re.match(r"\d", t) or t in "+-*/^(),."):
            raise TemplateError(f"character {t!r} is not allowed in a bound expression")
        # Julia rejects `exp (x)`; only keep a space between two adjacent words/numbers (which is then a syntax error)
        if out and re.match(r"\w", out[-1][-1]) and re.match(r"\w", t[0]):
            out.append(" ")
        out.append(t)
    return "".join(out)


def bound_statement(module: str, bound: str, domain: dict | None = None, consts: dict[str, str] | None = None) -> str:
    consts = consts or {}
    for k, v in consts.items():
        if not re.match(r"^[A-Z][A-Z0-9_]{0,6}$", k) or k in {"N", "PI"}:
            raise TemplateError(f"const name {k!r} must be UPPERCASE letters/digits (max 7) and not N or PI")
        try:
            Fraction(str(v))
        except (ValueError, ZeroDivisionError) as e:
            raise TemplateError(f"const {k}={v!r} is not a decimal or fraction") from e
    expr = check_expression(bound, consts)
    dom = {**DEFAULT_DOMAIN, **(domain or {})}
    unknown = set(dom) - set(DEFAULT_DOMAIN)
    if unknown:
        raise TemplateError(f"unknown domain variables {sorted(unknown)}; use d, a, b, N")
    parts = []
    for k in ("d", "a", "b"):
        lo, hi = dom[k]
        if Fraction(str(lo)) >= Fraction(str(hi)):
            raise TemplateError(f"domain {k}: lower bound must be below upper bound")
        if k in ("a", "b") and Fraction(str(lo)) <= -1:
            raise TemplateError(f"domain {k}: lower bound must be > -1 (the exact value needs a,b > -1)")
        if k == "d" and Fraction(str(lo)) <= 0:
            raise TemplateError("domain d: lower bound must be > 0")
        parts.append(f"{k} = ({_frac(lo)}, {_frac(hi)})")
    nlo, nhi = (int(x) for x in dom["N"])
    if not 2 <= nlo < nhi <= 4096:
        raise TemplateError("domain N: need 2 <= lo < hi <= 4096 (integers)")
    parts.append(f"N = {nlo}:{nhi}")
    const_lines = "\n".join(f'    {k} = parse(T, "{Fraction(str(v)).numerator}") / parse(T, "{Fraction(str(v)).denominator}")'
                            for k, v in consts.items())
    return f'''# Generated falsification statement: |error| <= {bound}
module {module}
include(joinpath(@__DIR__, "..", "..", "library", "DESinc.jl"))
using .DESinc

const INFO = (id = "{module}", form = :error_vs_bound, rigorous = false)
const DOMAIN = ({", ".join(parts)})

function parts(θ, ::Type{{T}}) where {{T<:Real}}
    d, a, b = T(θ.d), T(θ.a), T(θ.b)
    alpha = min(a, b) + 1
    N = θ.N
    h = de_h(N, d, alpha)
    PI = T(π)
{const_lines}
    err = abs(de_quad(family_f(a, b), N, h) - family_exact(a, b))
    bnd = {expr}
    return err, bnd
end

margin(θ, ::Type{{T}}) where {{T<:Real}} = (p = parts(θ, T); p[2] - p[1])
relmargin(θ, ::Type{{T}}) where {{T<:Real}} = (p = parts(θ, T); 1 - p[1] / p[2])

end # module
'''

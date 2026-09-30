# `certify` check: evaluate a statement's margin in interval arithmetic (Intervals.jl, MPFR outward rounding) at
# given points and/or on a grid over its DOMAIN. Every evaluated point ends as
#   certified     the enclosure of the margin is > 0: the claim holds at this point, for the exact real formula;
#   violated      the enclosure is < 0: a certified counterexample (the search stops here);
#   inconclusive  the enclosure contains 0, or an interval operation was undecidable (raise precision_bits).
# "certified" for the whole check means: holds at these finitely many points. It is NOT a proof over the DOMAIN.
# params: statement, module, points (list of tables, values as integers or exact strings "1//2", "0.25"),
#         grid (points per continuous dimension, 0 = none)
using TOML
include(joinpath(@__DIR__, "MiniJSON.jl")); using .MiniJSON
include(joinpath(@__DIR__, "Domain.jl")); using .Domain
include(joinpath(@__DIR__, "Intervals.jl")); using .Intervals

if Base.find_package("SpecialFunctions") !== nothing       # the DE library's gamma, extended to intervals
    import SpecialFunctions
    SpecialFunctions.gamma(x::Iv) = gamma_iv(x)
end

spec = TOML.parsefile(ENV["RUN_SPEC"])
p = spec["params"]
bits = spec["numerics"]["precision_bits"]
setprecision(BigFloat, bits)
include(joinpath(pwd(), p["statement"]))
stmt = getfield(Main, Symbol(p["module"]))

"Exact value of a point coordinate: integers stay integers, strings become exact rationals."
function exact(v)
    v isa Integer && return v
    v isa AbstractString || error("point values must be integers or strings, got $(repr(v))")
    s = strip(v)
    occursin("//", s) && return parse(BigInt, split(s, "//")[1]) // parse(BigInt, split(s, "//")[2])
    m = match(r"^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$", s)
    m === nothing && error("not an exact decimal: $s")
    digits = m[2] * something(m[3], "")
    isempty(digits) && error("not an exact decimal: $s")
    q = parse(BigInt, digits) // big(10)^length(something(m[3], ""))
    e = m[4] === nothing ? 0 : parse(Int, m[4])
    q = e >= 0 ? q * big(10)^e : q / big(10)^(-e)
    return m[1] == "-" ? -q : q
end

function point(dom, t::AbstractDict)
    names = keys(dom)
    Set(keys(t)) == Set(string.(names)) || error("point $(t) must give exactly the coordinates $(collect(names))")
    vals = map(names) do k
        x, r = exact(t[string(k)]), dom[k]
        inside = iscont(r) ? (r[1] <= x <= r[2]) : (x in r)
        inside || error("point coordinate $k = $x is outside the DOMAIN $(r)")
        x
    end
    return NamedTuple{names}(vals)
end

function evaluate(stmt, θ)
    try
        m = score(stmt, θ, Iv)
        m isa Iv || return ("inconclusive", "margin is not an interval (statement not generic in T)", nothing)
        lo(m) > 0 && return ("certified", "", m)
        hi(m) < 0 && return ("violated", "", m)
        return ("inconclusive", "enclosure contains 0", m)
    catch e
        e isa IntervalError || rethrow()
        return ("inconclusive", sprint(showerror, e), nothing)
    end
end

function main(stmt, p)
    pts = [point(stmt.DOMAIN, t) for t in get(p, "points", [])]
    g = get(p, "grid", 0)
    g > 0 && append!(pts, collect(grid(stmt.DOMAIN, g)))
    isempty(pts) && error("give `points` and/or `grid` > 0")
    n, cert, inconc, why, minlo, violation = 0, 0, 0, nothing, nothing, nothing
    for θ in pts
        n += 1
        res, msg, m = evaluate(stmt, θ)
        if res == "violated"
            violation = (θ, m)
            break                                   # the first certified counterexample settles it
        elseif res == "certified"
            cert += 1
            (minlo === nothing || lo(m) < minlo) && (minlo = lo(m))
        else
            inconc += 1
            why === nothing && (why = (θ, msg))
        end
    end
    outcome = violation !== nothing ? "violated" : inconc == 0 ? "certified" : "inconclusive"
    check = Dict{String,Any}("kind" => "certify", "outcome" => outcome, "points" => n, "certified_points" => cert,
                             "inconclusive_points" => inconc, "precision_bits" => bits,
                             "arithmetic" => "interval (MPFR outward rounding)",
                             "scope_note" => "certified = holds at these finitely many points; not a proof over DOMAIN")
    minlo === nothing || (check["min_certified_margin_lo"] = string(minlo))
    if violation !== nothing
        check["violation_theta"] = theta_dict(violation[1])
        check["violation_margin"] = [string(lo(violation[2])), string(hi(violation[2]))]
    end
    why === nothing || (check["first_inconclusive"] = Dict("theta" => theta_dict(why[1]), "reason" => why[2]))
    return check
end

check = main(stmt, p)
write_json(joinpath(ENV["RUN_OUT"], "reported.json"),
           Dict("check" => check, "metrics" => Dict("certified_points" => check["certified_points"])))

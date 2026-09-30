# `exhaustive` check: evaluate an exact statement at EVERY point of its finite, discrete DOMAIN (in order) and stop
# at the first counterexample. For claims about integers, rationals or finite structures (matrices, graphs,
# point sets), where floating point is the wrong tool. Statement contract:
#   const DOMAIN = (N = 1:4, k = [2, 3, 5])    discrete only: ranges or vectors
#   holds(θ)      true if the claim holds at θ; anything else (false, or a witness such as a matrix) is a
#                 counterexample and is recorded with repr()
#   cases(θ)      optional: how many elementary cases holds(θ) examined (recorded as coverage)
# Outcomes: violated (first counterexample θ and witness), exhausted (held at every point of the DOMAIN: a complete
# check of that finite set, still not a statement about anything beyond it), partial (stopped at `limit` points).
# params: statement, module, limit (maximum number of DOMAIN points)
using TOML
include(joinpath(@__DIR__, "MiniJSON.jl")); using .MiniJSON
include(joinpath(@__DIR__, "Domain.jl")); using .Domain

spec = TOML.parsefile(ENV["RUN_SPEC"])
p = spec["params"]
include(joinpath(pwd(), p["statement"]))
stmt = getfield(Main, Symbol(p["module"]))

function main(stmt, limit)
    dom = stmt.DOMAIN
    names = keys(dom)
    any(iscont(dom[k]) for k in names) && error("exhaustive needs a discrete DOMAIN (ranges or vectors)")
    total = prod(big(length(dom[k])) for k in names; init = big(1))
    n, cases, violation = 0, big(0), nothing
    for t in Iterators.product((dom[k] for k in names)...)
        n >= limit && break
        θ = NamedTuple{names}(t)
        n += 1
        r = stmt.holds(θ)
        isdefined(stmt, :cases) && (cases += stmt.cases(θ))
        if r !== true
            violation = (θ, r)
            break                                   # the first counterexample settles it
        end
    end
    outcome = violation !== nothing ? "violated" : n == total ? "exhausted" : "partial"
    check = Dict{String,Any}("kind" => "exhaustive", "outcome" => outcome, "points" => n,
                             "domain_size" => string(total), "arithmetic" => "exact",
                             "scope_note" => "exhausted = holds at every point of the finite DOMAIN only")
    isdefined(stmt, :cases) && (check["cases"] = string(cases))
    if violation !== nothing
        check["violation_theta"] = theta_dict(violation[1])
        w = repr(violation[2])
        check["witness"] = length(w) > 500 ? w[1:500] * "..." : w
    end
    return check
end

check = main(stmt, get(p, "limit", 1_000_000))
write_json(joinpath(ENV["RUN_OUT"], "reported.json"),
           Dict("check" => check, "metrics" => Dict("points" => check["points"])))

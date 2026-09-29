# `falsify` check: minimize a statement's score over its DOMAIN (random pool + pattern search)
# at BigFloat precision. "survived" means no violation found within the recorded effort;
# it is NOT a proof. Float64 evaluation is deliberately not offered: bounds this small
# are below double-precision roundoff.
# params: statement, module, evals (budget), starts (number of refinements)
using TOML, Random
include(joinpath(@__DIR__, "MiniJSON.jl")); using .MiniJSON
include(joinpath(@__DIR__, "Domain.jl")); using .Domain

spec = TOML.parsefile(ENV["RUN_SPEC"])
p = spec["params"]
bits = spec["numerics"]["precision_bits"]
seed = first(spec["numerics"]["seeds"])
setprecision(BigFloat, bits)
include(joinpath(pwd(), p["statement"]))
stmt = getfield(Main, Symbol(p["module"]))

function move(θ, dom, k, δ)
    x, r = θ[k], dom[k]
    if iscont(r)
        y = clamp(Float64(x) + δ, Float64(r[1]), Float64(r[2]))
    else
        y = Int(x) + round(Int, δ)
        y in r || return nothing
    end
    y == x && return nothing
    return merge(θ, NamedTuple{(k,)}((y,)))
end

function refine(f, dom, θ, v, limit, evals)
    names = keys(dom)
    steps = Dict(k => (iscont(dom[k]) ? (Float64(dom[k][2]) - Float64(dom[k][1])) / 4 : 8.0) for k in names)
    while evals[] < limit && maximum(values(steps)) > 1e-3
        improved = false
        for k in names, s in (-1, 1)
            evals[] >= limit && break
            cand = move(θ, dom, k, s * steps[k])
            cand === nothing && continue
            c = f(cand)
            if c < v
                θ, v, improved = cand, c, true
            end
        end
        improved || foreach(k -> steps[k] /= 2, names)
    end
    return θ, v
end

function main(stmt, seed, budget, starts)
    dom, names, rng, evals = stmt.DOMAIN, keys(stmt.DOMAIN), Xoshiro(seed), Ref(0)
    best = Ref{Any}((nothing, nothing))
    f = θ -> begin
        evals[] += 1
        v = score(stmt, θ, BigFloat)
        (best[][2] === nothing || v < best[][2]) && (best[] = (θ, v))
        v
    end
    randθ() = NamedTuple{names}(map(names) do k
        r = dom[k]
        iscont(r) ? Float64(r[1]) + (Float64(r[2]) - Float64(r[1])) * rand(rng) : rand(rng, r)
    end)
    pool = [(θ, f(θ)) for θ in (randθ() for _ in 1:max(starts, budget ÷ 4))]
    sort!(pool; by = last)
    for (i, (θ, v)) in enumerate(first(pool, starts))
        limit = evals[] + (budget - evals[]) ÷ (starts - i + 1)
        refine(f, dom, θ, v, limit, evals)
    end
    return best[], evals[]
end

(θ, v), n = main(stmt, seed, p["evals"], p["starts"])
check = Dict("kind" => "falsify", "outcome" => v < 0 ? "violated" : "survived",
             "evals" => n, "seed" => seed, "precision_bits" => bits,
             "best_score" => string(v), "best_theta" => theta_dict(θ))
write_json(joinpath(ENV["RUN_OUT"], "reported.json"),
           Dict("check" => check, "metrics" => Dict("best_score" => string(v))))

# `highprec` check: evaluate a statement's margin at high precision on a grid over its DOMAIN.
# params: statement (path relative to cwd), module (name), grid (points per continuous dim)
using TOML
include(joinpath(@__DIR__, "MiniJSON.jl")); using .MiniJSON
include(joinpath(@__DIR__, "Domain.jl")); using .Domain

spec = TOML.parsefile(ENV["RUN_SPEC"])
p = spec["params"]
bits = spec["numerics"]["precision_bits"]
setprecision(BigFloat, bits)
include(joinpath(pwd(), p["statement"]))
stmt = getfield(Main, Symbol(p["module"]))

function main(stmt, g)
    n, viol, worst, worstθ = 0, 0, nothing, nothing
    for θ in grid(stmt.DOMAIN, g)
        v = score(stmt, θ, BigFloat)
        n += 1
        v < 0 && (viol += 1)
        if worst === nothing || v < worst
            worst, worstθ = v, θ
        end
    end
    return n, viol, worst, worstθ
end

n, viol, worst, worstθ = main(stmt, p["grid"])
check = Dict("kind" => "highprec", "outcome" => viol == 0 ? "pass" : "fail",
             "points" => n, "violations" => viol, "precision_bits" => bits,
             "worst_score" => string(worst), "worst_theta" => theta_dict(worstθ))
write_json(joinpath(ENV["RUN_OUT"], "reported.json"),
           Dict("check" => check, "metrics" => Dict("worst_score" => string(worst))))

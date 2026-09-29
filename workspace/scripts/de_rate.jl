# Experiment: DE quadrature error vs N on the endpoint-singular family, at high precision.
# Reads RUN_SPEC (params a, b, d as decimal strings; N as an integer list); writes RUN_OUT.
using TOML
include(joinpath(@__DIR__, "..", "..", "checkers", "MiniJSON.jl")); using .MiniJSON
include(joinpath(@__DIR__, "..", "library", "DESinc.jl")); using .DESinc

spec = TOML.parsefile(ENV["RUN_SPEC"])
bits = spec["numerics"]["precision_bits"]
setprecision(BigFloat, bits)
p = spec["params"]
a, b, d = (parse(BigFloat, string(p[k])) for k in ("a", "b", "d"))
α = min(a, b) + 1
exact = family_exact(a, b)
floor_err = big"2.0"^(-(bits - 32))           # below this, errors are roundoff, not method error

errs, ceff, sat = Dict{String,Any}(), Dict{String,Any}(), Dict{String,Any}()
open(joinpath(ENV["RUN_OUT"], "errors.csv"), "w") do io
    println(io, "N,h,error")
    for N in p["N"]
        h = de_h(N, d, α)
        err = abs(de_quad(family_f(a, b), N, h) - exact)
        println(io, N, ',', h, ',', err)
        errs[string(N)] = string(err)
        ceff[string(N)] = err > 0 ? string(-log(err) * log(BigFloat(N)) / N) : "Inf"
        sat[string(N)] = err < floor_err
    end
end
write_json(joinpath(ENV["RUN_OUT"], "reported.json"),
           Dict("metrics" => Dict("error" => errs, "c_eff" => ceff, "saturated" => sat)))

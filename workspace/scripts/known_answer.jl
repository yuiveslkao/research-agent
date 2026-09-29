# Known-answer test for library/DESinc.jl against the mpmath reference (run as a `check` run).
# Julia arithmetic at 512 bits must reproduce the reference errors to ~25 digits.
using TOML
include(joinpath(@__DIR__, "..", "..", "checkers", "MiniJSON.jl")); using .MiniJSON
include(joinpath(@__DIR__, "..", "library", "DESinc.jl")); using .DESinc

spec = TOML.parsefile(ENV["RUN_SPEC"])
setprecision(BigFloat, spec["numerics"]["precision_bits"])
ref = TOML.parsefile(joinpath(@__DIR__, "..", "library", "testdata", "de_reference.toml"))
a, b, d = (parse(BigFloat, ref[k]) for k in ("a", "b", "d"))
α = min(a, b) + 1
exact = family_exact(a, b)
rows, ok = Dict{String,Any}(), true
for (Ns, r) in sort(collect(ref["error"]); by = kv -> parse(Int, kv[1]))
    N = parse(Int, Ns)
    h = de_h(N, d, α)
    err = abs(de_quad(family_f(a, b), N, h) - exact)
    want = parse(BigFloat, r["value"])
    rel = abs(err - want) / want
    good = rel < big"1e-25"
    ok &= good
    rows[Ns] = Dict("error" => string(err), "reference" => r["value"], "rel_diff" => string(rel), "ok" => good)
end
write_json(joinpath(ENV["RUN_OUT"], "reported.json"),
           Dict("check" => Dict("outcome" => ok ? "pass" : "fail"), "metrics" => rows))
exit(ok ? 0 : 1)

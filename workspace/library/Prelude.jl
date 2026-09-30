# Helpers for agent-written experiments (and any script following the run contract).
#   include(joinpath(@__DIR__, "..", "..", "library", "Prelude.jl")); using .Prelude
# Reads RUN_SPEC / RUN_OUT from the environment set by the harness.
module Prelude
using TOML
include(joinpath(@__DIR__, "..", "..", "checkers", "MiniJSON.jl"))
using .MiniJSON: write_json
export SPEC, PARAMS, OUT, use_precision!, real_param, report, save_csv

const SPEC = TOML.parsefile(ENV["RUN_SPEC"])
const PARAMS = get(SPEC, "params", Dict{String,Any}())
const OUT = ENV["RUN_OUT"]

"Set BigFloat precision from numerics.precision_bits."
use_precision!() = setprecision(BigFloat, SPEC["numerics"]["precision_bits"])

"Real parameter `name` as a BigFloat; pass non-dyadic reals as decimal strings."
function real_param(name::AbstractString)
    v = PARAMS[name]
    return v isa AbstractString ? parse(BigFloat, v) : BigFloat(v)
end

tojson(x::AbstractFloat) = string(x)
tojson(x::Rational) = string(x)                   # exact, e.g. "1//3"
tojson(x::Union{AbstractString,Integer,Bool,Nothing}) = x
tojson(x::AbstractDict) = Dict{String,Any}(string(k) => tojson(v) for (k, v) in x)
tojson(x::AbstractVector) = Any[tojson(v) for v in x]

"Write reported.json: `metrics` is a Dict; reals become decimal strings."
report(metrics::AbstractDict; notes::AbstractString = "") =
    write_json(joinpath(OUT, "reported.json"), Dict("metrics" => tojson(metrics), "notes" => notes))

"Write a CSV artifact into RUN_OUT (values printed with full precision)."
function save_csv(name::AbstractString, header, rows)
    open(joinpath(OUT, name), "w") do io
        println(io, join(header, ','))
        for r in rows
            println(io, join(string.(r), ','))
        end
    end
end

end # module

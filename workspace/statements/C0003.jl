# Executable statement C0003: expected to be VIOLATED (planted flaw: K = 2)
#
# Planted-flaw test for `falsify` (same statement as C0002, one constant differs).
# The bound shape exp(-K π d N / log(4dN/α)) is an ILLUSTRATIVE PLACEHOLDER, not a theorem.
# mpmath prototype (200 digits) on this DOMAIN: max err/bound = 83 for K = 2.
module C0003
include(joinpath(@__DIR__, "..", "library", "DESinc.jl"))
using .DESinc

const INFO = (id = "C0003", form = :error_vs_bound, rigorous = false)
const K = 2   # PLANTED FLAW: the correct constant is 1

const DOMAIN = (d = (1//2, 1//1), a = (-1//2, 1//1), b = (-1//2, 1//1), N = 8:64)

bound(N, d, α, ::Type{T}) where {T<:Real} = exp(-K * T(π) * d * N / log(4 * d * N / α))

function parts(θ, ::Type{T}) where {T<:Real}
    d, a, b = T(θ.d), T(θ.a), T(θ.b)
    α = min(a, b) + 1
    h = de_h(θ.N, d, α)
    err = abs(de_quad(family_f(a, b), θ.N, h) - family_exact(a, b))
    return err, bound(θ.N, d, α, T)
end

margin(θ, ::Type{T}) where {T<:Real} = (p = parts(θ, T); p[2] - p[1])
relmargin(θ, ::Type{T}) where {T<:Real} = (p = parts(θ, T); 1 - p[1] / p[2])

end # module

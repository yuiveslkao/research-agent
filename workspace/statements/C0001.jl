# Executable statement for claim C-0001.
#
# ILLUSTRATIVE PLACEHOLDER, NOT A THEOREM. The bound below is an arbitrary shape
# exp(-π d N / log(4dN/α)) chosen so that the Phase 0 pipeline has something to falsify.
# Replace `bound` with the bound of the result under study, and set DOMAIN to the
# parameter box the claim quantifies over.
#
# Self-contained: loads its own library dependencies. Generic over T<:Real; no Float64 literals.
module C0001
include(joinpath(@__DIR__, "..", "library", "DESinc.jl"))
using .DESinc

const INFO = (id = "C-0001", form = :error_vs_bound, rigorous = false)

# continuous: (lo, hi) exact rationals; discrete: ranges
const DOMAIN = (d = (1//5, 3//2), a = (-1//2, 2//1), b = (-1//2, 2//1), N = 8:64)

bound(N, d, α, ::Type{T}) where {T<:Real} = exp(-T(π) * d * N / log(4 * d * N / α))

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

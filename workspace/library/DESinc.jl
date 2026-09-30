# DE (double-exponential) quadrature on (-1, 1), generic over the number type T.
# Write no Float64 literals here: the same code must run at Float64, BigFloat, and later
# interval types.
module DESinc
using SpecialFunctions: gamma
export de_quad, de_h, family_f, family_exact

"""
    de_quad(f, N, h)

Approximate ∫_{-1}^{1} f(x) dx by h Σ_{k=-N}^{N} f(φ(kh)) φ'(kh), φ(t) = tanh(π/2 sinh t).
`f` is called as `f(x, omx, opx)` where omx = 1 - x and opx = 1 + x are computed without
cancellation near the endpoints.
"""
function de_quad(f, N::Integer, h::T) where {T<:Real}
    s = zero(T)
    halfpi = T(π) / 2
    for k in -N:N
        t = k * h
        u = halfpi * sinh(t)
        w = halfpi * cosh(t) / cosh(u)^2
        iszero(w) && continue                      # underflow at the far tails
        omx = 2 / (1 + exp(2u))
        opx = 2 / (1 + exp(-2u))
        s += f(tanh(u), omx, opx) * w
    end
    return h * s
end

"""Mesh size h = log(4dN/α)/N. A common choice; replace with the rule of the result under study."""
de_h(N::Integer, d::T, α::T) where {T<:Real} = log(4 * d * N / α) / N

"""Endpoint-singular test family f(x) = (1-x)^a (1+x)^b, a, b > -1."""
family_f(a::T, b::T) where {T<:Real} = (x, omx, opx) -> omx^a * opx^b

"""Exact integral of the family: 2^(a+b+1) Γ(a+1) Γ(b+1) / Γ(a+b+2)."""
family_exact(a::T, b::T) where {T<:Real} =
    T(2)^(a + b + 1) * gamma(a + 1) * gamma(b + 1) / gamma(a + b + 2)

end # module

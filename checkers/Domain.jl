# Shared helpers for statement checkers. A statement module defines
#   DOMAIN   NamedTuple; (lo, hi) tuple = continuous box, range/vector = discrete set
#   margin(θ, T)    >= 0 iff the claim holds at θ
#   relmargin(θ, T) optional, scale-free version (preferred when present)
module Domain
export iscont, grid, score, theta_dict

iscont(x) = x isa Tuple

"Cartesian grid over DOMAIN with `g` points per continuous dimension (exact Rationals)."
function grid(dom, g::Integer)
    names = keys(dom)
    axs = map(names) do k
        v = dom[k]
        iscont(v) ? [v[1] + (v[2] - v[1]) * (i // (g - 1)) for i in 0:g-1] : collect(v)
    end
    return (NamedTuple{names}(t) for t in Iterators.product(axs...))
end

score(stmt, θ, ::Type{T}) where {T} =
    isdefined(stmt, :relmargin) ? stmt.relmargin(θ, T) : stmt.margin(θ, T)

theta_dict(θ) = Dict(string(k) => string(v) for (k, v) in pairs(θ))

end # module

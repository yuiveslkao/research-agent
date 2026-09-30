# Exact and finite tools for agent experiments (stdlib only): rational linear algebra, enumeration, canonical forms
# under relabeling, overflow-checked integer matrices, and sequence guessing.
#   include(joinpath(@__DIR__, "..", "..", "library", "Exact.jl")); using .Exact
# Use Rational{BigInt} (qq("1/3"), qq(2)) for exact values; report them with string(x) (e.g. "1//3").
#
# Rigor labels: linear algebra, enumeration and checked arithmetic are exact. guess_polynomial and guess_recurrence
# only return a formula that also reproduces `check` terms NOT used to fit it; the result is a conjecture that fits
# the given terms, never a statement about all n.
module Exact
export Q, qq, rref, rank_q, nullspace_q, solve_q, interpolatory_weights,
       binary_matrices, combinations, permutations_of, canonical_form, orbit_size,
       checked_matmul, checked_matpow, guess_polynomial, guess_recurrence, differences

const Q = Rational{BigInt}

"Exact rational from an integer, a Rational, or a string such as \"3/4\", \"3//4\", \"0.25\", \"-1.5e-3\"."
qq(x::Integer) = Q(x)
qq(x::Rational) = Q(x)
function qq(s::AbstractString)
    t = replace(strip(s), "//" => "/")
    if occursin('/', t)
        a, b = split(t, '/')
        return Q(parse(BigInt, a), parse(BigInt, b))
    end
    m = match(r"^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$", t)
    (m === nothing || isempty(m[2] * something(m[3], ""))) && throw(ArgumentError("not an exact number: $s"))
    frac = something(m[3], "")
    q = Q(parse(BigInt, m[2] * frac), big(10)^length(frac))
    e = m[4] === nothing ? 0 : parse(Int, m[4])
    q = e >= 0 ? q * big(10)^e : q / big(10)^(-e)
    return m[1] == "-" ? -q : q
end

# ------------------------------------------------------------------ linear algebra over Q

"Reduced row echelon form over Q: (R, pivot columns)."
function rref(A::AbstractMatrix)
    R = Q.(A)
    m, n = size(R)
    pivots = Int[]
    r = 1
    for c in 1:n
        r > m && break
        p = findfirst(i -> !iszero(R[i, c]), r:m)
        p === nothing && continue
        p += r - 1
        R[r, :], R[p, :] = R[p, :], R[r, :]
        R[r, :] ./= R[r, c]
        for i in 1:m
            i != r && !iszero(R[i, c]) && (R[i, :] .-= R[i, c] .* R[r, :])
        end
        push!(pivots, c)
        r += 1
    end
    return R, pivots
end

rank_q(A::AbstractMatrix) = length(rref(A)[2])

"Basis of the null space {x : A x = 0} over Q, as columns of a matrix (n x k)."
function nullspace_q(A::AbstractMatrix)
    R, piv = rref(A)
    n = size(A, 2)
    free = setdiff(1:n, piv)
    N = zeros(Q, n, length(free))
    for (j, f) in enumerate(free)
        N[f, j] = 1
        for (i, p) in enumerate(piv)
            N[p, j] = -R[i, f]
        end
    end
    return N
end

"One exact solution of A x = b, or `nothing` if the system is inconsistent. General solution: x + nullspace_q(A) * t."
function solve_q(A::AbstractMatrix, b::AbstractVector)
    R, piv = rref(hcat(Q.(A), Q.(b)))
    n = size(A, 2)
    (n + 1) in piv && return nothing
    x = zeros(Q, n)
    for (i, p) in enumerate(piv)
        x[p] = R[i, end]
    end
    return x
end

"""Weights w with sum_i w_i x_i^k = moments[k+1] for k = 0..length(nodes)-1 (interpolatory rule), exactly;
`nothing` if no such weights exist. Example: nodes [-1, 0, 1], moments of [-1, 1] = [2, 0, 2//3] gives Simpson."""
function interpolatory_weights(nodes::AbstractVector, moments::AbstractVector)
    n = length(nodes)
    V = [Q(x)^k for k in 0:n-1, x in nodes]
    return solve_q(V, Q.(moments[1:n]))
end

# ------------------------------------------------------------------ enumeration and symmetry

"All n x n 0/1 matrices as Matrix{Int}, in the order of their bit encoding (entry (i,j) = bit (i-1)n+(j-1))."
function binary_matrices(n::Integer)
    last = n * n <= 62 ? (1 << (n * n)) - 1 : big(2)^(n * n) - 1
    return (Int[(bits >> ((i - 1) * n + (j - 1))) & 1 for i in 1:n, j in 1:n] for bits in 0:last)
end

"All k-element subsets of 1:n as sorted vectors, in lexicographic order."
function combinations(n::Integer, k::Integer)
    out = Vector{Vector{Int}}()
    c = collect(1:k)
    k > n && return out
    k == 0 && return [Int[]]
    while true
        push!(out, copy(c))
        i = k
        while i >= 1 && c[i] == n - k + i
            i -= 1
        end
        i == 0 && return out
        c[i] += 1
        for j in i+1:k
            c[j] = c[j-1] + 1
        end
    end
end

"All permutations of 1:n in lexicographic order."
function permutations_of(n::Integer)
    p = collect(1:n)
    out = [copy(p)]
    while true
        i = findlast(j -> p[j] < p[j+1], 1:n-1)
        i === nothing && return out
        j = findlast(t -> p[t] > p[i], 1:n)
        p[i], p[j] = p[j], p[i]
        reverse!(p, i + 1, n)
        push!(out, copy(p))
    end
end

"""Canonical form of a square matrix under simultaneous permutation of rows and columns (relabeling the vertices
of the directed graph it describes): the lexicographically smallest relabeled matrix. Cost n! n^2; fine to n = 7."""
function canonical_form(A::AbstractMatrix)
    n = size(A, 1)
    best = nothing
    for p in permutations_of(n)
        B = A[p, p]
        (best === nothing || vec(B) < vec(best)) && (best = B)
    end
    return best
end

"Number of distinct matrices obtained from A by simultaneous row/column permutations (the orbit size)."
orbit_size(A::AbstractMatrix) = length(Set(vec(A[p, p]) for p in permutations_of(size(A, 1))))

# ------------------------------------------------------------------ checked integer arithmetic

"Integer matrix product that throws OverflowError instead of wrapping around (use BigInt matrices to avoid it)."
function checked_matmul(A::AbstractMatrix{T}, B::AbstractMatrix{T}) where {T<:Integer}
    size(A, 2) == size(B, 1) || throw(DimensionMismatch("sizes $(size(A)) and $(size(B))"))
    C = zeros(T, size(A, 1), size(B, 2))
    for i in axes(A, 1), j in axes(B, 2)
        s = zero(T)
        for k in axes(A, 2)
            s = Base.checked_add(s, Base.checked_mul(A[i, k], B[k, j]))
        end
        C[i, j] = s
    end
    return C
end

function checked_matpow(A::AbstractMatrix{T}, k::Integer) where {T<:Integer}
    k >= 1 || throw(ArgumentError("k must be >= 1"))
    P = copy(A)
    for _ in 2:k
        P = checked_matmul(P, A)
    end
    return P
end

# ------------------------------------------------------------------ sequence guessing (conjectures, checked)

differences(s::AbstractVector) = [s[i+1] - s[i] for i in 1:length(s)-1]

"""Polynomial p of lowest degree with p(offset + i - 1) = s[i] for all given terms, fitted on all but the last
`check` terms and verified on those. Returns coefficient vector (constant first) over Q, or `nothing`."""
function guess_polynomial(s::AbstractVector; offset::Integer = 0, check::Integer = 2)
    m = length(s)
    for d in 0:(m - check - 1)
        pts = 1:(d + 1)
        V = [Q(offset + i - 1)^k for i in pts, k in 0:d]
        c = solve_q(V, Q.(s[pts]))
        c === nothing && continue
        all(sum(c[k+1] * Q(offset + i - 1)^k for k in 0:d) == s[i] for i in 1:m) && return c
    end
    return nothing
end

"""Linear recurrence s[n] = c1 s[n-1] + ... + cr s[n-r] with constant rational coefficients, of the lowest order
r <= maxorder that holds on every given term, where at least `check` equations were not used to determine the
coefficients. Returns [c1, ..., cr] over Q, or `nothing`."""
function guess_recurrence(s::AbstractVector; maxorder::Integer = 6, check::Integer = 2)
    m = length(s)
    for r in 1:maxorder
        neq = m - r                       # equations available
        neq >= r + check || break
        A = [Q(s[n - j]) for n in (r + 1):(2r), j in 1:r]
        c = solve_q(A, Q.(s[(r + 1):(2r)]))
        c === nothing && continue
        all(sum(c[j] * s[n - j] for j in 1:r) == s[n] for n in (r + 1):m) && return c
    end
    return nothing
end

end # module

# Answer-key verification for GQ-MAT-01 and GQ-MAT-02 (human-owned; agent runs cannot see eval/).
# Stdlib only:  julia eval/verify/matrix_powers.jl [Nmax_bruteforce=4] [Nmax_graph=5]
#
# For every N x N 0/1 matrix A (N <= Nmax_bruteforce) two independent tests are compared matrix by matrix:
#  (1) definition: the integer powers A^k are eventually periodic, detected exactly as a repeat A^i == A^j with
#      i < j <= K (Int128 arithmetic, K = 64, exact for N <= 4 since entries are at most N^(K-1) < 2^127).
#      A repeat implies boundedness. No repeat within K steps is classified as unbounded.
#  (2) graph test: in the digraph with adjacency A, every strongly connected component is either a vertex without
#      a loop or a single directed cycle (edges == vertices inside it), and no directed path joins two different
#      cyclic components.
# The script fails if (1) and (2) disagree on any matrix, then prints a(N). For N up to Nmax_graph it also
# counts with test (2) alone, and it prints a counterexample to "power-bounded implies nilpotent or permutation".

const K = 64

function matpow_repeat(A::Matrix{Int128})
    seen = Dict{Vector{Int128},Int}()
    P = copy(A)
    for k in 1:K
        key = vec(P)
        haskey(seen, key) && return true
        seen[key] = k
        P = P * A
    end
    return false
end

function reach(adj::Matrix{Bool})
    n = size(adj, 1)
    R = copy(adj)
    for k in 1:n, i in 1:n, j in 1:n        # Warshall: R[i,j] = path of length >= 1
        R[i, j] |= R[i, k] & R[k, j]
    end
    return R
end

function graph_bounded(adj::Matrix{Bool})
    n = size(adj, 1)
    R = reach(adj)
    comp = [findfirst(j -> j == i || (R[i, j] && R[j, i]), 1:n) for i in 1:n]   # representative per SCC
    cyclic = Int[]
    for c in unique(comp)
        vs = [v for v in 1:n if comp[v] == c]
        e = count(adj[u, v] for u in vs, v in vs)
        e == 0 && continue                     # single vertex without loop
        e == length(vs) || return false        # strongly connected with more edges than vertices: 2+ cycles
        push!(cyclic, c)
    end
    for c1 in cyclic, c2 in cyclic
        c1 != c2 && R[c1, c2] && return false  # a path from one cycle to another
    end
    return true
end

tomatrix(bits::Int, n::Int) = [((bits >> ((i - 1) * n + (j - 1))) & 1) == 1 for i in 1:n, j in 1:n]

isperm01(A) = all(sum(A; dims = 1) .== 1) && all(sum(A; dims = 2) .== 1)
isnilpotent(A) = iszero(Int128.(A)^size(A, 1))

function main(nb::Int, ng::Int)
    for n in 1:nb
        cnt, witness = 0, nothing
        for bits in 0:(2^(n * n) - 1)
            adj = tomatrix(bits, n)
            d = matpow_repeat(Int128.(adj))
            g = graph_bounded(adj)
            d == g || error("tests disagree for N=$n, matrix $(Int.(adj))")
            cnt += d
            if d && witness === nothing && !isnilpotent(adj) && !isperm01(adj)
                witness = Int.(adj)
            end
        end
        println("a($n) = $cnt   (definition and graph test agree on all $(2^(n*n)) matrices)")
        witness === nothing || println("  bounded but neither nilpotent nor a permutation: ", witness)
    end
    for n in (nb + 1):ng
        cnt = 0
        for bits in 0:(2^(n * n) - 1)
            cnt += graph_bounded(tomatrix(bits, n))
        end
        println("a($n) = $cnt   (graph test only)")
    end
end

main(length(ARGS) >= 1 ? parse(Int, ARGS[1]) : 4, length(ARGS) >= 2 ? parse(Int, ARGS[2]) : 5)

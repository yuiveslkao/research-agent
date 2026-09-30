# Answer-key verification for GQ-PRF-01 (human-owned; agent runs cannot see eval/). Stdlib only:
#   julia eval/verify/square_zero.jl [Nmax=4]
# Brute force: the largest number of ones in an N x N 0/1 matrix with A^2 = 0 equals floor(N^2/4); and the
# planted step of the supplied argument, N(N-1)/2 <= floor(N^2/4), is false for every N >= 3.
function maxones(n)
    best, witness = 0, nothing
    for bits in 0:(2^(n * n) - 1)
        A = [((bits >> ((i - 1) * n + (j - 1))) & 1) for i in 1:n, j in 1:n]
        ones = count(==(1), A)
        if ones > best && iszero(A * A)
            best, witness = ones, A
        end
    end
    return best, witness
end
nmax = length(ARGS) >= 1 ? parse(Int, ARGS[1]) : 4
for n in 1:nmax
    m, w = maxones(n)
    println("N=$n: max ones with A^2 = 0 is $m, floor(N^2/4) = $(n^2 ÷ 4)", m == n^2 ÷ 4 ? "" : "   MISMATCH",
            "; N(N-1)/2 = $(n * (n - 1) ÷ 2)", "; witness ", w)
    m == n^2 ÷ 4 || exit(1)
end
println("planted step N(N-1)/2 <= floor(N^2/4) fails for N = ", [n for n in 1:10 if n * (n - 1) ÷ 2 > n^2 ÷ 4])

# Tests for Exact.jl. Run: julia workspace/library/test_exact.jl   (stdlib only)
using Test
include(joinpath(@__DIR__, "Exact.jl")); using .Exact

@testset "exact numbers" begin
    @test qq("3/4") == 3 // 4 && qq("3//4") == 3 // 4 && qq("0.25") == 1 // 4 && qq("-1.5e-3") == -3 // 2000
    @test qq(7) isa Q && qq(2 // 6) == 1 // 3
    @test_throws ArgumentError qq("pi")
end
@testset "linear algebra over Q" begin
    A = [1 2 3; 2 4 6; 1 0 1]
    @test rank_q(A) == 2
    N = nullspace_q(A)
    @test size(N) == (3, 1) && all(iszero, Q.(A) * N)
    @test solve_q([1 1; 1 -1], [3, 1]) == Q[2, 1]
    @test solve_q([1 1; 2 2], [1, 3]) === nothing
    @test interpolatory_weights([-1, 0, 1], [2, 0, 2 // 3]) == Q[1 // 3, 4 // 3, 1 // 3]          # Simpson
    @test interpolatory_weights([0, 1, 2, 3], [3, 9 // 2, 9, 81 // 4]) == Q[3 // 8, 9 // 8, 9 // 8, 3 // 8]   # 3/8 rule
end
@testset "enumeration and symmetry" begin
    @test length(combinations(4, 2)) == 6 && combinations(4, 2)[1] == [1, 2] && combinations(3, 0) == [Int[]]
    @test permutations_of(3) == [[1, 2, 3], [1, 3, 2], [2, 1, 3], [2, 3, 1], [3, 1, 2], [3, 2, 1]]
    mats = collect(binary_matrices(2))
    @test length(mats) == 16 && mats[2] == [1 0; 0 0]
    reps = unique(canonical_form.(mats))
    @test length(reps) == 10                                   # directed graphs with loops on 2 unlabeled vertices
    @test sum(orbit_size, reps) == 16
    @test canonical_form([0 1; 0 0]) == canonical_form([0 0; 1 0])
end
@testset "checked integer arithmetic" begin
    A = Int8[10 10; 10 10]
    @test_throws OverflowError checked_matmul(A, A)
    @test checked_matpow([1 1; 0 1], 5) == [1 5; 0 1]
    @test checked_matpow(big.([2 0; 0 3]), 100)[2, 2] == big(3)^100
end
@testset "sequence guessing (checked on unused terms)" begin
    @test guess_polynomial([0, 1, 4, 9, 16, 25]) == Q[0, 0, 1]
    @test guess_polynomial([0, 1, 4]) === nothing                 # too few terms to check a quadratic
    @test guess_polynomial([1, 3, 5, 7]; offset = 1) == Q[-1, 2]
    @test guess_recurrence([1, 1, 2, 3, 5, 8, 13, 21]) == Q[1, 1]
    @test guess_recurrence([1, 2, 4, 8, 16]) == Q[2]
    @test guess_recurrence([2, 3, 5, 7, 11, 13, 17, 19, 23]; maxorder = 3) === nothing
    @test differences([1, 4, 9]) == [3, 5]
end

# Tests for Intervals.jl: enclosures computed at 256 bits must contain the value at the same point computed at
# 2048 bits. Run: julia checkers/test_intervals.jl   (stdlib only; run it in the pinned container after upgrades)
using Test
include(joinpath(@__DIR__, "Intervals.jl")); using .Intervals

# f at the same binary point x, extended exactly to 2048 bits
ref(f, x::Real) = setprecision(BigFloat, 2048) do
    f(BigFloat(x; precision = 2048))
end
gamma_near(v) = Intervals.gamma_r(v, Cint(0))
encloses(iv::Iv, v) = lo(iv) <= v <= hi(iv)

setprecision(BigFloat, 256) do
    @testset "construction" begin
        t = Iv(1 // 10)
        @test lo(t) < 1 // 10 < hi(t)
        @test lo(Iv(3)) == hi(Iv(3)) == 3
        @test encloses(Iv(π), setprecision(() -> BigFloat(π), BigFloat, 2048))
        @test lo(Iv(0.25)) == hi(Iv(0.25))
        @test Iv(2) + 1 isa Iv && 3 * Iv(1 // 3) isa Iv
        @test lo(parse(Iv, "7")) == hi(parse(Iv, "7")) == 7
        @test lo(parse(Iv, "0.1")) < 1 // 10 < hi(parse(Iv, "0.1"))
    end
    @testset "arithmetic encloses" begin
        third = Iv(1) / Iv(3)
        @test lo(third) < hi(third) && lo(3 * third) <= 1 <= hi(3 * third)
        @test encloses(Iv(2) - Iv(1 // 3), setprecision(() -> big(2) - big(1) / 3, BigFloat, 2048))
        @test lo(Iv(-2, 3) * Iv(-5, 1)) == -15 && hi(Iv(-2, 3) * Iv(-5, 1)) == 10
        @test_throws IntervalError Iv(1) / Iv(-1, 1)
    end
    @testset "functions enclose 2048-bit values" begin
        for x in ("0.3", "1.7", "-2.25", "12.5")
            xb = parse(BigFloat, x)                  # the 256-bit point
            X = Iv(xb)
            for f in (exp, sinh, cosh, tanh)
                @test encloses(f(X), ref(f, xb))
                @test lo(f(X)) < hi(f(X))            # outward rounding widened the point
            end
            if xb > 0
                @test encloses(log(X), ref(log, xb))
                @test encloses(sqrt(X), ref(sqrt, xb))
                @test encloses(gamma_iv(X), ref(gamma_near, xb))
            end
        end
        @test_throws IntervalError log(Iv(-1, 1))
        @test_throws IntervalError sqrt(Iv(-1, 1))
    end
    @testset "non-monotone cases" begin
        c = cosh(Iv(-1, 2))
        @test lo(c) == 1 && encloses(c, ref(cosh, 2))
        s = Iv(-2, 1)^2
        @test lo(s) == 0 && hi(s) == 4
        @test lo(Iv(-2, -1)^3) == -8 && hi(Iv(-2, -1)^3) == -1
        pw = Iv(1 // 2, 2)^Iv(-1, 1)
        @test lo(pw) == 1 // 2 && hi(pw) == 2
        for (a, b) in ((0.2, 0.9), (1.3, 1.6), (2.0, 3.5))       # decreasing, straddling the minimum, increasing
            g = gamma_iv(Iv(a, b))
            for t in range(a, b; length = 7)
                @test encloses(g, ref(gamma_near, t))
            end
        end
        @test_throws IntervalError gamma_iv(Iv(-1, 1))
        @test_throws IntervalError Iv(-1, 2)^Iv(1 // 2)
    end
    @testset "comparisons decide only when certain" begin
        @test Iv(1, 2) < Iv(3, 4)
        @test !(Iv(3, 4) < Iv(1, 2))
        @test_throws IntervalError Iv(1, 3) < Iv(2, 4)
        @test lo(min(Iv(1, 4), Iv(2, 3))) == 1 && hi(min(Iv(1, 4), Iv(2, 3))) == 3
        @test iszero(zero(Iv)) && !iszero(Iv(0, 1))
    end
    @testset "a DE-quadrature term encloses the 2048-bit value" begin
        term(T, k, h) = begin
            hp = T(π) / 2
            t = k * h
            u = hp * sinh(t)
            w = hp * cosh(t) / cosh(u)^2
            omx = 2 / (1 + exp(2u))
            opx = 2 / (1 + exp(-2u))
            omx^(T(1) / 2) * opx^(T(3) / 2) * w
        end
        v = setprecision(() -> term(BigFloat, 5, big(3) / 10), BigFloat, 2048)
        @test encloses(term(Iv, 5, Iv(3 // 10)), v)
    end
end

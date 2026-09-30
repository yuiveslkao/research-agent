# Interval arithmetic on BigFloat for the `certify` checker. Human-owned checker code, no packages.
#
# An `Iv` is a closed interval [lo, hi] of BigFloats. Every endpoint is computed by one MPFR call rounded outward
# (MPFR rounds + - * / exp log sqrt sinh cosh tanh pow gamma correctly in the requested direction), so the result
# of any expression built from these operations encloses the exact real value. Operations whose result is not an
# interval (division by an interval containing 0, log of a nonpositive interval, an undecidable comparison) throw
# IntervalError instead of guessing; the checker reports such points as inconclusive.
#
# Generic statement code runs unchanged with T = Iv: T(π), T(2), T(1//2), Int * Iv, x^a, x^2, min, abs, iszero.
module Intervals
export Iv, IntervalError, lo, hi, gamma_iv

import Base: +, -, *, /, ^, exp, log, sqrt, sinh, cosh, tanh, abs, min, max, zero, one, iszero, isfinite,
             <, <=, ==, inv, float, show, convert, promote_rule

const LIB = isdefined(Base.MPFR, :libmpfr) ? Base.MPFR.libmpfr : "libmpfr"
const RNDU = Cint(2)   # MPFR_RNDU, toward +Inf
const RNDD = Cint(3)   # MPFR_RNDD, toward -Inf

struct IntervalError <: Exception
    msg::String
end
Base.showerror(io::IO, e::IntervalError) = print(io, "IntervalError: ", e.msg)

struct Iv <: Real
    lo::BigFloat
    hi::BigFloat
    function Iv(a::BigFloat, b::BigFloat)
        (isnan(a) || isnan(b) || a > b) && throw(IntervalError("invalid interval [$a, $b]"))
        new(a, b)
    end
end
lo(x::Iv) = x.lo
hi(x::Iv) = x.hi

# ---- correctly rounded MPFR primitives (result at the current BigFloat precision)
for f in (:add, :sub, :mul, :div, :pow)
    @eval function $(Symbol(f, :_r))(x::BigFloat, y::BigFloat, r::Cint)
        z = BigFloat()
        ccall(($(QuoteNode(Symbol(:mpfr_, f))), $LIB), Cint,
              (Ref{BigFloat}, Ref{BigFloat}, Ref{BigFloat}, Cint), z, x, y, r)
        return z
    end
end
for f in (:exp, :log, :sqrt, :sinh, :cosh, :tanh, :gamma)
    @eval function $(Symbol(f, :_r))(x::BigFloat, r::Cint)
        z = BigFloat()
        ccall(($(QuoteNode(Symbol(:mpfr_, f))), $LIB), Cint, (Ref{BigFloat}, Ref{BigFloat}, Cint), z, x, r)
        return z
    end
end
function pow_si_r(x::BigFloat, n::Integer, r::Cint)
    z = BigFloat()
    ccall((:mpfr_pow_si, LIB), Cint, (Ref{BigFloat}, Ref{BigFloat}, Clong, Cint), z, x, Clong(n), r)
    return z
end

# ---- construction: exact where possible, otherwise widened to a certain enclosure
function _enclose(b::BigFloat, exact::Bool)
    exact ? Iv(b, b) : Iv(prevfloat(b), nextfloat(b))       # round-to-nearest is within one ulp
end
Iv(x::BigFloat) = Iv(x, x)
Iv(x::Iv) = x
Iv(x::Union{Float64,Float32,Float16}) = Iv(BigFloat(x))    # exact at precision >= 53
Iv(x::Integer) = (b = BigFloat(x); _enclose(b, b == x))
function Iv(x::Rational)
    n, d = BigFloat(numerator(x)), BigFloat(denominator(x))
    (n == numerator(x) && d == denominator(x)) || return _enclose(BigFloat(x), false)
    return Iv(div_r(n, d, RNDD), div_r(n, d, RNDU))
end
Iv(x::AbstractIrrational) = _enclose(BigFloat(x), false)
Iv(a::Real, b::Real) = Iv(lo(Iv(a)), hi(Iv(b)))
"Integers exactly; other decimals enclosed within one ulp of the correctly rounded value."
function Base.parse(::Type{Iv}, s::AbstractString)
    n = tryparse(BigInt, s)
    n === nothing ? _enclose(parse(BigFloat, s), false) : Iv(n)
end
convert(::Type{Iv}, x::Real) = Iv(x)
convert(::Type{Iv}, x::Iv) = x
promote_rule(::Type{Iv}, ::Type{<:Real}) = Iv

zero(::Type{Iv}) = Iv(BigFloat(0))
one(::Type{Iv}) = Iv(BigFloat(1))
zero(::Iv) = zero(Iv)
one(::Iv) = one(Iv)
float(x::Iv) = x
iszero(x::Iv) = iszero(x.lo) && iszero(x.hi)
isfinite(x::Iv) = isfinite(x.lo) && isfinite(x.hi)
show(io::IO, x::Iv) = print(io, "[", string(x.lo), ", ", string(x.hi), "]")

# ---- arithmetic
+(x::Iv, y::Iv) = Iv(add_r(x.lo, y.lo, RNDD), add_r(x.hi, y.hi, RNDU))
-(x::Iv, y::Iv) = Iv(sub_r(x.lo, y.hi, RNDD), sub_r(x.hi, y.lo, RNDU))
-(x::Iv) = Iv(-x.hi, -x.lo)
+(x::Iv) = x
function *(x::Iv, y::Iv)
    ends = ((x.lo, y.lo), (x.lo, y.hi), (x.hi, y.lo), (x.hi, y.hi))
    Iv(minimum(mul_r(a, b, RNDD) for (a, b) in ends), maximum(mul_r(a, b, RNDU) for (a, b) in ends))
end
function /(x::Iv, y::Iv)
    y.lo <= 0 <= y.hi && throw(IntervalError("division by an interval containing 0: $y"))
    ends = ((x.lo, y.lo), (x.lo, y.hi), (x.hi, y.lo), (x.hi, y.hi))
    Iv(minimum(div_r(a, b, RNDD) for (a, b) in ends), maximum(div_r(a, b, RNDU) for (a, b) in ends))
end
inv(x::Iv) = one(Iv) / x

# increasing functions
for f in (:exp, :sinh, :tanh)
    @eval $f(x::Iv) = Iv($(Symbol(f, :_r))(x.lo, RNDD), $(Symbol(f, :_r))(x.hi, RNDU))
end
function log(x::Iv)
    x.lo > 0 || throw(IntervalError("log of an interval that is not positive: $x"))
    Iv(log_r(x.lo, RNDD), log_r(x.hi, RNDU))
end
function sqrt(x::Iv)
    x.lo >= 0 || throw(IntervalError("sqrt of an interval with negative part: $x"))
    Iv(sqrt_r(x.lo, RNDD), sqrt_r(x.hi, RNDU))
end
function cosh(x::Iv)
    x.lo >= 0 && return Iv(cosh_r(x.lo, RNDD), cosh_r(x.hi, RNDU))
    x.hi <= 0 && return Iv(cosh_r(x.hi, RNDD), cosh_r(x.lo, RNDU))
    Iv(BigFloat(1), max(cosh_r(x.lo, RNDU), cosh_r(x.hi, RNDU)))
end
function abs(x::Iv)
    x.lo >= 0 && return x
    x.hi <= 0 && return -x
    Iv(BigFloat(0), max(-x.lo, x.hi))
end
min(x::Iv, y::Iv) = Iv(min(x.lo, y.lo), min(x.hi, y.hi))
max(x::Iv, y::Iv) = Iv(max(x.lo, y.lo), max(x.hi, y.hi))

function ^(x::Iv, n::Integer)
    n == 0 && return one(Iv)
    n < 0 && return inv(x^(-n))
    if iseven(n)
        a = abs(x)
        return Iv(pow_si_r(a.lo, n, RNDD), pow_si_r(a.hi, n, RNDU))
    end
    Iv(pow_si_r(x.lo, n, RNDD), pow_si_r(x.hi, n, RNDU))    # odd powers are increasing
end
# x^y for x > 0 is monotone in each argument separately, so its extremes over a box lie at the corners.
function ^(x::Iv, y::Iv)
    x.lo > 0 || throw(IntervalError("real power of an interval that is not positive: $x"))
    ends = ((x.lo, y.lo), (x.lo, y.hi), (x.hi, y.lo), (x.hi, y.hi))
    Iv(minimum(pow_r(a, b, RNDD) for (a, b) in ends), maximum(pow_r(a, b, RNDU) for (a, b) in ends))
end
^(x::Iv, y::Real) = x^Iv(y)
^(x::Real, y::Iv) = Iv(x)^y

# Gamma on (0, Inf): decreasing on (0, x0], increasing on [x0, Inf), x0 = 1.46163214496836..., Gamma(x0) = 0.88560319...
const X0_LO, X0_HI = big"1.4616", big"1.4617"
const GAMMA_MIN_LO = big"0.8856"
function gamma_iv(x::Iv)
    x.lo > 0 || throw(IntervalError("gamma is only enclosed on (0, Inf): $x"))
    x.lo == x.hi && return Iv(gamma_r(x.lo, RNDD), gamma_r(x.lo, RNDU))
    x.lo >= X0_HI && return Iv(gamma_r(x.lo, RNDD), gamma_r(x.hi, RNDU))
    x.hi <= X0_LO && return Iv(gamma_r(x.hi, RNDD), gamma_r(x.lo, RNDU))
    Iv(GAMMA_MIN_LO, max(gamma_r(x.lo, RNDU), gamma_r(x.hi, RNDU)))
end

# ---- comparisons: decided only when certain
function <(x::Iv, y::Iv)
    x.hi < y.lo && return true
    x.lo >= y.hi && return false
    throw(IntervalError("undecidable comparison $x < $y"))
end
function <=(x::Iv, y::Iv)
    x.hi <= y.lo && return true
    x.lo > y.hi && return false
    throw(IntervalError("undecidable comparison $x <= $y"))
end
function ==(x::Iv, y::Iv)
    (x.lo == x.hi == y.lo == y.hi) && return true
    (x.hi < y.lo || y.hi < x.lo) && return false
    throw(IntervalError("undecidable comparison $x == $y"))
end

end # module

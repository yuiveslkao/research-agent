"""Answer-key verification for GQ-DE-01 and GQ-DE-02 (human-owned; agent runs cannot see eval/).

Independent of the Julia library: a direct mpmath port of workspace/library/DESinc.jl (tanh-sinh DE rule on (-1,1),
h = log(4dN/alpha)/N, alpha = min(a,b)+1, f = (1-x)^a (1+x)^b, exact value 2^(a+b+1) G(a+1) G(b+1) / G(a+b+2)).

  python eval/verify/de_checks.py port      # reproduce workspace/library/testdata/de_reference.toml
  python eval/verify/de_checks.py planted   # max err/bound over the C0002/C0003 box for K = 1 and K = 2
  python eval/verify/de_checks.py rate      # effective constant c(N) = -ln(err) * log(4dN/alpha) / N vs pi^2

Needs mpmath (pip install mpmath). Exit code 1 if a check fails.
"""
import itertools
import sys
import tomllib
from pathlib import Path

import mpmath as mp

mp.mp.dps = 200
ROOT = Path(__file__).resolve().parents[2]


def de_quad(a, b, N, h):
    s = mp.mpf(0)
    halfpi = mp.pi / 2
    for k in range(-N, N + 1):
        t = k * h
        u = halfpi * mp.sinh(t)
        w = halfpi * mp.cosh(t) / mp.cosh(u) ** 2
        if w == 0:
            continue
        omx = 2 / (1 + mp.exp(2 * u))
        opx = 2 / (1 + mp.exp(-2 * u))
        s += omx ** a * opx ** b * w
    return h * s


def exact(a, b):
    return mp.mpf(2) ** (a + b + 1) * mp.gamma(a + 1) * mp.gamma(b + 1) / mp.gamma(a + b + 2)


def error(a, b, d, N):
    alpha = min(a, b) + 1
    h = mp.log(4 * d * N / alpha) / N
    return abs(de_quad(a, b, N, h) - exact(a, b)), h, alpha


def port() -> bool:
    ref = tomllib.loads((ROOT / "workspace/library/testdata/de_reference.toml").read_text())
    a, b, d = (mp.mpf(ref[k]) for k in ("a", "b", "d"))
    ok = True
    for n, e in sorted(ref["error"].items(), key=lambda kv: int(kv[0])):
        err, h, _ = error(a, b, d, int(n))
        rel = abs(err - mp.mpf(e["value"])) / mp.mpf(e["value"])
        ok &= rel < mp.mpf("1e-20")
        print(f"N={n:>4}  err={mp.nstr(err, 12)}  reference={mp.nstr(mp.mpf(e['value']), 12)}  rel.diff={mp.nstr(rel, 3)}")
    print("port matches the reference" if ok else "PORT MISMATCH")
    return ok


def planted() -> bool:
    ds = [mp.mpf(x) for x in ("0.5", "0.75", "1")]
    ab = [mp.mpf(x) for x in ("-0.5", "-0.25", "0", "0.5", "1")]
    worst = {1: (mp.mpf(0), None), 2: (mp.mpf(0), None)}
    for d, a, b, N in itertools.product(ds, ab, ab, (8, 12, 16, 24, 32, 48, 64)):
        err, _, alpha = error(a, b, d, N)
        for K in (1, 2):
            r = err / mp.exp(-K * mp.pi * d * N / mp.log(4 * d * N / alpha))
            if r > worst[K][0]:
                worst[K] = (r, (float(d), float(a), float(b), N))
    for K, (r, at) in worst.items():
        print(f"K={K}: max err/bound = {mp.nstr(r, 4)} at (d, a, b, N) = {at}")
    ok = worst[1][0] < 1 < worst[2][0]
    print("K=1 held and K=2 is violated on this grid" if ok else "UNEXPECTED")
    return ok


def rate() -> bool:
    a, b, d = mp.mpf("0.5"), mp.mpf("1.5"), mp.mpf(1)
    for N in (8, 16, 32, 64, 128, 256):
        mp.mp.dps = 60 + 2 * N                       # enough digits for errors near exp(-pi^2 N / log N)
        err, h, alpha = error(a, b, d, N)
        c = -mp.log(err) * h
        print(f"N={N:>4}  err={mp.nstr(err, 6)}  c(N)={mp.nstr(c, 6)}  c/pi^2={mp.nstr(c / mp.pi ** 2, 4)}")
    mp.mp.dps = 200
    return True


if __name__ == "__main__":
    which = sys.argv[1:] or ["port", "planted", "rate"]
    results = [{"port": port, "planted": planted, "rate": rate}[w]() for w in which]
    sys.exit(0 if all(results) else 1)

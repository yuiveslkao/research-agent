"""Independent mpmath reference for Example 1 of arXiv:2305.05127v4 (Tanaka).

Not part of the Julia stack: it exists so a future Julia implementation has a known answer.
Functional: F1 = W + V on R, W(mu) = 1/2 ∬ (x-y)^2 dmu dmu (the convention of eq. (2.4)),
V(mu) = ∫ (x-1)^2 dmu, so F1 = 2*Var + (mean-1)^2, transport L-smooth with L = 4, minimizer delta_1.
Gradient: G(x) = 2(x - mean) + 2(x - 1). NOTE: eq. (6.3) as printed omits the 1/2 in W1; with that
literal reading F1 = 3*Var + (mean-1)^2 and the true smoothness constant is 6, not 4.

Initial measure: the paper text I read does not specify mu0; here, 20 equispaced points in [-1, 3].
Run: python workspace/reference/measure_flow_ex1.py > workspace/library/testdata/ex1_reference.toml
"""
from mpmath import mp, mpf, nstr

mp.dps = 60
NP = 20
X0 = [mpf(-1) + mpf(4) * i / (NP - 1) for i in range(NP)]


def F(x, cw=1):
    n = len(x); m = sum(x) / n; s2 = sum((xi - m) ** 2 for xi in x) / n
    return cw * s2 + s2 + (m - 1) ** 2


def G(x, cw=1):
    m = sum(x) / len(x)
    return [cw * 2 * (xi - m) + 2 * (xi - 1) for xi in x]


def D(a):  # 1/2 E|a - X*|^2 with X* = 1
    return sum((ai - 1) ** 2 for ai in a) / (2 * len(a))


def gd(tau, iters, cw=1):
    g, X, out = mpf("1.5") * tau, list(X0), {}
    for n in range(iters + 1):
        out[n] = (F(X, cw), n * g * F(X, cw) + D(X))
        X = [xi - g * gi for xi, gi in zip(X, G(X, cw))]
    return out


def acc(tau, iters, cw=1):
    a = lambda n: (n + 1) * (n + 2) * tau / 16
    Y, Z, out = list(X0), list(X0), {}
    out[0] = (F(Y, cw), a(0) * F(Y, cw) + D(Z))
    for n in range(iters):
        c = (a(n + 1) - a(n)) / a(n + 1)
        X = [y + c * (z - y) for y, z in zip(Y, Z)]
        g = G(X, cw)
        Z = [z - (a(n + 1) - a(n)) * gi for z, gi in zip(Z, g)]
        Y = [xi - tau * gi for xi, gi in zip(X, g)]
        out[n + 1] = (F(Y, cw), a(n + 1) * F(Y, cw) + D(Z))
    return out


if __name__ == "__main__":
    print("# Known-answer data from workspace/reference/measure_flow_ex1.py (mpmath, 60 digits).")
    print("# Example 1 of arXiv:2305.05127v4, W with the 1/2 of eq. (2.4), L = 4; mu0 = 20 equispaced points in [-1, 3].")
    print('# "F" is F1 at the iterate (mu_n for gd, nu_n for acc); "lyap" is the Lyapunov value L_n / L^ac_n.')
    for tau in ("0.0025", "0.005", "0.01"):
        t = mpf(tau)
        for name, run in (("gd", gd), ("acc", acc)):
            r = run(t, 1000)
            for n in (10, 100, 1000):
                print(f'\n[{name}."{tau}".n{n}]\nF = "{nstr(r[n][0], 25)}"\nlyap = "{nstr(r[n][1], 25)}"')

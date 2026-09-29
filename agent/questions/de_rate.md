For the integral I(a,b) = ∫_{-1}^{1} (1-x)^a (1+x)^b dx with -1/2 <= a,b <= 1, the DE quadrature
(library DESinc: `de_quad`, mesh h = log(4dN/alpha)/N with alpha = min(a,b)+1, half-width d in [1/2, 1])
converges very fast. How does the error behave as N grows from 8 to 128? Estimate the effective rate
c in error ~ exp(-c N / log N) (or find a better description), how it depends on d and on min(a,b),
and state exactly which claims are established, in what scope and at what evidence grade, and which are only
observed.

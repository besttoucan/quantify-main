"""The statistics Quantify is willing to put its name to.

Everything here is standard, checkable method. No approximations dressed up as
results, no p-values pulled from a table of guesses. If a claim appears in the
product, the arithmetic behind it is in this file and can be argued with.

The pieces:

* Exact tail probabilities for Student's t and Fisher's F, via the regularised
  incomplete beta function. These are what turn "this looks like an effect" into
  "this effect would appear by chance about once in two hundred times".
* Ordinary least squares with real standard errors, so a coefficient arrives
  with the uncertainty attached to it.
* Benjamini and Hochberg's false discovery rate control. Testing a dozen drivers
  guarantees a false positive or two; without this correction a product reports
  noise with a straight face, which is worse than reporting nothing.
* Welch's t-test, which does not assume two periods have equal variance,
  because in a restaurant they never do.
* The newsvendor quantile, which is the right answer to "how many should I make"
  when being short costs a different amount from being over.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------

_LANCZOS = (
    676.5203681218851, -1259.1392167224028, 771.32342877765313,
    -176.61502916214059, 12.507343278686905, -0.13857109526572012,
    9.9843695780195716e-6, 1.5056327351493116e-7,
)


def log_gamma(x: float) -> float:
    """Lanczos approximation, accurate to about fifteen digits for x > 0."""
    if x < 0.5:
        return math.log(math.pi / abs(math.sin(math.pi * x))) - log_gamma(1.0 - x)
    x -= 1.0
    a = 0.99999999999980993
    t = x + 7.5
    for index, value in enumerate(_LANCZOS):
        a += value / (x + index + 1)
    return 0.5 * math.log(2 * math.pi) + (x + 0.5) * math.log(t) - t + math.log(a)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's algorithm for the continued fraction of the incomplete beta."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def regularised_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        log_gamma(a + b) - log_gamma(a) - log_gamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - math.exp(
        log_gamma(a + b) - log_gamma(a) - log_gamma(b)
        + b * math.log(1.0 - x) + a * math.log(x)
    ) * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_t_two_sided(t: float, degrees: float) -> float:
    """P(|T| >= |t|). The chance of seeing an effect this large from noise alone."""
    if degrees <= 0 or not math.isfinite(t):
        return 1.0
    x = degrees / (degrees + t * t)
    return max(0.0, min(1.0, regularised_incomplete_beta(degrees / 2.0, 0.5, x)))


def f_upper_tail(f: float, d1: float, d2: float) -> float:
    """P(F >= f). Used to ask whether a grouping explains anything at all."""
    if f <= 0 or d1 <= 0 or d2 <= 0 or not math.isfinite(f):
        return 1.0
    x = d2 / (d2 + d1 * f)
    return max(0.0, min(1.0, regularised_incomplete_beta(d2 / 2.0, d1 / 2.0, x)))


def normal_quantile(p: float) -> float:
    """Acklam's inverse normal CDF, good to about 1e-9."""
    p = min(max(p, 1e-12), 1 - 1e-12)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---------------------------------------------------------------------------
# Multiple testing
# ---------------------------------------------------------------------------

def benjamini_hochberg(pvalues: Sequence[float]) -> list[float]:
    """Turn raw p-values into q-values controlling the false discovery rate.

    Test twelve drivers at the 5% level and roughly one will look significant
    purely by chance. Reporting that one as a finding is how a forecasting tool
    loses an operator's trust for good. This is the correction that stops it.
    """
    n = len(pvalues)
    if not n:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    qvalues = [1.0] * n
    running = 1.0
    for rank in range(n - 1, -1, -1):
        index = order[rank]
        value = pvalues[index] * n / (rank + 1)
        running = min(running, value)
        qvalues[index] = min(1.0, running)
    return qvalues


# ---------------------------------------------------------------------------
# Linear algebra
# ---------------------------------------------------------------------------

def invert(matrix: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan with partial pivoting. None when the matrix is singular."""
    n = len(matrix)
    work = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < 1e-11:
            return None
        work[col], work[pivot] = work[pivot], work[col]
        divisor = work[col][col]
        work[col] = [value / divisor for value in work[col]]
        for row in range(n):
            if row == col:
                continue
            factor = work[row][col]
            if factor:
                work[row] = [left - factor * right for left, right in zip(work[row], work[col])]
    return [row[n:] for row in work]


class Regression:
    """Ordinary least squares, reporting what it does not know as well as what it does."""

    def __init__(self, coefficients: list[float], standard_errors: list[float],
                 residuals: list[float], degrees: int, r_squared: float, n: int) -> None:
        self.coefficients = coefficients
        self.standard_errors = standard_errors
        self.residuals = residuals
        self.degrees = degrees
        self.r_squared = r_squared
        self.n = n

    def t_statistic(self, index: int) -> float:
        error = self.standard_errors[index]
        return self.coefficients[index] / error if error > 0 else 0.0

    def p_value(self, index: int) -> float:
        return student_t_two_sided(self.t_statistic(index), self.degrees)

    def confidence_interval(self, index: int, level: float = 0.95) -> tuple[float, float]:
        # Normal critical value. With the sample sizes here (hundreds of days)
        # the difference from the exact t critical value is under a percent.
        z = normal_quantile(0.5 + level / 2.0)
        margin = z * self.standard_errors[index]
        return self.coefficients[index] - margin, self.coefficients[index] + margin


def least_squares(rows: list[list[float]], targets: list[float]) -> Regression | None:
    n = len(rows)
    if n == 0:
        return None
    p = len(rows[0])
    if n <= p + 1:
        return None

    gram = [[sum(rows[k][i] * rows[k][j] for k in range(n)) for j in range(p)] for i in range(p)]
    moment = [sum(rows[k][i] * targets[k] for k in range(n)) for i in range(p)]
    inverse = invert(gram)
    if inverse is None:
        return None

    beta = [sum(inverse[i][j] * moment[j] for j in range(p)) for i in range(p)]
    fitted = [sum(beta[i] * rows[k][i] for i in range(p)) for k in range(n)]
    residuals = [targets[k] - fitted[k] for k in range(n)]
    degrees = n - p
    sigma_squared = sum(value * value for value in residuals) / degrees
    errors = [math.sqrt(max(0.0, sigma_squared * inverse[i][i])) for i in range(p)]

    mean = sum(targets) / n
    total = sum((value - mean) ** 2 for value in targets)
    explained = 1.0 - (sum(value * value for value in residuals) / total) if total > 0 else 0.0
    return Regression(beta, errors, residuals, degrees, max(0.0, explained), n)


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------

def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return sum((value - average) ** 2 for value in values) / (len(values) - 1)


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = max(0.0, min(1.0, q)) * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return float(sorted_values[low]) * (1 - weight) + float(sorted_values[high]) * weight


def welch(a: Sequence[float], b: Sequence[float]) -> dict[str, float]:
    """Compare two periods without pretending their variances match."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"difference": 0.0, "t": 0.0, "p": 1.0, "degrees": 0.0, "n_a": na, "n_b": nb}
    va, vb = variance(a) / na, variance(b) / nb
    denominator = va + vb
    if denominator <= 0:
        return {"difference": mean(a) - mean(b), "t": 0.0, "p": 1.0, "degrees": 0.0, "n_a": na, "n_b": nb}
    t = (mean(a) - mean(b)) / math.sqrt(denominator)
    degrees = denominator ** 2 / (va ** 2 / (na - 1) + vb ** 2 / (nb - 1))
    return {
        "difference": mean(a) - mean(b), "t": t, "degrees": degrees,
        "p": student_t_two_sided(t, degrees), "n_a": na, "n_b": nb,
    }


def one_way_anova(groups: Sequence[Sequence[float]]) -> dict[str, float]:
    """Does this grouping explain anything, or are all the groups the same?"""
    usable = [group for group in groups if len(group) >= 2]
    if len(usable) < 2:
        return {"f": 0.0, "p": 1.0, "between_share": 0.0, "groups": len(usable)}
    everything = [value for group in usable for value in group]
    grand = mean(everything)
    between = sum(len(group) * (mean(group) - grand) ** 2 for group in usable)
    within = sum(sum((value - mean(group)) ** 2 for value in group) for group in usable)
    d1 = len(usable) - 1
    d2 = len(everything) - len(usable)
    if d2 <= 0 or within <= 0:
        return {"f": 0.0, "p": 1.0, "between_share": 0.0, "groups": len(usable)}
    f = (between / d1) / (within / d2)
    return {
        "f": f, "p": f_upper_tail(f, d1, d2), "groups": len(usable),
        "between_share": between / (between + within),
    }


def pearson(a: Sequence[float], b: Sequence[float]) -> dict[str, float]:
    n = min(len(a), len(b))
    if n < 4:
        return {"r": 0.0, "p": 1.0, "n": n}
    ma, mb = mean(a[:n]), mean(b[:n])
    covariance = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    spread_a = math.sqrt(sum((a[i] - ma) ** 2 for i in range(n)))
    spread_b = math.sqrt(sum((b[i] - mb) ** 2 for i in range(n)))
    if spread_a <= 0 or spread_b <= 0:
        return {"r": 0.0, "p": 1.0, "n": n}
    r = covariance / (spread_a * spread_b)
    r = max(-0.999999, min(0.999999, r))
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return {"r": r, "p": student_t_two_sided(t, n - 2), "n": n}


# ---------------------------------------------------------------------------
# Operations research
# ---------------------------------------------------------------------------

def newsvendor_quantity(
    sorted_demand: Sequence[float],
    cost_of_over: float,
    cost_of_under: float,
) -> dict[str, float]:
    """How many to make when being short costs a different amount from being over.

    The classic single-period stocking problem. Make Q and the best Q is the
    point where the chance of demand falling below it equals the underage cost
    over the total cost of being wrong in either direction. For food this is
    almost always above the average, because a wasted portion loses its food
    cost while a missed sale loses the whole margin.
    """
    total = cost_of_over + cost_of_under
    if total <= 0 or not sorted_demand:
        return {"quantity": quantile(sorted_demand, 0.5), "fractile": 0.5}
    fractile = cost_of_under / total
    return {
        "quantity": quantile(sorted_demand, fractile),
        "fractile": fractile,
        "median": quantile(sorted_demand, 0.5),
    }


def exceedance(sorted_demand: Sequence[float], level: float) -> float:
    """Share of comparable days on which demand went past `level`."""
    if not sorted_demand:
        return 0.0
    above = sum(1 for value in sorted_demand if value > level)
    return above / len(sorted_demand)


def describe_significance(p: float, q: float | None = None) -> str:
    """Plain words for a p-value, so the interface never prints a bare number."""
    value = q if q is not None else p
    if value < 0.01:
        return "strong"
    if value < 0.05:
        return "clear"
    if value < 0.15:
        return "suggestive"
    return "not established"

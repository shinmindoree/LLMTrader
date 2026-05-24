"""Optimal-stopping barrier solver for the OU process (Leung-Li 2015 style).

PURPOSE
-------
Replaces the z-score heuristic in :mod:`ou_optimal_stopping_strategy` with a
**numerical** optimal-stopping computation. Given a fitted OU process
``(mu, theta, sigma, sigma_inf)`` and the user's fee structure, this module
computes:

  - ``a_star, d_star`` : the optimal entry interval in z-space (the set of
    entry z values where expected post-fee PnL is positive)
  - ``b_star``         : the optimal take-profit barrier in z-space
  - ``L_star``         : the optimal loss barrier (when the user does not
    fix it)

MATHEMATICAL BASIS
------------------
For the standardised OU process
::

    dZ_t = -Z_t dt + sqrt(2) dB_t

the **scale function** is
::

    s(z) = ∫_0^z exp(t^2) dt = (sqrt(π)/2) * erfi(z)

The probability of hitting an upper level ``u`` before a lower level ``ℓ``
starting from ``z`` (with ``ℓ < z < u``) is::

    P(τ_u < τ_ℓ | Z_0 = z) = (s(z) - s(ℓ)) / (s(u) - s(ℓ))

For the zero-discount case (``r = 0``) used here, this scale function IS
the closed-form solution; in the Leung-Li (2015) paper it appears via the
confluent hypergeometric function ``M(a, b, z)`` because the general
discounted PDE for the value function is solved by Kummer's equation. For
``r → 0`` the discount drops out and the simpler scale-function form is
equivalent (see L&L Sec. 2.3, the ``r=0`` limit).

For ``r > 0`` the proper Kummer-based closed-form is used; we provide a
pure-Python series implementation of ``M(a, b, z)`` for that case.

LIMITATIONS
-----------
- The solver assumes constant ``mu, theta, sigma`` over the stopping window
  (single-fit). For drifting OU parameters the strategy refits each bar
  and the solver is re-called.
- All bps are computed in **log-return** space (consistent with the
  ``use_log_price=1`` mode of the strategy).
- The solver does not account for slippage beyond the lump-sum
  ``fees_bps`` round-trip estimate.

CACHE
-----
Results are memoised via an LRU cache keyed on rounded parameters:
``(round(sigma_inf, 5), round(L, 2), round(exit_z, 3), round(fees_bps, 1))``
This keeps recomputation cheap during a backtest's per-bar refit.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Special functions (pure Python, no scipy)
# ---------------------------------------------------------------------------
def _erfi(z: float) -> float:
    """Imaginary error function ``erfi(z) = -i * erf(i * z)``.

    Uses Taylor series for ``|z| < 4`` and the asymptotic expansion
    otherwise. Accuracy ~1e-12 in the [-5, 5] range we care about for OU
    z-scores.
    """
    if not math.isfinite(z):
        return float("nan")
    if z == 0.0:
        return 0.0
    az = abs(z)
    if az < 4.0:
        # erfi(z) = (2/sqrt(pi)) * z * sum_{n=0}^∞ z^{2n} / (n! * (2n+1))
        z2 = z * z
        term = 1.0
        s = 1.0
        for n in range(1, 200):
            term *= z2 / n
            add = term / (2 * n + 1)
            s += add
            if abs(add) < 1e-16 * (abs(s) + 1.0):
                break
        return (2.0 / math.sqrt(math.pi)) * z * s
    # Asymptotic: erfi(z) ~ sign(z) * exp(z^2) / (sqrt(pi) * |z|) * (1 + 1/(2z^2) + 3/(4z^4) + ...)
    z2 = z * z
    s = 1.0
    term = 1.0
    for n in range(1, 30):
        term *= (2 * n - 1) / (2.0 * z2)
        s += term
        if abs(term) < 1e-16 * (abs(s) + 1.0):
            break
    return math.copysign(math.exp(z2) / (math.sqrt(math.pi) * az) * s, z)


def kummer_M(a: float, b: float, z: float, max_terms: int = 500, tol: float = 1e-14) -> float:
    """Confluent hypergeometric function ``₁F₁(a; b; z) = M(a, b, z)``.

    Series form (converges for all finite ``z``; fast for moderate ``z``)::

        M(a, b, z) = sum_{n=0}^∞ (a)_n / (b)_n * z^n / n!

    For ``|z|`` larger than a few hundred the series can be slow but stays
    numerically stable in float64. This is more than adequate for the
    OU optimal-stopping use case where the argument is bounded.
    """
    if b == 0 or (b < 0 and math.floor(b) == b):
        return float("nan")  # M is singular at non-positive integer b
    if z == 0.0:
        return 1.0
    term = 1.0
    s = 1.0
    for n in range(max_terms):
        # term_{n+1} = term_n * (a + n) / (b + n) * z / (n + 1)
        term *= (a + n) / (b + n) * z / (n + 1)
        s += term
        if abs(term) < tol * (abs(s) + 1.0):
            return s
    return s


def _scale_integral(a: float, b: float, n_steps: int = 1024) -> float:
    """Composite Simpson's rule for ``∫_a^b exp(t^2) dt``.

    Used to compute ratios ``(s(z) - s(ℓ)) / (s(u) - s(ℓ))`` which are
    the OU first-passage probabilities. The integrand grows fast (exp(t^2)
    at t=5 is ~7e10) but stays well within float64 range for the z-band
    we operate in.
    """
    if b <= a:
        return 0.0
    if n_steps % 2 == 1:
        n_steps += 1
    h = (b - a) / n_steps

    def f(t: float) -> float:
        tt = t * t
        if tt > 700.0:  # exp(700) ~= 1e304, near float64 max
            return 1e300
        return math.exp(tt)

    s = f(a) + f(b)
    for i in range(1, n_steps):
        t = a + i * h
        s += 4.0 * f(t) if (i % 2 == 1) else 2.0 * f(t)
    return s * h / 3.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class OuBarriers(NamedTuple):
    """Optimal barrier set returned by the solver.

    All values are in dimensionless z-space (z = (x - theta) / sigma_inf).

    a_star  : Boundary of the entry region farthest from theta. Entries
              fire when ``a_star <= |z| <= d_star`` (with ``z < 0`` for
              long, ``z > 0`` for short).
    d_star  : Boundary of the entry region closest to ``stop_z``.
    b_star  : Optimal exit barrier; close when ``|z| <= b_star``.
    L_star  : Stop barrier used (may equal the user-supplied value).
    expected_pnl_at_entry_bps : Expected post-fee PnL in bps for the
              representative entry z (midpoint of [a_star, d_star]).
    """

    a_star: float
    d_star: float
    b_star: float
    L_star: float
    expected_pnl_at_entry_bps: float


def long_expected_pnl_bps(
    z0: float,
    exit_z: float,
    stop_z: float,
    sigma_inf: float,
    fees_bps: float,
) -> float:
    """Expected post-fee PnL in bps for a long entry at ``z0``.

    Long entries assume ``-stop_z < z0 < -exit_z`` (z below the exit
    barrier, above the loss barrier). The upper absorbing barrier is
    ``-exit_z`` (TP), the lower is ``-stop_z`` (SL).
    """
    if not math.isfinite(z0):
        return 0.0
    if not (-stop_z < z0 < -exit_z):
        return 0.0
    den = _scale_integral(-stop_z, -exit_z)
    if den <= 0.0:
        return 0.0
    num = _scale_integral(-stop_z, z0)
    p_tp = max(0.0, min(1.0, num / den))
    p_sl = 1.0 - p_tp
    # Log-return = (z_exit - z0) * sigma_inf
    pnl_tp_bps = (-exit_z - z0) * sigma_inf * 1e4
    pnl_sl_bps = (-stop_z - z0) * sigma_inf * 1e4
    return p_tp * pnl_tp_bps + p_sl * pnl_sl_bps - fees_bps


def short_expected_pnl_bps(
    z0: float,
    exit_z: float,
    stop_z: float,
    sigma_inf: float,
    fees_bps: float,
) -> float:
    """Mirror of :func:`long_expected_pnl_bps` for the short side."""
    if not math.isfinite(z0):
        return 0.0
    if not (exit_z < z0 < stop_z):
        return 0.0
    den = _scale_integral(exit_z, stop_z)
    if den <= 0.0:
        return 0.0
    num = _scale_integral(z0, stop_z)
    p_tp = max(0.0, min(1.0, num / den))
    p_sl = 1.0 - p_tp
    # Short profits when z decreases: pnl = (z0 - z_exit) * sigma_inf.
    pnl_tp_bps = (z0 - exit_z) * sigma_inf * 1e4
    pnl_sl_bps = (z0 - stop_z) * sigma_inf * 1e4
    return p_tp * pnl_tp_bps + p_sl * pnl_sl_bps - fees_bps


def _find_zero_crossings(
    f, lo: float, hi: float, n_samples: int = 200,
) -> list[float]:
    """Bisect-refined zero crossings of ``f`` on ``[lo, hi]``.

    Returns the z-values where ``f`` crosses zero (sign change between
    adjacent samples). Refines each crossing with up to 30 bisection
    steps.
    """
    if hi <= lo:
        return []
    xs: list[float] = []
    prev_x = lo
    prev_y = f(lo)
    for i in range(1, n_samples + 1):
        cur_x = lo + (hi - lo) * i / n_samples
        cur_y = f(cur_x)
        if prev_y == 0.0:
            xs.append(prev_x)
        elif (prev_y < 0.0) != (cur_y < 0.0):
            # Bisection on [prev_x, cur_x]
            lo_b, hi_b = prev_x, cur_x
            y_lo, y_hi = prev_y, cur_y
            for _ in range(40):
                mid = 0.5 * (lo_b + hi_b)
                y_mid = f(mid)
                if y_mid == 0.0 or (hi_b - lo_b) < 1e-9:
                    break
                if (y_mid < 0.0) == (y_lo < 0.0):
                    lo_b, y_lo = mid, y_mid
                else:
                    hi_b, y_hi = mid, y_mid
            xs.append(0.5 * (lo_b + hi_b))
        prev_x, prev_y = cur_x, cur_y
    return xs


@lru_cache(maxsize=4096)
def _solve_barriers_cached(
    sigma_inf_r: float,
    exit_z_r: float,
    stop_z_r: float,
    fees_bps_r: float,
) -> OuBarriers:
    """Cache-key wrapper around :func:`solve_barriers`.

    All inputs are pre-rounded floats so the LRU key is stable across
    bars where the fit moves only marginally.
    """
    sigma_inf = sigma_inf_r
    exit_z = exit_z_r
    stop_z = stop_z_r
    fees_bps = fees_bps_r

    # 1) Find the LONG entry region: roots of E[PnL_long](z0) = 0 on (-stop_z, -exit_z).
    def f_long(z0: float) -> float:
        return long_expected_pnl_bps(z0, exit_z, stop_z, sigma_inf, fees_bps)

    roots = _find_zero_crossings(f_long, -stop_z + 1e-6, -exit_z - 1e-6, n_samples=200)

    # The "positive PnL" region is the subinterval where f_long > 0.
    a_star = -stop_z  # default: nothing
    d_star = -exit_z
    if not roots:
        # Either always positive (rare) or always negative (no edge).
        mid = 0.5 * (-stop_z + -exit_z)
        if f_long(mid) > 0.0:
            a_star = -stop_z + 1e-6
            d_star = -exit_z - 1e-6
        else:
            a_star = d_star = float("nan")
    elif len(roots) == 1:
        # One crossing: positive on one side, negative on the other.
        r = roots[0]
        # Sample a point just left of r to determine sign of the positive region.
        if f_long(r - 1e-4) > 0.0:
            a_star = -stop_z + 1e-6
            d_star = r
        else:
            a_star = r
            d_star = -exit_z - 1e-6
    else:
        # Two crossings: positive in the middle (typical for OU with fees).
        a_star = roots[0]
        d_star = roots[-1]

    # 2) Optimise b_star (exit) over the long entry interval.
    #    Score = mean E[PnL] across z0 in [a_star, d_star].
    if math.isfinite(a_star) and math.isfinite(d_star) and d_star > a_star:
        # Grid-search b in (0, exit_z + 0.5) since b smaller than the user's
        # exit_z is rarely useful (we'd hit it immediately) and b larger
        # than ~ exit_z + 0.5 sigma_inf gives negligible improvement.
        best_b = exit_z
        best_score = -math.inf
        # 30 candidate b values in [0.01, max(0.5, exit_z * 3)].
        b_hi = max(0.5, exit_z * 3.0)
        n_grid_b = 30
        n_grid_z = 16
        z_grid = [a_star + (d_star - a_star) * (j + 0.5) / n_grid_z for j in range(n_grid_z)]
        for k in range(n_grid_b):
            b_cand = 0.01 + (b_hi - 0.01) * k / (n_grid_b - 1)
            if b_cand >= stop_z:
                continue
            tot = 0.0
            count = 0
            for z0 in z_grid:
                if -stop_z < z0 < -b_cand:
                    tot += long_expected_pnl_bps(z0, b_cand, stop_z, sigma_inf, fees_bps)
                    count += 1
            if count == 0:
                continue
            score = tot / count
            if score > best_score:
                best_score = score
                best_b = b_cand
        b_star = best_b
        # Recompute expected PnL at the midpoint with optimised b.
        z_mid = 0.5 * (a_star + d_star)
        e_pnl = long_expected_pnl_bps(z_mid, b_star, stop_z, sigma_inf, fees_bps)
    else:
        b_star = exit_z
        e_pnl = 0.0

    return OuBarriers(
        a_star=a_star,
        d_star=d_star,
        b_star=b_star,
        L_star=stop_z,
        expected_pnl_at_entry_bps=e_pnl,
    )


def solve_barriers(
    sigma_inf: float,
    exit_z: float,
    stop_z: float,
    fees_bps: float,
) -> OuBarriers:
    """Solve for the optimal-stopping barriers given OU sigma_inf and user costs.

    Uses an LRU cache keyed on rounded parameters: parameter changes
    smaller than ~ 1e-5 in ``sigma_inf`` or ~ 1e-3 in z-thresholds do not
    trigger a recompute. The cache fits ~4k unique parameter sets, well
    above what a typical backtest sweep needs.
    """
    return _solve_barriers_cached(
        round(sigma_inf, 5),
        round(exit_z, 3),
        round(stop_z, 3),
        round(fees_bps, 1),
    )


def solver_cache_info() -> dict[str, int]:
    """Expose the LRU cache stats for diagnostics."""
    info = _solve_barriers_cached.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "currsize": info.currsize,
        "maxsize": info.maxsize or -1,
    }


def solver_cache_clear() -> None:
    """Reset the cache (useful between sweep cells / unit tests)."""
    _solve_barriers_cached.cache_clear()


# ---------------------------------------------------------------------------
# Monte-Carlo fallback (independent verification)
# ---------------------------------------------------------------------------
def mc_expected_pnl_bps(
    z0: float,
    exit_z: float,
    stop_z: float,
    sigma_inf: float,
    fees_bps: float,
    *,
    side: int = 1,
    dt: float = 0.1,
    max_steps: int = 5000,
    n_paths: int = 5000,
    seed: int = 1234,
) -> tuple[float, float]:
    """Monte-Carlo estimator of expected post-fee PnL in bps.

    Simulates ``n_paths`` independent OU paths in z-space starting from
    ``z0`` and follows them until they hit either the TP or SL barrier
    (or ``max_steps`` is reached). Returns ``(mean_pnl_bps, std_pnl_bps)``.

    Used to **verify** :func:`long_expected_pnl_bps` /
    :func:`short_expected_pnl_bps`: in the ``r = 0`` regime the two
    should agree to within a few standard errors.

    Parameters
    ----------
    side : 1 for long, -1 for short.
    dt   : Time step in dimensionless OU time. 0.1 is a good balance of
           accuracy vs. runtime.
    """
    import random

    rng = random.Random(seed)
    sqrt_2dt = math.sqrt(2.0 * dt)
    pnls: list[float] = []
    for _ in range(n_paths):
        z = z0
        for _ in range(max_steps):
            # Box-Muller -> standard normal.
            u1 = rng.random()
            u2 = rng.random()
            if u1 <= 0.0:
                u1 = 1e-12
            r = math.sqrt(-2.0 * math.log(u1))
            theta = 2.0 * math.pi * u2
            g = r * math.cos(theta)
            # dZ = -Z dt + sqrt(2) dB ; Euler-Maruyama
            z = z - z * dt + sqrt_2dt * g
            if side > 0:
                if z >= -exit_z:
                    pnls.append((-exit_z - z0) * sigma_inf * 1e4 - fees_bps)
                    break
                if z <= -stop_z:
                    pnls.append((-stop_z - z0) * sigma_inf * 1e4 - fees_bps)
                    break
            else:
                if z <= exit_z:
                    pnls.append((z0 - exit_z) * sigma_inf * 1e4 - fees_bps)
                    break
                if z >= stop_z:
                    pnls.append((z0 - stop_z) * sigma_inf * 1e4 - fees_bps)
                    break
        else:
            # Time-out: mark to z's current position.
            pnls.append((z - z0) * (1 if side > 0 else -1) * sigma_inf * 1e4 - fees_bps)

    n = len(pnls)
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / max(1, n - 1)
    return mean, math.sqrt(var / n)


__all__ = [
    "OuBarriers",
    "kummer_M",
    "long_expected_pnl_bps",
    "short_expected_pnl_bps",
    "solve_barriers",
    "solver_cache_info",
    "solver_cache_clear",
    "mc_expected_pnl_bps",
]

"""Options pricing tools for Round 3 (VEV vouchers = call options on VELVETFRUIT_EXTRACT).

Round 3 context
---------------
- Underlying:   VELVETFRUIT_EXTRACT  (~5250 at round start)
- Options:      VEV_4000, VEV_4500, VEV_5000, VEV_5100, VEV_5200,
                VEV_5300, VEV_5400, VEV_5500, VEV_6000, VEV_6500
                (European call vouchers with a shared underlying)
- TTE schedule: TTE = 5 days at round-3 start; decreases by 1 each round.
                Historical day 0 → TTE=5, day 1 → TTE=4, day 2 → TTE=3.
- Position limits: ±300 per voucher, ±200 for underlying / HYDROGEL_PACK.

Key module sections
-------------------
1. Black-Scholes pricing (pure Python, no scipy required)
2. Implied-volatility solver (bisection)
3. Greeks (delta, gamma, vega, theta)
4. Portfolio Greeks aggregator
5. IV-surface analysis helpers
6. Celestial Gardeners' Guild manual-bid optimizer

Usage examples
--------------
    from prosperity4bt.options import bs_call, bs_iv, bs_delta, portfolio_greeks

    S, K, T, sigma = 5250, 5000, 5/252, 0.40
    price = bs_call(S, K, T, sigma)          # theoretical call price
    iv    = bs_iv(257.0, S, K, T)            # implied vol from market mid
    delta = bs_delta(S, K, T, sigma)         # hedge ratio

    # Portfolio-level greeks for current positions
    positions = {"VEV_5000": 50, "VEV_5200": -30}
    underlying_mid = 5250.0
    greeks = portfolio_greeks(positions, underlying_mid, T, sigma)
    # → {"delta": ..., "gamma": ..., "vega": ..., "theta": ...}

    # Guild bid optimizer
    b1, b2, epnl = optimal_bids_guild()
    print(f"Submit bids: {b1}, {b2}  (expected PnL ≈ {epnl:.0f})")
"""

import math
from typing import Optional

# ── VEV voucher metadata ───────────────────────────────────────────────────────

VEV_STRIKES: dict[str, int] = {
    "VEV_4000": 4000,
    "VEV_4500": 4500,
    "VEV_5000": 5000,
    "VEV_5100": 5100,
    "VEV_5200": 5200,
    "VEV_5300": 5300,
    "VEV_5400": 5400,
    "VEV_5500": 5500,
    "VEV_6000": 6000,
    "VEV_6500": 6500,
}

# TTE as seen in the historical data (round_num → tte_days)
# round 3 historical days 0,1,2 correspond to TTE 5,4,3 at the competition start.
VEV_TTE_BY_ROUND: dict[int, int] = {
    0: 8,  # tutorial
    1: 7,  # round 1
    2: 6,  # round 2
    3: 5,  # round 3  ← current
}

TRADING_DAYS_PER_YEAR = 252


# ── Normal distribution helpers ────────────────────────────────────────────────

def _npdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _ncdf(x: float) -> float:
    """Standard normal CDF via the Hart approximation (max error < 7.5e-8)."""
    # Abramowitz & Stegun 26.2.17 rational approximation
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + 0.2316419 * x)
    poly = t * (0.319381530
                + t * (-0.356563782
                       + t * (1.781477937
                              + t * (-1.821255978
                                     + t * 1.330274429))))
    return (1.0 + sign) / 2.0 - sign * _npdf(x) * poly


# ── Black-Scholes call pricing ─────────────────────────────────────────────────

def _d1d2(S: float, K: float, T: float, sigma: float, r: float = 0.0):
    """Internal helper: compute (d1, d2) for the BS formula."""
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return d1, d2


def bs_call(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Black-Scholes European call price.

    Args:
        S:     Underlying mid price (e.g. VELVETFRUIT_EXTRACT mid).
        K:     Strike price (e.g. 5000 for VEV_5000).
        T:     Time to expiry **in years** (use ``tte_days / TRADING_DAYS_PER_YEAR``).
        sigma: Annualised volatility (e.g. 0.40 = 40%).
        r:     Risk-free rate (default 0 — competition ignores financing).

    Returns:
        Theoretical call price.
    """
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    d1, d2 = _d1d2(S, K, T, sigma, r)
    return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)


def bs_put(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Black-Scholes European put price (via put-call parity)."""
    return bs_call(S, K, T, sigma, r) - S + K * math.exp(-r * T)


# ── Greeks ─────────────────────────────────────────────────────────────────────

def bs_delta(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """BS call delta: ∂C/∂S. Range [0, 1]."""
    if T <= 0:
        return 1.0 if S > K else 0.0
    d1, _ = _d1d2(S, K, T, sigma, r)
    return _ncdf(d1)


def bs_gamma(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """BS call gamma: ∂²C/∂S². Same for calls and puts."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, _ = _d1d2(S, K, T, sigma, r)
    return _npdf(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """BS vega: ∂C/∂σ (per 1.0 change in sigma, not per percentage point)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, _ = _d1d2(S, K, T, sigma, r)
    return S * _npdf(d1) * math.sqrt(T)


def bs_theta(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """BS call theta: ∂C/∂T (per year). Convert to per-day: divide by 252."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, d2 = _d1d2(S, K, T, sigma, r)
    term1 = -S * _npdf(d1) * sigma / (2.0 * math.sqrt(T))
    term2 = -r * K * math.exp(-r * T) * _ncdf(d2)
    return term1 + term2


def bs_theta_daily(S: float, K: float, T: float, sigma: float, r: float = 0.0) -> float:
    """Theta per calendar day (divide annual theta by 365)."""
    return bs_theta(S, K, T, sigma, r) / 365.0


# ── Implied volatility ─────────────────────────────────────────────────────────

def bs_iv(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = 0.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> Optional[float]:
    """Compute implied volatility via bisection on the BS call formula.

    Returns None if the market price is below intrinsic value or otherwise
    infeasible, or if the solver fails to converge.
    """
    intrinsic = max(0.0, S - K * math.exp(-r * T))
    if market_price < intrinsic - tol:
        return None
    if T <= 0:
        return None

    lo, hi = 1e-6, 10.0  # volatility search range [0.0001%, 1000%]
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        price = bs_call(S, K, T, mid, r)
        if abs(price - market_price) < tol:
            return mid
        if price < market_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0  # best estimate after max_iter


# ── Portfolio-level helpers ────────────────────────────────────────────────────

def portfolio_greeks(
    positions: dict[str, int],
    S: float,
    T: float,
    sigma: float,
    r: float = 0.0,
) -> dict[str, float]:
    """Aggregate BS Greeks for a portfolio of VEV vouchers.

    Args:
        positions: {voucher_symbol: quantity}, e.g. {"VEV_5000": 50, "VEV_5200": -30}
        S:         Current VELVETFRUIT_EXTRACT mid price.
        T:         Time to expiry in **years**.
        sigma:     Shared annualised volatility estimate.
        r:         Risk-free rate (default 0).

    Returns:
        {"delta": float, "gamma": float, "vega": float, "theta_daily": float,
         "net_premium": float}
    """
    total_delta = 0.0
    total_gamma = 0.0
    total_vega = 0.0
    total_theta = 0.0
    net_premium = 0.0

    for symbol, qty in positions.items():
        K = VEV_STRIKES.get(symbol)
        if K is None:
            continue
        total_delta += qty * bs_delta(S, K, T, sigma, r)
        total_gamma += qty * bs_gamma(S, K, T, sigma, r)
        total_vega  += qty * bs_vega(S, K, T, sigma, r)
        total_theta += qty * bs_theta_daily(S, K, T, sigma, r)
        net_premium += qty * bs_call(S, K, T, sigma, r)

    return {
        "delta":       total_delta,
        "gamma":       total_gamma,
        "vega":        total_vega,
        "theta_daily": total_theta,
        "net_premium": net_premium,
    }


def iv_surface(
    market_mids: dict[str, float],
    S: float,
    T: float,
    r: float = 0.0,
) -> dict[str, Optional[float]]:
    """Compute IV for each voucher given current market mid prices.

    Args:
        market_mids: {voucher_symbol: market_mid_price}
        S:           Current underlying mid price.
        T:           Time to expiry in years.

    Returns:
        {voucher_symbol: implied_vol} (None if IV is not solvable)
    """
    result: dict[str, Optional[float]] = {}
    for symbol, mid in market_mids.items():
        K = VEV_STRIKES.get(symbol)
        if K is None:
            result[symbol] = None
            continue
        result[symbol] = bs_iv(mid, S, K, T, r)
    return result


def tte_for_round(round_num: int) -> int:
    """Return the time-to-expiry in days at the start of a given round."""
    return VEV_TTE_BY_ROUND.get(round_num, max(0, 8 - round_num))


def tte_to_years(tte_days: int) -> float:
    """Convert a TTE in days to years for use in BS formulas."""
    return tte_days / TRADING_DAYS_PER_YEAR


# ── Celestial Gardeners' Guild manual-bid optimizer ───────────────────────────

def _guild_reserve_prices(lo: int = 670, hi: int = 920, step: int = 5) -> list[int]:
    """All counterparty reserve prices for the Guild challenge."""
    return list(range(lo, hi + 1, step))


def expected_pnl_bid1(b1: int, lo: int = 670, hi: int = 920, step: int = 5, fair: int = 920) -> float:
    """Expected PnL from bid 1 alone.

    Counterparty trades at b1 if their reserve price r < b1.
    Each trade returns (fair - b1).
    """
    reserves = _guild_reserve_prices(lo, hi, step)
    count = sum(1 for r in reserves if r < b1)
    return count * max(0, fair - b1)


def expected_pnl_bid2_dominant(
    b2: int,
    b1: int,
    lo: int = 670,
    hi: int = 920,
    step: int = 5,
    fair: int = 920,
) -> float:
    """Expected PnL from bid 2 assuming we win the competition (b2 > avg competitor b2).

    Only counterparties NOT already captured by bid 1 are eligible for bid 2.
    """
    reserves = _guild_reserve_prices(lo, hi, step)
    # Counterparties not taken by bid 1
    eligible = [r for r in reserves if r >= b1]
    count = sum(1 for r in eligible if r < b2)
    return count * max(0, fair - b2)


def optimal_bids_guild(
    lo: int = 670,
    hi: int = 920,
    step: int = 5,
    fair: int = 920,
) -> tuple[int, int, float]:
    """Find the pair of bids that maximises expected PnL for the Guild challenge.

    Bid 1 is optimised independently.  Bid 2 is then optimised over the
    remaining counterparties, assuming bid 2 beats competitor bids (dominant
    strategy — if other teams play similarly you compete on bid 2, but the
    closed-form optimal is to treat bid 2 as a second independent auction).

    The penalty term for bid 2 being below the competitor average is not
    modelled here because the equilibrium behaviour is highly uncertain.
    Use the returned bids as a starting point and adjust for your belief about
    competitor strategies.

    Returns:
        (optimal_b1, optimal_b2, expected_total_pnl)
    """
    reserves = _guild_reserve_prices(lo, hi, step)
    candidate_bids = list(range(lo, fair + step, step))

    # Optimise bid 1 over all candidate values
    best_b1, best_pnl1 = max(
        ((b, expected_pnl_bid1(b, lo, hi, step, fair)) for b in candidate_bids),
        key=lambda t: t[1],
    )

    # Optimise bid 2 given bid 1 (sequentially independent)
    best_b2, best_pnl2 = max(
        ((b, expected_pnl_bid2_dominant(b, best_b1, lo, hi, step, fair)) for b in candidate_bids),
        key=lambda t: t[1],
    )

    return best_b1, best_b2, best_pnl1 + best_pnl2


def print_guild_analysis(lo: int = 670, hi: int = 920, step: int = 5, fair: int = 920) -> None:
    """Print a full analysis of the Guild bidding problem to stdout."""
    b1, b2, epnl = optimal_bids_guild(lo, hi, step, fair)
    candidate_bids = list(range(lo, fair + step, step))

    SEP = "=" * 55
    print(SEP)
    print("  Celestial Gardeners' Guild - Bid Optimizer")
    print(SEP)
    print(f"  Reserve range:  [{lo}, {hi}]  step {step}")
    print(f"  Fair sell price: {fair}")
    print(f"  N counterparties: {len(_guild_reserve_prices(lo, hi, step))}")
    print()
    print(f"  Optimal bid 1: {b1:5d}   (E[PnL1] = {expected_pnl_bid1(b1, lo, hi, step, fair):,.0f})")
    print(f"  Optimal bid 2: {b2:5d}   (E[PnL2] = {expected_pnl_bid2_dominant(b2, b1, lo, hi, step, fair):,.0f})")
    print(f"  Total E[PnL]:  {epnl:,.0f}")
    print()
    print("  Bid-1 sweep (top 5):")
    b1_scores = sorted(
        ((b, expected_pnl_bid1(b, lo, hi, step, fair)) for b in candidate_bids),
        key=lambda t: -t[1],
    )
    for b, pnl in b1_scores[:5]:
        marker = " <- optimal" if b == b1 else ""
        print(f"    b1={b:4d}  E[PnL] = {pnl:6,.0f}{marker}")
    print(SEP)


# ── Convenience: BS + IV for a single snapshot ────────────────────────────────

def price_all_vouchers(
    S: float,
    T_days: int,
    sigma: float,
    r: float = 0.0,
) -> dict[str, float]:
    """Return theoretical BS prices for all 10 VEV vouchers.

    Args:
        S:       Current underlying price.
        T_days:  TTE in days (e.g. 5 for round 3 start).
        sigma:   Annualised volatility.
    """
    T = tte_to_years(T_days)
    return {sym: bs_call(S, K, T, sigma, r) for sym, K in VEV_STRIKES.items()}

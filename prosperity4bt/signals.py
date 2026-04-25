"""Pure utility functions for use inside Trader.run() implementations.

All functions operate on the standard datamodel types (OrderDepth, etc.)
and return plain Python floats/lists — no external dependencies required.

Usage example inside a Trader:
    from prosperity4bt.signals import ema, order_book_imbalance, weighted_mid

    class MyStrategy:
        _ema = 10000.0

        def trade(self, depth, position, trader_data):
            mid = weighted_mid(depth) or 10000.0
            self._ema = ema(mid, self._ema, alpha=0.1)
            imb = order_book_imbalance(depth) or 0.0
            ...
"""

from typing import Optional

from prosperity4bt.datamodel import OrderDepth


# ── Moving averages ────────────────────────────────────────────────────────────

def ema(new_val: float, prev_ema: float, alpha: float) -> float:
    """Single EMA update: alpha * new_val + (1 - alpha) * prev_ema."""
    return alpha * new_val + (1.0 - alpha) * prev_ema


def rolling_ema(values: list, alpha: float) -> list[float]:
    """Return a full EMA sequence computed from a list of values."""
    if not values:
        return []
    result = [float(values[0])]
    for v in values[1:]:
        result.append(ema(float(v), result[-1], alpha))
    return result


# ── Order-book derived prices ──────────────────────────────────────────────────

def mid_price(depth: OrderDepth) -> Optional[float]:
    """(best_bid + best_ask) / 2, or None if either side is empty."""
    if not depth.buy_orders or not depth.sell_orders:
        return None
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0


def weighted_mid(depth: OrderDepth) -> Optional[float]:
    """Micro-price: bid * ask_size + ask * bid_size / (bid_size + ask_size).

    This weights the mid toward whichever side has less liquidity, which is
    empirically a better short-term price predictor than the raw mid.
    """
    if not depth.buy_orders or not depth.sell_orders:
        return None
    best_bid = max(depth.buy_orders)
    best_ask = min(depth.sell_orders)
    bid_size = abs(depth.buy_orders[best_bid])
    ask_size = abs(depth.sell_orders[best_ask])
    total = bid_size + ask_size
    if total == 0:
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_size + best_ask * bid_size) / total


def spread(depth: OrderDepth) -> Optional[float]:
    """best_ask − best_bid, or None if either side is empty."""
    if not depth.buy_orders or not depth.sell_orders:
        return None
    return float(min(depth.sell_orders) - max(depth.buy_orders))


# ── Order-book imbalance ───────────────────────────────────────────────────────

def order_book_imbalance(depth: OrderDepth) -> Optional[float]:
    """L1 imbalance: (bid_vol − ask_vol) / (bid_vol + ask_vol).

    Returns a value in [-1, +1]:
        +1 = only bids  (buy pressure)
        -1 = only asks  (sell pressure)
         0 = balanced
    """
    if not depth.buy_orders or not depth.sell_orders:
        return None
    bid_vol = abs(depth.buy_orders[max(depth.buy_orders)])
    ask_vol = abs(depth.sell_orders[min(depth.sell_orders)])
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


# ── Market-impact / fill-cost estimates ───────────────────────────────────────

def vwap_fill_cost(depth: OrderDepth, size: int, side: str) -> Optional[float]:
    """Average fill price if we market-buy or market-sell `size` units now.

    side='buy'  → we lift from the ask side (ascending prices).
    side='sell' → we hit the bid side (descending prices).

    Returns None if the visible book doesn't have enough depth to fill `size`.
    """
    if side == "buy":
        levels = sorted(depth.sell_orders.items())          # ascending asks
        volumes = [(price, abs(vol)) for price, vol in levels]
    else:
        levels = sorted(depth.buy_orders.items(), reverse=True)  # descending bids
        volumes = [(price, abs(vol)) for price, vol in levels]

    remaining = size
    total_cost = 0.0
    for price, vol in volumes:
        fill = min(remaining, vol)
        total_cost += fill * price
        remaining -= fill
        if remaining == 0:
            return total_cost / size

    return None  # insufficient depth


def price_impact(depth: OrderDepth, size: int, side: str) -> Optional[float]:
    """Fractional price impact of filling `size` units: |vwap_fill - mid| / mid.

    Useful for gauging how much slippage a market order of a given size would incur.
    Returns None if mid or vwap_fill_cost is unavailable.
    """
    mid = mid_price(depth)
    vwap = vwap_fill_cost(depth, size, side)
    if mid is None or vwap is None or mid == 0:
        return None
    return abs(vwap - mid) / mid

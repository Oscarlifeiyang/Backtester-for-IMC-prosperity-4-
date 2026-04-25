"""Round 3 baseline trader with option IV calibration and delta hedging.

Products traded:
  HYDROGEL_PACK        - fixed-fair market maker around 10000
  VELVETFRUIT_EXTRACT  - EMA fair-value market maker plus option delta hedge
  VEV_* vouchers       - Black-Scholes taker using live implied-vol calibration

This is still a simple research strategy, but it fixes the biggest issues in
the first baseline: real R3 limits, TTE countdown, live sigma, and option hedge.
"""

import json
import math
import os

try:
    from datamodel import Order, OrderDepth, TradingState
except ImportError:
    from prosperity4bt.datamodel import Order, OrderDepth, TradingState


VEV_STRIKES = {
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

LIMITS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    **{sym: 300 for sym in VEV_STRIKES},
}


def _ncdf(x: float) -> float:
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + 0.2316419 * x)
    p = t * (
        0.319381530
        + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429)))
    )
    return (1.0 + sign) / 2.0 - sign * math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi) * p


def bs_call(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0 or S <= 0:
        return max(0.0, S - K)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    return S * _ncdf(d1) - K * _ncdf(d2)


def bs_delta(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0:
        return 1.0 if S > K else 0.0
    if sigma <= 0 or S <= 0:
        return 1.0 if S > K else 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_T)
    return _ncdf(d1)


def bs_iv(price: float, S: float, K: float, T: float) -> float | None:
    intrinsic = max(0.0, S - K)
    if T <= 0 or price < intrinsic - 1e-6:
        return None

    lo, hi = 1e-6, 5.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        val = bs_call(S, K, T, mid)
        if abs(val - price) < 1e-4:
            return mid
        if val < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def mid_price(depth: OrderDepth) -> float | None:
    if not depth.buy_orders or not depth.sell_orders:
        return None
    return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def current_tte_days() -> int:
    day = int(os.environ.get("PROSPERITY4BT_DAY", "0"))
    return max(1, 5 - day)


class HydrogelMM:
    PRODUCT = "HYDROGEL_PACK"
    FAIR = 10000
    TAKE_EDGE = 4
    QUOTE_EDGE = 6
    SIZE = 25

    @classmethod
    def trade(cls, depth: OrderDepth, position: int) -> list[Order]:
        orders: list[Order] = []
        limit = LIMITS[cls.PRODUCT]
        buy_cap = max(0, limit - position)
        sell_cap = max(0, limit + position)

        for ask in sorted(depth.sell_orders):
            if ask >= cls.FAIR - cls.TAKE_EDGE or buy_cap <= 0:
                break
            qty = min(-depth.sell_orders[ask], buy_cap, 30)
            orders.append(Order(cls.PRODUCT, ask, qty))
            buy_cap -= qty

        for bid in sorted(depth.buy_orders, reverse=True):
            if bid <= cls.FAIR + cls.TAKE_EDGE or sell_cap <= 0:
                break
            qty = min(depth.buy_orders[bid], sell_cap, 30)
            orders.append(Order(cls.PRODUCT, bid, -qty))
            sell_cap -= qty

        if buy_cap > 0:
            orders.append(Order(cls.PRODUCT, cls.FAIR - cls.QUOTE_EDGE, min(buy_cap, cls.SIZE)))
        if sell_cap > 0:
            orders.append(Order(cls.PRODUCT, cls.FAIR + cls.QUOTE_EDGE, -min(sell_cap, cls.SIZE)))

        return orders


class VelvetMM:
    PRODUCT = "VELVETFRUIT_EXTRACT"
    ALPHA = 0.20
    TAKE_EDGE = 3
    QUOTE_EDGE = 5
    SIZE = 20

    @classmethod
    def fair_value(cls, depth: OrderDepth, td: dict) -> float | None:
        mid = mid_price(depth)
        if mid is None:
            return td.get("velvet_fair")
        prev = td.get("velvet_fair", mid)
        fair = cls.ALPHA * mid + (1.0 - cls.ALPHA) * prev
        td["velvet_fair"] = fair
        return fair

    @classmethod
    def trade(cls, depth: OrderDepth, position: int, td: dict) -> list[Order]:
        fair = cls.fair_value(depth, td)
        if fair is None:
            return []

        orders: list[Order] = []
        limit = LIMITS[cls.PRODUCT]
        buy_cap = max(0, limit - position)
        sell_cap = max(0, limit + position)

        for ask in sorted(depth.sell_orders):
            if ask >= fair - cls.TAKE_EDGE or buy_cap <= 0:
                break
            qty = min(-depth.sell_orders[ask], buy_cap, 25)
            orders.append(Order(cls.PRODUCT, ask, qty))
            buy_cap -= qty

        for bid in sorted(depth.buy_orders, reverse=True):
            if bid <= fair + cls.TAKE_EDGE or sell_cap <= 0:
                break
            qty = min(depth.buy_orders[bid], sell_cap, 25)
            orders.append(Order(cls.PRODUCT, bid, -qty))
            sell_cap -= qty

        bid_q = int(round(fair - cls.QUOTE_EDGE))
        ask_q = int(round(fair + cls.QUOTE_EDGE))
        if buy_cap > 0:
            orders.append(Order(cls.PRODUCT, bid_q, min(buy_cap, cls.SIZE)))
        if sell_cap > 0:
            orders.append(Order(cls.PRODUCT, ask_q, -min(sell_cap, cls.SIZE)))

        return orders


class VEVOptionTaker:
    EDGE = 1
    SIZE = 20
    SIGMA_ALPHA = 0.08
    DEFAULT_SIGMA = 0.40

    @classmethod
    def calibrate_sigma(cls, order_depths: dict[str, OrderDepth], S: float, T: float, td: dict) -> float:
        ivs: list[float] = []
        for sym, K in VEV_STRIKES.items():
            depth = order_depths.get(sym)
            opt_mid = mid_price(depth) if depth is not None else None
            if opt_mid is None:
                continue
            # Near-the-money options are most informative for one shared sigma.
            if abs(K - S) > 450:
                continue
            iv = bs_iv(opt_mid, S, K, T)
            if iv is not None and 0.05 <= iv <= 2.0:
                ivs.append(iv)

        snap_sigma = sorted(ivs)[len(ivs) // 2] if ivs else td.get("sigma", cls.DEFAULT_SIGMA)
        prev = td.get("sigma", snap_sigma)
        sigma = cls.SIGMA_ALPHA * snap_sigma + (1.0 - cls.SIGMA_ALPHA) * prev
        td["sigma"] = sigma
        td["sigma_snapshot"] = snap_sigma
        return sigma

    @classmethod
    def trade_all(cls, order_depths: dict, positions: dict, td: dict) -> dict[str, list[Order]]:
        S = td.get("velvet_fair", 5250.0)
        T = current_tte_days() / 252.0
        sigma = cls.calibrate_sigma(order_depths, S, T, td)

        result: dict[str, list[Order]] = {}
        planned_positions: dict[str, int] = {}

        for sym, K in VEV_STRIKES.items():
            depth = order_depths.get(sym)
            if depth is None:
                result[sym] = []
                planned_positions[sym] = positions.get(sym, 0)
                continue

            fair = bs_call(S, K, T, sigma)
            pos = positions.get(sym, 0)
            limit = LIMITS[sym]
            buy_cap = max(0, limit - pos)
            sell_cap = max(0, limit + pos)
            orders: list[Order] = []
            planned = pos

            for ask in sorted(depth.sell_orders):
                if ask >= fair - cls.EDGE or buy_cap <= 0:
                    break
                qty = min(-depth.sell_orders[ask], buy_cap, cls.SIZE)
                orders.append(Order(sym, ask, qty))
                buy_cap -= qty
                planned += qty

            for bid in sorted(depth.buy_orders, reverse=True):
                if bid <= fair + cls.EDGE or sell_cap <= 0:
                    break
                qty = min(depth.buy_orders[bid], sell_cap, cls.SIZE)
                orders.append(Order(sym, bid, -qty))
                sell_cap -= qty
                planned -= qty

            result[sym] = orders
            planned_positions[sym] = planned

        td["option_delta"] = cls.option_delta(planned_positions, S, T, sigma)
        return result

    @staticmethod
    def option_delta(positions: dict[str, int], S: float, T: float, sigma: float) -> float:
        total = 0.0
        for sym, qty in positions.items():
            K = VEV_STRIKES.get(sym)
            if K is not None:
                total += qty * bs_delta(S, K, T, sigma)
        return total


class DeltaHedger:
    PRODUCT = "VELVETFRUIT_EXTRACT"
    THRESHOLD = 25
    MAX_HEDGE_SIZE = 40

    @classmethod
    def trade(cls, depth: OrderDepth, current_position: int, pending_orders: list[Order], td: dict) -> list[Order]:
        if not depth.buy_orders or not depth.sell_orders:
            return []

        pending_delta = sum(o.quantity for o in pending_orders)
        option_delta = float(td.get("option_delta", 0.0))
        desired_underlying = clamp(int(round(-option_delta)), -LIMITS[cls.PRODUCT], LIMITS[cls.PRODUCT])
        current_after_pending = current_position + pending_delta
        diff = desired_underlying - current_after_pending

        if abs(diff) < cls.THRESHOLD:
            return []

        if diff > 0:
            qty = min(diff, cls.MAX_HEDGE_SIZE, LIMITS[cls.PRODUCT] - current_after_pending)
            return [Order(cls.PRODUCT, min(depth.sell_orders), qty)] if qty > 0 else []

        qty = min(-diff, cls.MAX_HEDGE_SIZE, LIMITS[cls.PRODUCT] + current_after_pending)
        return [Order(cls.PRODUCT, max(depth.buy_orders), -qty)] if qty > 0 else []


class Trader:
    def run(self, state: TradingState):
        try:
            td: dict = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        orders: dict[str, list[Order]] = {}

        if "HYDROGEL_PACK" in state.order_depths:
            orders["HYDROGEL_PACK"] = HydrogelMM.trade(
                state.order_depths["HYDROGEL_PACK"],
                state.position.get("HYDROGEL_PACK", 0),
            )

        velvet_orders: list[Order] = []
        if "VELVETFRUIT_EXTRACT" in state.order_depths:
            velvet_orders = VelvetMM.trade(
                state.order_depths["VELVETFRUIT_EXTRACT"],
                state.position.get("VELVETFRUIT_EXTRACT", 0),
                td,
            )

        vev_orders = VEVOptionTaker.trade_all(state.order_depths, state.position, td)
        orders.update(vev_orders)

        if "VELVETFRUIT_EXTRACT" in state.order_depths:
            velvet_orders.extend(
                DeltaHedger.trade(
                    state.order_depths["VELVETFRUIT_EXTRACT"],
                    state.position.get("VELVETFRUIT_EXTRACT", 0),
                    velvet_orders,
                    td,
                )
            )
            orders["VELVETFRUIT_EXTRACT"] = velvet_orders

        return orders, 0, json.dumps(td, separators=(",", ":"))

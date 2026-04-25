import math
from collections import defaultdict
from typing import Any

from prosperity4bt.data import LIMITS
from prosperity4bt.models import BacktestResult

_DEFAULT_LIMIT = 80


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    """Compute performance metrics from a BacktestResult.

    Returns a dict with keys:
        total_pnl, product_pnl, sharpe, max_drawdown,
        trade_count, avg_trade_size, win_rate, inventory_utilization
    """
    # ActivityLogRow column layout (from runner.py):
    #   [0] day  [1] timestamp  [2] product
    #   [3..14]  bid/ask prices & volumes (3 levels each)
    #   [15] mid_price  [16] profit_and_loss
    mid_lookup: dict[tuple[int, str], float] = {}
    pnl_by_product: dict[str, dict[int, float]] = defaultdict(dict)

    for row in result.activity_logs:
        ts: int = row.columns[1]
        product: str = row.columns[2]
        mid = row.columns[15]
        pnl = row.columns[16]

        if mid != "" and float(mid) > 0:
            mid_lookup[(ts, product)] = float(mid)
        pnl_by_product[product][ts] = float(pnl)

    # Final per-product PnL (value at last timestamp)
    product_pnl: dict[str, float] = {
        p: max(ts_pnl.items(), key=lambda kv: kv[0])[1]
        for p, ts_pnl in pnl_by_product.items()
    }
    total_pnl = sum(product_pnl.values())

    # Total PnL time series (sum across products per timestamp)
    total_by_ts: dict[int, float] = defaultdict(float)
    for ts_pnl in pnl_by_product.values():
        for ts, pnl in ts_pnl.items():
            total_by_ts[ts] += pnl
    pnl_series = [total_by_ts[ts] for ts in sorted(total_by_ts)]

    # Sharpe ratio (per-step, not annualized — competition has no fixed time unit)
    sharpe = float("nan")
    if len(pnl_series) > 2:
        inc = [pnl_series[i] - pnl_series[i - 1] for i in range(1, len(pnl_series))]
        n = len(inc)
        mean = sum(inc) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in inc) / n)
        if std > 0:
            sharpe = mean / std

    # Max drawdown (peak-to-trough of total PnL curve)
    max_drawdown = 0.0
    peak = pnl_series[0] if pnl_series else 0.0
    for pnl in pnl_series:
        peak = max(peak, pnl)
        max_drawdown = min(max_drawdown, pnl - peak)

    # Our trades = buyer or seller == "SUBMISSION"
    our_trades = [
        tr for tr in result.trades
        if tr.trade.buyer == "SUBMISSION" or tr.trade.seller == "SUBMISSION"
    ]
    trade_count = len(our_trades)
    avg_trade_size = (
        sum(abs(tr.trade.quantity) for tr in our_trades) / trade_count
        if trade_count > 0 else 0.0
    )

    # Win rate: fraction of trades with favorable fill vs mid price at that timestamp
    wins = 0
    for tr in our_trades:
        t = tr.trade
        mid = mid_lookup.get((t.timestamp, t.symbol), 0.0)
        if mid <= 0:
            continue
        if t.buyer == "SUBMISSION" and t.price < mid:
            wins += 1
        elif t.seller == "SUBMISSION" and t.price > mid:
            wins += 1
    win_rate = wins / trade_count if trade_count > 0 else 0.0

    # Inventory utilization: reconstruct position from our trades, average |pos| / limit
    pos: dict[str, int] = defaultdict(int)
    pos_sum: dict[str, float] = defaultdict(float)
    pos_count: dict[str, int] = defaultdict(int)
    for tr in sorted(our_trades, key=lambda x: x.timestamp):
        t = tr.trade
        if t.buyer == "SUBMISSION":
            pos[t.symbol] += t.quantity
        else:
            pos[t.symbol] -= t.quantity
        pos_sum[t.symbol] += abs(pos[t.symbol])
        pos_count[t.symbol] += 1

    inventory_utilization = {
        p: (pos_sum[p] / pos_count[p]) / LIMITS.get(p, _DEFAULT_LIMIT)
        for p in pos_sum if pos_count[p] > 0
    }

    return {
        "total_pnl": total_pnl,
        "product_pnl": product_pnl,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "trade_count": trade_count,
        "avg_trade_size": avg_trade_size,
        "win_rate": win_rate,
        "inventory_utilization": inventory_utilization,
    }


def format_metrics(metrics: dict[str, Any]) -> str:
    """Format a metrics dict into a human-readable table string."""
    W = 58
    sep = "-" * W
    lines = [f"\n{sep}", "  Backtest Metrics", sep]

    lines.append(f"  {'Total PnL':<28} {metrics['total_pnl']:>14,.1f}")

    if metrics["product_pnl"]:
        lines.append(f"  {'Product PnL':}")
        for product, pnl in sorted(metrics["product_pnl"].items()):
            lines.append(f"    {product:<26} {pnl:>14,.1f}")

    s = metrics["sharpe"]
    sharpe_str = f"{s:.4f}" if not math.isnan(s) else "N/A"
    lines.append(f"  {'Sharpe (per step)':<28} {sharpe_str:>14}")
    lines.append(f"  {'Max Drawdown':<28} {metrics['max_drawdown']:>14,.1f}")
    lines.append(f"  {'Trade Count':<28} {metrics['trade_count']:>14,}")
    lines.append(f"  {'Avg Trade Size':<28} {metrics['avg_trade_size']:>14.1f}")
    lines.append(f"  {'Win Rate (fill vs mid)':<28} {metrics['win_rate']:>14.1%}")

    if metrics["inventory_utilization"]:
        lines.append(f"  {'Inventory Utilization':}")
        for product, util in sorted(metrics["inventory_utilization"].items()):
            lines.append(f"    {product:<26} {util:>14.1%}")

    lines.append(sep + "\n")
    return "\n".join(lines)

"""Decision-oriented Plotly dashboard for IMC backtests.

This dashboard is intentionally split into three views:
  1. Overview: what made/lost money and when drawdown happened.
  2. Product Drilldown: one product at a time, with price, trades, position,
     spread, and fill edge.
  3. Options IV: Round 3 VEV raw IV, relative IV, and smile snapshots.

The goal is to make the dashboard useful for changing strategy parameters, not
just to dump every available trace onto one plot.
"""

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Union

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False

from prosperity4bt.data import LIMITS
from prosperity4bt.models import BacktestResult
from prosperity4bt.options import VEV_STRIKES, bs_iv, tte_to_years
from prosperity4bt.visualizer import parse_visualizer_log

DEFAULT_LIMIT = 80
MAX_IV_COLUMNS = 450
ROLLING_WINDOW = 150
ZSCORE_WINDOWS = (50, 150, 500)


def _check_plotly() -> None:
    if not _HAS_PLOTLY:
        raise ImportError("plotly is required for the dashboard. Install it with: pip install plotly")


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)


def _product_limit(product: str) -> int:
    return LIMITS.get(product, DEFAULT_LIMIT)


def _drawdown(values: list[float]) -> list[float]:
    peak = values[0] if values else 0.0
    out = []
    for value in values:
        peak = max(peak, value)
        out.append(value - peak)
    return out


def _rolling_mean(values: list[Optional[float]], window: int = ROLLING_WINDOW) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    queue: list[float] = []
    total = 0.0
    for value in values:
        if value is not None:
            queue.append(float(value))
            total += float(value)
        if len(queue) > window:
            total -= queue.pop(0)
        out.append(total / len(queue) if queue else None)
    return out


def _rolling_zscore(values: list[Optional[float]], window: int = ROLLING_WINDOW) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    queue: list[float] = []
    for value in values:
        if value is not None:
            queue.append(float(value))
        if len(queue) > window:
            queue.pop(0)
        if value is None or len(queue) < 10:
            out.append(None)
            continue
        mean = sum(queue) / len(queue)
        var = sum((x - mean) ** 2 for x in queue) / len(queue)
        std = math.sqrt(var)
        out.append((float(value) - mean) / std if std > 1e-9 else 0.0)
    return out


def _finite(values: list[Optional[float]]) -> list[float]:
    return [float(v) for v in values if v is not None]


def _fill_type(trade: dict) -> str:
    if trade["buyer"] == "SUBMISSION":
        return "book" if trade.get("seller", "") == "" else "tape"
    return "book" if trade.get("buyer", "") == "" else "tape"


def _fill_edge(trade: dict, mid: float) -> float:
    if trade["buyer"] == "SUBMISSION":
        return mid - trade["price"]
    return trade["price"] - mid


def _compute_report_data(
    activity_by_product: dict[str, list[dict]],
    our_trades_by_product: dict[str, list[dict]],
) -> dict[str, Any]:
    total_by_ts: dict[int, float] = defaultdict(float)
    mid_lookup: dict[tuple[int, str], float] = {}
    product_stats: dict[str, dict[str, Any]] = {}
    fill_rows: list[dict[str, Any]] = []

    for product, rows in activity_by_product.items():
        rows_sorted = sorted(rows, key=lambda r: r["timestamp"])
        if not rows_sorted:
            continue

        for row in rows_sorted:
            total_by_ts[row["timestamp"]] += row["profit_and_loss"]
            mid_lookup[(row["timestamp"], product)] = row["mid_price"]

        product_stats[product] = {
            "final_pnl": rows_sorted[-1]["profit_and_loss"],
            "trade_count": 0,
            "book_fills": 0,
            "tape_fills": 0,
            "avg_edge": 0.0,
            "edge_sum": 0.0,
            "edge_qty": 0,
            "avg_size": 0.0,
            "size_sum": 0,
        }

    trade_count = 0
    book_fills = 0
    tape_fills = 0
    win_count = 0
    edge_sum = 0.0
    edge_qty = 0
    size_sum = 0

    for product, trades in our_trades_by_product.items():
        stats = product_stats.setdefault(
            product,
            {
                "final_pnl": 0.0,
                "trade_count": 0,
                "book_fills": 0,
                "tape_fills": 0,
                "avg_edge": 0.0,
                "edge_sum": 0.0,
                "edge_qty": 0,
                "avg_size": 0.0,
                "size_sum": 0,
            },
        )
        for trade in trades:
            mid = mid_lookup.get((trade["timestamp"], product))
            kind = _fill_type(trade)
            qty = trade["quantity"]
            side = "BUY" if trade["buyer"] == "SUBMISSION" else "SELL"

            trade_count += 1
            size_sum += qty
            stats["trade_count"] += 1
            stats["size_sum"] += qty

            if kind == "book":
                book_fills += 1
                stats["book_fills"] += 1
            else:
                tape_fills += 1
                stats["tape_fills"] += 1

            if mid is None or mid <= 0:
                continue

            edge = _fill_edge(trade, mid)
            edge_sum += edge * qty
            edge_qty += qty
            stats["edge_sum"] += edge * qty
            stats["edge_qty"] += qty
            if edge > 0:
                win_count += 1

            fill_rows.append(
                {
                    "timestamp": trade["timestamp"],
                    "product": product,
                    "side": side,
                    "price": trade["price"],
                    "mid": mid,
                    "quantity": qty,
                    "edge": edge,
                    "fill_type": kind,
                }
            )

    for stats in product_stats.values():
        stats["avg_edge"] = stats["edge_sum"] / stats["edge_qty"] if stats["edge_qty"] else 0.0
        stats["avg_size"] = stats["size_sum"] / stats["trade_count"] if stats["trade_count"] else 0.0

    pnl_timestamps = sorted(total_by_ts)
    pnl_series = [total_by_ts[ts] for ts in pnl_timestamps]
    dd_series = _drawdown(pnl_series)

    sharpe = float("nan")
    if len(pnl_series) > 2:
        increments = [pnl_series[i] - pnl_series[i - 1] for i in range(1, len(pnl_series))]
        mean = sum(increments) / len(increments)
        std = math.sqrt(sum((x - mean) ** 2 for x in increments) / len(increments))
        if std > 0:
            sharpe = mean / std

    return {
        "pnl_timestamps": pnl_timestamps,
        "pnl_series": pnl_series,
        "dd_series": dd_series,
        "total_pnl": pnl_series[-1] if pnl_series else 0.0,
        "max_drawdown": min(dd_series) if dd_series else 0.0,
        "sharpe": sharpe,
        "win_rate": win_count / trade_count if trade_count else 0.0,
        "trade_count": trade_count,
        "book_fills": book_fills,
        "tape_fills": tape_fills,
        "avg_size": size_sum / trade_count if trade_count else 0.0,
        "avg_edge": edge_sum / edge_qty if edge_qty else 0.0,
        "product_stats": product_stats,
        "fill_rows": sorted(fill_rows, key=lambda r: r["edge"]),
    }


def _metric_cards(data: dict[str, Any], title: str) -> str:
    def color(value: float) -> str:
        return "#16794c" if value >= 0 else "#b3261e"

    sharpe = data["sharpe"]
    sharpe_text = f"{sharpe:.4f}" if not math.isnan(sharpe) else "N/A"
    cards = [
        ("Total PnL", f"{data['total_pnl']:,.0f}", color(data["total_pnl"])),
        ("Max Drawdown", f"{data['max_drawdown']:,.0f}", "#b3261e"),
        ("Sharpe", sharpe_text, "#174ea6"),
        ("Win Rate", f"{data['win_rate']:.1%}", "#7b1fa2"),
        ("Avg Mid Edge", f"{data['avg_edge']:.2f}", color(data["avg_edge"])),
        ("Trades", f"{data['trade_count']:,}", "#202124"),
        ("Book Fills", f"{data['book_fills']:,}", "#202124"),
        ("Tape Fills", f"{data['tape_fills']:,}", "#202124"),
    ]
    items = "".join(
        f"<div class='metric'><span>{label}</span><strong style='color:{c}'>{value}</strong></div>"
        for label, value, c in cards
    )
    return f"<section class='top'><h1>{title}</h1><div class='metric-grid'>{items}</div></section>"


def _overview_figure(data: dict[str, Any]) -> "go.Figure":
    stats = data["product_stats"]
    products = sorted(stats, key=lambda p: stats[p]["final_pnl"], reverse=True)

    fig = make_subplots(
        rows=4,
        cols=1,
        subplot_titles=[
            "Total PnL",
            "Drawdown",
            "Product PnL Contribution",
            "Average Mid Edge by Product",
        ],
        vertical_spacing=0.10,
        row_heights=[0.30, 0.22, 0.25, 0.23],
    )
    fig.add_trace(
        go.Scatter(x=data["pnl_timestamps"], y=data["pnl_series"], name="Total PnL", line=dict(color="#16794c")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=data["pnl_timestamps"],
            y=data["dd_series"],
            name="Drawdown",
            fill="tozeroy",
            line=dict(color="#b3261e"),
            fillcolor="rgba(179,38,30,0.18)",
        ),
        row=2,
        col=1,
    )
    # Horizontal bars keep long product names readable and avoid subplot-title overlap.
    bar_products = list(reversed(products))
    pnl_vals = [stats[p]["final_pnl"] for p in bar_products]
    fig.add_trace(
        go.Bar(
            x=pnl_vals,
            y=bar_products,
            name="Product PnL",
            orientation="h",
            marker_color=["#16794c" if v >= 0 else "#b3261e" for v in pnl_vals],
        ),
        row=3,
        col=1,
    )
    edge_vals = [stats[p]["avg_edge"] for p in bar_products]
    fig.add_trace(
        go.Bar(
            x=edge_vals,
            y=bar_products,
            name="Avg Mid Edge",
            orientation="h",
            marker_color=["#16794c" if v >= 0 else "#b3261e" for v in edge_vals],
        ),
        row=4,
        col=1,
    )
    for row in [1, 2]:
        fig.add_hline(y=0, line=dict(color="#aab", width=1, dash="dot"), row=row, col=1)
    for row in [3, 4]:
        fig.add_vline(x=0, line=dict(color="#aab", width=1, dash="dot"), row=row, col=1)
    fig.update_layout(height=940, template="plotly_white", showlegend=False, margin=dict(l=170, r=30, t=70, b=55))
    fig.update_xaxes(title_text="PnL", row=3, col=1)
    fig.update_xaxes(title_text="Mid edge: positive = better than mid, negative = paid spread/adverse fill", row=4, col=1)
    return fig


def _product_table(data: dict[str, Any]) -> str:
    rows = []
    for product, stats in sorted(data["product_stats"].items(), key=lambda kv: kv[1]["final_pnl"], reverse=True):
        pnl = stats["final_pnl"]
        pnl_color = "#16794c" if pnl >= 0 else "#b3261e"
        edge_color = "#16794c" if stats["avg_edge"] >= 0 else "#b3261e"
        rows.append(
            "<tr>"
            f"<td>{product}</td>"
            f"<td class='num' style='color:{pnl_color}'>{pnl:,.0f}</td>"
            f"<td class='num'>{stats['trade_count']:,}</td>"
            f"<td class='num' style='color:{edge_color}'>{stats['avg_edge']:.2f}</td>"
            f"<td class='num'>{stats['book_fills']:,}</td>"
            f"<td class='num'>{stats['tape_fills']:,}</td>"
            "</tr>"
        )
    return (
        "<h2>Product Scorecard</h2>"
        "<table><thead><tr><th>Product</th><th>Final PnL</th><th>Trades</th>"
        "<th>Avg Mid Edge</th><th>Book</th><th>Tape</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _bad_fill_table(data: dict[str, Any], n: int = 20) -> str:
    rows = []
    for item in data["fill_rows"][:n]:
        rows.append(
            "<tr>"
            f"<td>{item['timestamp']}</td>"
            f"<td>{item['product']}</td>"
            f"<td>{item['side']}</td>"
            f"<td class='num'>{item['price']:,.0f}</td>"
            f"<td class='num'>{item['mid']:,.1f}</td>"
            f"<td class='num'>{item['quantity']}</td>"
            f"<td class='num bad'>{item['edge']:.2f}</td>"
            f"<td>{item['fill_type']}</td>"
            "</tr>"
        )
    return (
        "<h2>Worst Executions vs Mid</h2>"
        "<table><thead><tr><th>Timestamp</th><th>Product</th><th>Side</th><th>Price</th>"
        "<th>Mid</th><th>Qty</th><th>Mid Edge</th><th>Fill</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _positions(trades: list[dict], day_starts: list[int]) -> tuple[list[int], list[int]]:
    reset_points = sorted(day_starts[1:])
    reset_idx = 0
    pos = 0
    xs: list[int] = []
    ys: list[int] = []
    for trade in sorted(trades, key=lambda t: t["timestamp"]):
        while reset_idx < len(reset_points) and trade["timestamp"] >= reset_points[reset_idx]:
            xs.append(reset_points[reset_idx])
            ys.append(0)
            pos = 0
            reset_idx += 1
        pos += trade["quantity"] if trade["buyer"] == "SUBMISSION" else -trade["quantity"]
        xs.append(trade["timestamp"])
        ys.append(pos)
    return xs, ys


def _product_figure(product: str, rows: list[dict], trades: list[dict], day_starts: list[int]) -> "go.Figure":
    rows = sorted(rows, key=lambda r: r["timestamp"])
    trades = sorted(trades, key=lambda t: t["timestamp"])
    ts = [r["timestamp"] for r in rows]
    mid = [r["mid_price"] for r in rows]
    pnl = [r["profit_and_loss"] for r in rows]
    spread = [r.get("bid_ask_spread") for r in rows]
    imb = []
    mid_lookup = {}
    for row in rows:
        mid_lookup[row["timestamp"]] = row["mid_price"]
        bv = row.get("bid_volume_1") or 0
        av = row.get("ask_volume_1") or 0
        imb.append((bv - av) / (bv + av) if bv + av else 0.0)
    spread_smooth = _rolling_mean(spread)
    imb_smooth = _rolling_mean(imb)
    imb_z_by_window = {window: _rolling_zscore(imb, window) for window in ZSCORE_WINDOWS}

    buy_ts = [t["timestamp"] for t in trades if t["buyer"] == "SUBMISSION"]
    buy_px = [t["price"] for t in trades if t["buyer"] == "SUBMISSION"]
    sell_ts = [t["timestamp"] for t in trades if t["seller"] == "SUBMISSION"]
    sell_px = [t["price"] for t in trades if t["seller"] == "SUBMISSION"]
    pos_ts, pos_vals = _positions(trades, day_starts)

    edge_ts = []
    edge_vals = []
    edge_colors = []
    edge_text = []
    for trade in trades:
        m = mid_lookup.get(trade["timestamp"])
        if m is None or m <= 0:
            continue
        edge = _fill_edge(trade, m)
        edge_ts.append(trade["timestamp"])
        edge_vals.append(edge)
        edge_colors.append("#16794c" if edge >= 0 else "#b3261e")
        side = "buy" if trade["buyer"] == "SUBMISSION" else "sell"
        edge_text.append(f"{side} {trade['quantity']} @ {trade['price']}<br>mid {m:.1f}<br>edge {edge:.2f}")

    fig = make_subplots(
        rows=9,
        cols=1,
        shared_xaxes=True,
        subplot_titles=[
            f"{product}: Mid Price and Trades",
            "PnL",
            "Position",
            f"Spread {ROLLING_WINDOW}-Tick Rolling Mean",
            f"L1 Imbalance {ROLLING_WINDOW}-Tick Rolling Mean",
            "L1 Imbalance Z-Score - 50 Tick",
            "L1 Imbalance Z-Score - 150 Tick",
            "L1 Imbalance Z-Score - 500 Tick",
            "Mid Edge vs Fill Price",
        ],
        vertical_spacing=0.045,
        row_heights=[0.22, 0.11, 0.13, 0.09, 0.10, 0.10, 0.10, 0.10, 0.15],
    )
    fig.add_trace(go.Scatter(x=ts, y=mid, name="Mid", line=dict(color="#174ea6")), row=1, col=1)
    fig.add_trace(
        go.Scatter(x=buy_ts, y=buy_px, mode="markers", name="Buy", marker=dict(symbol="triangle-up", color="#16794c", size=8)),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=sell_ts, y=sell_px, mode="markers", name="Sell", marker=dict(symbol="triangle-down", color="#b3261e", size=8)),
        row=1,
        col=1,
    )
    fig.add_trace(go.Scatter(x=ts, y=pnl, name="PnL", line=dict(color="#16794c")), row=2, col=1)
    fig.add_trace(go.Scatter(x=pos_ts, y=pos_vals, name="Position", line=dict(shape="hv", color="#7b1fa2")), row=3, col=1)
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=spread_smooth,
            name=f"Spread {ROLLING_WINDOW}-tick mean",
            line=dict(color="#f29900", width=2),
            hovertemplate="timestamp=%{x}<br>rolling spread=%{y:.3f}<extra></extra>",
        ),
        row=4,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=imb_smooth,
            name=f"Imbalance {ROLLING_WINDOW}-tick mean",
            line=dict(color="#5f6368", width=2),
            hovertemplate="timestamp=%{x}<br>rolling imbalance=%{y:.3f}<extra></extra>",
        ),
        row=5,
        col=1,
    )
    z_rows = {50: 6, 150: 7, 500: 8}
    z_styles = {
        50: dict(color="#b3261e", width=1.0),
        150: dict(color="#174ea6", width=1.4),
        500: dict(color="#16794c", width=1.2),
    }
    for window, row_num in z_rows.items():
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=imb_z_by_window[window],
                name=f"Imbalance z-score {window}",
                line=z_styles[window],
                hovertemplate=f"timestamp=%{{x}}<br>{window}-tick imbalance z=%{{y:.2f}}<extra></extra>",
            ),
            row=row_num,
            col=1,
        )
        fig.add_hrect(y0=2, y1=4, fillcolor="rgba(179,38,30,0.10)", line_width=0, row=row_num, col=1)
        fig.add_hrect(y0=-4, y1=-2, fillcolor="rgba(22,121,76,0.10)", line_width=0, row=row_num, col=1)
        fig.add_hline(y=2, line=dict(color="#b3261e", dash="dot", width=1), row=row_num, col=1)
        fig.add_hline(y=-2, line=dict(color="#16794c", dash="dot", width=1), row=row_num, col=1)
    fig.add_trace(
        go.Scatter(
            x=edge_ts,
            y=edge_vals,
            mode="markers",
            name="Fill Edge",
            marker=dict(color=edge_colors, size=7),
            text=edge_text,
            hovertemplate="%{text}<extra></extra>",
        ),
        row=9,
        col=1,
    )
    limit = _product_limit(product)
    fig.add_hline(y=limit, line=dict(color="#f29900", dash="dash", width=1), row=3, col=1)
    fig.add_hline(y=-limit, line=dict(color="#f29900", dash="dash", width=1), row=3, col=1)
    for row in [2, 3, 5, 6, 7, 8, 9]:
        fig.add_hline(y=0, line=dict(color="#aab", dash="dot", width=1), row=row, col=1)
    fig.update_layout(
        height=1370,
        autosize=True,
        template="plotly_white",
        margin=dict(l=70, r=45, t=70, b=45),
        legend=dict(orientation="h"),
    )
    fig.update_yaxes(title_text="Spread", row=4, col=1)
    fig.update_yaxes(title_text="Imb", row=5, col=1)
    fig.update_yaxes(title_text="Z", row=6, col=1)
    fig.update_yaxes(title_text="Z", row=7, col=1)
    fig.update_yaxes(title_text="Z", row=8, col=1)
    return fig


def _product_sections(
    activity_by_product: dict[str, list[dict]],
    our_trades_by_product: dict[str, list[dict]],
    day_starts: list[int],
) -> str:
    products = sorted(activity_by_product)
    selector = "<select id='product-select'>" + "".join(f"<option value='{_safe_name(p)}'>{p}</option>" for p in products) + "</select>"
    divs = []
    for i, product in enumerate(products):
        fig = _product_figure(product, activity_by_product[product], our_trades_by_product.get(product, []), day_starts)
        style = "" if i == 0 else " style='display:none'"
        divs.append(
            f"<div class='product-panel' id='prod-{_safe_name(product)}'{style}>"
            + fig.to_html(full_html=False, include_plotlyjs=False)
            + "</div>"
        )
    return f"<div class='control-row'><label>Product</label>{selector}</div>{''.join(divs)}"


def _sample_indices(n: int, max_points: int) -> list[int]:
    if n <= max_points:
        return list(range(n))
    step = math.ceil(n / max_points)
    return list(range(0, n, step))


def _iv_matrix(activity_by_product: dict[str, list[dict]]) -> tuple[list[int], list[int], list[list[Optional[float]]]]:
    if "VELVETFRUIT_EXTRACT" not in activity_by_product:
        return [], [], []

    underlying = {
        row["timestamp"]: row
        for row in activity_by_product["VELVETFRUIT_EXTRACT"]
        if row["mid_price"] > 0
    }
    all_ts = sorted(underlying)
    idxs = _sample_indices(len(all_ts), MAX_IV_COLUMNS)
    timestamps = [all_ts[i] for i in idxs]
    strikes = sorted(VEV_STRIKES.values())
    symbol_by_strike = {strike: sym for sym, strike in VEV_STRIKES.items()}
    option_rows = {
        product: {row["timestamp"]: row for row in rows}
        for product, rows in activity_by_product.items()
        if product in VEV_STRIKES
    }

    matrix: list[list[Optional[float]]] = []
    for strike in strikes:
        symbol = symbol_by_strike[strike]
        row_values = []
        rows_by_ts = option_rows.get(symbol, {})
        for ts in timestamps:
            u = underlying[ts]
            opt = rows_by_ts.get(ts)
            if opt is None or opt["mid_price"] <= 0:
                row_values.append(None)
                continue
            tte_days = max(1, 5 - int(opt.get("day", 0)))
            iv = bs_iv(opt["mid_price"], u["mid_price"], strike, tte_to_years(tte_days))
            row_values.append(iv if iv is not None and 0 <= iv <= 3 else None)
        matrix.append(row_values)
    return timestamps, strikes, matrix


def _relative_iv(matrix: list[list[Optional[float]]]) -> list[list[Optional[float]]]:
    if not matrix:
        return []
    rows = len(matrix)
    cols = len(matrix[0])
    out = [[None for _ in range(cols)] for _ in range(rows)]
    for c in range(cols):
        col = [matrix[r][c] for r in range(rows) if matrix[r][c] is not None]
        if not col:
            continue
        median = sorted(col)[len(col) // 2]
        for r in range(rows):
            out[r][c] = None if matrix[r][c] is None else matrix[r][c] - median
    return out


def _clip_matrix(matrix: list[list[Optional[float]]], lo: float, hi: float) -> list[list[Optional[float]]]:
    return [
        [None if value is None else max(lo, min(hi, value)) for value in row]
        for row in matrix
    ]


def _rich_cheap_series(
    timestamps: list[int],
    strikes: list[int],
    rel: list[list[Optional[float]]],
) -> tuple[list[int], list[Optional[int]], list[Optional[float]], list[Optional[int]], list[Optional[float]]]:
    rich_strikes: list[Optional[int]] = []
    rich_values: list[Optional[float]] = []
    cheap_strikes: list[Optional[int]] = []
    cheap_values: list[Optional[float]] = []

    for col in range(len(timestamps)):
        vals = [(strikes[row], rel[row][col]) for row in range(len(strikes)) if rel[row][col] is not None]
        if not vals:
            rich_strikes.append(None)
            rich_values.append(None)
            cheap_strikes.append(None)
            cheap_values.append(None)
            continue
        rich_strike, rich_value = max(vals, key=lambda item: item[1])
        cheap_strike, cheap_value = min(vals, key=lambda item: item[1])
        rich_strikes.append(rich_strike)
        rich_values.append(rich_value)
        cheap_strikes.append(cheap_strike)
        cheap_values.append(cheap_value)

    return timestamps, rich_strikes, rich_values, cheap_strikes, cheap_values


def _options_section(activity_by_product: dict[str, list[dict]]) -> str:
    timestamps, strikes, matrix = _iv_matrix(activity_by_product)
    if not matrix:
        return "<p>No VEV option surface could be computed for this run.</p>"

    rel = _relative_iv(matrix)
    clipped_raw = _clip_matrix(matrix, 0.10, 1.10)
    clipped_rel = _clip_matrix(rel, -0.35, 0.35)
    rich_ts, rich_strikes, rich_values, cheap_strikes, cheap_values = _rich_cheap_series(timestamps, strikes, rel)
    raw = go.Figure(
        go.Heatmap(
            x=timestamps,
            y=strikes,
            z=clipped_raw,
            colorscale="Cividis",
            zmin=0.10,
            zmax=1.10,
            colorbar=dict(title="IV (clipped)"),
            hovertemplate="timestamp=%{x}<br>strike=%{y}<br>IV=%{z:.2%}<extra></extra>",
        )
    )
    raw.update_layout(
        title="Downsampled Raw IV Surface",
        height=420,
        template="plotly_white",
        xaxis_title="Timestamp",
        yaxis_title="Strike",
        margin=dict(l=55, r=35, t=65, b=45),
    )

    rel_fig = go.Figure(
        go.Heatmap(
            x=timestamps,
            y=strikes,
            z=clipped_rel,
            colorscale="RdBu",
            zmid=0,
            zmin=-0.35,
            zmax=0.35,
            colorbar=dict(title="Rel IV (clipped)"),
            hovertemplate="timestamp=%{x}<br>strike=%{y}<br>relative IV=%{z:.2%}<extra></extra>",
        )
    )
    rel_fig.update_layout(
        title="Relative IV: Rich/Cheap Strikes vs Same-Timestamp Median",
        height=460,
        template="plotly_white",
        xaxis_title="Timestamp",
        yaxis_title="Strike",
        margin=dict(l=55, r=35, t=65, b=45),
    )

    rich_cheap_fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        subplot_titles=["Richest and Cheapest Strike Over Time", "Relative IV Magnitude"],
        vertical_spacing=0.12,
    )
    rich_cheap_fig.add_trace(
        go.Scatter(x=rich_ts, y=rich_strikes, mode="markers", name="Richest strike", marker=dict(color="#174ea6", size=5)),
        row=1,
        col=1,
    )
    rich_cheap_fig.add_trace(
        go.Scatter(x=rich_ts, y=cheap_strikes, mode="markers", name="Cheapest strike", marker=dict(color="#b3261e", size=5)),
        row=1,
        col=1,
    )
    rich_cheap_fig.add_trace(
        go.Scatter(x=rich_ts, y=rich_values, mode="lines", name="Rich rel IV", line=dict(color="#174ea6")),
        row=2,
        col=1,
    )
    rich_cheap_fig.add_trace(
        go.Scatter(x=rich_ts, y=cheap_values, mode="lines", name="Cheap rel IV", line=dict(color="#b3261e")),
        row=2,
        col=1,
    )
    rich_cheap_fig.add_hline(y=0, line=dict(color="#aab", dash="dot", width=1), row=2, col=1)
    rich_cheap_fig.update_layout(
        title="Action View: Which Strike Is Rich/Cheap?",
        height=520,
        template="plotly_white",
        margin=dict(l=55, r=35, t=65, b=45),
        legend=dict(orientation="h"),
    )
    rich_cheap_fig.update_yaxes(title_text="Strike", row=1, col=1)
    rich_cheap_fig.update_yaxes(title_text="Rel IV", row=2, col=1)

    smile_fig = go.Figure()
    sample_cols = sorted(set([0, len(timestamps) // 4, len(timestamps) // 2, 3 * len(timestamps) // 4, len(timestamps) - 1]))
    for c in sample_cols:
        ts = timestamps[c]
        day = ts // 1_000_000
        local_t = ts - day * 1_000_000
        smile_fig.add_trace(
            go.Scatter(
                x=strikes,
                y=[matrix[r][c] for r in range(len(strikes))],
                mode="lines+markers",
                name=f"Day {day} t={local_t}",
            )
        )
    smile_fig.update_layout(
        title="IV Smile Snapshots",
        height=430,
        template="plotly_white",
        xaxis_title="Strike",
        yaxis_title="IV",
        margin=dict(l=55, r=35, t=65, b=45),
    )

    return (
        "<p class='hint'>Use raw IV to see regime changes. Use relative IV to find rich/cheap strikes at the same timestamp. White cells mean no robust IV was available, usually because the option was illiquid or below intrinsic value.</p>"
        + raw.to_html(full_html=False, include_plotlyjs=False)
        + rel_fig.to_html(full_html=False, include_plotlyjs=False)
        + rich_cheap_fig.to_html(full_html=False, include_plotlyjs=False)
        + smile_fig.to_html(full_html=False, include_plotlyjs=False)
    )


def _style() -> str:
    return """
    body { margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; color: #1f1f1f; background: #f7f9fc; }
    h1 { margin: 0 0 12px; font-size: 22px; }
    h2 { margin: 22px 0 10px; font-size: 16px; }
    .top, .page { max-width: 1540px; margin: 12px auto; background: #fff; border: 1px solid #dfe5ef; border-radius: 8px; padding: 16px; }
    .tabs { position: sticky; top: 0; z-index: 10; display: flex; gap: 8px; padding: 10px 16px; background: #f7f9fc; border-bottom: 1px solid #dfe5ef; }
    .tabs button { border: 1px solid #cbd5e1; background: #fff; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-weight: 600; }
    .tabs button.active { background: #174ea6; color: #fff; border-color: #174ea6; }
    .view { display: none; }
    .view.active { display: block; }
    .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap: 10px; }
    .metric { border: 1px solid #e0e6f0; border-radius: 6px; padding: 10px 12px; background: #fbfdff; }
    .metric span { display: block; font-size: 11px; color: #667085; margin-bottom: 5px; }
    .metric strong { display: block; font-family: Consolas, Menlo, monospace; font-size: 20px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { padding: 7px 8px; border-bottom: 1px solid #edf1f7; text-align: left; }
    th { color: #667085; font-weight: 700; background: #fafcff; }
    .num { text-align: right; font-family: Consolas, Menlo, monospace; }
    .bad { color: #b3261e; font-weight: 700; }
    .control-row { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
    select { padding: 7px 10px; border-radius: 6px; border: 1px solid #cbd5e1; min-width: 260px; }
    .hint { color: #5f6368; font-size: 13px; margin: 0 0 12px; }
    .product-panel { width: 100%; }
    """


def _script() -> str:
    return """
    <script>
    function resizeVisiblePlots() {
      if (!window.Plotly) return;
      document.querySelectorAll('.view.active .js-plotly-plot').forEach(plot => {
        Plotly.Plots.resize(plot);
      });
    }
    function showView(id) {
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      document.querySelectorAll('.tabs button').forEach(b => b.classList.remove('active'));
      document.getElementById(id).classList.add('active');
      document.querySelector('[data-view="' + id + '"]').classList.add('active');
      setTimeout(resizeVisiblePlots, 80);
    }
    function showProduct(id) {
      document.querySelectorAll('.product-panel').forEach(p => p.style.display = 'none');
      const panel = document.getElementById('prod-' + id);
      if (panel) panel.style.display = 'block';
      setTimeout(resizeVisiblePlots, 80);
    }
    document.addEventListener('DOMContentLoaded', () => {
      document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
      const select = document.getElementById('product-select');
      if (select) select.addEventListener('change', () => showProduct(select.value));
      setTimeout(resizeVisiblePlots, 250);
    });
    </script>
    """


def _make_html(
    title: str,
    data: dict[str, Any],
    overview_fig: "go.Figure",
    product_html: str,
    options_html: str,
) -> str:
    overview_html = overview_fig.to_html(full_html=False, include_plotlyjs="cdn")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<title>{title}</title><style>{_style()}</style></head><body>"
        "<nav class='tabs'>"
        "<button class='active' data-view='overview'>Overview</button>"
        "<button data-view='product'>Product Drilldown</button>"
        "<button data-view='options'>Options IV</button>"
        "</nav>"
        + _metric_cards(data, title)
        + "<main class='page'>"
        + "<section id='overview' class='view active'>"
        + overview_html
        + _product_table(data)
        + _bad_fill_table(data)
        + "</section>"
        + "<section id='product' class='view'>"
        + "<h2>Product Drilldown</h2><p class='hint'>Choose one product. Axes rescale to that product, so options are no longer crushed by HYDROGEL_PACK.</p>"
        + product_html
        + "</section>"
        + "<section id='options' class='view'>"
        + "<h2>Options IV Analysis</h2>"
        + options_html
        + "</section>"
        + "</main>"
        + _script()
        + "</body></html>"
    )


def _activity_from_result(result: BacktestResult) -> tuple[dict[str, list[dict]], list[int]]:
    activity_by_product: dict[str, list[dict]] = defaultdict(list)
    day_first_ts: dict[int, int] = {}
    for row in result.activity_logs:
        cols = row.columns
        day = int(cols[0])
        ts = int(cols[1])
        product = cols[2]
        day_first_ts[day] = min(ts, day_first_ts.get(day, ts))
        bp1 = cols[3] if cols[3] != "" else None
        bv1 = int(cols[4]) if cols[4] != "" else None
        ap1 = cols[9] if cols[9] != "" else None
        av1 = int(cols[10]) if cols[10] != "" else None
        activity_by_product[product].append(
            {
                "day": day,
                "timestamp": ts,
                "product": product,
                "bid_price_1": bp1,
                "bid_volume_1": bv1,
                "ask_price_1": ap1,
                "ask_volume_1": av1,
                "bid_ask_spread": ap1 - bp1 if bp1 is not None and ap1 is not None else None,
                "mid_price": float(cols[15]) if cols[15] != "" else 0.0,
                "profit_and_loss": float(cols[16]),
            }
        )
    return activity_by_product, [day_first_ts[d] for d in sorted(day_first_ts)]


def _trades_from_result(result: BacktestResult) -> dict[str, list[dict]]:
    trades_by_product: dict[str, list[dict]] = defaultdict(list)
    for row in result.trades:
        trade = row.trade
        if trade.buyer == "SUBMISSION" or trade.seller == "SUBMISSION":
            trades_by_product[trade.symbol].append(
                {
                    "timestamp": trade.timestamp,
                    "buyer": trade.buyer,
                    "seller": trade.seller,
                    "symbol": trade.symbol,
                    "price": trade.price,
                    "quantity": trade.quantity,
                }
            )
    return trades_by_product


def create_dashboard_from_result(result: BacktestResult, output_html: Optional[Union[str, Path]] = None) -> str:
    _check_plotly()
    activity_by_product, day_starts = _activity_from_result(result)
    trades_by_product = _trades_from_result(result)
    title = f"Backtest Dashboard - Round {result.round_num}"
    data = _compute_report_data(activity_by_product, trades_by_product)
    html = _make_html(
        title,
        data,
        _overview_figure(data),
        _product_sections(activity_by_product, trades_by_product, day_starts),
        _options_section(activity_by_product),
    )
    if output_html is not None:
        Path(output_html).write_text(html, encoding="utf-8")
        print(f"Dashboard saved to {output_html}")
    return html


def create_dashboard(log_path: Union[str, Path], output_html: Optional[Union[str, Path]] = None) -> str:
    _check_plotly()
    payload = parse_visualizer_log(Path(log_path))
    activity_by_product: dict[str, list[dict]] = defaultdict(list)
    day_first_ts: dict[int, int] = {}
    for row in payload["activity_logs"]:
        activity_by_product[row["product"]].append(row)
        day = int(row.get("day", 0))
        ts = int(row["timestamp"])
        day_first_ts[day] = min(ts, day_first_ts.get(day, ts))

    trades_by_product: dict[str, list[dict]] = defaultdict(list)
    for trade in payload["trades"]:
        if trade["buyer"] == "SUBMISSION" or trade["seller"] == "SUBMISSION":
            trades_by_product[trade["symbol"]].append(trade)

    title = f"Backtest Dashboard - {Path(log_path).name}"
    day_starts = [day_first_ts[d] for d in sorted(day_first_ts)]
    data = _compute_report_data(activity_by_product, trades_by_product)
    html = _make_html(
        title,
        data,
        _overview_figure(data),
        _product_sections(activity_by_product, trades_by_product, day_starts),
        _options_section(activity_by_product),
    )
    if output_html is not None:
        Path(output_html).write_text(html, encoding="utf-8")
        print(f"Dashboard saved to {output_html}")
    return html

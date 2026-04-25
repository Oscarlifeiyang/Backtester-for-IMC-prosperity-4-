"""Market data analysis tools for exploring raw price/trade CSVs.

Can be used as a library or run as a script:
    python -m prosperity4bt.market_analysis \\
        --round 1 --data ./bt_data/round1 --out analysis.html

Functions:
    load_prices(data_dir, round_num, days=None)  → list[dict]
    load_trades(data_dir, round_num, days=None)  → list[dict]
    compute_book_stats(price_rows)               → list[dict]  (adds spread, imbalance, etc.)
    compute_trade_flow(trade_rows, window=20)    → list[dict]  (adds rolling buy/sell balance)
    detect_vol_regimes(price_rows, window=200)   → list[dict]  (adds 'regime' + 'rolling_vol')
    plot_data_overview(price_rows, trade_rows, output_html)  → HTML string
"""

import csv
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False


# ── Data loading ───────────────────────────────────────────────────────────────

def load_prices(
    data_dir: Union[str, Path],
    round_num: int,
    days: Optional[list[int]] = None,
) -> list[dict]:
    """Load price CSV files for a round and return a flat list of row dicts.

    Handles zero mid-prices by carrying the last valid value forward.
    If `days` is None, all matching files in `data_dir` are loaded.
    """
    data_dir = Path(data_dir)

    if days is None:
        days = []
        for f in sorted(data_dir.glob(f"prices_round_{round_num}_day_*.csv")):
            try:
                days.append(int(f.stem.split("_day_")[-1]))
            except ValueError:
                pass

    rows: list[dict] = []
    last_valid_mid: dict[str, float] = {}

    for day in sorted(days):
        path = data_dir / f"prices_round_{round_num}_day_{day}.csv"
        if not path.exists():
            continue

        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            for row in reader:
                product = row["product"]
                mid = float(row["mid_price"]) if row["mid_price"] else 0.0
                if mid > 0:
                    last_valid_mid[product] = mid
                elif product in last_valid_mid:
                    mid = last_valid_mid[product]

                def _int(col: str) -> Optional[int]:
                    return int(row[col]) if row.get(col, "") else None

                rows.append({
                    "day": int(row["day"]),
                    "timestamp": int(row["timestamp"]),
                    "product": product,
                    "bid_price_1": _int("bid_price_1"),
                    "bid_volume_1": _int("bid_volume_1"),
                    "bid_price_2": _int("bid_price_2"),
                    "bid_volume_2": _int("bid_volume_2"),
                    "bid_price_3": _int("bid_price_3"),
                    "bid_volume_3": _int("bid_volume_3"),
                    "ask_price_1": _int("ask_price_1"),
                    "ask_volume_1": _int("ask_volume_1"),
                    "ask_price_2": _int("ask_price_2"),
                    "ask_volume_2": _int("ask_volume_2"),
                    "ask_price_3": _int("ask_price_3"),
                    "ask_volume_3": _int("ask_volume_3"),
                    "mid_price": mid,
                })

    return rows


def load_trades(
    data_dir: Union[str, Path],
    round_num: int,
    days: Optional[list[int]] = None,
) -> list[dict]:
    """Load trade CSV files for a round and return a flat list of row dicts."""
    data_dir = Path(data_dir)

    if days is None:
        days = []
        for f in sorted(data_dir.glob(f"trades_round_{round_num}_day_*.csv")):
            try:
                days.append(int(f.stem.split("_day_")[-1]))
            except ValueError:
                pass

    rows: list[dict] = []
    for day in sorted(days):
        path = data_dir / f"trades_round_{round_num}_day_{day}.csv"
        if not path.exists():
            continue

        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            for row in reader:
                rows.append({
                    "timestamp": int(row["timestamp"]),
                    "buyer": row["buyer"],
                    "seller": row["seller"],
                    "symbol": row["symbol"],
                    "price": int(float(row["price"])),
                    "quantity": int(row["quantity"]),
                })

    return rows


# ── Feature computation ────────────────────────────────────────────────────────

def compute_book_stats(price_rows: list[dict]) -> list[dict]:
    """Add derived order-book features to each row (returns new list of dicts).

    Added fields:
        spread          – best_ask - best_bid (None if one side empty)
        imbalance       – L1 (bid_vol - ask_vol) / (bid_vol + ask_vol)
        weighted_mid    – micro-price: (bid*ask_vol + ask*bid_vol) / total_vol
        total_bid_depth – sum of bid volumes across all 3 levels
        total_ask_depth – sum of ask volumes across all 3 levels
    """
    result = []
    for row in price_rows:
        bp1, bv1 = row.get("bid_price_1"), row.get("bid_volume_1") or 0
        ap1, av1 = row.get("ask_price_1"), row.get("ask_volume_1") or 0

        spr = (ap1 - bp1) if (bp1 is not None and ap1 is not None) else None
        total_l1 = bv1 + av1
        imbalance = (bv1 - av1) / total_l1 if total_l1 > 0 else 0.0

        if bp1 is not None and ap1 is not None and total_l1 > 0:
            wmid = (bp1 * av1 + ap1 * bv1) / total_l1
        else:
            wmid = row["mid_price"]

        total_bid = sum(row.get(f"bid_volume_{i}") or 0 for i in range(1, 4))
        total_ask = sum(row.get(f"ask_volume_{i}") or 0 for i in range(1, 4))

        result.append({
            **row,
            "spread": spr,
            "imbalance": imbalance,
            "weighted_mid": wmid,
            "total_bid_depth": total_bid,
            "total_ask_depth": total_ask,
        })
    return result


def compute_trade_flow(trade_rows: list[dict], window: int = 20) -> list[dict]:
    """Add rolling buy/sell flow balance to each trade row.

    Direction heuristic: a named buyer with no named seller is buyer-initiated,
    and vice versa.  When both or neither are named the volume is split evenly.

    Added fields:
        buy_flow       – rolling sum of buy volume over last `window` trades
        sell_flow      – rolling sum of sell volume over last `window` trades
        flow_imbalance – (buy_flow - sell_flow) / (buy_flow + sell_flow)
    """
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in trade_rows:
        by_symbol[row["symbol"]].append(row)

    result: list[dict] = []
    for symbol, rows in by_symbol.items():
        rows = sorted(rows, key=lambda r: r["timestamp"])
        buy_vols: list[float] = []
        sell_vols: list[float] = []

        for row in rows:
            qty = row["quantity"]
            has_buyer = bool(row["buyer"])
            has_seller = bool(row["seller"])
            if has_buyer and not has_seller:
                buy_vols.append(float(qty))
                sell_vols.append(0.0)
            elif has_seller and not has_buyer:
                buy_vols.append(0.0)
                sell_vols.append(float(qty))
            else:
                buy_vols.append(qty / 2.0)
                sell_vols.append(qty / 2.0)

        for i, row in enumerate(rows):
            w = max(0, i - window + 1)
            rb = sum(buy_vols[w:i + 1])
            rs = sum(sell_vols[w:i + 1])
            total = rb + rs
            result.append({
                **row,
                "buy_flow": rb,
                "sell_flow": rs,
                "flow_imbalance": (rb - rs) / total if total > 0 else 0.0,
            })

    return result


def detect_vol_regimes(
    price_rows: list[dict],
    window: int = 200,
) -> list[dict]:
    """Classify each row as 'high_vol' or 'low_vol' using a rolling std of returns.

    Processes each product independently.  The threshold is the median rolling
    volatility across all timestamps for that product.

    Added fields:
        rolling_vol – rolling std of mid-price returns
        regime      – 'high_vol' | 'low_vol'
    """
    by_product: dict[str, list[dict]] = defaultdict(list)
    for row in price_rows:
        by_product[row["product"]].append(row)

    result: list[dict] = []
    for product, rows in by_product.items():
        rows = sorted(rows, key=lambda r: r["timestamp"])
        mids = [r["mid_price"] for r in rows]

        returns = [0.0] + [
            (mids[i] - mids[i - 1]) / mids[i - 1] if mids[i - 1] > 0 else 0.0
            for i in range(1, len(mids))
        ]

        rolling_stds: list[float] = []
        for i in range(len(returns)):
            w = returns[max(0, i - window + 1): i + 1]
            n = len(w)
            mean = sum(w) / n
            std = (sum((x - mean) ** 2 for x in w) / n) ** 0.5
            rolling_stds.append(std)

        sorted_stds = sorted(rolling_stds)
        median = sorted_stds[len(sorted_stds) // 2]

        for row, std in zip(rows, rolling_stds):
            result.append({
                **row,
                "rolling_vol": std,
                "regime": "high_vol" if std >= median else "low_vol",
            })

    return result


# ── Visualization ──────────────────────────────────────────────────────────────

def plot_data_overview(
    price_rows: list[dict],
    trade_rows: Optional[list[dict]] = None,
    output_html: Optional[Union[str, Path]] = None,
) -> str:
    """Build an interactive Plotly HTML dashboard from market data.

    Panels (shared x-axis):
        1. Mid price & weighted mid (micro-price)
        2. Bid-ask spread
        3. Order book imbalance (L1)
        4. Total visible depth (bid vs ask)
        5. Trade flow imbalance (only if trade_rows provided)

    A product-selector dropdown lets you focus on one product at a time.

    Returns the HTML string; also writes to `output_html` if provided.
    """
    if not _HAS_PLOTLY:
        raise ImportError(
            "plotly is required for visualization. Install it with:\n"
            "  pip install plotly"
        )

    stats_rows = compute_book_stats(price_rows)

    by_product: dict[str, list[dict]] = defaultdict(list)
    for row in stats_rows:
        by_product[row["product"]].append(row)
    for rows in by_product.values():
        rows.sort(key=lambda r: r["timestamp"])

    products = sorted(by_product.keys())

    flow_by_product: dict[str, list[dict]] = {}
    if trade_rows:
        for row in compute_trade_flow(trade_rows):
            sym = row["symbol"]
            flow_by_product.setdefault(sym, []).append(row)
        for rows in flow_by_product.values():
            rows.sort(key=lambda r: r["timestamp"])

    n_panels = 5 if trade_rows else 4
    subplot_titles = [
        "Mid Price & Weighted Mid (Micro-Price)",
        "Bid-Ask Spread",
        "Order Book Imbalance (L1)",
        "Total Order Book Depth",
    ]
    if trade_rows:
        subplot_titles.append("Rolling Trade Flow Imbalance")

    row_heights = [0.32, 0.17, 0.17, 0.17, 0.17] if trade_rows else [0.40, 0.20, 0.20, 0.20]

    fig = make_subplots(
        rows=n_panels, cols=1,
        shared_xaxes=True,
        subplot_titles=subplot_titles,
        vertical_spacing=0.06,
        row_heights=row_heights,
    )

    traces_per_product = 6 + (1 if trade_rows else 0)
    product_trace_ranges: dict[str, tuple[int, int]] = {}
    trace_idx = 0

    for product in products:
        rows = by_product.get(product, [])
        ts   = [r["timestamp"]     for r in rows]
        mids = [r["mid_price"]     for r in rows]
        wmid = [r["weighted_mid"]  for r in rows]
        sprd = [r.get("spread")    for r in rows]
        imb  = [r.get("imbalance", 0.0) for r in rows]
        bdep = [r.get("total_bid_depth", 0) for r in rows]
        adep = [r.get("total_ask_depth", 0) for r in rows]

        start = trace_idx

        fig.add_trace(go.Scatter(x=ts, y=mids, name=f"{product} Mid",
                                 line=dict(width=1.8)), row=1, col=1)
        trace_idx += 1
        fig.add_trace(go.Scatter(x=ts, y=wmid, name=f"{product} WMid",
                                 line=dict(width=1.2, dash="dot")), row=1, col=1)
        trace_idx += 1
        fig.add_trace(go.Scatter(x=ts, y=sprd, name=f"{product} Spread",
                                 line=dict(width=1)), row=2, col=1)
        trace_idx += 1
        fig.add_trace(go.Scatter(x=ts, y=imb, name=f"{product} Imb",
                                 line=dict(width=1)), row=3, col=1)
        trace_idx += 1
        fig.add_trace(go.Scatter(x=ts, y=bdep, name=f"{product} BidDep",
                                 fill="tozeroy", line=dict(width=1, color="#2ca02c"),
                                 opacity=0.5), row=4, col=1)
        trace_idx += 1
        fig.add_trace(go.Scatter(x=ts, y=adep, name=f"{product} AskDep",
                                 fill="tozeroy", line=dict(width=1, color="#d62728"),
                                 opacity=0.5), row=4, col=1)
        trace_idx += 1

        if trade_rows:
            flow = flow_by_product.get(product, [])
            fts  = [r["timestamp"]      for r in flow]
            fimb = [r["flow_imbalance"] for r in flow]
            fig.add_trace(go.Scatter(x=fts, y=fimb, name=f"{product} Flow",
                                     line=dict(width=1)), row=5, col=1)
            trace_idx += 1

        product_trace_ranges[product] = (start, trace_idx)

    total = trace_idx

    buttons = [dict(label="All Products", method="update",
                    args=[{"visible": [True] * total}])]
    for product in products:
        s, e = product_trace_ranges[product]
        vis = [False] * total
        for i in range(s, e):
            vis[i] = True
        buttons.append(dict(label=product, method="update", args=[{"visible": vis}]))

    fig.update_layout(
        updatemenus=[dict(
            active=0, buttons=buttons, type="dropdown",
            x=0.0, xanchor="left", y=1.08, yanchor="top",
            bgcolor="#f4f4f4", bordercolor="#ccc",
        )],
        title="Market Data Analysis",
        height=960,
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", y=-0.04),
    )

    fig.add_hline(y=0, line=dict(dash="dot", color="#aaa", width=0.8), row=3, col=1)
    if trade_rows:
        fig.add_hline(y=0, line=dict(dash="dot", color="#aaa", width=0.8), row=5, col=1)

    html_str = fig.to_html(full_html=True, include_plotlyjs="cdn")

    if output_html is not None:
        out = Path(output_html)
        out.write_text(html_str, encoding="utf-8")
        print(f"Market analysis dashboard saved to {out}")

    return html_str


# ── CLI entry point ────────────────────────────────────────────────────────────

def _main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m prosperity4bt.market_analysis",
        description="Generate a market data analysis dashboard from raw CSV files.",
    )
    parser.add_argument("--round", type=int, required=True, metavar="N",
                        help="Round number (e.g. 1)")
    parser.add_argument("--data", required=True, metavar="DIR",
                        help="Directory containing prices/trades CSVs")
    parser.add_argument("--days", type=int, nargs="+", metavar="D",
                        help="Specific days to load (default: all found)")
    parser.add_argument("--product", metavar="PRODUCT",
                        help="Show only this product in the dashboard")
    parser.add_argument("--out", default="market_analysis.html", metavar="FILE",
                        help="Output HTML file (default: market_analysis.html)")
    args = parser.parse_args()

    print(f"Loading round {args.round} data from {args.data} ...")
    prices = load_prices(args.data, args.round, args.days)
    trades = load_trades(args.data, args.round, args.days)

    if args.product:
        prices = [r for r in prices if r["product"] == args.product]
        trades = [r for r in trades if r["symbol"] == args.product]

    print(f"Loaded {len(prices)} price rows, {len(trades)} trade rows.")
    plot_data_overview(prices, trades or None, args.out)


if __name__ == "__main__":
    _main()

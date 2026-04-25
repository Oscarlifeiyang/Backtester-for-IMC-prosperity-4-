"""Parameter sensitivity and strategy comparison tools.

Usage example — 1D sweep:
    from prosperity4bt.sensitivity import sweep_1d, plot_1d_sensitivity
    from prosperity4bt.metrics import compute_metrics

    def backtest_fn(params):
        # Patch your Trader class, run backtest, return metrics dict
        MyTrader.TAKE_EDGE = params["TAKE_EDGE"]
        result = run_backtest(MyTrader(), ...)
        return compute_metrics(result)

    rows = sweep_1d("TAKE_EDGE", [0.5, 1.0, 1.5, 2.0, 2.5], base_params={}, backtest_fn=backtest_fn)
    plot_1d_sensitivity(rows, "TAKE_EDGE", output_html="take_edge_sweep.html")

Usage example — 2D heatmap:
    rows = sweep_2d("TAKE_EDGE", "QUOTE_EDGE",
                    [1, 2, 3], [1, 2, 3],
                    base_params={}, backtest_fn=backtest_fn)
    plot_2d_heatmap(rows, "TAKE_EDGE", "QUOTE_EDGE", output_html="heatmap.html")

Usage example — strategy comparison:
    strategies = {"aggressive": AggressiveTrader, "passive": PassiveTrader}
    def run(cls):
        result = run_backtest(cls(), ...)
        return compute_metrics(result)
    compare_strategies(strategies, run, output_html="comparison.html")
"""

from pathlib import Path
from typing import Any, Callable, Optional, Union

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False


def _check_plotly() -> None:
    if not _HAS_PLOTLY:
        raise ImportError(
            "plotly is required for visualization. Install it with:\n"
            "  pip install plotly"
        )


# ── Sweep functions ────────────────────────────────────────────────────────────

def sweep_1d(
    param_name: str,
    values: list,
    base_params: dict,
    backtest_fn: Callable[[dict], dict],
    verbose: bool = True,
) -> list[dict]:
    """Sweep one parameter over a list of candidate values.

    Args:
        param_name:  The key to vary in the params dict.
        values:      List of values to try.
        base_params: Other parameters held constant; merged with the varied one.
        backtest_fn: Called with the merged params dict; must return a metrics
                     dict containing at least 'total_pnl'.
        verbose:     Print progress to stdout.

    Returns:
        List of result dicts: {param_name: value, **metrics}
    """
    results = []
    for val in values:
        params = {**base_params, param_name: val}
        if verbose:
            print(f"  {param_name}={val!r} ...", end=" ", flush=True)
        metrics = backtest_fn(params)
        row = {param_name: val, **metrics}
        results.append(row)
        if verbose:
            pnl = metrics.get("total_pnl", "?")
            print(f"PnL={pnl:,.0f}" if isinstance(pnl, (int, float)) else f"PnL={pnl}")
    return results


def sweep_2d(
    param1: str,
    param2: str,
    values1: list,
    values2: list,
    base_params: dict,
    backtest_fn: Callable[[dict], dict],
    verbose: bool = True,
) -> list[dict]:
    """Sweep two parameters over a 2D grid.

    Returns:
        List of result dicts: {param1: v1, param2: v2, **metrics}
    """
    results = []
    total = len(values1) * len(values2)
    count = 0
    for v1 in values1:
        for v2 in values2:
            params = {**base_params, param1: v1, param2: v2}
            count += 1
            if verbose:
                print(f"  [{count}/{total}] {param1}={v1!r}, {param2}={v2!r} ...",
                      end=" ", flush=True)
            metrics = backtest_fn(params)
            row = {param1: v1, param2: v2, **metrics}
            results.append(row)
            if verbose:
                pnl = metrics.get("total_pnl", "?")
                print(f"PnL={pnl:,.0f}" if isinstance(pnl, (int, float)) else f"PnL={pnl}")
    return results


def compare_strategies(
    strategies: dict[str, Any],
    backtest_fn: Callable[[Any], dict],
    output_html: Optional[Union[str, Path]] = None,
    verbose: bool = True,
) -> list[dict]:
    """Run multiple strategy configurations and compare their metrics.

    Args:
        strategies:  Dict mapping a display name to an arbitrary config object
                     (a Trader class, a params dict, etc.).
        backtest_fn: Called with each config; must return a metrics dict.
        output_html: If given, write a bar-chart comparison to this path.

    Returns:
        List of result dicts sorted by total_pnl descending:
        [{name: str, **metrics}, ...]
    """
    results = []
    for name, config in strategies.items():
        if verbose:
            print(f"  Running '{name}' ...", end=" ", flush=True)
        metrics = backtest_fn(config)
        row = {"name": name, **metrics}
        results.append(row)
        if verbose:
            pnl = metrics.get("total_pnl", "?")
            print(f"PnL={pnl:,.0f}" if isinstance(pnl, (int, float)) else f"PnL={pnl}")

    results.sort(key=lambda r: r.get("total_pnl", 0), reverse=True)

    if output_html is not None:
        _plot_strategy_bar(results, output_html)

    return results


# ── Plot functions ─────────────────────────────────────────────────────────────

def plot_1d_sensitivity(
    sweep_results: list[dict],
    param_name: str,
    metric_keys: Optional[list[str]] = None,
    output_html: Optional[Union[str, Path]] = None,
) -> str:
    """Generate a multi-panel line chart for a 1D parameter sweep.

    Args:
        sweep_results: Output of sweep_1d().
        param_name:    The parameter that was varied (x-axis).
        metric_keys:   Metrics to plot; defaults to the most useful ones found
                       in the results (total_pnl, sharpe, max_drawdown, win_rate).
        output_html:   If given, write the HTML to this path.

    Returns:
        The self-contained HTML string.
    """
    _check_plotly()

    if metric_keys is None:
        candidates = ["total_pnl", "sharpe", "max_drawdown", "win_rate"]
        available = set(sweep_results[0].keys()) - {param_name}
        metric_keys = [k for k in candidates if k in available]
        if not metric_keys:
            metric_keys = [k for k in sweep_results[0] if k != param_name]

    n = len(metric_keys)
    fig = make_subplots(
        rows=n, cols=1,
        shared_xaxes=True,
        subplot_titles=[k.replace("_", " ").title() for k in metric_keys],
        vertical_spacing=0.10,
    )

    xs = [r[param_name] for r in sweep_results]
    color = "#1f77b4"

    for i, key in enumerate(metric_keys, start=1):
        ys = [r.get(key) for r in sweep_results]
        fig.add_trace(
            go.Scatter(x=xs, y=ys, mode="lines+markers",
                       line=dict(color=color), marker=dict(size=6),
                       name=key.replace("_", " ").title()),
            row=i, col=1,
        )
        # Star the best value
        valid = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if valid:
            if key in ("total_pnl", "sharpe", "win_rate"):
                bx, by = max(valid, key=lambda t: t[1])
            else:
                bx, by = min(valid, key=lambda t: t[1])
            fig.add_trace(
                go.Scatter(x=[bx], y=[by], mode="markers",
                           marker=dict(symbol="star", size=14, color="gold",
                                       line=dict(color="darkorange", width=1)),
                           name=f"Best {key.replace('_', ' ')}", showlegend=True),
                row=i, col=1,
            )

    fig.update_layout(
        title=f"Parameter Sensitivity: {param_name}",
        height=max(300 * n + 100, 500),
        template="plotly_white",
        hovermode="x unified",
    )
    fig.update_xaxes(title_text=param_name, row=n, col=1)

    html_str = fig.to_html(full_html=True, include_plotlyjs="cdn")
    if output_html is not None:
        Path(output_html).write_text(html_str, encoding="utf-8")
        print(f"Sensitivity plot saved to {output_html}")
    return html_str


def plot_2d_heatmap(
    sweep_results: list[dict],
    param1: str,
    param2: str,
    metric_key: str = "total_pnl",
    output_html: Optional[Union[str, Path]] = None,
) -> str:
    """Generate an interactive 2D heatmap from a sweep_2d() result.

    Args:
        sweep_results: Output of sweep_2d().
        param1:        First parameter (y-axis rows).
        param2:        Second parameter (x-axis columns).
        metric_key:    Which metric to display.
        output_html:   If given, write the HTML to this path.

    Returns:
        The self-contained HTML string.
    """
    _check_plotly()

    v1_vals = sorted(set(r[param1] for r in sweep_results))
    v2_vals = sorted(set(r[param2] for r in sweep_results))
    lookup = {(r[param1], r[param2]): r.get(metric_key) for r in sweep_results}

    z = [[lookup.get((v1, v2)) for v2 in v2_vals] for v1 in v1_vals]
    text = [[f"{v:,.1f}" if isinstance(v, (int, float)) else "N/A" for v in row] for row in z]

    fig = go.Figure(go.Heatmap(
        x=v2_vals, y=v1_vals, z=z,
        colorscale="RdYlGn",
        colorbar=dict(title=metric_key.replace("_", " ").title()),
        text=text,
        texttemplate="%{text}",
        textfont={"size": 9},
        hoverongaps=False,
    ))

    fig.update_layout(
        title=f"2D Sensitivity Heatmap — {metric_key.replace('_', ' ').title()}",
        xaxis_title=param2,
        yaxis_title=param1,
        template="plotly_white",
        height=600,
    )

    html_str = fig.to_html(full_html=True, include_plotlyjs="cdn")
    if output_html is not None:
        Path(output_html).write_text(html_str, encoding="utf-8")
        print(f"Heatmap saved to {output_html}")
    return html_str


def _plot_strategy_bar(results: list[dict], output_html: Union[str, Path]) -> None:
    """Bar chart comparing strategies by total PnL."""
    names = [r["name"] for r in results]
    pnls  = [r.get("total_pnl", 0) for r in results]
    colors = ["#2ca02c" if p >= 0 else "#d62728" for p in pnls]

    fig = go.Figure(go.Bar(
        x=names, y=pnls,
        marker_color=colors,
        text=[f"{p:,.0f}" for p in pnls],
        textposition="outside",
    ))
    fig.update_layout(
        title="Strategy Comparison — Total PnL",
        xaxis_title="Strategy",
        yaxis_title="Total PnL",
        template="plotly_white",
        height=500,
    )
    html_str = fig.to_html(full_html=True, include_plotlyjs="cdn")
    Path(output_html).write_text(html_str, encoding="utf-8")
    print(f"Strategy comparison saved to {output_html}")

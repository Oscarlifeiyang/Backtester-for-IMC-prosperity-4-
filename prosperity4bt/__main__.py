import sys
from collections import defaultdict
from datetime import datetime
from functools import reduce
from importlib import import_module, metadata, reload
from pathlib import Path
from typing import Annotated, Any, Optional

from typer import Argument, Option, Typer

from prosperity4bt.data import has_day_data
from prosperity4bt.file_reader import FileReader, FileSystemReader, PackageResourcesReader
from prosperity4bt.models import BacktestResult, TradeMatchingMode
from prosperity4bt.open import open_visualizer
from prosperity4bt.runner import run_backtest


def parse_algorithm(algorithm: Path) -> Any:
    """Import the user-provided trader module from a filesystem path."""

    sys.path.append(str(algorithm.parent))
    return import_module(algorithm.stem)


def parse_data(data_root: Optional[Path]) -> FileReader:
    """Choose whether backtest data should be read from disk or packaged resources."""

    if data_root is not None:
        return FileSystemReader(data_root)
    else:
        return PackageResourcesReader()


def parse_days(file_reader: FileReader, days: list[str]) -> list[tuple[int, int]]:
    """Expand CLI day arguments into concrete ``(round, day)`` pairs that have data files."""

    parsed_days = []

    for arg in days:
        if "-" in arg:
            round_num, day_num = map(int, arg.split("-", 1))

            if not has_day_data(file_reader, round_num, day_num):
                print(f"Warning: no data found for round {round_num} day {day_num}")
                continue

            parsed_days.append((round_num, day_num))
        else:
            round_num = int(arg)

            parsed_days_in_round = []
            # Day files in the bundled datasets can include negative setup days,
            # so scan a broad range and keep only the files that actually exist.
            for day_num in range(-5, 100):
                if has_day_data(file_reader, round_num, day_num):
                    parsed_days_in_round.append((round_num, day_num))

            if len(parsed_days_in_round) == 0:
                print(f"Warning: no data found for round {round_num}")
                continue

            parsed_days.extend(parsed_days_in_round)

    if len(parsed_days) == 0:
        print("Error: did not find data for any requested round/day")
        sys.exit(1)

    return parsed_days


def parse_out(out: Optional[Path], no_out: bool) -> Optional[Path]:
    """Resolve the output-log destination, or disable log writing when requested."""

    if out is not None:
        return out

    if no_out:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path.cwd() / "backtests" / f"{timestamp}.log"


def print_day_summary(result: BacktestResult) -> None:
    """Print end-of-day profit per product using the final activity-log snapshot."""

    last_timestamp = result.activity_logs[-1].timestamp

    product_lines = []
    total_profit = 0

    for row in reversed(result.activity_logs):
        if row.timestamp != last_timestamp:
            break

        product = row.columns[2]
        profit = row.columns[-1]

        product_lines.append(f"{product}: {profit:,.0f}")
        total_profit += profit

    print(*reversed(product_lines), sep="\n")
    print(f"Total profit: {total_profit:,.0f}")


def merge_results(
    a: BacktestResult, b: BacktestResult, merge_profit_loss: bool, merge_timestamps: bool
) -> BacktestResult:
    """Combine two day results into one log stream for saving or visualization."""

    sandbox_logs = a.sandbox_logs[:]
    activity_logs = a.activity_logs[:]
    trades = a.trades[:]

    if merge_timestamps:
        # Shift later days forward so the merged log stays strictly increasing
        # and can be consumed as a single continuous timeline.
        a_last_timestamp = a.activity_logs[-1].timestamp
        timestamp_offset = a_last_timestamp + 100
    else:
        timestamp_offset = 0

    sandbox_logs.extend([row.with_offset(timestamp_offset) for row in b.sandbox_logs])
    trades.extend([row.with_offset(timestamp_offset) for row in b.trades])

    if merge_profit_loss:
        # Carry each product's final realized PnL into the next day so the merged
        # activity log shows cumulative profit instead of resetting every day.
        profit_loss_offsets = defaultdict(float)
        for row in reversed(a.activity_logs):
            if row.timestamp != a_last_timestamp:
                break

            profit_loss_offsets[row.columns[2]] = row.columns[-1]

        activity_logs.extend(
            [row.with_offset(timestamp_offset, profit_loss_offsets[row.columns[2]]) for row in b.activity_logs]
        )
    else:
        activity_logs.extend([row.with_offset(timestamp_offset, 0) for row in b.activity_logs])

    return BacktestResult(a.round_num, a.day_num, sandbox_logs, activity_logs, trades)


def write_output(output_file: Path, merged_results: BacktestResult) -> None:
    """Write sandbox logs, activity logs, and trade history to the output file."""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w+", encoding="utf-8") as file:
        file.write("Sandbox logs:\n")
        for row in merged_results.sandbox_logs:
            file.write(str(row))

        file.write("\n\n\nActivities log:\n")
        file.write(
            "day;timestamp;product;bid_price_1;bid_volume_1;bid_price_2;bid_volume_2;bid_price_3;bid_volume_3;ask_price_1;ask_volume_1;ask_price_2;ask_volume_2;ask_price_3;ask_volume_3;mid_price;profit_and_loss\n"
        )
        file.write("\n".join(map(str, merged_results.activity_logs)))

        file.write("\n\n\n\n\nTrade History:\n")
        file.write("[\n")
        file.write(",\n".join(map(str, merged_results.trades)))
        file.write("]")


def print_overall_summary(results: list[BacktestResult]) -> None:
    """Print the final profit for each requested day and the grand total."""

    print("Profit summary:")

    total_profit = 0
    for result in results:
        last_timestamp = result.activity_logs[-1].timestamp

        profit = 0
        for row in reversed(result.activity_logs):
            if row.timestamp != last_timestamp:
                break

            profit += row.columns[-1]

        print(f"Round {result.round_num} day {result.day_num}: {profit:,.0f}")
        total_profit += profit

    print(f"Total profit: {total_profit:,.0f}")


def format_path(path: Path) -> str:
    """Return a user-friendly path, relative to the current directory when possible."""

    cwd = Path.cwd()
    if path.is_relative_to(cwd):
        return str(path.relative_to(cwd))
    else:
        return str(path)


def version_callback(value: bool) -> None:
    """Handle Typer's eager ``--version`` flag before normal CLI execution."""

    if value:
        print(f"prosperity4btx {metadata.version('prosperity4btx')}")
        sys.exit(0)


app = Typer(context_settings={"help_option_names": ["--help", "-h"]})


@app.command()
def cli(
    algorithm: Annotated[Path, Argument(help="Path to the Python file containing the algorithm to backtest.", show_default=False, exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    days: Annotated[list[str], Argument(help="The days to backtest on. <round>-<day> for a single day, <round> for all days in a round.", show_default=False)],
    merge_pnl: Annotated[bool, Option("--merge-pnl", help="Merge profit and loss across days.")] = False,
    vis: Annotated[bool, Option("--vis", help="Open backtest results in https://jmerle.github.io/imc-prosperity-3-visualizer/ when done.")] = False,
    out: Annotated[Optional[Path], Option(help="File to save output log to (defaults to backtests/<timestamp>.log).", show_default=False, dir_okay=False, resolve_path=True)] = None,
    no_out: Annotated[bool, Option("--no-out", help="Skip saving output log.")] = False,
    data: Annotated[Optional[Path], Option(help="Path to data directory. Must look similar in structure to https://github.com/Xeeshan85/imc-prosperity-4-backtester/tree/main/prosperity4bt/resources.", show_default=False, exists=True, file_okay=False, dir_okay=True, resolve_path=True)] = None,
    print_output: Annotated[bool, Option("--print", help="Print the trader's output to stdout while it's running.")] = False,
    match_trades: Annotated[TradeMatchingMode, Option(help="How to match orders against market trades. 'all' matches trades with prices equal to or worse than your quotes, 'worse' matches trades with prices worse than your quotes, 'none' does not match trades against orders at all.")] = TradeMatchingMode.all,
    no_progress: Annotated[bool, Option("--no-progress", help="Don't show progress bars.")] = False,
    original_timestamps: Annotated[bool, Option("--original-timestamps", help="Preserve original timestamps in output log rather than making them increase across days.")] = False,
    metrics: Annotated[bool, Option("--metrics", help="Print a performance metrics table (Sharpe, drawdown, win rate, etc.) after each backtest.")] = False,
    dashboard: Annotated[Optional[Path], Option("--dashboard", help="Generate an interactive Plotly HTML dashboard from the merged backtest results. Provide a path for the output file (e.g. --dashboard dashboard.html).", show_default=False, dir_okay=False, resolve_path=False)] = None,
    version: Annotated[bool, Option("--version", "-v", help="Show the program's version number and exit.", is_eager=True, callback=version_callback)] = False,
) -> None:  # fmt: skip
    """Run one or more backtests from the command line and optionally save the merged logs."""

    if out is not None and no_out:
        print("Error: --out and --no-out are mutually exclusive")
        sys.exit(1)

    try:
        trader_module = parse_algorithm(algorithm)
    except ModuleNotFoundError as e:
        print(f"{algorithm} is not a valid algorithm file: {e}")
        sys.exit(1)

    if not hasattr(trader_module, "Trader"):
        print(f"{algorithm} does not expose a Trader class")
        sys.exit(1)

    file_reader = parse_data(data)
    parsed_days = parse_days(file_reader, days)
    output_file = parse_out(out, no_out)

    # Progress bars and live stdout printing both occupy the terminal, so only
    # enable tqdm when trader output is not being streamed directly.
    show_progress_bars = not no_progress and not print_output

    results = []
    for round_num, day_num in parsed_days:
        print(f"Backtesting {algorithm} on round {round_num} day {day_num}")

        # Reload between runs so module-level state in the trader file does not
        # leak across different backtest days.
        reload(trader_module)

        result = run_backtest(
            trader_module.Trader(),
            file_reader,
            round_num,
            day_num,
            print_output,
            match_trades,
            True,
            show_progress_bars,
        )

        print_day_summary(result)

        if metrics:
            from prosperity4bt.metrics import compute_metrics, format_metrics
            print(format_metrics(compute_metrics(result)))

        if len(parsed_days) > 1:
            print()

        results.append(result)

    if len(parsed_days) > 1:
        print_overall_summary(results)

    if output_file is not None:
        # Saved output expects a single result object, so fold all selected days
        # into one merged log while preserving the requested timestamp/PnL rules.
        merged_results = reduce(lambda a, b: merge_results(a, b, merge_pnl, not original_timestamps), results)
        write_output(output_file, merged_results)
        print(f"\nSuccessfully saved backtest results to {format_path(output_file)}")
    else:
        merged_results = reduce(lambda a, b: merge_results(a, b, merge_pnl, not original_timestamps), results)

    if dashboard is not None:
        try:
            from prosperity4bt.dashboard import create_dashboard_from_result
            create_dashboard_from_result(merged_results, dashboard)
        except ImportError as e:
            print(f"Warning: could not generate dashboard — {e}")

    if vis and output_file is not None:
        open_visualizer(output_file)


def main() -> None:
    """Invoke the Typer application when the package is run as a script."""

    app()


if __name__ == "__main__":
    main()

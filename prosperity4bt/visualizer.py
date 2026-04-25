import csv
import html
import json
import re
from io import StringIO
from pathlib import Path
from typing import Any


SANDBOX_HEADER = "Sandbox logs:\n"
ACTIVITIES_HEADER = "\n\n\nActivities log:\n"
TRADE_HISTORY_HEADER = "\n\n\n\n\nTrade History:\n"
ASSETS_DIR = Path(__file__).with_name("visualizer_assets")


class ActualTradeResultVisualizer:
    """Parse submission feedback logs into the existing visualizer payload shape."""

    REQUIRED_KEYS = {"activitiesLog", "tradeHistory"}
    LOG_KEYS = ("logs", "sandboxLogs")

    @classmethod
    def matches(cls, log_text: str) -> bool:
        stripped = log_text.lstrip()
        if not stripped.startswith("{"):
            return False

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return False

        return (
            isinstance(payload, dict)
            and cls.REQUIRED_KEYS.issubset(payload)
            and any(key in payload for key in cls.LOG_KEYS)
        )

    @classmethod
    def parse(cls, log_file: Path) -> dict[str, Any]:
        payload = json.loads(log_file.read_text(encoding="utf-8"))
        activity_logs = _parse_activities(payload["activitiesLog"])
        trades = _normalize_trades(payload["tradeHistory"])
        sandbox_source = payload.get("logs", payload.get("sandboxLogs", []))
        sandbox_logs = _normalize_sandbox_logs(sandbox_source)

        result = _build_visualizer_payload(log_file, activity_logs, trades, sandbox_logs)
        result["meta"]["log_type"] = "actual_trade"
        result["meta"]["submission_id"] = payload.get("submissionId")
        return result


def _split_sections(log_text: str) -> tuple[str, str, str]:
    try:
        sandbox_end = log_text.index(ACTIVITIES_HEADER)
        trades_start = log_text.index(TRADE_HISTORY_HEADER)
    except ValueError as exc:
        raise ValueError("Log file does not match the expected backtest output format") from exc

    if not log_text.startswith(SANDBOX_HEADER):
        raise ValueError("Log file is missing the sandbox log section header")

    sandbox_text = log_text[len(SANDBOX_HEADER) : sandbox_end]
    activities_text = log_text[sandbox_end + len(ACTIVITIES_HEADER) : trades_start]
    trades_text = log_text[trades_start + len(TRADE_HISTORY_HEADER) :]
    return sandbox_text.strip(), activities_text.strip(), trades_text.strip()


def _parse_sandbox_logs(sandbox_text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    rows = []
    index = 0

    while index < len(sandbox_text):
        while index < len(sandbox_text) and sandbox_text[index].isspace():
            index += 1

        if index >= len(sandbox_text):
            break

        row, next_index = decoder.raw_decode(sandbox_text, index)
        rows.append(_normalize_sandbox_log_row(row))
        index = next_index

    return rows


def _parse_number(value: str) -> int | float | None:
    if value == "":
        return None

    if "." in value:
        return float(value)

    return int(value)


def _calculate_bid_ask_spread(bid_price: int | float | None, ask_price: int | float | None) -> int | float | None:
    if bid_price is None or ask_price is None:
        return None

    return ask_price - bid_price


def _parse_activities(activities_text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(StringIO(activities_text), delimiter=";")
    rows = []

    for row in reader:
        parsed_row = {
            "day": int(row["day"]),
            "timestamp": int(row["timestamp"]),
            "product": row["product"],
            "mid_price": float(row["mid_price"]),
            "profit_and_loss": float(row["profit_and_loss"]),
        }

        for level in (1, 2, 3):
            parsed_row[f"bid_price_{level}"] = _parse_number(row[f"bid_price_{level}"])
            parsed_row[f"bid_volume_{level}"] = _parse_number(row[f"bid_volume_{level}"])
            parsed_row[f"ask_price_{level}"] = _parse_number(row[f"ask_price_{level}"])
            parsed_row[f"ask_volume_{level}"] = _parse_number(row[f"ask_volume_{level}"])

        parsed_row["bid_ask_spread"] = _calculate_bid_ask_spread(
            parsed_row["bid_price_1"],
            parsed_row["ask_price_1"],
        )

        rows.append(parsed_row)

    return rows


def _normalize_sandbox_log_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": int(row["timestamp"]),
        "sandboxLog": row.get("sandboxLog", ""),
        "lambdaLog": row.get("lambdaLog", ""),
    }


def _normalize_sandbox_logs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalize_sandbox_log_row(row) for row in rows]


def _normalize_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": int(trade["timestamp"]),
            "buyer": trade["buyer"],
            "seller": trade["seller"],
            "symbol": trade["symbol"],
            "currency": trade["currency"],
            "price": int(trade["price"]),
            "quantity": int(trade["quantity"]),
        }
        for trade in trades
    ]


def _parse_trades(trades_text: str) -> list[dict[str, Any]]:
    sanitized_text = re.sub(r",(\s*[}\]])", r"\1", trades_text)
    trades = json.loads(sanitized_text)
    return _normalize_trades(trades)


def _build_visualizer_payload(
    log_file: Path,
    activity_logs: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    sandbox_logs: list[dict[str, Any]],
) -> dict[str, Any]:
    products = sorted({row["product"] for row in activity_logs})
    timestamps = sorted({row["timestamp"] for row in activity_logs})
    days = sorted({row["day"] for row in activity_logs})

    final_profit_loss = {}
    for row in activity_logs:
        final_profit_loss[row["product"]] = row["profit_and_loss"]

    return {
        "meta": {
            "file_name": log_file.name,
            "days": days,
            "products": products,
            "timestamps": timestamps,
            "timestamp_count": len(timestamps),
            "trade_count": len(trades),
            "sandbox_event_count": sum(1 for row in sandbox_logs if row["sandboxLog"]),
            "final_profit_loss": final_profit_loss,
        },
        "activity_logs": activity_logs,
        "trades": trades,
        "sandbox_logs": sandbox_logs,
    }


def parse_backtest_log(log_file: Path) -> dict[str, Any]:
    log_text = log_file.read_text(encoding="utf-8")
    sandbox_text, activities_text, trades_text = _split_sections(log_text)

    sandbox_logs = _parse_sandbox_logs(sandbox_text)
    activity_logs = _parse_activities(activities_text)
    trades = _parse_trades(trades_text)

    result = _build_visualizer_payload(log_file, activity_logs, trades, sandbox_logs)
    result["meta"]["log_type"] = "backtest"
    return result


def parse_visualizer_log(log_file: Path) -> dict[str, Any]:
    log_text = log_file.read_text(encoding="utf-8")
    if ActualTradeResultVisualizer.matches(log_text):
        return ActualTradeResultVisualizer.parse(log_file)

    return parse_backtest_log(log_file)


def _read_asset_text(name: str) -> str:
    return (ASSETS_DIR / name).read_text(encoding="utf-8")


def _render_index_html(log_file: Path) -> str:
    template = _read_asset_text("index.html")
    replacements = {
        "__TITLE__": html.escape(f"Backtest Visualizer | {log_file.name}"),
        "__LOG_NAME__": html.escape(log_file.name),
    }

    for needle, value in replacements.items():
        template = template.replace(needle, value)

    return template


def build_visualizer_assets(log_file: Path) -> dict[str, tuple[str, bytes]]:
    payload = parse_visualizer_log(log_file)
    payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    assets = {
        "/index.html": ("text/html; charset=utf-8", _render_index_html(log_file).encode("utf-8")),
        "/styles.css": ("text/css; charset=utf-8", _read_asset_text("styles.css").encode("utf-8")),
        "/app.js": ("application/javascript; charset=utf-8", _read_asset_text("app.js").encode("utf-8")),
        "/data.json": ("application/json; charset=utf-8", payload_json),
    }
    assets["/"] = assets["/index.html"]
    return assets


def build_visualizer_html(log_file: Path) -> str:
    content_type, body = build_visualizer_assets(log_file)["/index.html"]
    _ = content_type
    return body.decode("utf-8")

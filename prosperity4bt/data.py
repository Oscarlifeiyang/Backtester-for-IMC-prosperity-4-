from collections import defaultdict
from dataclasses import dataclass

from prosperity4bt.datamodel import Symbol, Trade
from prosperity4bt.file_reader import FileReader

LIMITS = {
    "EMERALDS": 80,
    "TOMATOES": 80,
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    "VEV_4000": 300,
    "VEV_4500": 300,
    "VEV_5000": 300,
    "VEV_5100": 300,
    "VEV_5200": 300,
    "VEV_5300": 300,
    "VEV_5400": 300,
    "VEV_5500": 300,
    "VEV_6000": 300,
    "VEV_6500": 300,
}


@dataclass
class PriceRow:
    """One price-book snapshot for a product at a specific backtest timestamp."""

    day: int
    timestamp: int
    product: Symbol
    bid_prices: list[int]
    bid_volumes: list[int]
    ask_prices: list[int]
    ask_volumes: list[int]
    mid_price: float
    profit_loss: float


def get_column_values(columns: list[str], indices: list[int]) -> list[int]:
    """Read ordered numeric CSV columns until the first empty value is reached."""

    values = []

    for index in indices:
        value = columns[index]
        if value == "":
            break

        values.append(int(value))

    return values


@dataclass
class ObservationRow:
    """One conversion-observation record keyed by timestamp."""

    timestamp: int
    bidPrice: float
    askPrice: float
    transportFees: float
    exportTariff: float
    importTariff: float
    sugarPrice: float
    sunlightIndex: float


@dataclass
class BacktestData:
    """Timestamp-indexed market data bundle used by the backtest runner."""

    round_num: int
    day_num: int

    prices: dict[int, dict[Symbol, PriceRow]]
    trades: dict[int, dict[Symbol, list[Trade]]]
    observations: dict[int, ObservationRow]
    products: list[Symbol]
    profit_loss: dict[Symbol, float]


def create_backtest_data(
    round_num: int, day_num: int, prices: list[PriceRow], trades: list[Trade], observations: list[ObservationRow]
) -> BacktestData:
    """Group raw CSV rows into the lookup tables consumed during simulation."""

    prices_by_timestamp: dict[int, dict[Symbol, PriceRow]] = defaultdict(dict)
    for row in prices:
        prices_by_timestamp[row.timestamp][row.product] = row

    trades_by_timestamp: dict[int, dict[Symbol, list[Trade]]] = defaultdict(lambda: defaultdict(list))
    for trade in trades:
        trades_by_timestamp[trade.timestamp][trade.symbol].append(trade)

    products = sorted(set(row.product for row in prices))
    profit_loss = {product: 0.0 for product in products}

    observations_by_timestamp = {row.timestamp: row for row in observations}

    return BacktestData(
        round_num=round_num,
        day_num=day_num,
        prices=prices_by_timestamp,
        trades=trades_by_timestamp,
        observations=observations_by_timestamp,
        products=products,
        profit_loss=profit_loss,
    )


def has_day_data(file_reader: FileReader, round_num: int, day_num: int) -> bool:
    """Return whether the price file for a round/day exists."""

    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_day_{day_num}.csv"]) as file:
        return file is not None


def read_day_data(file_reader: FileReader, round_num: int, day_num: int, no_names: bool) -> BacktestData:
    """Load one round/day of prices, trades, and observations from CSV files."""

    prices = []
    with file_reader.file([f"round{round_num}", f"prices_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is None:
            raise ValueError(f"Prices data is not available for round {round_num} day {day_num}")

        for line in file.read_text(encoding="utf-8").splitlines()[1:]:
            columns = line.split(";")

            prices.append(
                PriceRow(
                    day=int(columns[0]),
                    timestamp=int(columns[1]),
                    product=columns[2],
                    bid_prices=get_column_values(columns, [3, 5, 7]),
                    bid_volumes=get_column_values(columns, [4, 6, 8]),
                    ask_prices=get_column_values(columns, [9, 11, 13]),
                    ask_volumes=get_column_values(columns, [10, 12, 14]),
                    mid_price=float(columns[15]),
                    profit_loss=float(columns[16]),
                )
            )

    trades = []
    with file_reader.file([f"round{round_num}", f"trades_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            for line in file.read_text(encoding="utf-8").splitlines()[1:]:
                columns = line.split(";")

                trades.append(
                    Trade(
                        symbol=columns[3],
                        price=int(float(columns[5])),
                        quantity=int(columns[6]),
                        buyer=columns[1],
                        seller=columns[2],
                        timestamp=int(columns[0]),
                    )
                )

    observations = []
    with file_reader.file([f"round{round_num}", f"observations_round_{round_num}_day_{day_num}.csv"]) as file:
        if file is not None:
            for line in file.read_text(encoding="utf-8").splitlines()[1:]:
                columns = line.split(",")

                observations.append(
                    ObservationRow(
                        timestamp=int(columns[0]),
                        bidPrice=float(columns[1]),
                        askPrice=float(columns[2]),
                        transportFees=float(columns[3]),
                        exportTariff=float(columns[4]),
                        importTariff=float(columns[5]),
                        sugarPrice=float(columns[6]),
                        sunlightIndex=float(columns[7]),
                    )
                )

    return create_backtest_data(round_num, day_num, prices, trades, observations)

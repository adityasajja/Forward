# File-backed market data for the CLI

CSV and Parquet backtests need two inputs: price bars and an instrument catalog.
The bar file cannot say whether its rows describe an equity, future, option, or
index, so the CLI does not guess.

The catalog is a JSON array. Each row must include the instrument's symbol,
asset class, currency, exchange, multiplier, tick size, minimum quantity, and
quantity step. Optional fields must use names from `forward.core.Instrument`.
Unknown fields and duplicate symbols are errors.

```json
[
  {
    "symbol": "AAPL",
    "asset_class": "EQUITY",
    "currency": "USD",
    "exchange": "NASDAQ",
    "multiplier": 1.0,
    "tick_size": 0.01,
    "min_quantity": 1.0,
    "quantity_step": 1.0
  }
]
```

Use the same catalog for backtests and walk-forward research:

```bash
uv run forward backtest --source csv --path data/bars.csv --instruments data/instruments.json
uv run forward research --source parquet --path data/bars.parquet --instruments data/instruments.json
```

Synthetic runs use the instrument definitions from the synthetic data generator
and do not need a catalog.

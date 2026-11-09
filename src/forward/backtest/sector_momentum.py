"""Opening sector momentum research backtest; never submits broker orders.

Run: python -m forward.backtest.sector_momentum --bars bars.csv --universe universe.csv
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Config:
    cash: float = 100_000
    top_sectors: int = 1
    slippage_bps: float = 5
    fee_bps: float = 5

    def __post_init__(self):
        if (
            not all(np.isfinite(x) for x in (self.cash, self.slippage_bps, self.fee_bps))
            or self.cash <= 0
            or self.top_sectors < 1
            or not 0 <= self.slippage_bps < 100
            or self.fee_bps < 0
        ):
            raise ValueError("Invalid capital, sector count, or costs")


def backtest(bars: pd.DataFrame, universe: pd.DataFrame, config: Config = Config()):
    """Return trade ledger, daily equity/skip ledger, and summary.

    Bars: timestamp,symbol,open,high,low,close,prev_close (minute START times).
    Universe: date,symbol,kind,sector; kind=index/sector/stock. Exactly one
    index per day. Sector rows identify sector indices by the sector field.
    Universe must be historical, complete, and limited to shortable stocks.
    """
    b, u = bars.copy(), universe.copy()
    required = {"timestamp", "symbol", "open", "high", "low", "close", "prev_close"}
    if not required <= set(b) or not {"date", "symbol", "kind", "sector"} <= set(u):
        raise ValueError("Missing input columns; see sector_momentum.md")
    b["timestamp"] = pd.to_datetime(b.timestamp, format="mixed")
    if b.timestamp.dt.tz is None:
        b["timestamp"] = b.timestamp.dt.tz_localize("Asia/Kolkata")
    else:
        b["timestamp"] = b.timestamp.dt.tz_convert("Asia/Kolkata")
    if (b.timestamp.dt.second.ne(0) | b.timestamp.dt.microsecond.ne(0)).any():
        raise ValueError("Expected minute-start timestamps")
    b["date"] = b.timestamp.dt.strftime("%Y-%m-%d")
    b["time"] = b.timestamp.dt.strftime("%H:%M")
    u["date"] = pd.to_datetime(u.date).dt.strftime("%Y-%m-%d")
    if b.duplicated(["timestamp", "symbol"]).any() or u.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate instrument timestamps or universe members")
    prices = b[["open", "high", "low", "close", "prev_close"]].astype(float)
    if (
        not np.isfinite(prices).all().all()
        or (prices <= 0).any().any()
        or (prices.high < prices[["open", "close", "low"]].max(axis=1)).any()
        or (prices.low > prices[["open", "close", "high"]].min(axis=1)).any()
    ):
        raise ValueError("Invalid OHLC or previous-close prices")
    b[prices.columns] = prices
    trades, daily = [], []
    equity = config.cash
    slip, fee = config.slippage_bps / 10_000, config.fee_bps / 10_000
    for date in sorted(set(b.date) | set(u.date)):
        members = u[u.date == date]
        day = b[b.date == date]
        status = "missing_opening_data"
        opening = day[day.time == "09:15"].set_index("symbol")
        if (
            members.empty
            or len(members[members.kind == "index"]) != 1
            or not set(members.symbol) <= set(opening.index)
        ):
            daily.append(dict(date=date, equity=equity, status=status))
            continue
        if not set(members.kind) <= {"index", "sector", "stock"}:
            raise ValueError("Unknown universe kind")
        ranks = members.join(opening[["open", "prev_close"]], on="symbol")
        ranks["change"] = ranks.open / ranks.prev_close - 1
        direction = int(np.sign(ranks.loc[ranks.kind == "index", "change"].iloc[0]))
        sectors = ranks[(ranks.kind == "sector") & (ranks.change * direction > 0)]
        sectors = sectors.sort_values(["change", "symbol"], ascending=[direction < 0, True])
        stocks = ranks[
            (ranks.kind == "stock")
            & ranks.sector.isin(sectors.head(config.top_sectors).sector)
            & (ranks.change * direction > 0)
        ]
        stocks = stocks.sort_values(["change", "symbol"], ascending=[direction < 0, True])
        if direction == 0 or stocks.empty:
            daily.append(dict(date=date, equity=equity, status="no_signal"))
            continue
        selected = stocks.iloc[0]
        path = day[
            (day.symbol == selected.symbol) & (day.time >= "09:20") & (day.time <= "15:20")
        ].sort_values("timestamp")
        expected = pd.date_range(f"{date} 09:20", f"{date} 15:20", freq="min", tz="Asia/Kolkata")
        if path.empty or path.iloc[0].timestamp != expected[0]:
            daily.append(dict(date=date, equity=equity, status="incomplete_trade_session"))
            continue
        entry = float(path.iloc[0].open) * (1 + direction * slip)
        qty = int(equity / (entry * (1 + fee)))
        if qty < 1:
            daily.append(dict(date=date, equity=equity, status="insufficient_cash"))
            continue
        target, stop = entry * (1 + direction * 0.01), entry * (1 - direction * 0.01)
        completed = False
        for i, row in enumerate(path.itertuples()):
            if row.timestamp != expected[i]:
                break
            # 15:20 square-off executes at the open, before that minute's range.
            if row.time == "15:20":
                raw, reason = row.open, "square_off"
            elif direction * (row.open - stop) <= 0:
                raw, reason = row.open, "stop_gap"
            elif direction * (row.open - target) >= 0:
                raw, reason = target, "target_gap"
            else:
                hit_stop = row.low <= stop if direction > 0 else row.high >= stop
                hit_target = row.high >= target if direction > 0 else row.low <= target
                if hit_stop:
                    raw, reason = stop, "stop_both_hit" if hit_target else "stop"
                elif hit_target:
                    raw, reason = target, "target"
                else:
                    continue
            exit_price = raw * (1 - direction * slip)
            gross = direction * qty * (exit_price - entry)
            costs = qty * (entry + exit_price) * fee
            net = gross - costs
            equity += net
            trades.append(
                dict(
                    date=date,
                    symbol=selected.symbol,
                    sector=selected.sector,
                    side="BUY" if direction > 0 else "SHORT",
                    entry_time=path.iloc[0].timestamp,
                    exit_time=row.timestamp,
                    entry=entry,
                    exit=exit_price,
                    quantity=qty,
                    target=target,
                    stop=stop,
                    reason=reason,
                    gross_pnl=gross,
                    fees=costs,
                    net_pnl=net,
                    equity=equity,
                )
            )
            completed = True
            break
        daily.append(
            dict(
                date=date,
                equity=equity,
                status="traded" if completed else "incomplete_trade_session",
            )
        )
    t, d = pd.DataFrame(trades), pd.DataFrame(daily)
    curve = pd.Series([config.cash] + [r["equity"] for r in daily])
    pnl = t.net_pnl if len(t) else pd.Series(dtype=float)
    losses = -pnl[pnl < 0].sum()
    summary = dict(
        days=len(d),
        trades=len(t),
        initial_cash=config.cash,
        final_equity=equity,
        net_return_pct=(equity / config.cash - 1) * 100,
        win_rate_pct=float((pnl > 0).mean() * 100) if len(t) else None,
        profit_factor=float(pnl[pnl > 0].sum() / losses) if losses else None,
        max_daily_closed_equity_drawdown_pct=float((curve / curve.cummax() - 1).min() * 100),
        skipped_days=sum(r["status"] != "traded" for r in daily),
    )
    return t, d, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", default="reports/sector_momentum")
    parser.add_argument("--cash", type=float, default=100_000)
    parser.add_argument("--top-sectors", type=int, default=1)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--fee-bps", type=float, default=5)
    args = parser.parse_args()
    t, d, summary = backtest(
        pd.read_csv(args.bars),
        pd.read_csv(args.universe),
        Config(args.cash, args.top_sectors, args.slippage_bps, args.fee_bps),
    )
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / "trades.csv", index=False)
    d.to_csv(out / "daily.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

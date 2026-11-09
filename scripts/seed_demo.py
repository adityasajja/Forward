"""Fill an EMPTY data directory with clearly marked synthetic demo data, so the app can be run,
screenshotted and demonstrated with something in it.

    Set FORWARD_DEMO_PASSWORD or pass --password before running the script.
    forward serve                              # then browse

What it makes: ten synthetic stocks and a Nifty-like series, a worked-example strategy, two paper
deployments, and about three months of backdated paper fills written through the real order tables.
A ``.demo`` file marks the directory, and the API reports ``demo: true``, so the interface says
"Demo data" wherever the numbers appear. Nothing here resembles a real result.

It REFUSES to run if the data directory already holds anything. It never mixes demo data into a
real one: use a fresh clone or an empty ``data/``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEMO_CONFIG = ROOT / "config" / "demo_seed.json"


def business_days(end: date, n: int) -> list[date]:
    return [d.date() for d in pd.bdate_range(end=pd.Timestamp(end), periods=n)]


def write_prices(
    root: Path,
    days: list[date],
    rng: np.random.Generator,
    config: dict,
) -> dict[str, pd.Series]:
    """Write synthetic prices using the explicit demo configuration.

    Args:
        root: Data directory that receives the generated files.
        days: Business dates to write.
        rng: Random number generator seeded by the caller.
        config: Demo configuration loaded from ``config/demo_seed.json``.

    Returns:
        A map from each stock symbol to its generated closing prices.
    """
    symbols = config["symbols"]
    price_config = config["price_series"]
    runtime_config = config["demo_runtime"]
    daily = root / "iifl_daily" / runtime_config["exchange"]
    daily.mkdir(parents=True)
    closes: dict[str, pd.Series] = {}
    bar_hour, bar_minute = map(int, price_config["bar_time"].split(":"))
    ts = [datetime(d.year, d.month, d.day, bar_hour, bar_minute) for d in days]
    for i, sym in enumerate(symbols):
        growth_rate = price_config["growth_rate"] + price_config["growth_step"] * (
            i % price_config["growth_step_period"]
        )
        steps = rng.normal(growth_rate, price_config["daily_volatility"], len(days)).cumsum()
        close = (price_config["starting_price"] + price_config["price_step"] * i) * np.exp(steps)
        high = close * (1 + rng.uniform(price_config["wick_min"], price_config["wick_max"], len(days)))
        low = close * (1 - rng.uniform(price_config["wick_min"], price_config["wick_max"], len(days)))
        open_ = close * (1 + rng.normal(0, price_config["open_volatility"], len(days)))
        frame = pd.DataFrame({
            "ts": pd.to_datetime(ts), "open": open_, "high": np.maximum.reduce([high, open_, close]),
            "low": np.minimum.reduce([low, open_, close]), "close": close,
            "volume": rng.integers(
                price_config["volume_min"], price_config["volume_max"], len(days)
            ).astype(float),
        })
        frame.to_parquet(daily / f"{sym}{price_config['stock_file_suffix']}.parquet")
        closes[sym] = pd.Series(close, index=days)
    # a Nifty-like series: the average of the stocks' log returns, with its own noise
    ret = np.mean([np.log(s).diff().fillna(0).to_numpy() for s in closes.values()], axis=0)
    level = price_config["nifty_level"] * np.exp(
        (ret * price_config["nifty_return_scale"] + rng.normal(
            0, price_config["nifty_noise"], len(days)
        )).cumsum()
    )
    idx_dir = root / "iifl_daily" / runtime_config["index_exchange_directory"]
    idx_dir.mkdir(parents=True)
    pd.DataFrame({"ts": pd.to_datetime(ts), "open": level,
                  "high": level * (1 + price_config["nifty_daily_range"]),
                  "low": level * (1 - price_config["nifty_daily_range"]),
                  "close": level, "volume": price_config["nifty_volume"]}).to_parquet(
                      idx_dir / f"{price_config['index_symbol']}.parquet"
                  )
    (idx_dir / f"{price_config['index_symbol']}.source").write_text(
        price_config["index_source"], encoding="utf8"
    )
    uni = root / "universe"
    uni.mkdir()
    (uni / runtime_config["stock_universe_file"]).write_text(",".join(symbols), encoding="utf8")
    rows = "".join(
        f"{s.title()} Ltd.,{ind},{s},EQ,{price_config['isin_prefix']}{i:09d}\n"
        for i, (s, ind) in enumerate(symbols.items())
    )
    (uni / runtime_config["index_universe_file"]).write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n" + rows, encoding="utf8"
    )
    return closes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--config",
        type=Path,
        default=DEMO_CONFIG,
        help="path to the explicit demo data configuration",
    )
    ap.add_argument(
        "--password",
        default=os.environ.get("FORWARD_DEMO_PASSWORD"),
        help="password for the demo owner account (or set FORWARD_DEMO_PASSWORD)",
    )
    args = ap.parse_args()
    if not args.password:
        ap.error("provide --password or set FORWARD_DEMO_PASSWORD")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    print(f"Configuration: {args.config.resolve()}")
    symbols = config["symbols"]
    price_config = config["price_series"]
    runtime_config = config["demo_runtime"]
    portfolio_config = config["portfolio"]
    trade_config = config["trade_generation"]

    root = ROOT / "data"
    if root.exists() and any(root.iterdir()):
        print(f"refusing: {root} is not empty. Demo data is never mixed into a real data directory.", file=sys.stderr)
        return 2
    root.mkdir(parents=True, exist_ok=True)

    from forward.market_calendar import IST, NSEMarketCalendar
    from forward.services.data_status import expected_session

    rng = np.random.default_rng(config["seed"])
    last = expected_session(datetime.now(IST), NSEMarketCalendar())
    days = business_days(last, config["history_sessions"])
    closes = write_prices(root, days, rng, config)
    (root / ".demo").write_text("synthetic demo data: nothing here is a real result\n", encoding="utf8")

    from fastapi.testclient import TestClient
    from forward.appdb.repositories import OrderEventRepository, OrderRepository
    from forward.appdb.schema import deployments, orders
    from sqlalchemy import update

    from forward.api.main import app
    from forward.appdb.engine import get_app_db

    client = TestClient(
        app,
        client=(runtime_config["client_host"], runtime_config["client_port"]),
    )
    account = config["demo_account"]
    r = client.post(
        "/api/v1/auth/bootstrap",
        json={**account, "password": args.password},
    )
    assert r.status_code == 201, r.text
    client.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    seeded = client.post("/api/v1/strategies/seed")
    assert seeded.status_code in (200, 201), seeded.text
    strategy_id = seeded.json()["strategy"]["strategy_id"]
    version = seeded.json()["version"]["version"]

    db = get_app_db()
    trade_days = days[-config["trade_sessions"]:]
    py = random.Random(config["seed"])
    total_fills = 0
    for capital in portfolio_config["capital"]:
        d = client.post("/api/v1/paper/deployments", json={"strategy_id": strategy_id, "strategy_version": version, "capital": capital})
        assert d.status_code == 201, d.text
        dep = d.json()
        user_id = dep["user_id"]
        i = py.randint(trade_config["initial_session_min"], trade_config["initial_session_max"])
        while i < len(trade_days) - trade_config["minimum_remaining_sessions"]:
            sym = py.choice(list(symbols))
            hold = py.randint(
                trade_config["holding_sessions_min"], trade_config["holding_sessions_max"]
            )
            j = min(i + hold, len(trade_days) - 1)
            entry_day, exit_day = trade_days[i], trade_days[j]
            px_in, px_out = float(closes[sym][entry_day]), float(closes[sym][exit_day])
            qty = max(
                portfolio_config["minimum_quantity"],
                int(capital * portfolio_config["allocation_fraction"] / px_in),
            )
            move = (px_out / px_in - 1.0) * 100.0
            reasons = {
                "BUY": (
                    f"breakout: closed above its {trade_config['breakout_lookback']}-day "
                    f"high of {px_in * trade_config['breakout_reference_price_multiplier']:,.2f}"
                ),
                "SELL": (
                    f"take_profit: up {move:.1f}% against an average of {px_in:,.2f}"
                    if move >= trade_config["take_profit_pct"]
                    else f"stop_loss: down {move:.1f}% against an average of {px_in:,.2f}"
                    if move <= trade_config["stop_loss_pct"]
                    else (
                        f"trend_exit: closed below its {trade_config['trend_exit_lookback']}-day "
                        "average"
                    )
                ),
            }
            for side, day, px, slippage in (
                ("BUY", entry_day, px_in, portfolio_config["entry_slippage"]),
                ("SELL", exit_day, px_out, portfolio_config["exit_slippage"]),
            ):
                px *= 1 + slippage if side == "BUY" else 1 - slippage
                at = datetime(
                    day.year,
                    day.month,
                    day.day,
                    portfolio_config["entry_hour_utc"],
                    portfolio_config["entry_minute_utc"],
                )
                commission = round(px * qty * portfolio_config["commission_rate"], 2)
                with db.session() as session:
                    o = OrderRepository.create(
                        session,
                        user_id=user_id,
                        symbol=f"{sym}{price_config['stock_file_suffix']}",
                        side=side,
                        quantity=qty,
                        deployment_id=dep["deployment_id"],
                        strategy_id=strategy_id,
                        strategy_version=version,
                        requested_price=px,
                        tag=runtime_config["order_tag"],
                    )
                    OrderEventRepository.append(
                        session,
                        order_id=o["order_id"],
                        to_status="NEW",
                        ts=at,
                        raw={"reason": reasons[side]},
                        source="demo",
                    )
                    OrderEventRepository.append(
                        session,
                        order_id=o["order_id"],
                        from_status="NEW",
                        to_status="FILLED",
                        ts=at,
                        fill_ts=at,
                        filled_qty=float(qty),
                        filled_price=round(px, 2),
                        commission=commission,
                        requested_price=px,
                        source="demo",
                    )
                    session.execute(
                        update(orders)
                        .where(orders.c.order_id == o["order_id"])
                        .values(created_at=at, updated_at=at)
                    )
                total_fills += 1
            i = j + py.randint(
                trade_config["next_entry_gap_min"], trade_config["next_entry_gap_max"]
            )
        with db.session() as session:
            session.execute(update(deployments).where(deployments.c.deployment_id == dep["deployment_id"]).values(
                status="RUNNING",
                started_at=datetime(
                    trade_days[0].year,
                    trade_days[0].month,
                    trade_days[0].day,
                    portfolio_config["deployment_start_hour_utc"],
                    portfolio_config["deployment_start_minute_utc"],
                ),
            ))
    # Bring the trade journal (which Performance reads) up to date with the ledger, as the app does.
    synced = client.post("/api/v1/paper/sync-journal")
    assert synced.status_code == 200, synced.text
    assert synced.json()["in_sync"], synced.json()
    print(
        f"seeded {len(symbols)} synthetic demo stocks to {last}, a strategy, "
        f"{len(portfolio_config['capital'])} paper deployments and {total_fills} fills"
    )
    print(f"log in as: {account['username']}   (then: forward serve)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

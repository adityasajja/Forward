"""Shared fixtures for the Phase 1 test suites.

Two rules this file exists to enforce:

* **Every test gets its own store.** A test that writes to ``data/app.db`` would
  pass on a clean machine and fail on a used one, and would mutate the operator's
  real accounts. ``fresh_env`` points ``APP_DB_URL`` at ``tmp_path`` and clears
  every module-level singleton, so ordering never matters.
* **The market-data fixture is synthetic and deterministic.** Tests that need
  price history build their own parquet cache rather than reading the operator's
  3,000-file cache — a test whose result depends on today's data is not a test.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forward.appdb.engine import AppDatabase, reset_app_db_cache
from forward.config.settings import get_settings
from forward.data.instrument_catalog import InstrumentMaster


class EmptyCachedInstrumentHistory:
    """Return no cached instrument data to paper runner unit tests."""

    def get(self, symbol: str):
        """Return no instrument record.

        Args:
            symbol: Instrument symbol requested by the runner.

        Returns:
            No instrument record.
        """
        return None

    def daily_bars(self, cache_file: str | None):
        """Return no daily bars.

        Args:
            cache_file: Cache key requested by the runner.

        Returns:
            No daily frame.
        """
        return None


def empty_cached_instrument_history_port() -> EmptyCachedInstrumentHistory:
    """Build the empty history adapter used by paper runner tests.

    Returns:
        An adapter that has no listed instruments or cached bars.
    """
    return EmptyCachedInstrumentHistory()


@pytest.fixture()
def fresh_env(tmp_path, monkeypatch):
    """Isolated settings + singletons for one test."""
    monkeypatch.setenv("APP_DB_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    # Keep flat-file consumers on test-owned data. Learning reads its explicit
    # research directory; signal context uses its configured market-data root.
    monkeypatch.setenv("FORWARD_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("RESEARCH_DATA_DIRECTORY", str(tmp_path / "data"))
    monkeypatch.setenv("FORWARD_SECRET_KEY", "unit-test-key")
    # Live-order tests place orders at whatever time the suite runs; the market-hours guard has its own
    # tests that inject a clock.
    monkeypatch.setenv("ENFORCE_MARKET_HOURS", "false")
    # The audit file sink defaults to <repo>/data/audit: without this, every test run appended to the
    # operator's real audit log.
    monkeypatch.setenv("FORWARD_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    # scrypt is deliberately ~0.2s per hash in production; every API test creates
    # accounts, so that cost was paid dozens of times per file. The cost factor
    # is stored in each hash, so verification still runs the real code path.
    monkeypatch.setattr("forward.auth.passwords._N", 2**4)
    # The broker session is a cwd-relative file by default; without this a test
    # run on a machine with a live login talks to the operator's real broker.
    monkeypatch.setenv("IIFL_SESSION_CACHE", str(tmp_path / "iifl_session.json"))
    # Forced to dev so the loopback bypass is reachable and the session cookie is
    # not marked Secure (which a plain-HTTP test client would refuse to store).
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ALLOW_SIGNUP", "false")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "10000")
    monkeypatch.setenv("RATE_LIMIT_LOGIN_PER_MINUTE", "10000")

    _reset_all()
    yield
    _reset_all()


def _reset_all() -> None:
    get_settings.cache_clear()
    reset_app_db_cache()
    from forward.wiring.audit import reset_audit_log_adapter
    from forward.wiring.auth import reset_auth_service, reset_rate_limiter
    from forward.wiring.broker_access import reset_broker_access

    reset_audit_log_adapter()
    reset_broker_access()
    reset_auth_service()
    reset_rate_limiter()
    # The learning service caches a built dataset for the process. Leaving it
    # alive across tests hands the next test a dataset built against the
    # previous test's database and data root.
    from forward.services.learning import reset_learning_service

    reset_learning_service()
    from forward.data.screener_market import clear_screener_frame_cache

    clear_screener_frame_cache()
    # Composed research services share the signal-context adapter and its
    # configured database and data root.
    from forward.composition import (
        reset_backtest_service,
        reset_daily_learning_service,
        reset_insights_service,
    )
    from forward.wiring.market_intel import reset_market_intel_service
    from forward.wiring.self_learning import reset_self_learning_engine
    from forward.wiring.signal_context import reset_signal_context_service

    reset_backtest_service()
    reset_daily_learning_service()
    reset_signal_context_service()
    reset_self_learning_engine()
    reset_market_intel_service()
    reset_insights_service()
@pytest.fixture()
def app_db(fresh_env) -> AppDatabase:
    db = AppDatabase()
    db.prepare()
    return db


@pytest.fixture()
def market_cache(tmp_path) -> Path:
    """A tiny, deterministic market-data cache in the real on-disk layout."""
    root = tmp_path / "data"
    daily = root / "iifl_daily" / "NSEEQ"
    daily.mkdir(parents=True)

    for index, symbol in enumerate(("RELIANCE", "TCS", "INFY", "HDFCBANK")):
        frame = _price_frame(seed=index + 1)
        # Two spellings, as the real cache has: the -EQ series file and, for one
        # symbol, a bare-ticker duplicate. The master must collapse them.
        frame.to_parquet(daily / f"{symbol}-EQ.parquet")
        if symbol == "RELIANCE":
            frame.to_parquet(daily / "RELIANCE.parquet")

    # A granularity directory that is NOT an exchange. The master must not read
    # "15M" as an exchange name.
    intraday = root / "iifl_15m"
    intraday.mkdir()
    _price_frame(seed=9).to_parquet(intraday / "TCS-EQ.parquet")

    universe = root / "universe"
    universe.mkdir()
    # Single-line, comma-separated — the documented trap. A whitespace split
    # yields one token and zero symbols.
    (universe / "n50.txt").write_text("RELIANCE,TCS,INFY", encoding="utf-8")
    (universe / "mid150.txt").write_text("HDFCBANK", encoding="utf-8")

    csv_path = universe / "ind_nifty50list.csv"
    csv_path.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\n"
        "Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n",
        encoding="utf-8",
    )
    return root


def _price_frame(seed: int, periods: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0005, 0.012, periods).cumsum()
    close = 100.0 * np.exp(steps)
    high = close * (1 + rng.uniform(0.001, 0.02, periods))
    low = close * (1 - rng.uniform(0.001, 0.02, periods))
    open_ = close * (1 + rng.normal(0, 0.004, periods))
    volume = rng.integers(100_000, 900_000, periods).astype(float)
    return pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=periods, freq="B"),
            "open": open_,
            "high": np.maximum.reduce([high, open_, close]),
            "low": np.minimum.reduce([low, open_, close]),
            "close": close,
            "volume": volume,
        }
    )


@pytest.fixture()
def master(market_cache) -> InstrumentMaster:
    """An instrument catalog over the synthetic market-data cache."""
    return InstrumentMaster(cache_root=market_cache)


@pytest.fixture()
def client(fresh_env, master):
    """An unauthenticated TestClient.

    Deliberately **not** used as a context manager. Entering the context runs the
    app's lifespan, which warms the breadth cache by scoring a sample of the real
    on-disk universe (~15s) and builds the real instrument master. That belongs to
    a running server, not to a unit test; requests work fine without it.
    """
    from fastapi.testclient import TestClient

    from forward.api.main import app

    return TestClient(app, client=("127.0.0.1", 51234))


@pytest.fixture()
def auth_client(fresh_env, master):
    """TestClient with the owner account already bootstrapped."""
    from fastapi.testclient import TestClient

    from forward.api.main import app

    test_client = TestClient(app, client=("127.0.0.1", 51234))
    response = test_client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "username": "owner",
            "password": "Str0ngPassw0rd",
            "display_name": "Owner",
        },
    )
    assert response.status_code == 201, response.text
    test_client.headers.update({"Authorization": f"Bearer {response.json()['token']}"})
    return test_client

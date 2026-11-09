"""A deployment can mix exchanges with a validated per-symbol override map."""

import pytest

from forward.config.settings import get_settings
from forward.paper.config import RunnerConfig


def _runner_config(row):
    settings = get_settings()
    return RunnerConfig.from_deployment(
        row,
        platform_interval_seconds=settings.runner_interval_seconds,
        platform_lookback_days=settings.runner_lookback_days,
    )


def test_symbols_without_an_override_use_the_deployment_exchange():
    cfg = _runner_config(
        {
            "config": {
                "symbols": ["RELIANCE-EQ", "TCS-EQ"],
                "exchange": "nseeq",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
            }
        }
    )
    assert cfg.symbol_exchanges == {}
    assert {cfg.exchange_of(s) for s in cfg.symbols} == {"NSEEQ"}


def test_a_symbol_can_trade_on_another_exchange():
    cfg = _runner_config(
        {
            "config": {
                "symbols": ["RELIANCE-EQ", "500325"],
                "exchange": "NSEEQ",
                "timeframe": "1d",
                "max_open_positions": 10,
                "signal_basis": "closed_bar",
                "symbol_exchanges": {"500325": "bseeq"},
            }
        }
    )
    assert cfg.exchange_of("RELIANCE-EQ") == "NSEEQ"
    assert cfg.exchange_of("500325") == "BSEEQ"


def test_a_malformed_override_map_is_rejected():
    with pytest.raises(ValueError, match="symbol_exchanges must be an object"):
        _runner_config(
            {
                "config": {
                    "symbols": ["A"],
                    "exchange": "NSEEQ",
                    "timeframe": "1d",
                    "max_open_positions": 10,
                    "signal_basis": "closed_bar",
                    "symbol_exchanges": ["not", "a", "dict"],
                }
            }
        )

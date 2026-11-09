import pytest

from forward.signals.formula import FormulaError, compile_formula
from forward.signals.requirements import formula_requirements
from forward.strategy.definition import validate_definition


def definition(formula, history=210, timeframe="1d"):
    return {"engine_key":"signals_entry", "context":{"style":"swing" if timeframe == "1d" else "intraday", "timeframe":timeframe, "hold_overnight":timeframe == "1d"}, "rules":{"entry":{"custom_entry_formula":formula,"min_history_bars":history,"setup":"custom_entry"},"exit":{}}, "params":{}}


def test_daily_session_indicators_rejected_but_intraday_allowed():
    daily = validate_definition(definition("price > session_vwap"))
    assert any(e["code"] == "intraday_indicator_on_daily" for e in daily["errors"])
    intraday = validate_definition(definition("price > session_vwap", timeframe="5m"))
    assert not any(e["code"] == "intraday_indicator_on_daily" for e in intraday["errors"])


def test_nested_comparisons_and_crossovers_count_both_periods():
    _, bars, _ = formula_requirements("(rsi(14) < 30 or ema_cross_up(9, 250)) and price > sma200")
    assert bars == 251
    report = validate_definition(definition("ema_cross_up(9, 250)", 210))
    assert any(e["code"] == "insufficient_indicator_history" for e in report["errors"])
    report = validate_definition(definition("ema_cross_up(9, 250)", 251))
    assert not any(e["code"] == "insufficient_indicator_history" for e in report["errors"])


@pytest.mark.parametrize("formula", ["rsi(0) < 30", "sma(1.5) > 0", "ema_cross_up(9, 2001)"])
def test_invalid_periods_rejected_at_compile(formula):
    with pytest.raises(FormulaError, match="whole-number"):
        compile_formula(formula)


@pytest.mark.parametrize("name,bars", [("tema", 900), ("trix", 901), ("adx", 601), ("roc", 301), ("stochrsi_d", 318), ("hma", 317)])
def test_advanced_indicator_history_and_periods(name, bars):
    assert formula_requirements(f"{name}(300) > 0")[1] == bars
    report = validate_definition(definition(f"{name}(300) > 0", bars - 1))
    assert any(e["code"] == "insufficient_indicator_history" for e in report["errors"])
    with pytest.raises(FormulaError, match="whole-number"):
        compile_formula(f"{name}(1.5) > 0")


def test_fixed_parameter_functions_keep_ignored_argument_contract():
    compile_formula("obv(0) > 0 and ad(0) > 0 and psar(0) > 0")

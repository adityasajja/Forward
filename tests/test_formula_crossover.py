"""Crossover functions in the rule language: true only on the bar the cross happens."""

import pandas as pd
import pytest

from forward.signals.formula import FormulaError, build_context, compile_formula


def _frame(closes):
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000})


def _eval(formula, closes):
    return compile_formula(formula).eval(build_context(_frame(closes)))


FALLING_THEN_RISING = [100 - i for i in range(30)] + [70 + 3 * i for i in range(15)]


def test_ema_cross_up_fires_once_on_the_crossing_bar_only():
    fired = [i for i in range(35, len(FALLING_THEN_RISING) + 1) if _eval("ema_cross_up(9, 21)", FALLING_THEN_RISING[:i])]
    assert len(fired) == 1  # not on every bar the fast EMA is above the slow one


def test_being_above_is_not_a_cross():
    assert not _eval("ema_cross_up(9, 21)", [100 + i for i in range(60)])  # steadily rising: fast long since above slow
    assert _eval("ema(9) > ema(21)", [100 + i for i in range(60)])


def test_ema_cross_down_is_the_mirror():
    rising_then_falling = [100 + i for i in range(30)] + [130 - 3 * i for i in range(15)]
    fired = [i for i in range(35, len(rising_then_falling) + 1) if _eval("ema_cross_down(9, 21)", rising_then_falling[:i])]
    assert len(fired) == 1


def test_a_short_history_is_not_a_signal():
    assert not _eval("ema_cross_up(9, 21)", [100 + i for i in range(10)])  # fewer bars than the slow average needs


@pytest.mark.parametrize("bad", ["ema_cross_up(9)", "ema_cross_up(9, 21, 5)", "ema_cross_up(9, x)", "ema(9, 21)", "ema_cross_up(fast=9, slow=21)"])
def test_the_arguments_are_policed(bad):
    with pytest.raises(FormulaError):
        compile_formula(bad)

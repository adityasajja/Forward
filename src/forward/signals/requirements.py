"""Static requirements for the core formula indicators; never evaluates code."""
import ast

SESSION_FIELDS = frozenset({"session_vwap", "vwap_upper_1", "vwap_lower_1", "vwap_upper_2", "vwap_lower_2", "minutes_since_open", "or_high", "or_low", "today_open", "open_eq_high_day", "open_eq_low_day"})
PERIOD_FUNCTIONS = frozenset({"rsi", "sma", "ema", "wma", "atr", "stoch_k", "highest", "lowest"})
PERIOD_FUNCTIONS |= frozenset({"tema", "dema", "hma", "kama", "trima", "zlma", "vwma", "midpoint", "midprice", "cci", "willr", "mfi", "cmo", "roc", "mom", "trix", "adx", "plus_di", "minus_di", "stochrsi_k", "stochrsi_d", "aroon_up", "aroon_down", "vortex_plus", "vortex_minus", "rvi", "coppock", "natr", "stdev", "bbu", "bbl", "bbp", "kc_upper", "kc_lower", "donchian_upper", "donchian_lower", "supertrend", "supertrend_dir", "cmf", "efi", "eom"})
CROSS_FUNCTIONS = frozenset({"ema_cross_up", "ema_cross_down", "sma_cross_up", "sma_cross_down"})
FIXED_HISTORY = {"rsi14": 15, "rsi5": 6, "rsi2": 3, "sma20": 20, "sma50": 50, "sma200": 200, "ema9": 9, "ema21": 21, "ema50": 50, "atr14": 15, "roc20": 21, "vol_avg20": 20, "peak20": 20, "peak60": 60, "macd_hist": 35, "bb_width_pct": 20}


def indicator_history(name: str, period: int) -> int:
    """Minimum input for the configured smoothing chain, including library defaults."""
    if name in {"tema", "trix"}:
        return 3 * period + (1 if name == "trix" else 0)
    if name in {"dema", "adx"}:
        return 2 * period + 1
    if name in {"stochrsi_k", "stochrsi_d"}:
        return 14 + period + (2 if name.endswith("_k") else 4)
    if name == "hma":
        return period + int(period ** 0.5)
    if name == "coppock":
        return period + 14
    if name == "rvi":
        return 2 * period
    if name in CROSS_FUNCTIONS or name in {"rsi", "atr", "natr", "roc", "mom", "cmo", "mfi", "efi", "eom", "plus_di", "minus_di", "aroon_up", "aroon_down", "vortex_plus", "vortex_minus", "supertrend", "supertrend_dir", "kama", "kc_upper", "kc_lower"}:
        return period + 1
    return period


def formula_requirements(source: str) -> tuple[set[str], int, list[str]]:
    try:
        tree = ast.parse(source or "", mode="eval")
    except (SyntaxError, TypeError):
        return set(), 1, []  # Syntax is owned by compile_formula.
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    need = max((FIXED_HISTORY.get(name, 1) for name in names), default=1)
    errors = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name not in PERIOD_FUNCTIONS | CROSS_FUNCTIONS:
            continue
        values = [a.value for a in node.args if isinstance(a, ast.Constant)]
        if len(values) != len(node.args) or not values:
            continue
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not float(v).is_integer() or not 1 <= v <= 2000 for v in values):
            errors.append(f"{name}: use whole-number periods from 1 to 2000 bars")
            continue
        need = max(need, indicator_history(name, int(max(values))))
    return names & SESSION_FIELDS, need, errors

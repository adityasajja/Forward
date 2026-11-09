from forward.signals.strategy import SignalEntryStrategy
from forward.strategy.python_import import compile_python_strategy


class PythonRules(SignalEntryStrategy):
    name = "python_rules"

    def __init__(self, source="", **params):
        rules = compile_python_strategy(source)
        history = max(rules["entry"]["min_history_bars"], rules["exit"]["min_history_bars"])
        # Formula source is pinned by the version; run params may change sizing,
        # but cannot replace the imported entry/exit logic.
        super().__init__(**{**params, **rules["entry"], **rules["exit"], "lookback": max(history + 10, 260)})

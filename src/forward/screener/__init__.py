"""The screener.

A user defines a condition tree, the tree is evaluated against the cached NSE
dailies, the survivors are ranked, and **every survivor explains itself**.

The last part is the point. A screener that returns symbols without saying why
is a list you have to take on faith, and a screen you cannot audit is
indistinguishable from a hunch — the same standard this codebase applies to
signals and to backtests.

Layout
------

``indicators``  the indicator catalog: every named value a condition can test,
                each one a thin declaration over ``forward.strategy.indicators``.
``conditions``  the condition tree — leaves, groups, operators — and its
                evaluation to ``(matched, evidence)``.
``service``     universe loading, the scan itself, ranking, and saved scans.

The indicator maths is **not** reimplemented here. Every series comes from
``forward.strategy.indicators``, which is what makes a screener hit and a backtest
signal agree about what RSI 14 means.
"""

from forward.core.errors import ScreenerError
from forward.screener.conditions import (
    ConditionError,
    Evidence,
    Group,
    Leaf,
    Node,
    count_nodes,
    evaluate_node,
    evaluate_symbol,
    iter_leaves,
    parse_node,
    validate_tree,
)
from forward.screener.indicators import (
    AVAILABLE_INDICATORS,
    INDICATOR_CATALOG,
    INDICATOR_INDEX,
    IndicatorSpec,
    indicator_series,
    last_value,
    resolve_indicator,
)
from forward.screener.service import ScreenerService

__all__ = [
    "AVAILABLE_INDICATORS",
    "ConditionError",
    "Evidence",
    "Group",
    "INDICATOR_CATALOG",
    "INDICATOR_INDEX",
    "IndicatorSpec",
    "Leaf",
    "Node",
    "ScreenerError",
    "ScreenerService",
    "count_nodes",
    "evaluate_node",
    "evaluate_symbol",
    "indicator_series",
    "iter_leaves",
    "last_value",
    "parse_node",
    "resolve_indicator",
    "validate_tree",
]

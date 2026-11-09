"""Import every SQL table definition before database initialization.

The import order preserves the original metadata registration sequence. SQLAlchemy
resolves string foreign keys after all owning modules have registered their tables.
"""

from __future__ import annotations

import forward.appdb.account_schema as _account_schema  # noqa: F401
import forward.appdb.audit_schema as _audit_schema  # noqa: F401
import forward.appdb.execution_schema as _execution_schema  # noqa: F401
import forward.appdb.options_schema as _options_schema  # noqa: F401
import forward.appdb.portfolio_schema as _portfolio_schema  # noqa: F401
import forward.appdb.research_schema as _research_schema  # noqa: F401
import forward.appdb.screener_schema as _screener_schema  # noqa: F401
import forward.appdb.strategy_backtest_schema as _strategy_backtest_schema  # noqa: F401
import forward.appdb.system_schema as _system_schema  # noqa: F401
import forward.appdb.trade_schema as _trade_schema  # noqa: F401
import forward.appdb.watchlists_schema as _watchlists_schema  # noqa: F401

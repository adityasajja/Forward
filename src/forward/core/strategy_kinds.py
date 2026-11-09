"""Shared names for supported strategy kinds."""

from __future__ import annotations

import re

OPTIONS_STRATEGY_KIND = "options"
DEFAULT_STRATEGY_KIND = "rules"
STRATEGY_KINDS = ("nocode", "rules", "code", OPTIONS_STRATEGY_KIND)
STRATEGY_KIND_PATTERN = "(?i)^(?:" + "|".join(re.escape(kind) for kind in STRATEGY_KINDS) + ")$"

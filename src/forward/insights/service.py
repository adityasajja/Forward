"""Build the day's read and send it: market scenario, buy ideas, your holdings.

One digest, once a day after the close, because the data is daily and nothing new exists
between closes. The page shows the same thing on demand. Every line is either a description
of the current state or a number measured on the local history, with its limits attached.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import UTC, date, datetime
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict

from forward.core.insights_ports import (
    InsightsHoldingsPort,
    InsightsStateStoragePort,
    InsightsWatchlistPort,
)
from forward.core.market_intel_ports import (
    MarketIntelDailyBarsPort,
    MarketIntelSummaryPort,
)
from forward.core.session import IST
from forward.insights.evidence import DISCLAIMER
from forward.insights.ideas import buy_ideas
from forward.insights.market import changes_since, describe_market, signature
from forward.insights.stall import StallConfig
from forward.insights.watch import WatchConfig, watch_holding


class InsightsSettings(BaseModel):
    """User-selected parts of the daily market digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    market_changes: bool
    buy_ideas: bool
    holdings_watch: bool
    max_buy_ideas: int
    send_after: str


class InsightsService:
    def __init__(
        self,
        holdings: InsightsHoldingsPort,
        storage: InsightsStateStoragePort,
        watchlists: InsightsWatchlistPort,
        market_intel: MarketIntelSummaryPort,
        market_data: MarketIntelDailyBarsPort,
        stall_config: StallConfig,
        watch_config: WatchConfig,
        default_settings: InsightsSettings,
        stale_after_days: int,
        cache_ttl_seconds: float,
    ) -> None:
        """Create the insights use case with its configured data and market reader.

        Args:
            holdings: Port that reads current holdings and reports their source.
            storage: Port for insight settings and delivery state.
            watchlists: Port that reads symbols from the user's watchlists.
            market_intel: Port that provides current market measurements.
            market_data: Port that provides cached bars for holdings checks.
            stall_config: Required periods and thresholds for quiet-holding analysis.
            watch_config: Required thresholds for holding descriptions.
            default_settings: Required settings used before a user saves preferences.
            stale_after_days: Age after which the market data is stale.
            cache_ttl_seconds: Lifetime of a cached digest.
        """
        self._holdings = holdings
        self._storage = storage
        self._watchlists = watchlists
        self._market_intel = market_intel
        self._market_data = market_data
        self._stall_config = stall_config
        self._watch_config = watch_config
        self._default_settings = default_settings
        self._stale_after_days = stale_after_days
        self._cache_ttl_seconds = cache_ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict[str | None, tuple[float, dict[str, Any]]] = {}

    # ------------------------------------------------------------------ settings + state
    def settings(self) -> InsightsSettings:
        saved = self._storage.load_settings()
        if saved is not None:
            return InsightsSettings(**saved)
        return self._default_settings

    def save_settings(self, settings: InsightsSettings) -> InsightsSettings:
        self._storage.save_settings(settings.model_dump())
        with self._lock:
            self._cache.clear()
        return settings

    def _state(self) -> dict[str, Any]:
        return dict(self._storage.load_state())

    def _save_state(self, state: dict[str, Any]) -> None:
        self._storage.save_state(state)

    # ------------------------------------------------------------------ the read
    def build(self, *, user_id: str | None = None, client: Any | None = None, fresh: bool = False) -> dict[str, Any]:
        """The day's read. Cached for two minutes so opening the page is cheap."""
        now = time.monotonic()
        with self._lock:
            expired_users = [
                cached_user_id
                for cached_user_id, (cached_at, _digest) in self._cache.items()
                if now - cached_at >= self._cache_ttl_seconds
            ]
            for cached_user_id in expired_users:
                del self._cache[cached_user_id]
            cached = self._cache.get(user_id)
            if cached and not fresh:
                return cached[1]

        summary, _sectors, contexts = self._market_intel.compute_all()
        sd = summary.as_dict()
        market = describe_market(sd)
        sig = signature(market)
        state = self._state()
        changes = changes_since(state.get("market"), sig)

        watchlist = self._watchlist_symbols(user_id)
        nifty_1m_pct = sd.get("nifty_1m_return_pct")
        ideas_available = nifty_1m_pct is not None and math.isfinite(float(nifty_1m_pct))
        ideas = (
            buy_ideas(
                contexts,
                nifty_1m_pct=float(nifty_1m_pct),
                regime=market["regime"],
                watchlist=watchlist,
                limit=self.settings().max_buy_ideas,
            )
            if ideas_available
            else []
        )
        last_ideas = set(state.get("ideas", []))
        for idea in ideas:
            idea["is_new"] = idea["symbol"] not in last_ideas

        held = self._holdings.load(source="LIVE", client=client)
        items = []
        for h in held["holdings"]:
            ctx = contexts.get(h["symbol"])
            try:
                item = watch_holding(
                    h,
                    self._market_data.load_frame(h["symbol"]),
                    self._stall_config,
                    self._watch_config,
                    rs_20d=ctx.relative_strength_nifty_20d if ctx else None,
                )
            except ValueError as exc:
                item = {
                    "symbol": str(h["symbol"]).replace("-EQ", ""),
                    "qty": h.get("qty"),
                    "flags": [],
                    "assessment_available": False,
                    "assessment_error": str(exc),
                }
            items.append(item)
        items.sort(key=lambda r: (not any(f["kind"] == "slipped" for f in r["flags"]), -len(r["flags"])))

        data_as_of = market.get("stocks_as_of")
        stale_days = None
        if data_as_of:
            stale_days = (datetime.now(IST).date() - date.fromisoformat(data_as_of)).days

        digest = {
            "as_of": datetime.now(UTC).isoformat(),
            "data_as_of": data_as_of,
            "stale": stale_days is not None and stale_days > self._stale_after_days,
            "stale_days": stale_days,
            "market": market,
            "changes": changes,
            "ideas": ideas,
            "ideas_available": ideas_available,
            "ideas_error": None if ideas_available else "NIFTY 1-month return is unavailable.",
            "holdings": {"source": held["source"], "as_of": held["as_of"], "items": items},
            "disclaimer": DISCLAIMER,
            "last_sent": state.get("last_sent"),
            "_signature": sig,
        }
        with self._lock:
            self._cache[user_id] = (time.monotonic(), digest)
        return digest

    def _watchlist_symbols(self, user_id: str | None) -> set[str]:
        if not user_id:
            return set()
        return self._watchlists.symbols_for_user(user_id)

    # ------------------------------------------------------------------ the message
    def render(self, digest: dict[str, Any], settings: InsightsSettings | None = None) -> tuple[str, str]:
        settings = settings or self.settings()
        m = digest["market"]
        lines = [f"Market: {m['word']}. {m['headline']}", m["context"]]
        if settings.market_changes and digest["changes"]:
            lines += ["", "What changed:"] + [f"- {c}" for c in digest["changes"]]
        if settings.buy_ideas and digest["ideas"]:
            lines += ["", "Buy ideas (setups that held up in history):"]
            for i in digest["ideas"]:
                tag = " (new)" if i.get("is_new") else ""
                fact = (
                    f"down {abs(i['move_20d_pct']):.0f}% in 20 days"
                    if i["setup"] == "oversold"
                    else f"{i['vs_nifty_20d_pct']:+.0f}% vs Nifty over 20 days"
                )
                lines.append(
                    f"- {i['symbol']}{tag}: {i['setup_label'].lower()}, {fact}. "
                    f"A stop near {i['stop_price']:,.2f} ({i['stop_pct']}% below)."
                )
        if settings.holdings_watch:
            flagged = [h for h in digest["holdings"]["items"] if h["flags"]]
            if flagged:
                lines += ["", "Your holdings:"]
                for h in flagged:
                    lines.append(f"- {h['symbol']}: " + "; ".join(f["title"].lower() + " - " + f["detail"] for f in h["flags"]))
        if digest["data_as_of"]:
            note = f"Stock data as of {date.fromisoformat(digest['data_as_of']):%d %b}"
            note += f" ({digest['stale_days']} days old)." if digest["stale"] else "."
            lines += ["", note, digest["disclaimer"]]
        return "Today's read", "\n".join(lines)

    # ------------------------------------------------------------------ sending
    def send(self, *, user_id: str | None = None, client: Any | None = None, force: bool = False) -> dict[str, Any]:
        from forward.alerts.channels import channels_from_settings
        from forward.config.settings import get_settings

        settings = self.settings()
        if not settings.enabled and not force:
            return {"sent": False, "reason": "disabled"}
        digest = self.build(user_id=user_id, client=client, fresh=True)
        title, body = self.render(digest, settings)
        sent = False
        used = None
        for channel in channels_from_settings(get_settings()):
            try:
                if channel.send(title, body):
                    sent, used = True, type(channel).__name__
                    break
            except Exception as exc:  # noqa: BLE001 - try the next channel
                logger.warning("insights send via {} failed: {}", type(channel).__name__, exc)
        if sent:
            state = self._state()
            state.update(
                {
                    "last_sent": datetime.now(UTC).isoformat(),
                    "last_sent_date": datetime.now(IST).date().isoformat(),
                    "market": digest["_signature"],
                    "ideas": [i["symbol"] for i in digest["ideas"]],
                }
            )
            self._save_state(state)
            with self._lock:
                self._cache.clear()
        return {"sent": sent, "channel": used, "body": body}

    def maybe_send_daily(self, client: Any | None = None) -> dict[str, Any] | None:
        """Called on a timer: send once per weekday after the configured time."""
        settings = self.settings()
        now = datetime.now(IST)
        if not settings.enabled or now.weekday() >= 5:
            return None
        hh, mm = (int(x) for x in settings.send_after.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return None
        if self._state().get("last_sent_date") == now.date().isoformat():
            return None
        return self.send(client=client)

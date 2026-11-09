"""Is the paper trading actually able to trade? One read that answers the questions that were hard to answer by hand.

A quiet paper account looks the same whether the market was quiet or something was broken: no signal, no price
history for the stocks being watched, or a stale cache. This turns those into plain facts per deployment (how many of
its stocks have history, how fresh it is, how many entry signals fired lately).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

import pandas as pd

from forward.config.settings import get_settings
from forward.core.calendar_ports import MarketCalendarPort
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.live_market_data_ports import MarketDataPort
from forward.core.paper_health_ports import PaperHealthStoragePort
from forward.core.paper_runner_ports import PaperHealthRunnerPort
from forward.core.session import IST

logger = logging.getLogger("forward.services.paper_health")

_RUNNER_UNAVAILABLE = "The paper runner is not running, so nothing is being evaluated."


def _pending_result(
    now: datetime,
    calendar: MarketCalendarPort,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a pending health result when runner status is unavailable.

    Args:
        now: Time used for this health response.
        calendar: Exchange calendar used to report market hours.
        previous: Last completed health result, when one exists.

    Returns:
        A pending result with the last deployment details kept for context.
    """
    result = dict(previous or {})
    problems = list(result.get("problems", []))
    if _RUNNER_UNAVAILABLE not in problems:
        problems.append(_RUNNER_UNAVAILABLE)
    runner = dict(result.get("runner") or {})
    runner.update(
        running=False,
        in_market_hours=calendar.is_market_open(now),
    )
    result.update(
        pending=True,
        as_of=now.isoformat(),
        runner=runner,
        deployments=result.get("deployments", {}),
        problems=problems,
    )
    return result


def _runner_is_running(runner: PaperHealthRunnerPort | None) -> bool:
    """Return whether the paper runner reports that it is available.

    Args:
        runner: Runner port supplied by the application composition layer.

    Returns:
        Whether the runner status call completed and reported a running process.
    """
    if runner is None:
        return False
    try:
        return bool(runner.status().get("running"))
    except Exception:  # noqa: BLE001 - unavailable runner status keeps health pending
        logger.exception("paper runner status is unavailable")
        return False


def _deployment_health(
    row: dict[str, Any],
    frames: dict[tuple[str, str], pd.DataFrame | None],
    now: datetime,
    cal: Any,
    *,
    market_data: MarketDataPort,
    history: CachedMarketFramesPort,
    runner: PaperHealthRunnerPort,
    signal_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]],
    prepared: dict | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    from forward.paper.config import RunnerConfig
    from forward.services.data_status import expected_session, sessions_behind
    from forward.signals.rules import eval_entry

    cfg = RunnerConfig.from_deployment(
        row,
        platform_interval_seconds=settings.runner_interval_seconds,
        platform_lookback_days=settings.runner_lookback_days,
    )
    symbols = list(cfg.symbols)
    from forward.strategy.definition import parse_definition, resolve_rules

    definition, _ = parse_definition(row.get("definition"))
    if (definition or {}).get("engine_key") == "intraday_vwap_mean_reversion":
        bars = market_data.observed_bars(now).get("bars", {})
        have = sum(len(bars.get(s, [])) >= 21 for s in symbols)
        rules_ok = resolve_rules(definition).ok
        return {
            "watching": len(symbols),
            "with_data": have,
            "stale": 0,
            "data_through": None,
            "rules_ok": rules_ok,
            "signals_recent": None,
            "signal_symbols": [],
            "sessions": 0,
            "problems": [] if rules_ok else ["VWAP definition is invalid"],
            "data_requirement": "Complete observed five-minute session bars, aligned Nifty and fresh bid/ask; gaps skip",
        }
    if (definition or {}).get("engine_key") == "nifty_opening_sector_momentum":
        opening = market_data.opening_snapshot(now.date().isoformat())
        have = sum(s in opening["stocks"] for s in symbols)
        rules_ok = resolve_rules(definition).ok
        return {
            "watching": len(symbols),
            "with_data": have,
            "stale": 0,
            "data_through": None,
            "rules_ok": rules_ok,
            "signals_recent": None,
            "signal_symbols": [],
            "sessions": 0,
            "problems": [] if rules_ok else ["Opening-sector definition is invalid"],
            "data_requirement": "Fresh 09:15 index and stock opening observations; incomplete days are skipped",
        }
    expected = expected_session(now, cal)
    prepared = {} if prepared is None else prepared
    have, stale, last_bar = 0, 0, None
    closed_frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        ex = cfg.exchange_of(sym)
        key = (sym, ex)
        if key not in frames:
            try:
                frames[key] = history.daily_frame(sym, ex)
            except Exception:  # noqa: BLE001
                frames[key] = None
        f = frames[key]
        if f is None or f.empty or "ts" not in f.columns:
            continue
        have += 1
        prepared_key = (sym, ex, expected)
        if prepared_key not in prepared:
            days = pd.to_datetime(f["ts"]).dt.date
            closed = f.loc[days <= expected].reset_index(drop=True)  # completed sessions only
            last = pd.Timestamp(closed["ts"].iloc[-1]).date() if not closed.empty else None
            behind = sessions_behind(last, expected, cal) if last is not None else 0
            prepared[prepared_key] = (closed, last, behind)
        closed, last, behind = prepared[prepared_key]
        if closed.empty:
            continue
        last_bar = last if last_bar is None or last > last_bar else last_bar
        if behind > 0:
            stale += 1
        closed_frames[sym] = closed

    resolved = runner.resolve_rules(row)
    signals: dict[str, Any] = {"count": None, "symbols": []}
    if resolved is not None:
        cache_key = (str(row["deployment_id"]), str(last_bar))
        hit = signal_cache.get(cache_key)
        if hit and time.time() - hit[0] < settings.paper_health_signal_cache_ttl_seconds:
            signals = hit[1]
        else:
            found: dict[str, int] = {}
            for sym, closed in closed_frames.items():
                for k in range(settings.paper_health_recent_sessions):
                    part = closed.iloc[: len(closed) - k]
                    if len(part) < 60:
                        break
                    try:
                        if eval_entry(sym, part, resolved[0]):
                            found[sym] = found.get(sym, 0) + 1
                    except Exception:  # noqa: BLE001 - one bad frame must not hide the rest
                        break
            signals = {"count": sum(found.values()), "symbols": sorted(found)[:6]}
            signal_cache[cache_key] = (time.time(), signals)

    problems: list[str] = []
    if resolved is None:
        problems.append("Its rules could not be loaded, so it cannot trade.")
    if symbols and have == 0:
        problems.append("None of its stocks has price history.")
    elif have < len(symbols):
        problems.append(f"{len(symbols) - have} of {len(symbols)} stocks have no price history.")
    if stale:
        problems.append(f"Price history is behind for {stale} stock{'s' if stale != 1 else ''}.")
    return {
        "watching": len(symbols),
        "with_data": have,
        "stale": stale,
        "data_through": last_bar.isoformat() if last_bar else None,
        "rules_ok": resolved is not None,
        "signals_recent": signals["count"],
        "signal_symbols": signals["symbols"],
        "sessions": settings.paper_health_recent_sessions,
        "problems": problems,
    }


def _compute_health(
    user_id: str,
    market_data: MarketDataPort,
    history: CachedMarketFramesPort,
    storage: PaperHealthStoragePort,
    runner: PaperHealthRunnerPort,
    signal_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]],
    calendar: MarketCalendarPort,
) -> dict[str, Any]:
    now = datetime.now(IST)
    if not _runner_is_running(runner):
        return _pending_result(now, calendar)
    rows = storage.health_deployments(user_id)

    frames: dict[tuple[str, str], pd.DataFrame | None] = {}
    prepared: dict = {}  # Reuse completed-bar preparation only within this health refresh.
    per: dict[str, Any] = {}
    for r in rows:
        try:
            per[r["deployment_id"]] = _deployment_health(
                r,
                frames,
                now,
                calendar,
                market_data=market_data,
                history=history,
                runner=runner,
                signal_cache=signal_cache,
                prepared=prepared,
            )
        except Exception as exc:  # noqa: BLE001 - one deployment must not blank the page
            logger.exception("paper health failed for %s", str(r.get("deployment_id"))[:8])
            per[r["deployment_id"]] = {
                "problems": [f"Could not be checked: {exc}"],
                "watching": 0,
                "with_data": 0,
                "stale": 0,
                "rules_ok": False,
            }

    problems: list[str] = []
    broken = [d for d in per.values() if d.get("problems")]
    if broken:
        problems.append(
            f"{len(broken)} of {len(per)} strategies {'has' if len(broken) == 1 else 'have'} a data or rules problem. Open {'it' if len(broken) == 1 else 'them'} to see what."
        )
    if not _runner_is_running(runner):
        return _pending_result(now, calendar)
    return {
        "pending": False,
        "as_of": now.isoformat(),
        "runner": {
            "running": True,
            "in_market_hours": calendar.is_market_open(now),
        },
        "deployments": per,
        "problems": problems,
    }


class PaperHealthService:
    """Check whether saved paper deployments have the data and runner they need.

    The service owns its short-lived result cache and background refresh state.
    Its ports are supplied by the composition layer, so API routes do not build
    their own health workflow.
    """

    def __init__(
        self,
        market_data: MarketDataPort,
        history: CachedMarketFramesPort,
        storage: PaperHealthStoragePort,
        runner: PaperHealthRunnerPort | None,
        calendar: MarketCalendarPort,
    ) -> None:
        """Create the health service with the app's market and storage ports.

        Args:
            market_data: Live observations used by intraday deployments.
            history: Cached daily bars used by historical health checks.
            storage: Deployment and strategy definitions from persistence.
            runner: Process runner, or ``None`` when startup could not create it.
            calendar: Exchange calendar used to measure data freshness and session hours.
        """
        self._market_data = market_data
        self._history = history
        self._storage = storage
        self._runner = runner
        self._calendar = calendar
        self._lock = threading.Lock()
        self._signal_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._results: dict[str, tuple[float, dict[str, Any]]] = {}
        self._inflight: set[str] = set()

    def _refresh(self, user_id: str) -> None:
        """Compute and cache health without blocking an API request."""
        runner = self._runner
        if runner is None:
            return
        try:
            result = _compute_health(
                user_id,
                self._market_data,
                self._history,
                self._storage,
                runner,
                self._signal_cache,
                self._calendar,
            )
            with self._lock:
                self._results[user_id] = (time.time(), result)
        except Exception:  # noqa: BLE001
            logger.exception("paper health refresh failed")
        finally:
            with self._lock:
                self._inflight.discard(user_id)

    def _refresh_async(self, user_id: str) -> None:
        """Start one background refresh for an account."""
        if self._runner is None:
            return
        with self._lock:
            if user_id in self._inflight:
                return
            self._inflight.add(user_id)
        threading.Thread(
            target=self._refresh,
            args=(user_id,),
            daemon=True,
            name="paper-health",
        ).start()

    def build_health(self, user_id: str) -> dict[str, Any]:
        """Return cached health and schedule a refresh when its state is old.

        Args:
            user_id: Account whose paper deployments should be checked.

        Returns:
            The latest health snapshot. It is pending while the runner is
            unavailable or while the first complete check is still running.
        """
        if self._runner is None:
            return _pending_result(datetime.now(IST), self._calendar)
        runner_running = _runner_is_running(self._runner)
        with self._lock:
            hit = self._results.get(user_id)
        if not runner_running:
            previous = hit[1] if hit else None
            return _pending_result(datetime.now(IST), self._calendar, previous)
        if (
            hit
            and time.time() - hit[0] < get_settings().paper_health_result_ttl_seconds
            and not hit[1].get("pending", False)
        ):
            return hit[1]
        self._refresh_async(user_id)
        if hit:
            return {**hit[1], "refreshing": True}
        return {
            "pending": True,
            "problems": [],
            "deployments": {},
            "runner": {"running": True},
        }

    def warm(self) -> None:
        """Refresh health for accounts with running deployments at app startup."""
        runner = self._runner
        if runner is None:
            return
        users = {row["user_id"] for row in runner.running_deployments()}
        for user_id in users:
            self._refresh_async(user_id)

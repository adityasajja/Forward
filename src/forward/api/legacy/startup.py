"""Startup work: warm the caches, start the paper runner and the background loops."""

from __future__ import annotations

import asyncio
import logging
import threading

from forward.core.live_market_data_ports import MarketDataPort
from forward.core.paper_runner_ports import PaperHealthRunnerPort

logger = logging.getLogger("forward.api")

#: Set when the first Markets pass has finished (or failed), so the slower breadth
#: warm-up can queue behind it instead of competing for the CPU.
_MARKET_INTEL_WARMED = threading.Event()


async def on_startup() -> None:
    from forward.api.stream import get_broadcaster
    from forward.wiring.alerts import get_intelligent_alert_monitor

    get_broadcaster().set_loop(asyncio.get_running_loop())
    await get_intelligent_alert_monitor().start()
    market_data = get_broadcaster()
    runner = _start_paper_runner(market_data=market_data)
    _warm_paper_health(runner=runner, market_data=market_data)
    _warm_breadth_cache()
    _warm_market_intel()
    _warm_instrument_master()
    _warm_setup_stats()
    asyncio.create_task(_insights_loop())
    asyncio.create_task(_market_history_loop())
    asyncio.create_task(_jobs_loop())
    asyncio.create_task(_screener_watch_loop())


def _warm_paper_health(
    *, runner: PaperHealthRunnerPort | None, market_data: MarketDataPort
) -> None:
    """Warm paper health in the background before the first page request.

    Args:
        runner: Started paper runner, or ``None`` when startup did not start it.
        market_data: Shared stream used by the health check.
    """
    if runner is None:
        return
    from forward.composition import get_paper_health_service

    threading.Thread(
        target=get_paper_health_service(market_data).warm,
        daemon=True,
        name="paper-health-warm",
    ).start()


async def _screener_watch_loop() -> None:
    """Re-run watched scans when their interval is up. Checked every 30 seconds."""
    from forward.composition import get_screener_watch_service

    await asyncio.sleep(90)  # let startup finish first
    while True:
        try:
            await asyncio.to_thread(get_screener_watch_service().run_due)
        except Exception:  # noqa: BLE001 - a failed pass must not stop the loop
            logger.exception("screener watch pass failed")
        await asyncio.sleep(30)


async def _jobs_loop() -> None:
    """Run scheduled maintenance, research, and account-recording jobs."""
    from forward.config.settings import research_data_root
    from forward.jobs.daily import DailyJob, JobStore, run_forever
    from forward.jobs.research import build_research_jobs

    data_root = research_data_root()
    from forward.composition import (
        get_backup_service,
        get_daily_market_maintenance_service,
    )
    from forward.wiring.market_data import get_cached_market_frames_port

    market_history = get_cached_market_frames_port()
    backup = DailyJob(
        "backup",
        get_backup_service().run,
        at=(2, 30),
    )
    # Record data quality without filling gaps from another source.
    data_check = DailyJob(
        "data_check",
        lambda: get_daily_market_maintenance_service().check_data(data_root),
        at=(17, 10),
        weekdays_only=True,
    )
    from forward.composition import get_insights_holdings_port
    from forward.services.pnl_calendar import refresh_real

    def record_real_pnl() -> None:
        from forward.wiring.broker_access import get_authenticated_broker_client

        client = get_authenticated_broker_client()
        holdings = get_insights_holdings_port().load(source="LIVE", client=client)["holdings"]
        from forward.composition import get_pnl_calendar_data
        from forward.config.settings import get_settings

        refresh_real(
            get_pnl_calendar_data(),
            holdings,
            sessions=get_settings().pnl_calendar_backfill_sessions,
        )

    def capture_tradebook() -> None:
        from forward.wiring.broker_access import get_authenticated_broker_client
        from forward.wiring.broker_data import build_tradebook_capture_service

        build_tradebook_capture_service(get_authenticated_broker_client(), data_root).capture()

    real_pnl = DailyJob("real_pnl", record_real_pnl, at=(17, 50), weekdays_only=True)
    # After the close and before the day-scoped records reset; tried again if the login is not live.
    tradebook = DailyJob(
        "tradebook",
        capture_tradebook,
        at=(15, 45),
        weekdays_only=True,
        retries=6,
        retry_minutes=20,
    )
    jobs = [
        backup,
        data_check,
        *build_research_jobs(data_root, market_history),
        real_pnl,
        tradebook,
    ]
    await run_forever(jobs, JobStore(data_root))


async def _market_history_loop() -> None:
    """Keep BSE and NSE SME daily history current, from the exchanges' own end-of-day files."""
    from forward.composition import get_daily_market_maintenance_service
    from forward.config.settings import research_data_root

    data_root = research_data_root()

    def run() -> None:
        result = get_daily_market_maintenance_service().sync_exchange_history(data_root)
        if result is None:
            return
        logger.info("market history sync: %s", result)

    await asyncio.sleep(180)  # let API startup finish first
    while True:
        try:
            await asyncio.to_thread(run)
        except Exception:  # noqa: BLE001 - a failed sync must not stop the loop
            logger.exception("market history sync failed")
        await asyncio.sleep(1800)


async def _insights_loop() -> None:
    """Send the day's read once, after the close. Checked every five minutes."""
    from forward.composition import get_insights_service

    def run():
        from forward.wiring.broker_access import get_authenticated_broker_client

        client = get_authenticated_broker_client()
        return get_insights_service().maybe_send_daily(client=client)

    while True:
        await asyncio.sleep(300)
        try:
            await asyncio.to_thread(run)
        except Exception:  # noqa: BLE001 - a failed send must not stop the loop
            logger.exception("daily insights send failed")


def _start_paper_runner(*, market_data: MarketDataPort) -> PaperHealthRunnerPort | None:
    """Start the continuous paper-trading loop.

    The runner is what turns the paper engine from something a request drives
    into something that runs: without it, a deployment marked RUNNING evaluates
    nothing and fills nothing, and the dashboard shows a live strategy that is
    actually inert.

    Best-effort, never fatal. A runner that cannot start must not take the API
    down with it — the deployment routes and the health endpoint are how an
    operator finds out what is wrong, and ``GET /api/v1/paper/runner`` reports
    ``running: false`` rather than pretending the loop is up.
    """
    try:
        from forward.composition import start_paper_runner

        runner = start_paper_runner(market_data=market_data)
        logger.info("paper runner started")
        return runner
    except Exception as exc:  # noqa: BLE001 — the API must still serve
        logger.warning("paper runner could not start: %s", exc)
        return None


def _warm_instrument_master() -> None:
    """Build the instrument index off the request path.

    A cold build reads 3,000+ parquet footers and takes ~8s. Paying that on the
    first watchlist or search request would look like a hung page, so it is paid
    at startup on a daemon thread — the same pattern as the breadth cache below.
    """
    try:
        from forward.composition import get_instrument_directory_service

        get_instrument_directory_service().warm()
    except Exception as exc:  # noqa: BLE001 — best effort, never fatal
        logger.warning("instrument master warm-up could not start: %s", exc)


def _warm_setup_stats() -> None:
    """Replay every intelligent-alert rule's history off the request path.

    ~60s over a few hundred symbols to answer "how long does this setup
    usually take, and how often does it actually reach target" — see
    `forward.signals_stats`. Paid once here, in the background, rather than on
    whichever request first asks for it.
    """
    try:
        import threading

        from forward.wiring.alerts import get_setup_stats_service

        # A 30-60s CPU job; held back so the pages a user opens first (markets,
        # watchlist) are not competing with it for the first minute.
        timer = threading.Timer(
            90.0,
            lambda: get_setup_stats_service().warm(background=True),
        )
        timer.daemon = True
        timer.start()
    except Exception as exc:  # noqa: BLE001 — best effort, never fatal
        logger.warning("setup-stats warm-up could not start: %s", exc)


def _warm_market_intel() -> None:
    """Run the full-universe market pass once at startup, off the request path.

    The first Markets or Today visit otherwise pays ~5s reading ~500 parquet files.
    """
    import threading

    def work() -> None:
        try:
            from forward.wiring.market_intel import get_market_intel_service

            get_market_intel_service().compute_all()
        except Exception as exc:  # noqa: BLE001 - best effort, never fatal
            logger.warning("market intelligence warm-up failed: %s", exc)
        finally:
            _MARKET_INTEL_WARMED.set()

    threading.Thread(target=work, daemon=True, name="forward-market-intel-warm").start()


def _warm_breadth_cache() -> None:
    """Compute the 5-session breadth series once, off the request path.

    Scoring a universe sample five times takes ~15s. Paying that on the first
    dashboard poll would look like a hung page, so it is paid at startup on a
    daemon thread instead — by the time a browser connects, the answer is
    usually already cached.
    """
    import threading

    def work() -> None:
        try:
            import time as _t

            from forward.composition import get_dashboard_summary_service

            # Behind the Markets pass: both are CPU-bound Python, and run together
            # each takes several times longer, so the page opened first waited on both.
            _MARKET_INTEL_WARMED.wait(timeout=60)
            t0 = _t.monotonic()
            get_dashboard_summary_service().breadth_trend("NSEEQ")
            logger.info("breadth cache warmed in %.1fs", _t.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001 — best effort, never fatal
            logger.warning("breadth warm-up failed: %s", exc)

    threading.Thread(target=work, daemon=True, name="forward-breadth-warm").start()

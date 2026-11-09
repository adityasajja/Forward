"""Command line entrypoint.

    uv run forward backtest --strategy sma_crossover --source synthetic
    uv run forward research --strategy sma_crossover --train 5000 --test 1250
    uv run forward login
    uv run forward instruments sync --exchanges NSEEQ,NSEFO
    uv run forward live --symbols NSEFO:NIFTY-I
    uv run forward serve
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from forward.commands.alerts import run_alerts
from forward.commands.backtest import run_backtest, run_research
from forward.commands.briefing import run_briefing
from forward.commands.broker import run_instruments, run_login, run_quote
from forward.commands.gpu import run_gpu
from forward.commands.history import run_history
from forward.commands.learning import run_learn
from forward.commands.maintenance import run_backup, run_data_check, run_restore
from forward.commands.portfolio import run_portfolio
from forward.commands.signals import run_signals
from forward.commands.strategy import run_strategy
from forward.core.strategy_kinds import DEFAULT_STRATEGY_KIND, STRATEGY_KINDS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forward", description="Algo trading backend")
    sub = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------
    bt = sub.add_parser("backtest", help="run a backtest")
    bt.add_argument("--strategy", default="sma_crossover")
    bt.add_argument("--source", default="synthetic", choices=["synthetic", "csv", "parquet"])
    bt.add_argument("--path", help="directory or file for csv/parquet sources")
    bt.add_argument(
        "--instruments",
        help="JSON array of complete instrument records, required for csv/parquet sources",
    )
    bt.add_argument("--symbols", default="AAPL,MSFT")
    bt.add_argument("--cash", type=float, default=1_000_000.0)
    bt.add_argument("--fast", type=int, default=20)
    bt.add_argument("--slow", type=int, default=50)
    bt.add_argument("--futures", action="store_true", help="model symbols as futures (margin)")
    bt.add_argument("--square-off-eod", action="store_true")
    bt.add_argument("--slippage-bps", type=float, default=5.0)
    bt.add_argument(
        "--max-daily-loss",
        type=float,
        default=None,
        help="halt trading after this much loss in a day (default: 10%% of starting cash)",
    )
    bt.add_argument("--save", help="write equity curve to this CSV path")

    # ------------------------------------------------------------------
    rs = sub.add_parser(
        "research", help="walk-forward validation — score a strategy out of sample"
    )
    rs.add_argument("--strategy", default="sma_crossover")
    rs.add_argument("--source", default="synthetic", choices=["synthetic", "csv", "parquet"])
    rs.add_argument("--path", help="directory or file for csv/parquet sources")
    rs.add_argument(
        "--instruments",
        help="JSON array of complete instrument records, required for csv/parquet sources",
    )
    rs.add_argument("--symbols", default="AAPL")
    rs.add_argument("--cash", type=float, default=1_000_000.0)
    rs.add_argument("--train", type=int, default=5_000, help="bars used to pick parameters")
    rs.add_argument("--test", type=int, default=1_250, help="unseen bars each fold is scored on")
    rs.add_argument("--step", type=int, default=None, help="slide size (default: --test)")
    rs.add_argument("--fast", default="10,20,30", help="comma-separated parameter grid")
    rs.add_argument("--slow", default="50,100", help="comma-separated parameter grid")
    rs.add_argument("--min-trades", type=int, default=100)
    rs.add_argument("--confidence", type=float, default=0.95)
    rs.add_argument("--save", help="write per-fold results to this CSV path")

    # ------------------------------------------------------------------
    pf = sub.add_parser("portfolio", help="pull live portfolio from IIFL")
    pf.add_argument(
        "section", nargs="?", default="all",
        choices=["all", "limits", "positions", "holdings", "orders", "trades"],
    )
    pf.add_argument("--limit", type=int, default=25, help="max rows printed per section")
    pf.add_argument("--json", dest="json_path", help="write raw payload to this file")
    pf.add_argument("--save", dest="save_dir", help="write one CSV per section into this directory")

    # ------------------------------------------------------------------
    qt = sub.add_parser("quote", help="live market quotes for symbols")
    qt.add_argument("--symbols", required=True, help="comma-separated, e.g. RELIANCE-EQ,INFY-EQ")
    qt.add_argument("--exchange", default="NSEEQ")
    qt.add_argument("--json", dest="json_path", help="write raw payload to this file")

    # ------------------------------------------------------------------
    sg = sub.add_parser("signals", help="buy/sell signals for your book and a watchlist")
    sg.add_argument(
        "action", nargs="?", default="scan",
        choices=["scan", "buy", "sell", "validate", "init-config"],
    )
    sg.add_argument("--symbols", help="watchlist for the buy scan (comma-separated)")
    sg.add_argument("--telegram", action="store_true", help="push the report to Telegram")
    sg.add_argument("--json", dest="json_path", help="write signals to this JSON file")
    sg.add_argument("--train", type=int, default=300, help="walk-forward train bars (daily)")
    sg.add_argument("--test", type=int, default=200, help="walk-forward test bars (daily)")
    sg.add_argument(
        "--search", action="store_true",
        help="sweep a parameter grid instead of testing one config (validate only)",
    )

    # ------------------------------------------------------------------
    login = sub.add_parser("login", help="complete the IIFL OAuth login")
    login.add_argument("--client-id", help="clientId returned to your redirect URL")
    login.add_argument("--auth-code", help="authCode returned to your redirect URL")
    login.add_argument("--print-url", action="store_true", help="only print the login URL")

    # ------------------------------------------------------------------
    inst = sub.add_parser("instruments", help="instrument master")
    inst.add_argument("action", choices=["sync", "search"])
    inst.add_argument("--exchanges", default="NSEEQ,NSEFO")
    inst.add_argument("--query", default="NIFTY")
    inst.add_argument("--limit", type=int, default=20)

    # ------------------------------------------------------------------
    live = sub.add_parser("live", help="stream ticks from the IIFL bridge")
    live.add_argument("--topics", default="nseeq/2885", help="comma separated exchange/id")
    live.add_argument("--seconds", type=float, default=10.0)

    gpu = sub.add_parser("gpu", help="GPU status, and a batched SMA-crossover parameter sweep")
    gpu_sub = gpu.add_subparsers(dest="action", required=True)
    gpu_sub.add_parser("info", help="show the compute device")
    sw = gpu_sub.add_parser("sweep", help="screen every fast/slow pair across the whole market")
    sw.add_argument("--fast", default="5:50:5", help="start:stop:step (inclusive), e.g. 5:50:5")
    sw.add_argument("--slow", default="20:200:10", help="start:stop:step (inclusive)")
    sw.add_argument("--days", type=int, default=750, help="bars of history to use")
    sw.add_argument("--cost-bps", type=float, default=10.0)
    sw.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    sw.add_argument("--top", type=int, default=10)
    sw.add_argument("--chunk", type=int, default=16, help="pairs per batch (lower if the GPU runs out of memory)")
    sw.add_argument("--compare", action="store_true", help="also time the CPU and report the speed-up")

    serve = sub.add_parser("serve", help="start the app: FastAPI + built frontend on one port")
    serve.add_argument("--host", help="override API_HOST")
    serve.add_argument("--port", type=int, help="override API_PORT")
    serve.add_argument("--no-browser", action="store_true", help="don't open the dashboard on start")
    serve.add_argument("--rebuild", action="store_true", help="force a frontend rebuild")
    serve.add_argument("--no-build", action="store_true", help="skip the build/missing check")

    # ------------------------------------------------------------------
    sub.add_parser("backup", help="write a backup of the database and state now")
    sub.add_parser("data-check", help="scan the price history for unadjusted splits and gaps")
    restore = sub.add_parser("restore", help="restore a backup (stop the server first)")
    restore.add_argument("archive", help="path to a Forward backup ZIP archive")
    restore.add_argument(
        "--force",
        action="store_true",
        help="move the current database aside before restoring",
    )

    hist = sub.add_parser("history", help="bulk history cache + full-market scan")
    hist.add_argument("action", choices=["sync", "scan-all", "refresh-eod", "sync-index"])
    hist.add_argument("--exchange", default="NSEEQ")
    hist.add_argument("--interval", default="1d")
    hist.add_argument("--from", dest="from_date", default=None)
    hist.add_argument("--to", dest="to_date", default=None)
    hist.add_argument("--workers", type=int, default=6)
    hist.add_argument("--start", type=int, default=0)
    hist.add_argument("--end", type=int, default=None)
    hist.add_argument("--index-name", choices=["NIFTY 50", "INDIA VIX"])
    hist.add_argument("--index-contract-source", choices=["CACHE", "HTTP"])
    hist.add_argument("--index-interval", choices=["1d", "15m"])

    # ------------------------------------------------------------------
    al = sub.add_parser("alerts", help="evaluate price/indicator alert rules")
    al.add_argument("action", choices=["check", "test", "list"])

    # ------------------------------------------------------------------
    br = sub.add_parser("brief", help="morning briefing (preview or Telegram it)")
    br.add_argument("action", choices=["preview", "send"], nargs="?", default="preview")

    # ------------------------------------------------------------------
    # Read-only by design: every action here inspects what already happened.
    # Learning has no action that writes to a deployment, an order or a risk
    # limit. Learning may propose; execution stays with
    # Strategy -> RiskEngine -> OMS -> Broker/Paper. (Authoring a strategy is
    # `forward strategy`, which writes to `strategies`/`strategy_versions` and
    # nothing else.)
    learn = sub.add_parser("learn", help="what the trade history says (read-only)")
    learn.add_argument(
        "action", nargs="?", default="status",
        choices=["status", "dataset", "performance", "report", "drift", "snapshot"],
    )
    learn.add_argument("--strategy", help="restrict to one strategy (id, or id@version)")
    learn.add_argument(
        "--reference", default=None,
        help="drift: which source is the baseline (default: BACKTEST)",
    )
    learn.add_argument(
        "--roles", default=None,
        help="comma-separated subset of backtest,paper,live (default: all present)",
    )
    learn.add_argument(
        "--by", default=None,
        help="comma-separated axes to slice on (default: the declared axis set)",
    )
    learn.add_argument(
        "--metric",
        default=None,
        help=(
            "outcome column the buckets are ranked on; defaults to the column "
            "the book actually records (net_pnl where any trade has rupees, "
            "otherwise return_pct)"
        ),
    )

    learn.add_argument("--min-sample", type=int, default=None, help="override the sample floor")
    learn.add_argument(
        "--window-days", type=int, default=90,
        help="history window the daily report compares against",
    )
    learn.add_argument("--rows", type=int, default=15, help="max buckets printed per axis")
    learn.add_argument("--no-write", action="store_true", help="do not persist the snapshot parquet")
    learn.add_argument("--json", dest="json_path", help="write the structured result to this file")

    # ------------------------------------------------------------------
    # Authoring. Unlike ``learn``, this one writes — to ``strategies`` and
    # ``strategy_versions`` only. It cannot place an order, start a deployment or
    # change a risk limit; those stay behind Strategy -> Risk -> OMS -> venue.
    st = sub.add_parser(
        "strategy",
        help="create strategies and their immutable versions",
    )
    st.add_argument(
        "action", nargs="?", default="list",
        choices=["list", "show", "create", "version", "validate", "seed"],
    )
    # `forward strategy show <id>` is what people actually type. Without this the
    # natural form dies in argparse with "unrecognized arguments", which reads
    # as a broken command rather than a missing flag.
    st.add_argument(
        "target", nargs="?",
        help="the strategy id, for show/version/validate (same as --strategy-id)",
    )
    st.add_argument("--user", default=None, help="account id (default: the only/owner account)")
    st.add_argument("--name", default=None, help="strategy name, for create/seed")
    st.add_argument(
        "--kind", default=DEFAULT_STRATEGY_KIND, choices=STRATEGY_KINDS,
        help="how the strategy is defined (default: rules)",
    )
    st.add_argument("--description", default=None)
    st.add_argument("--engine-key", default=None, help="registry key this version wraps")
    st.add_argument("--strategy-id", default=None, help="the strategy to act on")
    st.add_argument(
        "--definition", default=None,
        help="path to a JSON file holding the definition (version/validate)",
    )
    st.add_argument("--version", type=int, default=None, help="version number, for show/validate")
    st.add_argument("--change-note", default=None)
    st.add_argument("--json", dest="json_path", help="write the structured result to this file")

    # ------------------------------------------------------------------
    dev = sub.add_parser("dev", help="development: FastAPI (reload) + Vite dev server")
    dev.add_argument("--no-browser", action="store_true", help="don't open the dashboard on start")
    return parser















def _run_live(args) -> int:
    import time

    from forward.brokers.iifl.auth import SessionStore
    from forward.brokers.iifl.bridge import BridgeClient
    from forward.config.settings import get_settings

    settings = get_settings()
    session = SessionStore(settings.iifl_session_cache).load()
    if session is None:
        logger.error("no active session — run `forward login` first")
        return 1

    bridge = BridgeClient(
        session,
        tls_verify=settings.bridge_tls_verify,
        host=settings.bridge_host,
        port=settings.bridge_port,
        reconnect_min_seconds=settings.bridge_reconnect_min_seconds,
        reconnect_max_seconds=settings.bridge_reconnect_max_seconds,
        client_id=session.preferred_username(),
    )
    bridge.on_feed = lambda topic, feed: print(
        f"{topic} ltp={feed.ltp:.2f} bid={feed.best_bid_price:.2f} ask={feed.best_ask_price:.2f} vol={feed.traded_volume}"
    )
    bridge.on_error = lambda code, msg: logger.error("{}: {}", code, msg)
    try:
        bridge.connect(settings.bridge_connect_timeout_seconds)
        topics = [t.strip() for t in args.topics.split(",")]
        bridge.subscribe_feed(topics)
        logger.info("streaming {} for {}s ...", topics, args.seconds)
        time.sleep(args.seconds)
    finally:
        bridge.disconnect()
    return 0














def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _web_dir() -> Path:
    return _project_root() / "web"


def _web_dist() -> Path:
    return _web_dir() / "dist"


def _frontend_built() -> bool:
    dist = _web_dist()
    return (dist / "index.html").exists() and (dist / "assets").is_dir()


def _build_frontend() -> None:
    """Build the Vite app with the repository's configured Bun toolchain.

    Raises:
        RuntimeError: If Bun is unavailable or the frontend build fails.
    """
    import shutil
    import subprocess

    bun = shutil.which("bun")
    if bun is None:
        raise RuntimeError(
            "Bun is required to build the frontend; install the web dependencies first"
        )
    logger.info("building frontend with Bun …")
    res = subprocess.run([bun, "run", "build"], cwd=str(_web_dir()))
    if res.returncode != 0:
        raise RuntimeError(
            f"frontend build failed with exit code {res.returncode}"
        )


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        if not webbrowser.open(url):
            logger.info("dashboard at {}", url)
    except Exception:  # noqa: BLE001 — headless machines just get the URL
        logger.info("dashboard at {}", url)


def _open_browser_when_ready(host: str, port: int, url: str, timeout: float = 90.0) -> None:
    """Open the dashboard once the server accepts connections.

    Args:
        host: API host to probe.
        port: API port to probe.
        url: Dashboard URL to open.
        timeout: Maximum wait time in seconds.
    """
    import socket
    import threading
    import time

    def wait_then_open() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1):
                    break
            except OSError:
                time.sleep(0.3)
        _open_browser(url)

    threading.Thread(target=wait_then_open, name="forward-open-browser", daemon=True).start()


def _session_ready() -> None:
    from forward.brokers.iifl.auth import SessionStore
    from forward.config.settings import get_settings

    settings = get_settings()
    session = SessionStore(settings.iifl_session_cache).load()
    if session:
        logger.info(
            "IIFL session for {} valid until {}",
            session.client_id,
            session.expires_at.strftime("%d-%b %H:%M IST"),
        )
    else:
        logger.warning("no active IIFL session — log in from the dashboard when it opens")






def _run_serve(args) -> int:
    """Run the production app with its built frontend."""
    import uvicorn

    from forward.config.settings import get_settings

    settings = get_settings()
    host = args.host or settings.api_host
    port = args.port or settings.api_port

    if args.no_build and not _frontend_built():
        raise RuntimeError("--no-build requires an existing web/dist/index.html")
    if not args.no_build and (args.rebuild or not _frontend_built()):
        _build_frontend()
    if not _frontend_built():
        raise RuntimeError("frontend build finished without producing web/dist/index.html")

    url = f"http://{host}:{port}"
    logger.info("Starting Forward on {}", url)
    _session_ready()
    if not args.no_browser:
        _open_browser_when_ready(host, port, url)
    uvicorn.run("forward.api.main:app", host=host, port=port, reload=False, log_level="info")
    return 0


def _run_dev(args) -> int:
    """Run the FastAPI backend (reload) and the Vite dev server together."""
    import os
    import shutil
    import signal
    import subprocess
    import time

    from forward.config.settings import get_settings

    settings = get_settings()
    host = settings.api_host
    port = settings.api_port
    if not _web_dir().exists():
        logger.error("web/ directory missing at {}", _web_dir())
        return 2
    bun = shutil.which("bun")
    if bun is None:
        raise RuntimeError("Bun is required to run the frontend development server")

    children = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "forward.api.main:app",
             "--host", host, "--port", str(port), "--reload"],
            cwd=str(_project_root()),
        ),
        subprocess.Popen(
            [bun, "run", "dev"],
            cwd=str(_web_dir()),
            env={**os.environ, "VITE_API_URL": f"http://127.0.0.1:{port}"},
        ),
    ]

    url = "http://localhost:5173"
    logger.info("dashboard (dev) at {}", url)
    if not args.no_browser:
        _open_browser(url)

    def _terminate(*_: object) -> None:
        for p in children:
            if p.poll() is None:
                p.terminate()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    signal.signal(signal.SIGINT, _terminate)
    signal.signal(signal.SIGTERM, _terminate)
    try:
        while all(p.poll() is None for p in children):
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        for p in children:
            if p.poll() is None:
                p.kill()
    return 0






def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {message}")
    handlers = {
        "backtest": run_backtest,
        "research": run_research,
        "portfolio": run_portfolio,
        "quote": run_quote,
        "signals": run_signals,
        "login": run_login,
        "instruments": run_instruments,
        "history": run_history,
        "backup": run_backup,
        "restore": run_restore,
        "data-check": run_data_check,
        "alerts": run_alerts,
        "brief": run_briefing,
        "learn": run_learn,
        "strategy": run_strategy,
        "live": _run_live,
        "serve": _run_serve,
        "gpu": run_gpu,
        "dev": _run_dev,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

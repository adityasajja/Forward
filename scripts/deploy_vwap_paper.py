"""Create the user-authorized paper experiment through application services.

The optional late Nifty exception is restricted to the human-authorized
15 October 2026 session and is never a permanent timing change.
"""

import argparse
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

from forward.appdb.repositories import DeploymentRepository, SystemStateRepository
from sqlalchemy import select

from forward.appdb.engine import get_app_db
from forward.appdb.forward_check_storage import SqlForwardCheckStorage
from forward.appdb.paper_deployment_storage import SqlPaperDeploymentStorage
from forward.appdb.paper_ledger_storage import SqlPaperLedgerStorage
from forward.appdb.portfolio_storage import SqlPortfolioStorage
from forward.appdb.schema import deployments
from forward.appdb.strategy_storage import SqlStrategyAuthoringStorage
from forward.data.cached_market_frames import CachedMarketFramesAdapter
from forward.instruments.strategy_universe import ResearchedStrategyUniverse
from forward.services.deployments import DeploymentService
from forward.services.paper_ledger import PaperLedger
from forward.services.sector_opening import IST, choose
from forward.services.strategies import StrategyService
from forward.strategy.strategies.vwap_reversion import CONTEXT, ENGINE
from forward.wiring.paper import get_live_paper_price_source

parser = argparse.ArgumentParser()
parser.add_argument("--start", action="store_true")
parser.add_argument("--authorize-nifty-late-today", action="store_true")
args = parser.parse_args()
db = get_app_db()
out = Path("reports/mean_reversion/paper_setup_2026-10-15")
out.mkdir(parents=True, exist_ok=True)
with db.session() as session:
    prior = dict(
        session.execute(
            select(deployments).where(
                deployments.c.deployment_id == "1635eaef19a14f169b09233d671a47df"
            )
        )
        .mappings()
        .one()
    )
user_id = prior["user_id"]
strategies = StrategyService(
    SqlStrategyAuthoringStorage(db),
    ResearchedStrategyUniverse(CachedMarketFramesAdapter()),
)
name = "Intraday VWAP Mean Reversion"
existing = next((s for s in strategies.list(user_id) if s["name"] == name), None)
universe = json.loads(Path("web/src/lib/sector-opening-universe.json").read_text())
definition = {
    "engine_key": ENGINE,
    "params": {
        "universe": universe,
        "spec_version": 1,
        "bar_source": "observed_live_quotes",
        "allocation": 0.25,
        "max_positions": 2,
    },
    "context": CONTEXT,
}
if existing is None:
    existing = strategies.create(
        user_id,
        name=name,
        kind="code",
        engine_key=ENGINE,
        description="Experimental long-only 5m VWAP recovery. Two positions, three attempts/day; risk 0.25% per trade, 1% daily loss gate. Observed live quote bars with continuity checks. Not historically validated.",
    )
    strategies.create_version(
        user_id,
        existing["strategy_id"],
        definition=definition,
        change_note="User-authorized paper forward test; fixed v1 rules and explicit observed-quote bar provenance.",
    )
service = DeploymentService(
    storage=SqlPaperDeploymentStorage(db),
    ledger=PaperLedger(
        storage=SqlPaperLedgerStorage(db),
        price_source=get_live_paper_price_source(),
    ),
    forward_trade_storage=SqlForwardCheckStorage(db),
    portfolio_storage=SqlPortfolioStorage(db),
)
with db.session() as session:
    rows = DeploymentRepository.list_for_user(session, user_id)
deployment = next(
    (
        r
        for r in rows
        if r["strategy_id"] == existing["strategy_id"]
        and r["mode"] == "PAPER"
        and r["strategy_version"] == 1
    ),
    None,
)
if deployment is None:
    deployment = service.create(
        user_id,
        strategy_id=existing["strategy_id"],
        strategy_version=1,
        capital=500000,
        mode="PAPER",
    )
if args.start and deployment["status"] != "RUNNING":
    deployment = service.start(user_id, deployment["deployment_id"])

if args.authorize_nifty_late_today:
    now = datetime.now(IST)
    if now.date().isoformat() != "2026-10-15" or not "09:20" < now.strftime("%H:%M") < "14:55":
        raise ValueError("Late-entry authorization is restricted to 15 October 2026 after 09:20")
    if prior["mode"] != "PAPER" or prior["status"] != "RUNNING":
        raise ValueError("Nifty deployment must already be running in PAPER")
    ledger = PaperLedger(
        storage=SqlPaperLedgerStorage(db),
        price_source=get_live_paper_price_source(),
    )
    portfolio = ledger.portfolio(user_id, deployment_id=prior["deployment_id"], mode="PAPER")
    if any(not p.is_flat for p in portfolio.positions.values()):
        raise ValueError("Existing position prevents a late opening entry")
    from forward.composition import build_order_service

    if build_order_service(portfolio=portfolio).open_orders(
        user_id, deployment_id=prior["deployment_id"]
    ):
        raise ValueError("Outstanding orders prevent a late opening entry")
    key = f"paper.sector_opening.{prior['deployment_id']}.2026-10-15"
    with db.session() as session:
        state = SystemStateRepository.get_value(session, key)
    if not state or state.get("attempted") or state.get("entry_exception"):
        raise ValueError("Missing, already attempted or already authorized day state")
    (out / "nifty_before_late_exception.json").write_text(json.dumps(state, indent=2))
    state = copy.deepcopy(state)
    decision = choose(universe, state["snapshot"])
    if decision["status"] != "selected":
        raise ValueError("Original opening snapshot still does not select a stock")
    state["original_skip"] = {k: state.get(k) for k in ["status", "reason", "missing"]}
    state.update(decision)
    state["missing"] = []
    state["entry_exception"] = {
        "day": "2026-10-15",
        "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "authorized_by": user_id,
        "reason": "Human user explicitly allowed opening late today after RELIANCE alias diagnosis; paper only",
    }
    with db.session() as session:
        SystemStateRepository.set(
            session, key, state, actor=user_id, reason=state["entry_exception"]["reason"]
        )
    print(
        json.dumps(
            {"late_nifty_selection": decision, "exception": state["entry_exception"]}, indent=2
        )
    )

artifact = {
    "strategy_id": existing["strategy_id"],
    "deployment_id": deployment["deployment_id"],
    "version": 1,
    "mode": deployment["mode"],
    "status": deployment["status"],
    "capital": deployment["capital"],
    "definition": definition,
}
(out / "deployment.json").write_text(json.dumps(artifact, indent=2))
print(json.dumps({k: v for k, v in artifact.items() if k != "definition"}, indent=2))

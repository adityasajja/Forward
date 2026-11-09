# Architecture decisions

This log records important design choices made while changing Forward. Add an
entry when a choice affects module boundaries, stored data, or how a user runs a
strategy.

## 2026-11-09: Reuse the declared HTTP library for exchange history

**Status:** Accepted

**Decision:** Public exchange-history downloads use `httpx`, which is already a
Forward runtime dependency. The request timeout, history start date, and worker
count come from required settings and are passed through composition.

**Reason:** The history adapter used an undeclared `requests` import for an HTTP
job that the declared `httpx` dependency already supports.

**Effect:** Exchange redirects remain enabled. Each request is made once;
transport errors stop the history sync. Missing settings fail at configuration
load rather than using code defaults.

## 2026-11-09: Build chart candles from the live tick buffer

**Status:** Accepted

**Decision:** `DuckEngine` aggregates the tick sequence supplied by the live
tick-candle service. It does not keep a second tick table or calculate VWAP.
DuckDB uses its own runtime settings; the adapter does not set fixed thread or
memory limits.

**Reason:** The live broadcaster already owns a bounded tick buffer and is the
only caller of this adapter. The database insert, stored-tick resample, and VWAP
methods had no callers and duplicated data storage.

**Effect:** Chart candles still use the same live tick sequence. DuckDB no longer
creates an unused `live_ticks` table or carries unused insert and VWAP code.
Each tick must contain an epoch, timestamp, last price, and quantity. Invalid
timestamps and aggregation errors reach the caller instead of turning into
default values or an empty candle result.

## 2026-11-07: Keep trading cost rules in the domain core

**Status:** Accepted

**Decision:** `forward.core.costs` owns commission, statutory fee, and slippage
models. Backtests, paper venues, and strategy planning import those rules from
the core module.

**Reason:** These models calculate trading rules from values and instruments.
They do not send orders or need execution lifecycle state, so they belong in the
shared domain core rather than the execution adapter package.

**Effect:** Cost formulas and defaults stay the same. All first-party imports
use the canonical core module; `forward.execution.costs` is removed.

## 2026-11-07: Keep stored API-key scope parsing in the auth policy layer

**Status:** Accepted

**Decision:** `forward.auth.api_key_scopes` parses recognized and unknown stored
API-key scopes. `AuthService` logs unknown scopes and still limits permissions
to the account role.

**Reason:** Scope parsing is a pure authorization policy. It does not need the
service's session or user storage operations.

**Effect:** Unknown permissions remain denied and are still logged. Authentication
and role checks keep the same behavior.

## 2026-11-07: Keep strategy definition identity separate from parsing

**Status:** Accepted

**Decision:** `forward.strategy.definition_identity` owns canonical JSON and
SHA-256 identity for saved strategy definitions. The definition module keeps
parsing and validation.

**Reason:** Canonical serialization and hashing are deterministic identity rules.
They do not need the strategy parser or database repository.

**Effect:** The same definition produces the same stored identity. Repositories
and authoring services import the identity functions directly, with no re-export.

## 2026-11-07: Keep WebSocket frame encoding outside connection management

**Status:** Accepted

**Decision:** `forward.api.stream_protocol` owns normalized tick payloads and
tick/status frame encoding. `forward.api.stream` owns subscriptions and
connection lifecycle.

**Reason:** Frame shape and JSON encoding do not need live-feed connection state.

**Effect:** WebSocket frame fields and values stay the same. The broadcaster
uses one encoder for live and cached tick frames.

## 2026-11-07: Keep deployment entry ranking separate from the run loop

**Status:** Accepted

**Decision:** `forward.services.deployment_entry_ranking` orders qualified
entry candidates using the configured formula or the existing gap score.
`DeploymentLoop` owns evaluation state and order placement.

**Reason:** Candidate ordering depends on candidate data and an optional formula,
not on clocks, storage, or runner state.

**Effect:** Entry order and formula errors keep the same behavior. The run loop
handles failures and skipped evaluations as before.

## 2026-11-07: Keep sector aggregation outside market data loading

**Status:** Accepted

**Decision:** `forward.market_intel.sectors` turns measured stocks into sector
metrics. The market-intelligence service loads and validates history, enriches
stocks, and stores the snapshot.

**Reason:** Sector summaries need only the measured stocks, benchmark returns,
and active thresholds. They do not need data adapters or cache state.

**Effect:** Sector output is unchanged. The market snapshot fingerprint includes
the aggregation module, so changes to its calculations refresh stored results.

## 2026-11-07: Keep forward-evidence counts outside learning storage

**Status:** Accepted

**Decision:** `forward.research.learning_counts` summarizes already-built
learning rows by evidence grade, date window, strategy, and version.
`LearningService` owns dataset loading and captures the report timestamp.

**Reason:** Counting supplied rows does not need dataset storage or cache state.
The caller supplies the timestamp so the data build and date windows use the
same point in time.

**Effect:** The response keys and evidence-grade rules stay the same. The service
continues to own dataset access and the research module owns aggregation.

## 2026-11-07: Keep attribution calculations pure

**Status:** Accepted

**Decision:** Trade return and realized-risk calculations live in
`forward.analytics.attribution_metrics`. The attribution service assembles the
trade inputs and calls these functions.

**Reason:** These calculations need only trade values. They do not need storage,
market history, or service state, so keeping them pure makes the boundary clear
and lets other analytics reuse the same rules.

**Effect:** Attribution output and API behavior stay the same. The service keeps
data loading and record creation; the analytics module owns the calculations.

## 2026-11-07: Keep monitoring timeline projection pure

**Status:** Accepted

**Decision:** Deployment event parsing and timeline construction live in
`forward.services.monitoring_timeline`. `DeploymentMonitor` reads the rows and
uses that projection for timeline responses.

**Reason:** Turning order and event rows into an ordered timeline does not need
database access or runner state. A pure projection is easier to inspect and
reuse.

**Effect:** The monitoring service API and response shape stay the same. Storage
access remains in the service; event-to-timeline mapping lives in one module.

## 2026-11-07: Store learning drift results outside drift calculations

**Status:** Accepted

**Decision:** Drift result models and their JSON conversion live in
`forward.research.learning_drift_models`. `learning_drift` owns the comparison
rules and calculations.

**Reason:** Result shapes are shared by learning services and drift analysis.
They should not be defined inside the calculation module.

**Effect:** Consumers import the models from their owning module. The drift
calculation module does not re-export them.

## 2026-11-07: Keep market regime rules outside market data assembly

**Status:** Accepted

**Decision:** `forward.market_intel.regime` owns the pure classification rules.
The market-intelligence service gathers measurements and calls the classifier.

**Reason:** Classification needs measurements and a versioned config, not a
market-data adapter or cache. Keeping it separate makes the rule boundary clear.

**Effect:** The service output stays the same. The snapshot fingerprint includes
the classifier source, so stored results refresh when its rules change.

## 2026-11-07: Keep signal outcome math outside context analysis

**Status:** Accepted

**Decision:** `forward.signal_context.outcomes` owns the pure signed-return
calculation for a resolved journal episode. The context service resolves the
journal record and uses that calculation.

**Reason:** Return math depends only on the episode's entry, exit, and side. It
does not need context storage or market data.

**Effect:** Context analysis keeps the same output. Outcome math has one focused
owner and can be reused without the service.

## 2026-11-07: Keep daily report output rules with the report model

**Status:** Accepted

**Decision:** `DailyReport`, its headline and JSON projection, and its amount
formatter live in `forward.services.learning_report_model`. The report builder
selects observations and calculates report values.

**Reason:** Building the report and describing its output are separate jobs.
The model owns the meaning and presentation of its fields; the builder owns the
data work.

**Effect:** The report response stays the same. The learning service imports the
model from its canonical module, with no re-export from the builder.

## 2026-11-07: Keep completed-candle selection separate from deployment timing

**Status:** Accepted

**Decision:** `forward.services.deployment_frames` selects and orders completed
intraday bars before evaluation. `DeploymentLoop` keeps clock conversion,
history refresh, and refresh-state updates.

**Reason:** Candle selection is a data projection. It does not need the runner's
clock, loaders, or mutable deployment state.

**Effect:** The loop's execution path and return contract stay the same. The
completed-bar rules can be inspected without constructing a deployment loop.

## 2026-11-07: Keep learning attribution field mapping separate

**Status:** Accepted

**Decision:** `forward.services.learning_dataset_attribution` maps one stored
attribution record to the learning dataset's attribution columns. The dataset
builder keeps the join and missing-feature updates.

**Reason:** The column mapping is a pure projection. It does not need the
builder's data access or row-selection state.

**Effect:** The dataset keeps the same fields and missing-value behavior. The
builder calls the projection once for each matched attribution record.

## 2026-11-07: Keep journal identity validation out of attribution orchestration

**Status:** Accepted

**Decision:** `forward.analytics.attribution_inputs` validates and normalizes the
symbol and side required from a journal episode. `AttributionService` uses this
result while it assembles storage-backed inputs.

**Reason:** Journal identity has a small, explicit contract and does not need
the service's storage, market data, or provenance workflow.

**Effect:** The same journal rows remain accepted or rejected. Attribution
orchestration keeps its existing provenance and storage behavior.

## 2026-11-07: Give installation state its own schema module

**Status:** Accepted

**Decision:** The shared SQLAlchemy metadata lives in `forward.appdb.metadata`.
The installation-wide `system_state` table lives in
`forward.appdb.system_schema`; its repository imports that table directly.

**Reason:** Database metadata is shared infrastructure, while `system_state`
has one owner and does not belong in the general table-definition file.

**Effect:** `AppDatabase` creates tables from the shared metadata as before.
The table remains registered at the same point in the schema, with the same
columns and constraints.

## 2026-11-07: Keep process wiring in the composition root

**Status:** Accepted

**Decision:** `forward.composition` stays the owner of shared, process-scoped
service instances. Feature adapters and the paper-runner builder live in their
focused wiring modules; the runner owns its run and stop lifecycle.

**Reason:** The remaining composition code creates those services or keeps the
single process instance used by startup and API routes. Moving it would spread
process lifecycle ownership across unrelated entry points.

**Effect:** Composition remains the outer dependency boundary. Feature code
continues to depend on ports and services, not on the composition root.

## 2026-11-07: Keep backtest result shaping outside run lifecycle

**Status:** Accepted

**Decision:** `forward.backtest.projections` owns timestamp text, curve points,
trade response rows, and monthly return compounding. `BacktestService` owns run
submission, storage, and lifecycle.

**Reason:** These output transformations use only stored result values. They do
not need the service's worker pool, storage adapter, or run state.

**Effect:** Backtest response values and lifecycle remain unchanged. The pure
projections are reusable without creating a service.

## 2026-11-07: Keep portfolio dashboard projections outside the use case

**Status:** Accepted

**Decision:** `forward.services.portfolio_dashboard` builds exposure summaries,
limit warnings, conflict descriptions, and active-limit rows. `PortfolioService`
keeps book aggregation, storage reads, and risk decisions.

**Reason:** Dashboard rows need only an assembled book, values, and policy.
They do not need the service's storage adapter or mutation flow.

**Effect:** Portfolio responses and enforcement remain unchanged. Pure display
projections can be reused without constructing the service.

## 2026-11-07: Show only stored strategy validation evidence

**Status:** Accepted

**Decision:** The strategy catalog reports metrics only from the stored validation
report. If a strategy has no result in that report, its state is `untested`.

**Reason:** Fixed metrics embedded in the service can go stale while still looking
like measured evidence. The catalog must not show them as current results.

**Behavior:** `/strategies` no longer returns baked-in historical metrics for
`signals_entry` or `cross_sectional_momentum`. Those entries remain listed and show
`{"state":"untested"}` until a stored report includes a result for them.

**Code:** `src/forward/services/research_catalog.py`.

## 2026-11-07: Index public exchange history as a separate source

**Status:** Accepted

**Decision:** The instrument catalog scans both `iifl_daily` and `public_daily`.
It records the exchange directory as the instrument's exchange and does not copy
or merge public bars into the broker cache.

**Reason:** `market_history` stores BSE and NSE SME data under `public_daily`, but
the catalog previously ignored those files. This hid valid instruments from
search even though the application had cached their history.

**Behavior:** Public and broker files remain separate. The catalog reads symbol
names and identity fields from `instruments_names.csv` when that file is present.

**Code:** `src/forward/data/instrument_catalog.py`.

## 2026-11-07: Do not substitute last price for tick VWAP

**Status:** Accepted

**Decision:** The tick VWAP endpoint reports `null` when its requested window has
no ticks with positive trade size. It never substitutes the latest trade price
or treats a zero-size quote as one unit of volume.

**Reason:** A last-traded price is not a volume-weighted average. Returning it as
VWAP makes a missing-data result look like a measured indicator.

**Behavior:** Empty VWAP responses use `ticks_count: 0` and `total_qty: 0`, matching
the populated response shape.

**Code:** `src/forward/api/stream.py`.

## 2026-11-07: Report unavailable risk and market data instead of substituting

**Status:** Accepted

**Decision:** Risk-state reads fail with HTTP 503 when durable storage cannot
answer. The chart candle endpoint requires an authenticated IIFL session and
does not silently replace a failed broker read with cached candles. Live
watchlist quotes report broker errors or incomplete coverage to the caller.

**Reason:** A made-up `paper` status or cached price can look like current state.
Callers need a clear error before they decide whether to wait, sign in, or request
cached data explicitly.

**Behavior:** `/risk/*` and `/health` return service unavailable when the stored
risk state cannot be read. `/candles` returns the broker authentication error
without a cached-data substitution. The watchlist route keeps its explicit
`live=false` path for local cached prices.

**Code:** `src/forward/api/legacy/access.py`,
`src/forward/api/legacy/market_data.py`, and
`src/forward/api/routers/watchlists.py`.

Earlier entries describe code paths as they existed when the decisions were made.
Later entries record follow-up moves to focused modules.

## 2026-11-03: Keep one persistence path for the application

**Status:** Accepted

**Decision:** Remove the unused Postgres/Timescale data repository. Keep market
history in its current file-backed adapters and application records in
`forward.appdb`.

**Reason:** No application service used the second repository. It duplicated
order and fill tables and kept a separate set of database settings alive.

**Behavior:** Runtime behavior stays the same because current application code
already uses the file-backed market adapters and `AppDatabase`.

**Code:** `src/forward/data/store.py`, `src/forward/appdb`,
`src/forward/config/settings.py`, and `.env.example`.

## 2026-11-03: Ranking errors stop new entries

**Status:** Accepted

**Decision:** If a configured entry ranking formula fails to compile or a
candidate lacks enough data to rank or divides by zero, the paper runner
records an error and skips new entries for that pass. It does not replace the
formula or missing score with a different ranking rule.

**Reason:** A strategy must not buy a different set of symbols because its
ranking rule or market data is broken. Exits already processed on the pass
remain valid and are allowed to complete.

**Behavior:** Runs without a ranking formula keep their existing gap-based
ordering. Runs with a valid ranking formula keep the formula's ordering.

**Code:** `src/forward/services/runner.py` and
`src/forward/signals/formula.py`.

## 2026-11-03: Keep paper deployment settings outside the runner service

**Status:** Accepted

**Decision:** Store `RunnerConfig` in `forward.paper.config`. The runner,
deployment use case, and paper health check import the shared model from there.

**Reason:** Deployment settings are shared application data. Keeping them in
the runner service made other use cases depend on a large orchestration module.

**Behavior:** Configuration fields, defaults, strategy context rules, and
runtime behavior stay the same.

**Code:** `src/forward/paper/config.py`,
`src/forward/services/runner.py`, `src/forward/services/deployments.py`, and
`src/forward/services/paper_health.py`.

## 2026-11-03: Build backtest market data from existing ports

**Status:** Accepted

**Decision:** `CachedBacktestMarketDataAdapter` receives a historical catalog,
the shared daily-bar cache, and a cache root. It does not import the instrument
master service or read daily Parquet files on its own.

**Reason:** The adapter had a direct dependency on another service and repeated
the shared cache reader's file access. Both dependencies already have ports or
adapters.

**Behavior:** Symbol selection, preferred cache filenames, longest-history
selection, and intraday loading keep their rules. The adapter no longer builds
an impossible warning for a file that is longer than the longest selected file.

**Code:** `src/forward/data/backtest_market_data.py`,
`src/forward/data/cache_store.py`, and `src/forward/composition.py`.

## 2026-11-03: Inject the daily cache root into its status adapter

**Status:** Accepted

**Decision:** Require `CachedDailyHistoryStatusAdapter` callers to pass the
daily cache root. The composition root supplies the configured root.

**Reason:** The adapter should not choose a global cache path when its caller
already owns application configuration.

**Behavior:** Cache status reads the same directory as before.

**Code:** `src/forward/data/history_status.py` and
`src/forward/composition.py`.

## 2026-11-03: Use one path for intraday history files

**Status:** Accepted

**Decision:** Read intraday candles only from `iifl_1m/<symbol>.parquet` under
the configured cache root.

**Reason:** Trying a second folder layout hides where history files belong and
keeps an unused compatibility path alive.

**Behavior:** Missing files remain absent from the returned frames. Existing
files in the documented cache folder load the same way.

**Code:** `src/forward/data/intraday_history.py`.

## 2026-11-03: Keep the live runner behind core ports

**Status:** Accepted

**Decision:** Make the live runner require feed and broker ports. Keep IIFL
feed and warmup construction in broker adapters. Put shared portfolio accounting
in `forward.execution` so live and backtest runners depend on the same domain
component.

**Reason:** The live use case imported IIFL clients and a backtest-only module.
Those imports tied strategy execution to concrete network code and put shared
accounting in the wrong layer.

**Behavior:** Callers supply the feed and broker. When warmup bars are enabled,
the caller must also supply a history adapter; a failed or empty warmup stops
the run instead of starting with cold indicators. Strategy and fill-accounting
errors also stop the loop, and the feed is closed during cleanup.

**Code:** `src/forward/live/runner.py`, `src/forward/core/live_feed_ports.py`,
`src/forward/core/broker_ports.py`, `src/forward/execution/portfolio.py`,
`src/forward/brokers/iifl/warmup.py`, and live-runner callers.

## 2026-11-03: Keep market intelligence sources explicit

**Status:** Accepted

**Decision:** Use the listed sector universe, exact canonical daily-cache names,
the exchange and instrument ID from the IIFL index contract list, and the
official cached NIFTY 50 index. Do not replace missing data with another
universe, ticker spelling, hardcoded contract, ETF, or public series.

**Reason:** Substituted sources can change rankings and benchmark results while
making the page appear complete. Missing inputs should stay visible as missing.

**Behavior:** Index refresh errors are logged. Existing cache data is kept, and
an unavailable or stale index is reported as unavailable.

**Code:** `src/forward/data/market_intel_market_data.py`,
`src/forward/data/market_intel_index_refresh.py`, and
`src/forward/market_intel/models.py`, `src/forward/signal_context`, and
`src/forward/services/learning.py`.

## 2026-11-03: Market scan defaults come from composition

**Status:** Accepted

**Decision:** Scanner services receive their default universe and history start
date when composition builds them. Routes parse request fields and map results
to HTTP responses; they do not import scanner rules to select a universe.

**Reason:** A scan use case should not make the route choose a market universe.
Composition is the boundary that joins configured values to market adapters.

**Behavior:** The default universe, date range, empty-watchlist behavior, scan
ordering, and response fields stay the same.

**Code:** `src/forward/services/market_scanning.py`,
`src/forward/services/local_scans.py`, `src/forward/composition.py`, and
`src/forward/api/legacy/scanner.py`.

## 2026-11-03: F&O paper fee construction belongs to the run service

**Status:** Accepted

**Decision:** The options route passes the fee values from the request. The
options forward service converts percentage values and builds the commission
model used by the simulated broker.

**Reason:** HTTP input fields should not make the router depend on the backtest
cost model or know how that model stores percentages.

**Behavior:** Fee values and simulated fill costs stay the same.

**Code:** `src/forward/services/options_forward.py` and
`src/forward/api/routers/options.py`.

## 2026-11-03: Strategy loss caps are applied by the risk service

**Status:** Accepted

**Decision:** The risk service combines the configured platform limits with the
F&O strategy's daily loss cap. The options route asks for the resulting limits
without reading or changing a risk snapshot.

**Reason:** Risk limits and their combination rules belong to the risk use case.
The route should not edit a risk value object or copy the platform kill switch.

**Behavior:** The lower daily loss limit and current kill-switch state continue
to govern the options paper runner.

**Code:** `src/forward/services/risk.py` and
`src/forward/api/routers/options.py`.

## 2026-11-03: Alert rules and delivery run through an application service

**Status:** Accepted

**Decision:** Alert rule changes, event reads, live checks, and test messages go
through `AlertService`. Composition supplies storage, market history, and the
configured delivery channels. The API maps a missing rule to HTTP 404.

**Reason:** Alert behavior should not make HTTP routes depend on alert models,
engines, channel implementations, or settings.

**Behavior:** Rule fields, missing-rule responses, market-open data, delivery
order, first-success behavior, and response fields stay the same.

**Code:** `src/forward/services/alerts.py`, `src/forward/composition.py`, and
`src/forward/api/legacy/alerts.py`.

## 2026-11-03: Briefing settings and delivery use configured services

**Status:** Accepted

**Decision:** `BriefingService` validates and saves editor values, supplies the
configured default watchlist, and loads delivery channels when sending. The API
passes request values and returns the service result.

**Reason:** The route should not import briefing rules, scanner universes, or
notification adapters to save or send a briefing.

**Behavior:** An empty watchlist still selects the configured universe. Message
content, disabled-send handling, first-success delivery, and response fields stay
the same.

**Code:** `src/forward/services/briefing.py`, `src/forward/composition.py`, and
`src/forward/api/legacy/briefing.py`.

## 2026-11-03: Manual live orders are built by the execution service

**Status:** Accepted

**Decision:** The execution service creates the live order draft and derives its
idempotency key from the client order ID. The route passes the signed quantity
and request fields to the service.

**Reason:** Order shape and retry identity belong with the shared execution
lifecycle. The route should not construct OMS value objects or derive OMS keys.

**Behavior:** Buy and sell direction, price fields, LIVE mode, request
idempotency, risk checks, venue submission, and HTTP error mapping stay the same.

**Code:** `src/forward/services/execution.py` and
`src/forward/api/legacy/orders.py`.

## 2026-11-03: Trade signal settings are merged by their service

**Status:** Accepted

**Decision:** `TradeSignalSettingsService` merges partial editor changes with
the saved settings, validates the complete value, and saves it. The route passes
the request body to the service.

**Reason:** Merging persisted configuration is application behavior. Keeping it
in one service prevents another caller from using a different update rule.

**Behavior:** Partial updates keep existing values, validated fields and the
returned settings shape stay the same.

**Code:** `src/forward/core/trade_signal_models.py`,
`src/forward/services/trade_signal_factory.py`, and
`src/forward/api/legacy/signals.py`.

## 2026-11-03: Trade signal settings use a dedicated application service

**Status:** Accepted

**Decision:** `TradeSignalSettingsService` lives in the application services
package. `TradeSignalSettings` is a core model, and composition supplies the
storage port.

**Reason:** Persisting and merging settings is application work. Keeping the
service separate from signal calculation makes each module easier to follow.

**Behavior:** Defaults, saved values, partial updates, validation, and storage
fields stay the same.

**Code:** `src/forward/services/trade_signal_settings.py`,
`src/forward/core/trade_signal_models.py`, and `src/forward/composition.py`.

## 2026-11-03: Trade signal queue state belongs to an application service

**Status:** Superseded by the 2026-11-06 trade signal boundary decision.

**Decision:** The in-memory trade signal queue and its storage-port calls live in
`SignalQueue` under `forward.services`. The signal model and trade calculations
stay in `forward.trade_signals`.

**Reason:** Loading, expiring, and persisting queued signals are application
state changes, separate from building a signal from market data.

**Behavior:** Queue ordering, expiry, replacement, status changes, and persisted
records stay the same.

**Code:** `src/forward/services/signal_queue.py`,
`src/forward/composition.py`, and the former `src/forward/trade_signals.py`.

## 2026-11-03: The research strategy catalog is an application query

**Status:** Accepted

**Decision:** `ResearchCatalogService` combines the built-in strategy registry,
warmup needs, and saved validation results. The route returns this query result
without importing the strategy registry or shaping its evidence.

**Reason:** Strategy research status is a read model built from strategy
definitions and saved results. It should have one owner.

**Behavior:** Strategy ordering, tunable fields, warmup counts, prior results,
validation values, and response fields stay the same.

**Code:** `src/forward/services/research_catalog.py`,
`src/forward/composition.py`, and `src/forward/api/legacy/research.py`.

## 2026-11-03: Research history selects its configured default universe

**Status:** Accepted

**Decision:** `ResearchHistoryService` receives the default symbols from
composition and uses them when a research request has no explicit symbols.

**Reason:** Selecting the market universe is part of loading research history.
The HTTP route should not import the scanner's universe constant.

**Behavior:** An empty symbol list still loads the same default universe from
the selected history source.

**Code:** `src/forward/services/research_history.py`,
`src/forward/composition.py`, and `src/forward/api/legacy/research.py`.

## 2026-11-03: Research accepts canonical history source names

**Status:** Accepted

**Decision:** Research requests use `cache`, `fetch`, or `synthetic` as their
source value. The `history` alias is removed.

**Reason:** A source should have one name in the API and the strategy builder.
The current frontend does not send the old alias.

**Behavior:** Requests with a supported source are unchanged; `history` now gets
the same 400 response as any unknown source.

**Code:** `src/forward/api/legacy/research.py`.

## 2026-11-03: Research parameter grids come from the strategy catalog

**Status:** Accepted

**Decision:** `ResearchCatalogService` selects the parameter grid for each
registered research strategy. The research route passes editor values to the
catalog instead of importing signal-search rules.

**Reason:** Which parameters a strategy can search belongs beside the strategy
catalog and its tunable field list.

**Behavior:** The SMA and entry-signal grids, including the search switch, stay
the same.

**Code:** `src/forward/services/research_catalog.py` and
`src/forward/api/legacy/research.py`.

## 2026-11-03: Walk-forward execution belongs to the research service

**Status:** Accepted

**Decision:** `ResearchValidationService` sizes the folds, applies warmup and
parameter rules, runs the backtest and validation engines, and builds the report
data. The route selects the feed, maps input errors to HTTP, and serializes
equity points.

**Reason:** Window sizing, strategy evaluation, and benchmark calculation are
research behavior. Keeping that work out of the route leaves HTTP handling at
the transport boundary.

**Behavior:** Window formulas, warnings, risk and cost settings, benchmark
window, report fields, and HTTP 422 handling stay the same.

**Code:** `src/forward/services/research_validation.py`,
`src/forward/composition.py`, and `src/forward/api/legacy/research.py`.

## 2026-11-03: Keep in-memory feeds in the core layer

**Status:** Accepted

**Decision:** `ListFeed` and the Polars-free snapshot row conversion live in
`forward.core.feeds`. Polars dataframe conversion stays in the data layer.

**Reason:** Backtests and research both need a fixed feed, while core objects
must not import a dataframe library. Keeping row conversion shared avoids
copying the OHLCV mapping into each adapter.

**Behavior:** `ListFeed` provides snapshots and instruments. Code that needs a
Polars frame uses `snapshots_to_frame` from `forward.data.base`.

**Code:** `src/forward/core/feeds.py`, `src/forward/data/base.py`, and
`tests/test_data_base.py`.

## 2026-11-03: Fail the architecture check on invalid Python

**Status:** Accepted

**Decision:** Let the architecture check report parse and decoding errors
instead of treating an unreadable module as having no imports.

**Reason:** Skipping a module can make a forbidden dependency pass unnoticed.
The check must inspect every Python module it finds.

**Behavior:** Invalid source stops the architecture check with its parse error.
No empty-import fallback is used.

**Code:** `tests/test_architecture.py`.

## 2026-11-03: Put the research bar filter in the research layer

**Status:** Accepted

**Decision:** Keep the reversion-spike filter in
`forward.research.data_hygiene`. Research rules and their scripts call that
shared implementation; they do not import it from the cache adapter package.

**Reason:** The filter is a research rule for excluding known vendor glitches.
The current data adapters do not apply it while loading market data, so the
implementation belongs beside the research that uses it.

**Behavior:** The filter keeps the same inputs, thresholds, and returned rows.
There is no old-module import alias.

**Code:** `src/forward/research/data_hygiene.py`, research callers, scripts,
and `tests/test_hygiene.py`.

## 2026-11-03: Backtests receive market data through a port

**Status:** Accepted

**Decision:** `BacktestRunner` receives `BacktestMarketDataPort` for cache reads
and feed construction. `CachedBacktestMarketDataAdapter` owns parquet paths,
instrument cache metadata, Polars conversion, and intraday history loading.
`BacktestService` owns both readiness checks and hold-benchmark requests, so the
API route does not assemble compute objects or read market data.

**Reason:** Backtest rules should receive frames and a feed without knowing
which cache files or dataframe adapter supplied them. The route should not
assemble compute objects or load market data itself.

**Behavior:** Cache selection still prefers the instrument master's recorded
file and the longest available series. An unreadable candidate now raises its
read error instead of being skipped for another file. Universe lookup errors
reach the caller instead of being treated as an empty universe.

**Code:** `src/forward/core/ports.py`,
`src/forward/data/backtest_market_data.py`,
`src/forward/data/intraday_history.py`, `src/forward/backtest/runner.py`,
`src/forward/backtest/readiness.py`, `src/forward/services/backtests.py`,
`src/forward/api/routers/backtests.py`, and
`tests/test_builder_safeguards.py`.

## 2026-11-03: Research engines receive their market frames

**Status:** Accepted

**Decision:** `SelfLearningEngine` accepts market frames for training and
signal scans. The API obtains them through `CachedMarketFramesPort` and passes
them to the engine.

**Reason:** A research calculation should not open a cache or decide which
market-data source to read. Its caller already owns that boundary.

**Behavior:** The engine uses the supplied frames for each operation. It does
not read the cache or invent rows for an empty frame set.

**Code:** `src/forward/research/self_learning.py` and
`src/forward/api/legacy/signals.py`.

## 2026-11-03: Learning and attribution read market data through a port

**Status:** Accepted

**Decision:** `LearningDatasetBuilder` and `AttributionService` receive
`CachedMarketFramesPort`. API composition shares the same cache adapter used by
other market-data services.

**Reason:** An application service should request market frames through an
interface. It should not import the parquet loader, own file selection, or turn
a failed read into an empty result.

**Behavior:** A valid cache miss still returns no frame. A cache read error
surfaces to the caller. Tests supply an in-memory port with exact symbol
matching.

**Code:** `src/forward/services/learning.py`,
`src/forward/services/attribution.py`, `src/forward/api/composition.py`, and
`tests/market_data_fakes.py`.

## 2026-11-03: Learning file access uses an artifact port

**Status:** Accepted

**Decision:** `LearningDatasetBuilder` and `LearningService` use
`LearningArtifactsPort` for the paper ledger, sector table, and snapshot files.
`LocalLearningArtifactsAdapter` owns the configured directory and calls the
existing input parsers and JSON file writer.

**Reason:** Learning rules should not choose a directory or open JSONL, CSV, or
JSON report files directly. The API composition layer supplies one adapter to
both the builder and learning service.

**Behavior:** Missing records are reported by the existing ledger parser. File
read failures surface to the service caller instead of being replaced with an
empty ledger or sector table. Snapshot write failures also surface to the
caller. No learning file operation silently switches to an empty result.

**Code:** `src/forward/core/ports.py`,
`src/forward/data/learning_artifacts.py`,
`src/forward/services/learning.py`, `src/forward/api/composition.py`, and
the learning service tests.

## 2026-11-03: The URL is the only navigation state

**Status:** Accepted

**Decision:** Parse only the current tab and sub-page names. A valid bare tab
uses its declared first page; an unknown name is rejected. Keep route state in
the URL and remove the duplicate saved tab value.

**Reason:** Translating old route names and replacing an unknown sub-page with
the first page hides invalid links and keeps two sources of navigation state.
The current route map is the single source of truth.

**Behavior:** Current links and valid bare tabs behave as before. Old aliases
and unknown sub-pages show a not-found page instead of opening another screen.
No compatibility or route fallback is added.

**Code:** `web/src/lib/nav.ts`, `web/src/App.tsx`, and
`web/src/lib/nav.test.ts`.

## 2026-11-03: Paper P&L reads through a storage port

**Status:** Accepted

**Decision:** `pnl_calendar` reads closed paper trades through
`PaperPnlStoragePort`. `SqlPaperPnlStorage` owns the SQL query, and API
composition provides the adapter to the calendar route.

**Reason:** A P&L view is a use case, so SQL session handling and table joins
belong in the storage adapter. The service should work from the records it is
given and should not open a database or replace a failed read with an empty
calendar.

**Behavior:** Paper P&L uses the recorded net value. A missing database read
fails visibly instead of silently omitting paper trades, and missing net P&L is
not replaced with gross P&L. This removes those two existing fallback paths.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/pnl_calendar_storage.py`,
`src/forward/services/pnl_calendar.py`,
`src/forward/api/composition.py`, and `src/forward/api/legacy/pnl.py`.

## 2026-11-02: Correct malformed exception clauses

**Status:** Accepted

**Decision:** Write multi-type exception handlers as tuples in the paper
service and forward-evidence selector.

**Reason:** The old syntax prevents Python from importing these modules, so the
paper and evidence paths cannot run. The tuple form is valid Python.

**Behavior:** Keep each handler's existing return value and caught exception
types. This repairs the syntax without adding a new fallback path.

**Code:** `src/forward/services/paper_prices.py`,
`src/forward/services/paper_venue.py`, `src/forward/services/paper_ledger.py`,
`src/forward/services/deployments.py`, and
`src/forward/services/forward_trade_evidence.py`.

## 2026-11-02: Show paper P&L to paise precision

**Status:** Accepted

**Decision:** Format paper P&L with the shared INR formatter using up to two
decimal places. Decide whether a run is waiting from its exact P&L value,
without rounding it first.

**Reason:** Whole-rupee rounding hid small gains and losses and could sort a
nonzero run into the waiting group. The shared formatter keeps money display
consistent across standard and F&O paper runs.

**Behavior:** P&L displays paise. A run with any nonzero realized P&L is shown
with active results even when it has no open position.

**Code:** `web/src/PaperRuns.tsx`, `web/src/lib/format.ts`, and
`web/src/lib/paper-monitor.ts`.

## 2026-11-02: Record architecture choices with the code changes

**Status:** Accepted

**Decision:** Add a short entry here for each refactor choice that changes a
module boundary, data ownership, or strategy execution behavior. Each entry
states the choice, why it was made, its behavior impact, and the affected code.

**Reason:** The code should show both what changed and why, so later work can
follow the same design without guessing.

**Behavior:** Record decisions as they are implemented. Do not treat an existing
fallback as approved for new code; ask before adding one.

## 2026-11-02: Reconciliation uses a storage port

**Status:** Accepted

**Decision:** `ReconciliationService` depends on `ReconciliationStoragePort`.
`SqlReconciliationStorage` owns database reads and writes, and the API
composition layer connects the service to that adapter.

**Reason:** The reconciliation rules should not need to know about SQLAlchemy
sessions or repository classes. This keeps the use case separate from its
database adapter and makes another storage implementation possible without
rewriting the rules.

**Behavior:** Keep the existing comparison, persistence, audit, and read
behavior. This change adds no new fallback behavior.

**Code:** `src/forward/core/ports.py`, `src/forward/services/reconcile.py`,
`src/forward/appdb/reconciliation_storage.py`, and `src/forward/api/composition.py`.

## 2026-11-02: Reuse shared frontend building blocks

**Status:** Accepted

**Decision:** Prefer existing UI components, shared hooks, API types, and view
helpers before adding new frontend code. Keep shared values, formatting rules,
and behavior in one place when more than one panel needs them. Use Forward as
the product name in user-facing text and project documentation.

**Reason:** Reusing the frontend's existing parts keeps screens consistent and
avoids copying the same rules or values into separate panels. Forward is the
platform name shown to users.

**Behavior:** This is a refactor rule. It does not change screen behavior or add
fallback behavior.

## 2026-11-02: Use Forward as the only platform name

**Status:** Accepted

**Decision:** Name the Python package, CLI, launcher, application labels, API
key prefix, and configuration keys `Forward`. Average True Range remains an
indicator term, not a platform name.

**Reason:** One platform name should appear consistently in setup, the user
interface, logs, and documentation.

**Behavior:** There is no compatibility alias for the old package, CLI, or
secret environment variable. Operators use `forward` and `FORWARD_SECRET_KEY`.

**Code:** `src/forward/`, `pyproject.toml`, `start-forward.bat`, and the
Forward app configuration.

## 2026-11-02: Backtests use a storage port

**Status:** Accepted

**Decision:** `BacktestService` depends on `BacktestStoragePort`. The SQL
adapter owns repository and session work, and API composition wires that
adapter into the service.

**Reason:** Backtest lifecycle and result behavior belong in the use case.
They should not depend on database sessions or repository classes.

**Behavior:** Result artefacts are still written with the completion state in
one transaction. Failure cleanup and the failed state also remain atomic. The
existing strategy listing and error handling are unchanged; this refactor adds
no fallback behavior.

**Code:** `src/forward/core/ports.py`, `src/forward/services/backtests.py`,
`src/forward/appdb/backtest_storage.py`, and `src/forward/api/composition.py`.

## 2026-11-02: Risk state uses its storage port

**Status:** Accepted

**Decision:** `RiskStateService` reads and updates risk values through
`RiskStateStoragePort`. `SqlRiskStateStorage` owns the database transaction and
audit writes.

**Reason:** Kill-switch, mode, and limit rules belong in the service. SQL
sessions and repositories belong in the storage adapter.

**Behavior:** State validation, audit reasons, state-change metadata, and
transaction boundaries remain the same. This change adds no fallback behavior.

**Code:** `src/forward/core/ports.py`, `src/forward/services/risk.py`,
`src/forward/appdb/risk_state_storage.py`, and `src/forward/api/composition.py`.

## 2026-11-02: Forward checks use a storage port

**Status:** Accepted

**Decision:** `ForwardCheckService` calculates the comparison from rows returned
by `ForwardCheckStoragePort`. `SqlForwardCheckStorage` owns the database
queries, and API composition wires the adapter to the service.

**Reason:** Trade comparison and verdict rules should not depend on SQL tables,
sessions, or the paper service's internal database object.

**Behavior:** The latest completed backtest is still compared with closed paper
or live trades, using the same version and forward-evidence rules. The verdict
thresholds do not change, and this change adds no fallback behavior.

**Code:** `src/forward/core/ports.py`, `src/forward/services/forward_check.py`,
`src/forward/appdb/forward_check_storage.py`,
`src/forward/api/composition.py`, and `src/forward/api/routers/paper.py`.

## 2026-11-02: Order state changes use a unit of work

**Status:** Accepted

**Decision:** `OrderService` works through `OrderStoragePort` and
`OrderUnitOfWorkPort`. `SqlOrderStorage` owns the SQL session and savepoints;
the service keeps order validation, risk decisions, and legal state changes.

**Reason:** Order events and the order projection must change in one
transaction. A unit of work keeps that boundary without giving the use case a
SQL session or repository classes.

**Behavior:** Order ownership checks, event ordering, idempotency claims,
savepoint rollback, risk checks, fill validation, and state transitions remain
in place. The runner and API composition supply the SQL adapter. This change
adds no fallback behavior.

**Code:** `src/forward/core/ports.py`, `src/forward/services/orders.py`,
`src/forward/appdb/order_storage.py`, `src/forward/services/runner.py`,
`src/forward/api/composition.py`, and `src/forward/api/legacy/startup.py`.

## 2026-11-02: Paper health reuses runner storage

**Status:** Accepted

**Decision:** The paper-health use case reads deployment and exact-version data
through `PaperHealthStoragePort`. `SqlPaperRunnerStorage` implements this port
along with `PaperRunnerStoragePort`, and API composition shares the adapter
with the runner and the health endpoint.

**Reason:** Paper health and the execution runner need the same saved
deployments and immutable strategy definitions. Sharing the existing adapter
removes duplicate SQL reads and keeps version lookup in one place.

**Behavior:** Owner scoping, RUNNING and PAUSED selection, version definitions,
startup warming, and health refresh behavior remain unchanged. This change
adds no fallback behavior.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/paper_runner_storage.py`,
`src/forward/services/paper_health.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/startup.py`, and `src/forward/api/routers/paper.py`.

## 2026-11-02: Live readiness uses forward-trade storage

**Status:** Accepted

**Decision:** The LIVE deployment gate reads forward journal rows through
`ForwardTradeStoragePort`. The SQL adapter implements both that port and
`ForwardCheckStoragePort`, and API composition shares one adapter across the
readiness gate and backtest comparison service.

**Reason:** The gate and comparison service must apply the same owner, version,
and forward-evidence rules. A shared read port and pure evidence selectors
keep SQL out of the gate and stop the two use cases from drifting apart.

**Behavior:** The effective-sample threshold, clustering, missing-version rule,
forward labels, owner filter, and strategy filter stay the same. A deployment
must receive its storage port explicitly; there is no default database or
evidence fallback.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/forward_check_storage.py`,
`src/forward/services/forward_trade_evidence.py`,
`src/forward/services/forward_check.py`, `src/forward/services/gating.py`,
`src/forward/services/deployments.py`, `src/forward/services/paper_ledger.py`,
`src/forward/services/champions.py`,
`src/forward/api/composition.py`, and the deployment service callers.

## 2026-11-02: Layering checks inspect the Forward package

**Status:** Accepted

**Decision:** Point the architecture check at `src/forward`, recognize the real
top-level Forward packages, and resolve both absolute and relative imports.

**Reason:** The check still searched for the old package directory and only
recognized imports that started with the old package prefix. It could not report whether the
current Forward package followed the documented layer rules.

**Behavior:** This changes only the architecture check. It does not change app
behavior or add a compatibility path.

**Code:** `tests/test_architecture.py`.

## 2026-11-02: Champion comparison reads through a storage port

**Status:** Accepted

**Decision:** `ChampionService` reads owned strategy versions and running PAPER
deployments through `ChampionStoragePort`, implemented by `SqlChampionStorage`.
The service receives storage, learning, context, and deployment services when
it is built.

**Reason:** Champion comparison contained SQL queries and could create hidden
default services. Keeping the read boundary in an adapter makes ownership
checks testable and keeps the use case independent of database sessions.

**Behavior:** Version reads are owner-scoped, including comparisons where both
versions are named. Context-read and malformed-definition errors now surface
instead of being changed into placeholder dashboard data. No fallback
dependencies or error-suppression paths are added.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/champion_storage.py`,
`src/forward/services/champions.py`, `src/forward/api/composition.py`, and
`tests/test_champion_challenger_e2e.py`.

## 2026-11-02: Daily learning uses explicit account-scoped dependencies

**Status:** Accepted

**Decision:** Require the learning, optimization, and signal-context use cases
when constructing `DailyLearningCycleService`. Pass the account ID into the
learning dataset query and keep only records with explicit evidence class and
strategy ID fields.

**Reason:** The cycle used a shared dataset query, a default `system` account,
and legacy source-field substitutes. It also fetched the context service from
global composition during execution. Explicit account scope prevents mixing
accounts, and explicit dependencies keep service wiring at the application
boundary.

**Behavior:** Dataset build failures and optimization failures remain visible
in the cycle report. An unreadable strategy version is reported as an error and
skipped; it is not replaced by an empty definition. Context failures are
reported and do not create placeholder observations. Old rows without explicit
evidence class or strategy ID are not treated as current evidence.

**Code:** `src/forward/services/daily_learning.py`,
`src/forward/api/composition.py`, and
`tests/test_daily_learning_cycle_e2e.py`.

## 2026-11-03: Paper money values keep paise precision

**Status:** Accepted

**Decision:** Paper-screen money formatting shows up to two decimal places and
does not force trailing zeroes.

**Reason:** Rounding rupee P&L to a whole number hides small gains and losses.
The paper screen should show the precision in the measured value while keeping
whole-rupee values easy to scan.

**Behavior:** Unknown money values still render as an em dash, and zero remains
a measured zero. No replacement value is supplied for missing data.

**Code:** `web/src/lib/format.ts` and `web/src/lib/paper-monitor.ts`.

## 2026-11-03: Portfolio use cases depend on a storage port

**Status:** Accepted

**Decision:** `PortfolioService`, its order gate, and the strategy-capital check
read and write through `PortfolioStoragePort`. `SqlPortfolioStorage` owns the
SQL queries and transaction used for portfolio policies, deployments, fills,
strategy labels, and journal episodes. Application composition supplies the
adapter to portfolio, paper-runner, deployment, and learning workflows.

**Reason:** Portfolio limits and reports are application rules. Database
sessions and repository calls belong in the storage adapter, where they can be
changed without rewriting those rules.

Learning services also receive their database at application composition.
They do not look up the global database when a caller leaves it out.

**Behavior:** Policy validation, fill folding, ownership filters, and P&L math
stay the same. Strategy-name read errors now reach the caller instead of being
silently hidden. No new fallback behavior is added.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/portfolio_storage.py`,
`src/forward/services/portfolio.py`, `src/forward/services/paper_ledger.py`,
`src/forward/services/runner.py`, `src/forward/services/learning.py`, and
`src/forward/api/composition.py`.

## 2026-11-03: Learning services use storage ports

**Status:** Accepted

**Decision:** `LearningDatasetBuilder` and `LearningService` receive a
`LearningStoragePort` for SQL-backed evidence reads. Learning observations and
cycle reports use the existing `DailyLearningStoragePort`. Composition shares
the daily-learning adapter with the cycle service and the learning use case.
Readiness reuses `PortfolioStoragePort` for account strategies and deployments.

**Reason:** Learning rules should not build SQL queries, open sessions, or
resolve other application services through the composition module.

**Behavior:** Row shaping, evidence grades, matching windows, readiness rules,
and observation calculations stay in the learning service. SQL queries and
repository calls stay in `SqlLearningStorage`; observation writes and cycle
storage stay in the existing daily-learning adapter.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/learning_storage.py`,
`src/forward/services/learning.py`, and `src/forward/api/composition.py`.

## 2026-11-03: Share pure learning metrics in analytics

**Status:** Accepted

**Decision:** Put cumulative drawdown and context-score band calculations in
`forward.analytics.learning_metrics`. Portfolio, analytics, and learning reports
share those functions.

**Reason:** These are pure calculations used by both research and application
services. Keeping them in the shared analytics layer avoids making services
depend on research and prevents duplicate metric code.

**Behavior:** The calculations, score-band labels, and rounding stay the same.
Only module ownership and imports change. No compatibility re-export is kept.

**Code:** `src/forward/analytics/learning_metrics.py`,
`src/forward/research/learning_context.py`,
`src/forward/research/learning_readiness.py`,
`src/forward/services/portfolio.py`, `src/forward/services/analytics.py`, and
`tests/test_learning_context.py`.

## 2026-11-03: Journal reconciliation uses a transaction port

**Status:** Accepted

**Decision:** `TradeJournalService` coordinates reconciliation through a
`TradeJournalStoragePort` unit of work. The SQL adapter owns the session and
repository operations, and the runner and attribution service receive the
journal use case through application composition.

**Reason:** Episode matching and excursion calculations are application rules.
They should not open SQL sessions or know repository classes. One unit of work
keeps each reconciliation pass atomic while keeping its SQL details outside the
use case.

**Behavior:** The fold, episode matching, provenance grade, P&L, excursion, and
exit-reason rules stay the same. The unused platform-wide `reconcile_all`
function is removed because no caller used it.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/trade_journal_storage.py`,
`src/forward/services/journal.py`, `src/forward/services/runner.py`,
`src/forward/services/attribution.py`, `src/forward/api/composition.py`, and
the runner and attribution call sites.

## 2026-11-03: Attribution uses a storage port

**Status:** Accepted

**Decision:** `AttributionService` reads journal inputs and stores attribution
results through `AttributionStoragePort`. It receives the paper fill ledger as
a separate port. SQL queries and repository calls live in
`SqlAttributionStorage`.

**Reason:** Attribution rules should be usable without opening database
sessions. The fill history already has one owner, `PaperLedger`, so the
attribution service reuses that source instead of rebuilding fill accounting.

**Behavior:** Provenance, matching windows, fingerprints, filters, and
calculation rules stay the same. The attribution sweep continues to scan all
closed journal rows after reconciling the requested deployment, matching its
existing behavior. Journal symbol, side, and quantity must be valid. Source is
read from the owning PAPER or LIVE deployment; missing deployments and unknown
modes fail the attribution instead of being guessed. Dead attribution helpers
and parameters with no callers are removed.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/attribution_storage.py`,
`src/forward/services/attribution.py`, `src/forward/api/composition.py`, and
`tests/test_attribution_chain.py`.

## 2026-11-03: Track records use portfolio storage

**Status:** Accepted

**Decision:** `TrackRecordService` receives the existing portfolio storage and
paper fill ledger through its constructor. Deployment and journal queries run
through `PortfolioStoragePort`.

**Reason:** Track record replay and portfolio reporting use the same deployment
and journal records. Reusing one adapter keeps SQL out of the track record
service and avoids duplicating repository queries.

**Behavior:** Deployment ownership, PAPER filters, fill replay, and journal
counts keep their existing rules. Selecting a deployment still returns no
record when it does not belong to the account.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/portfolio_storage.py`,
`src/forward/services/track_record.py`, and `src/forward/api/composition.py`.

## 2026-11-03: Deployment monitoring uses query adapters

**Status:** Accepted

**Decision:** `DeploymentMonitor` receives portfolio storage and order-history
storage. Deployment ownership and journal pages use `PortfolioStoragePort`;
order timelines use `OrderMonitoringStoragePort` implemented by
`SqlOrderStorage`. It also receives the runner state reader explicitly.

**Reason:** Monitoring is a read-only use case. It should not open SQL sessions
or depend on order placement operations. The narrow read protocols expose only
the records the screen needs. Composition resolves the runner and supplies its
status interface.

**Behavior:** Owner checks, order/event limits, event ordering, journal counts,
and timeline shaping stay the same. The two journal pages still share one SQL
session. Runner selection now happens in application composition instead of
inside the monitoring service.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/order_storage.py`,
`src/forward/appdb/portfolio_storage.py`,
`src/forward/services/monitoring.py`, `src/forward/api/composition.py`, and
the monitoring call sites.

## 2026-11-03: Options run records belong to the application schema

**Status:** Accepted

**Decision:** Define the options dataset, backtest, and forward-run tables in
the shared SQLAlchemy metadata used by application startup.

**Reason:** Options repositories already write these records. Keeping their
tables in the central schema lets the normal database setup create them and
keeps the repository contract aligned with the stored columns.

**Behavior:** The existing dataset and run fields, owner filters, and ordering
stay the same. The definitions add the foreign keys and indexes required by
those reads.

**Code:** `src/forward/appdb/schema.py` and
`src/forward/appdb/options_repository.py`.

## 2026-11-03: Paper runner receives its ledger

**Status:** Accepted

**Decision:** `PaperRunner` and `get_runner` receive a `PaperRunnerLedgerPort`.
Only the composition root builds the SQL-backed `PaperLedger`.

**Reason:** The runner coordinates deployment evaluation. It should not own a
database handle or decide how its book is loaded. Injecting the ledger keeps
account replay behind one existing adapter.

**Behavior:** Portfolio and snapshot reads still use `PaperLedger` and keep
their current filters and valuation rules. Runner startup still configures one
process-wide runner; its storage and ledger are supplied together at creation.

**Code:** `src/forward/core/ports.py`, `src/forward/services/runner.py`,
`src/forward/api/legacy/startup.py`, and the runner call sites.

## 2026-11-03: Invalid saved risk policy fails closed

**Status:** Accepted

**Decision:** Raise a storage error when a saved portfolio policy cannot be
decoded. Only a missing policy row means that no policy is configured.

**Reason:** Treating corrupt saved data as no policy can disable the order gate
without telling the operator.

**Behavior:** Valid policies and accounts with no saved policy keep their
current behavior. Invalid saved JSON now stops policy evaluation instead of
allowing orders through as if no limits existed.

**Code:** `src/forward/services/portfolio.py` and
`src/forward/api/composition.py`.

## 2026-11-03: Paper account replay uses a storage port

**Status:** Accepted

**Decision:** `PaperLedger` receives `PaperLedgerStoragePort`. SQL order-event
queries and row-to-fill mapping live in `SqlPaperLedgerStorage`.

**Reason:** Account replay uses portfolio rules, while SQL adapters own query
details. Keeping both jobs in one paper service made an application service open
database sessions and made reconciliation import a service from the storage
layer.

**Behavior:** Fill ordering, incremental quantities, recorded commissions,
slippage, account ownership, activity fields, and batched snapshots keep their
current rules. Reconciliation receives the ledger through its port.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/paper_ledger_storage.py`,
`src/forward/appdb/reconciliation_storage.py`,
`src/forward/services/paper_ledger.py`, `src/forward/services/reconcile.py`,
`src/forward/api/composition.py`, and the paper ledger call sites.

## 2026-11-03: Deployment lifecycle uses a storage port

**Status:** Accepted

**Decision:** `DeploymentService` receives `PaperDeploymentStoragePort`. The
SQL adapter owns deployment queries and state changes. A unit of work keeps
saved-strategy validation and deployment creation in one transaction.

**Reason:** The deployment use case should decide whether a strategy can run and
which lifecycle transition is valid. It should not open SQL sessions or call
repositories directly.

**Behavior:** Ownership checks, saved-version validation, live-deployment gates,
capital checks, lifecycle errors, order limits, and transaction scope stay the
same. Reconciliation and paper account replay remain separate ports.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/paper_deployment_storage.py`,
`src/forward/services/deployments.py`, `src/forward/api/composition.py`, and the
deployment call sites.

## 2026-11-03: Deployment monitoring reads through a ledger port

**Status:** Accepted

**Decision:** `DeploymentService` reads snapshots through
`PaperDeploymentLedgerPort`, which `PaperLedger` implements.

**Reason:** Deployment lifecycle rules need account values, but should not rely
on one concrete ledger class. The port states exactly which reads monitoring
uses and leaves account replay inside the ledger use case.

**Behavior:** Deployment lists, positions, and P&L use the same snapshot
operations and values as before.

**Code:** `src/forward/core/ports.py` and
`src/forward/services/deployments.py`.

## 2026-11-03: Paper trading responsibilities have separate modules

**Status:** Accepted

**Decision:** Keep quote sources, order matching, account replay, and deployment
lifecycle in `paper_prices.py`, `paper_venue.py`, `paper_ledger.py`, and
`deployments.py` respectively. Callers import the module that owns each use
case directly.

**Reason:** The former paper service combined four jobs and made unrelated
callers depend on one large module. The separate modules still use the shared
order, portfolio, price, and storage contracts.

**Behavior:** Matching rules, prices, costs, account replay, and deployment
transitions stay unchanged. The old combined import path is removed so new
callers name the responsibility they use.

**Code:** `src/forward/services/paper_prices.py`,
`src/forward/services/paper_venue.py`,
`src/forward/services/paper_ledger.py`, `src/forward/services/deployments.py`,
and their callers.

## 2026-11-03: Scanner scoring stays separate from broker market access

**Status:** Accepted

**Decision:** Keep momentum scoring as a pure function. `MarketScanningService`
coordinates scanning through `ScannerMarketPort`, and
`IiflScannerMarketAdapter` owns contract lookup and IIFL candle parsing.
`InstrumentMaster.contract_id` returns an ID only for an exact listed symbol.

**Reason:** The scanner should be usable with another market data source without
importing IIFL code. Prefix matching can silently select another security, so
the broker adapter must require the exact symbol in its contract master.

**Behavior:** Momentum calculations and the default universe stay the same.
Unknown symbols produce a per-symbol scan error. Bare aliases no longer map to
listed symbols; callers must send the exact trading symbol.

**Code:** `src/forward/core/ports.py`, `src/forward/scanner.py`,
`src/forward/services/market_scanning.py`,
`src/forward/brokers/iifl/scanner_market.py`,
`src/forward/brokers/iifl/contracts.py`, and scan callers.

## 2026-11-03: Backtests consume a core feed port

**Status:** Accepted

**Decision:** Define `BacktestFeedPort` in the core layer. Backtest,
optimization, validation, and experiment code consume that interface. Concrete
CSV, cached, synthetic, and IIFL feeds stay in the data and broker adapters.

**Reason:** Research services need ordered market snapshots and instrument
metadata, not a dependency on the module that happens to implement those
snapshots. The core port states only the data the backtest engine uses.

**Behavior:** Feed order and instrument mappings do not change. Existing feed
implementations satisfy the interface through their current `load()` and
`instruments` members.

**Code:** `src/forward/core/ports.py`, `src/forward/backtest/engine.py`,
`src/forward/optimization/evaluator.py`, `src/forward/research/validate.py`,
`src/forward/services/experiment.py`, and
`src/forward/services/optimization.py`.

## 2026-11-03: Forward-research routes use a query service

**Status:** Accepted

**Decision:** Dashboard routes request forward-research reports through
`ForwardTrackingService`. `FileForwardTrackingAdapter` owns the data path and
the calls to the file-backed research stores. API composition wires the adapter.

**Reason:** A route should not build file stores or import a data-root constant
from another service. The adapter is the single place that joins those file
stores to the dashboard query.

**Behavior:** The two tracker response bodies and their stored files stay the
same. The configured project data directory is supplied at application startup.

**Code:** `src/forward/core/ports.py`,
`src/forward/data/forward_tracking.py`,
`src/forward/services/forward_tracking.py`,
`src/forward/api/composition.py`, and
`src/forward/api/legacy/tracker.py`.

## 2026-11-03: Risk services receive live policy at composition

**Status:** Accepted

**Decision:** `RiskStateService` receives live-order policy through
`LiveRiskPolicyPort`. Each risk snapshot stores the selected values and can be
serialized without reading application configuration. The OMS gate uses the
same snapshot values.

**Reason:** A use case and its result model should not reach into the settings
module. Composition is the boundary that joins runtime settings to the service.

**Behavior:** The default order cap and market-hours rule keep their configured
values. Their source is passed in when the service is built. The unused
`reset_risk_state_cache` no-op has been removed because the service has no cache.

**Code:** `src/forward/core/ports.py`, `src/forward/services/risk.py`,
`src/forward/api/composition.py`, and the risk-service construction sites.

## 2026-11-03: Strategy research reads its universe through an instrument adapter

**Status:** Accepted

**Decision:** `StrategyService` depends on `StrategyUniversePort`. The instrument
adapter owns research-universe selection and exact IIFL contract-symbol checks.
The API and CLI provide that adapter when they compose the service.

**Reason:** Strategy authoring should not import research jobs, broker contracts,
or scanner code. Exact contract checks make a missing broker symbol visible
instead of letting prefix matching or a different history threshold change the
research result without notice.

**Behavior:** The primary long-history universe is unchanged. Shorter-history
selection and prefix-based symbol correction are removed. A missing exact
contract now raises an error.

**Code:** `src/forward/core/ports.py`,
`src/forward/instruments/strategy_universe.py`,
`src/forward/services/strategies.py`, `src/forward/api/composition.py`, and
`src/forward/cli.py`.

## 2026-11-03: Use uppercase Forward environment names

**Status:** Accepted

**Decision:** Read application environment values through uppercase names such
as `FORWARD_DATA_ROOT`, `FORWARD_AUDIT_PATH`, and `FORWARD_SECRET_KEY`.

**Reason:** Direct environment reads are case-sensitive on some deployment
systems. One uppercase prefix gives operators and tests the same names on every
platform.

**Behavior:** Lowercase `forward_` spellings are removed. There is no alias for
the old spelling.

**Code:** `src/forward/services/learning.py`,
`src/forward/signal_context/service.py`, `src/forward/audit/log.py`,
`src/forward/api/legacy/common.py`, and test setup.

## 2026-11-03: The paper runner receives its live broker provider

**Status:** Accepted

**Decision:** `PaperRunner` receives `LiveBrokerProviderPort` at construction.
The API startup composition passes the authenticated IIFL broker factory. The
runner does not import or create a broker by itself.

**Reason:** A service should use a broker port and let the application
composition choose the IIFL adapter. A hidden default made it possible to
construct a runner without stating how live execution connects.

**Behavior:** Paper matching and the live risk gate keep their current rules.
Live deployment execution still obtains the broker only when it needs to build
the live venue. Runner construction now requires the provider.

**Code:** `src/forward/core/ports.py`, `src/forward/services/runner.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-03: Intraday history is an explicit IIFL adapter

**Status:** Accepted

**Decision:** Intraday deployment warmup uses `IntradayHistoryPort`. The IIFL
adapter owns session access, exact contract lookup, and candle loading. Candle
response conversion lives beside the IIFL feed code and is shared by signals
and history tools.

**Reason:** The deployment runner should describe the data it needs without
constructing a broker client or handling IIFL contract files. A failed session,
missing contract, or history request must be visible to the runner instead of
silently changing how the strategy starts.

**Behavior:** Intraday strategies still seed their frame from recent broker
candles and refresh it through the same loader. The runner now pauses warmup
when the adapter cannot provide the requested data. It does not switch to a
second history source.

**Code:** `src/forward/core/ports.py`,
`src/forward/brokers/iifl/intraday_history.py`,
`src/forward/brokers/iifl/feeds.py`, `src/forward/data/iifl_history.py`,
`src/forward/services/runner.py`, and API startup.

## 2026-11-03: Signal scans use a market data port

**Status:** Accepted

**Decision:** Signal rules run in `SignalScanningService` through
`SignalMarketPort`. The IIFL adapter normalizes holdings, exact contracts, and
quotes. Signal formatting and research-universe selection live in their own
modules.

**Reason:** Signal evaluation should not parse IIFL response envelopes or own
contract-file access. The adapter maps broker rows to core models before the
service applies entry and exit rules.

**Behavior:** Scan rules, weighted holding prices, quote keys, and report text
keep their existing results. Daily broker history and local-cache history now
have separate calls. A broker history failure no longer silently switches to
the local cache; paper history explicitly reads the cache.

**Code:** `src/forward/core/models.py`, `src/forward/core/ports.py`,
`src/forward/data/iifl_history.py`, `src/forward/data/universe.py`,
`src/forward/brokers/iifl/signal_market.py`,
`src/forward/services/signal_scanning.py`, `src/forward/signals/report.py`,
and their callers.

## 2026-11-03: IIFL history access stays in broker adapters

**Status:** Accepted

**Decision:** Daily candle reads and bulk history sync use IIFL adapters.
`forward.data.history` only reads the local bar cache. The CLI supplies its
authenticated client factory to `IiflHistorySyncAdapter`.

**Reason:** Cache reads should not need broker credentials, and broker response
parsing belongs beside the IIFL API that defines the response shape.

**Behavior:** Bulk sync dates, concurrency, result counts, file names, and
per-symbol error reporting keep their prior behavior. Cached history consumers
continue to use `load_cached`.

**Code:** `src/forward/brokers/iifl/daily_history.py`,
`src/forward/brokers/iifl/history_sync.py`,
`src/forward/data/history.py`, `src/forward/data/iifl_history.py`,
`src/forward/brokers/iifl/signal_market.py`, and `src/forward/cli.py`.

## 2026-11-03: Deep research history is loaded through an adapter

**Status:** Accepted

**Decision:** Research requests use `ResearchHistoryService` through
`CachedResearchHistoryPort` or `ResearchHistoryPort`. The cache adapter reads
the available local symbols; the IIFL adapter resolves contracts and builds a
deep daily backtest feed. API composition supplies the correct adapter.

**Reason:** A route should not own IIFL contract lookup, candle requests, or
feed assembly. Those steps belong in a broker adapter that can be replaced
without changing walk-forward evaluation.

**Behavior:** Cache runs still include only requested symbols that have local
bars and keep the existing HTTP 503 versus 404 responses. Deep runs skip
missing exact contracts and symbols without bars. A deep request with no
usable history still returns HTTP 503. Feed ordering and instrument metadata
follow the existing research path.

**Code:** `src/forward/core/ports.py`,
`src/forward/brokers/iifl/research_history.py`,
`src/forward/data/research_history.py`,
`src/forward/services/research_history.py`, `src/forward/api/composition.py`,
and `src/forward/api/legacy/research.py`.

## 2026-11-03: Broker login routes use a session service

**Status:** Accepted

**Decision:** Login status, authorization-code exchange, and local session
clearing go through `BrokerSessionService` and `BrokerSessionPort`.

`IiflSessionAdapter` owns the IIFL client and its session file. API composition
provides the adapter to the login routes.

**Reason:** Login is still a broker action. Keeping `SessionStore` and
`IiflClient` out of the route makes the broker protocol replaceable and gives
the API one application boundary for session operations.

**Behavior:** The status fields, callback page, login error status, and local
logout behavior stay the same. Logout still clears the local session only.

**Code:** `src/forward/core/ports.py`,
`src/forward/brokers/iifl/session.py`,
`src/forward/services/broker_session.py`, `src/forward/api/composition.py`,
and `src/forward/api/legacy/login.py`.

## 2026-11-03: Health reads broker session through its service

The health route now asks `BrokerSessionService` for session state instead of
opening the IIFL session file itself. This keeps broker session storage behind
the same port used by login and logout while preserving the health response.

**Reason:** Health is part of the API, while session files belong to the IIFL
adapter. Using the session service keeps that boundary clear.

**Behavior:** The health response keeps its existing fields and values.

**Code:** `src/forward/api/legacy/system.py`.

## 2026-11-03: Database health checks run behind an application port

**Status:** Accepted

**Decision:** The API reads database status from `DatabaseHealthService`. The
SQL adapter owns the background probe and its cache window, configured through
`database_health_cache_seconds`.

**Reason:** The API should not start database threads or import the SQL engine.
The cached probe also keeps the health request quick while the database check
runs.

**Behavior:** The health response still returns the latest known database
status immediately. A new probe starts when the configured cache window expires.

**Code:** `src/forward/core/ports.py`, `src/forward/appdb/health.py`,
`src/forward/services/database_health.py`, `src/forward/config/settings.py`,
`src/forward/api/composition.py`, and `src/forward/api/legacy/system.py`.

## 2026-11-03: Cache coverage is read by market data adapters

**Status:** Accepted

**Decision:** Daily-bar cache coverage and IIFL contract-file coverage are read
through `MarketCacheStatusService` and separate cache adapters.

**Reason:** The status routes report on local data stores. They should not
inspect cache paths or parse broker files themselves.

**Behavior:** The endpoints keep their current response fields and report the
same file counts, sizes, and sync times.

**Code:** `src/forward/core/ports.py`,
`src/forward/data/history_status.py`,
`src/forward/brokers/iifl/contract_cache_status.py`,
`src/forward/services/market_cache_status.py`, `src/forward/api/composition.py`,
and `src/forward/api/legacy/system.py`.

## 2026-11-03: Market routes use the broker instrument catalog service

**Status:** Accepted

**Decision:** Market data routes resolve broker contract IDs and search symbols
through `InstrumentCatalogService`. The IIFL adapter owns contract-file loading
and the configured in-memory cache lifetime.

**Reason:** Contract file parsing and IIFL identifiers belong behind the broker
boundary. The UI route should use a stable instrument reference from the core.

**Behavior:** Symbol results keep the same fields. Cold contract caches keep
returning HTTP 503. Exact contract lookup and per-symbol quote errors keep their
existing outcomes.

**Code:** `src/forward/core/models.py`, `src/forward/core/ports.py`,
`src/forward/core/errors.py`, `src/forward/brokers/iifl/instrument_catalog.py`,
`src/forward/services/instrument_catalog.py`, `src/forward/config/settings.py`,
`src/forward/api/composition.py`, and `src/forward/api/legacy/market_data.py`.

## 2026-11-03: Chart routes use a market data service

**Status:** Accepted

**Decision:** Candle and quote requests go through `ChartMarketDataService` and
`ChartMarketDataPort`. The IIFL adapter translates listed instruments into
broker requests and returns chart-ready candles.

**Reason:** Market data routes should not call broker client methods or unpack
broker candle arrays.

**Behavior:** Candle fields and quote response handling stay the same. Daily
cache reads still serve the chart when no broker session is available.

**Code:** `src/forward/core/models.py`, `src/forward/core/ports.py`,
`src/forward/brokers/iifl/chart_market.py`,
`src/forward/brokers/iifl/responses.py`, `src/forward/services/chart_market_data.py`,
`src/forward/api/composition.py`, `src/forward/api/legacy/common.py`,
`src/forward/api/legacy/market_data.py`, and `src/forward/cli.py`.

The CLI and legacy API share the IIFL response row decoder so parsing has one
owner.

## 2026-11-03: Dashboard account reads use a broker port

**Status:** Accepted

**Decision:** Dashboard summary and portfolio reads use
`BrokerAccountReadService` and `BrokerAccountReadPort`.
`IiflAccountReadAdapter` owns IIFL response parsing and holding quote enrichment.
The shared read coalescer lives with application services.

**Reason:** API routes should shape HTTP responses without calling IIFL endpoints.
IIFL field names, response envelopes, and quote requests belong in the broker
adapter. Concurrent read sharing belongs in the application layer.

**Behavior:** Section names, ordering, counts, errors, concurrent requests,
holding filters, and quote enrichment keep their existing behavior. Completed
responses are not cached. No new fallback behavior is added.

**Code:** `src/forward/core/models.py`, `src/forward/core/ports.py`,
`src/forward/brokers/iifl/account_read.py`,
`src/forward/services/broker_account_read.py`,
`src/forward/services/read_coalescer.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/dashboard.py`, `tests/test_portfolio_last_price.py`,
and `tests/test_read_coalescer.py`.

## 2026-11-03: Dashboard metrics use a history port

**Status:** Accepted

**Decision:** `DashboardSummaryService` calculates position P&L, trade
performance, and market breadth from broker records and `DashboardHistoryPort`.
`CachedDashboardHistoryAdapter` reads the daily cache and returns only the close
records needed by those calculations. The API route only supplies the current
broker session and returns the service result.

**Reason:** Market cache files and pandas frames should stay behind a data
adapter. Startup warming and HTTP requests should use the same breadth service.

**Behavior:** The summary fields, cache windows, freshness check, and breadth
sample remain the same. JSON cleaning is shared by the API and summary service.
A valid cache miss still means a close is unavailable. A cache read error now
surfaces to the summary service instead of being reported as an ordinary miss.

**Code:** `src/forward/core/models.py`, `src/forward/core/ports.py`,
`src/forward/core/serialization.py`, `src/forward/data/dashboard_history.py`,
`src/forward/services/dashboard_summary.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/common.py`, `src/forward/api/legacy/dashboard.py`,
`src/forward/api/legacy/startup.py`, and `tests/test_dashboard_summary.py`.

## 2026-11-03: Local scans use a cached market-data port

**Status:** Accepted

**Decision:** `LocalScanService` handles full-market and custom scans through
`CachedMarketFramesPort`. The trade-signal route uses the same port for its
market frames. `CachedMarketFramesAdapter` owns the local cache call and
composition shares it with both services.

**Reason:** HTTP routes should handle HTTP input and output. Scan calculations,
result caching, and file-backed history belong in the application and data
layers.

**Behavior:** Scan response fields, sorting, the full-market cache (120 seconds
by default), symbol filtering, cache invalidation after history refresh, history
reads, and cache-copy behavior stay the same. Empty-cache errors keep their
current HTTP status and message. No fallback is added.

**Code:** `src/forward/core/ports.py`, `src/forward/data/cached_market_frames.py`,
`src/forward/services/local_scans.py`, `src/forward/config/settings.py`,
`.env.example`, `src/forward/api/composition.py`,
`src/forward/api/legacy/signals.py`, `src/forward/api/legacy/scanner.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-03: Saved scans use storage and application services

**Status:** Accepted

**Decision:** Saved scan listing, creation, replacement, and deletion go through
`SavedScanService` and `SavedScanStoragePort`. `JsonSavedScanStorageAdapter`
owns JSON file reads and writes. The storage path comes from application
settings.

**Reason:** The scanner routes should not own local file access or scan
persistence rules.

**Behavior:** Built-in scans, first-read file creation, JSON format, route
responses, and the existing unreadable-file behavior stay the same. No new
fallback behavior is added.

**Code:** `src/forward/core/ports.py`, `src/forward/data/saved_scans.py`,
`src/forward/services/saved_scans.py`, `src/forward/config/settings.py`,
`.env.example`, `src/forward/api/composition.py`, and
`src/forward/api/legacy/scanner.py`.

## 2026-11-03: Research reports use a report port

**Status:** Accepted

**Decision:** The validation and evidence routes use `ValidationReportService`
to shape payloads. `JsonValidationReportAdapter` reads named report files through
`ValidationReportPort`. The report directory is configured in application
settings.

**Reason:** HTTP route modules should not read files or merge research results.

**Behavior:** Response keys, missing-report hints, read-error messages, alpha
hunt merging, and the optional episodic premise behavior stay the same. No new
fallback behavior is added.

**Code:** `src/forward/core/ports.py`, `src/forward/data/validation_reports.py`,
`src/forward/services/validation_reports.py`, `src/forward/config/settings.py`,
`.env.example`, `src/forward/api/composition.py`, and
`src/forward/api/legacy/validation.py`, `src/forward/api/legacy/research.py`,
and `tests/test_evidence.py`.

## 2026-11-03: Legacy audit routes use an audit-log service

**Status:** Accepted

**Decision:** Legacy API routes write and read the JSONL audit trail through
`AuditLogService` and `AuditLogPort`. `JsonlAuditLogAdapter` reuses the shared
audit file writer. The API helper module no longer owns audit paths or file I/O.

**Reason:** Audit storage must have one path implementation and should not be
mixed into HTTP helper code.

**Behavior:** Event fields, append-only writes, newest-first reads, configured
path overrides, best-effort writes, and zero-limit handling stay the same. No
new fallback behavior is added.

**Code:** `src/forward/core/ports.py`, `src/forward/data/audit_log.py`,
`src/forward/services/audit_log.py`, `src/forward/audit/log.py`,
`src/forward/api/composition.py`, `src/forward/api/legacy/common.py`,
`src/forward/api/legacy/system.py`, `src/forward/api/legacy/orders.py`,
`src/forward/api/legacy/risk.py`, `src/forward/api/legacy/signals.py`,
`tests/test_execution_mode.py`, and `tests/test_legacy_order_routes.py`.

## 2026-11-03: Position reads use a broker-position service

**Status:** Accepted

**Decision:** The legacy positions route reads through `BrokerPositionsService`
and `BrokerPositionReadPort`. The live broker gate stays in the HTTP adapter;
the service shapes normalized position records.

**Reason:** Routes should return query results without owning broker reads or
position mapping.

**Behavior:** The response fields and `_live_broker` access gate stay the same.

**Code:** `src/forward/core/ports.py`, `src/forward/services/broker_positions.py`,
`src/forward/api/composition.py`, and `src/forward/api/legacy/orders.py`.

## 2026-11-03: Legacy account reads use a broker account service

**Status:** Accepted

**Decision:** Signal scans and risk status read normalized holdings, positions,
and limits through `BrokerAccountReadService` and its broker account adapter.
The signal scan skips holding quote enrichment because it only needs quantities
and average prices.

**Reason:** Route code should not decode raw broker payloads. Reads that need
different data should declare that need at the service boundary.

**Behavior:** Existing response fields, scan-held symbol rules, and risk panel
error handling stay the same. Holding quotes remain enabled for dashboard reads.
No new fallback behavior is added.

**Code:** `src/forward/core/ports.py`, `src/forward/brokers/iifl/account_read.py`,
`src/forward/services/broker_account_read.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/common.py`, `src/forward/api/legacy/risk.py`, and
`src/forward/api/legacy/signals.py`.

## 2026-11-03: Data freshness uses a cache adapter

**Status:** Accepted

**Decision:** `build_status` receives a `MarketDataStatusPort`. The local cache
adapter reads parquet dates and sidecar metadata; the service applies exchange
calendar rules and builds the response.

**Reason:** Freshness rules should be testable without local files, and file
layout should stay outside the service.

**Behavior:** The endpoint fields and freshness labels stay the same. Only the
six named liquid shares count as stock samples; an unrelated parquet file no
longer stands in when those samples are missing.

**Code:** `src/forward/core/models.py`, `src/forward/core/ports.py`,
`src/forward/data/data_status.py`, `src/forward/services/data_status.py`,
`src/forward/api/composition.py`, `tests/test_data_status.py`, and
`tests/test_loading_pipeline.py`.

## 2026-11-03: Daily trade capture uses broker and storage ports

**Status:** Accepted

**Decision:** `TradebookCaptureService` coordinates a broker read port and a
daily storage port. The IIFL adapter reuses the shared response parser, and the
JSON adapter keeps the existing per-day file format.

**Reason:** The capture rule should not depend on an IIFL response envelope or
the local file layout.

**Behavior:** Trade rows are saved before positions are requested, so a
positions error does not lose trades already captured. Empty broker rows still
write no file, and the returned counts stay the same.

**Code:** `src/forward/core/ports.py`, `src/forward/brokers/iifl/tradebook.py`,
`src/forward/data/daily_tradebook.py`, `src/forward/services/tradebook.py`,
`src/forward/api/composition.py`, `src/forward/api/legacy/startup.py`, and
`tests/test_tradebook.py`.

## 2026-11-03: Paper health loads history through the cache port

**Status:** Accepted

**Decision:** Paper health reads one symbol's daily bars through
`CachedMarketFramesPort`. The existing cache adapter owns the parquet read.

**Reason:** A health use case should not know where daily bars are stored, and
the cache already has a shared application port.

**Behavior:** Health still reads the same symbol and exchange bars and keeps its
per-refresh frame reuse. No new fallback behavior is added.

**Code:** `src/forward/core/ports.py`, `src/forward/data/cached_market_frames.py`,
`src/forward/services/paper_health.py`, `src/forward/api/routers/paper.py`,
`src/forward/api/legacy/startup.py`, and `tests/test_paper_health.py`.

## 2026-11-03: P&L calendar uses a file data adapter

**Status:** Accepted

**Decision:** P&L calendar calculations receive `PnlCalendarDataPort` for
settlements, realized entries, saved holdings P&L, and daily closes. The file
adapter owns JSON, JSONL, and parquet reads and writes.

**Reason:** Calendar calculations should not depend on local path layout.

**Behavior:** Paper sources, trading profit precedence, estimate rules, close
selection, and stored file shapes stay the same.

**Code:** `src/forward/core/ports.py`, `src/forward/data/pnl_calendar.py`,
`src/forward/services/pnl_calendar.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/pnl.py`, `src/forward/api/legacy/startup.py`, and
`tests/test_pnl_calendar.py`.

## 2026-11-03: Paper runner reads daily bars through a market-data port

**Status:** Accepted

**Decision:** Daily deployment loops receive `CachedMarketFramesPort` from the
application composition root. The runner does not import the parquet loader.

**Reason:** Strategy evaluation should depend on the history it needs, not on
the file-backed implementation that supplies it.

**Behavior:** Warmup and the next-session refresh request the same symbol and
exchange through the existing cache adapter. A missing port stops runner setup
instead of selecting an implicit history source.

**Code:** `src/forward/core/ports.py`, `src/forward/data/cached_market_frames.py`,
`src/forward/services/runner.py`, `src/forward/api/legacy/startup.py`,
`tests/`, and `scripts/verify_paper_workflow.py`.

## 2026-11-03: Cached chart candles use instrument and history ports

**Status:** Accepted

**Decision:** Cached chart candle calculations receive `CachedInstrumentHistoryPort`.
Instrument lookup and daily-bar reads stay in the existing instrument and cache
adapters.

**Reason:** Chart calculations should not read paths or parquet files directly,
and a second cache reader would duplicate existing cache code.

**Behavior:** Daily, weekly, and monthly output keeps the existing shape. A
cache miss still returns `None`, so the current API can keep its broker request
behavior unchanged.

**Code:** `src/forward/core/ports.py`, `src/forward/data/watchlist_market.py`,
`src/forward/services/chart_data.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/market_data.py`, and `tests/test_chart_data.py`.

## 2026-11-03: Track records read prices through a market-data port

**Status:** Accepted

**Decision:** Paper track-record replay receives stock closes, benchmark data,
and demo-data status through `TrackRecordMarketDataPort`. Its adapter reuses the
instrument cache for stock bars and owns benchmark file reads.

**Reason:** Ledger replay and performance calculations should not resolve cache
paths or read files.

**Behavior:** The same closes, benchmark source, demo flag, and unavailable-price
behavior feed the existing response calculations.

**Code:** `src/forward/core/ports.py`, `src/forward/data/track_record.py`,
`src/forward/services/track_record.py`, and `src/forward/api/composition.py`.

## 2026-11-03: VWAP paper liquidity comes from an injected data port

**Status:** Accepted

**Decision:** The VWAP paper strategy receives prior-session liquidity through
`VwapLiquidityPort`. The NSE bhavcopy adapter owns file selection, reads, and
the per-session cache.

**Reason:** The runner and strategy should not choose a working directory or
read bhavcopy files themselves.

**Behavior:** The same 20 complete sessions, equity-series filter, turnover
conversion, and empty-data entry gate remain in place. The runner cannot start
without the required liquidity port.

**Code:** `src/forward/core/ports.py`, `src/forward/data/vwap_liquidity.py`,
`src/forward/services/vwap_reversion.py`, `src/forward/services/runner.py`,
`src/forward/api/composition.py`, `src/forward/api/legacy/startup.py`, and
`tests/`.

## 2026-11-03: Paper venue liquidity uses the shared instrument history port

**Status:** Accepted

**Decision:** The runner receives `CachedInstrumentHistoryPort` and uses it to
read the average volume needed by paper fill pricing. Chart candles, watchlists,
track records, and paper pricing share the same instrument and daily-bar adapter.

**Reason:** Pricing should not open parquet files or resolve cache paths inside a
service. One adapter should own symbol lookup and cache reads.

**Behavior:** The venue keeps the same average of recent cached volume. Unknown
symbols and absent bars return no volume; adapter errors remain visible.

**Code:** `src/forward/core/ports.py`, `src/forward/data/watchlist_market.py`,
`src/forward/services/paper_prices.py`, `src/forward/services/runner.py`,
`src/forward/api/composition.py`, `src/forward/api/legacy/startup.py`,
`tests/`, and `scripts/verify_paper_workflow.py`.

## 2026-11-03: Options backtests fingerprint data through the dataset port

**Status:** Accepted

**Decision:** `OptionsDatasetPort` owns hashing the imported bars and contract
catalog. `OptionsBacktestService` passes dataset references to the port and does
not read the files itself.

**Reason:** Dataset identity depends on the bytes stored by the CSV adapter. The
application service should not know how those bytes are read from disk.

**Behavior:** The backtest result keeps the same SHA-256 fingerprint over the
bars file followed by the instruments file.

**Code:** `src/forward/core/ports.py`, `src/forward/data/options_feed.py`, and
`src/forward/services/options_backtest.py`.

## 2026-11-03: Parameter screening receives history through a cache port

**Status:** Accepted

**Decision:** The SMA screen service receives `CachedMarketFramesPort`. Matrix
building and ranking no longer load the panel file from the compute package, and
the API route calls the screen service instead of importing compute functions.

**Reason:** File access belongs in the data adapter, while the route should only
translate HTTP input and service errors. The same cache adapter already serves
the rest of the application.

**Behavior:** The screen keeps its existing history window, moving-average grid,
holdout split, compute cost, concurrency limit, and response fields.

**Code:** `src/forward/compute/sweep.py`,
`src/forward/services/parameter_screen.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/research.py`, and `src/forward/cli.py`.

## 2026-11-03: Strategy history windows live with strategy code

**Status:** Accepted

**Decision:** `RollingWindow` belongs to `forward.strategy`, where
`StrategyContext` uses it. Tick aggregation stays in `forward.data`.

**Reason:** A strategy's recent-bar view is part of its context, not a market
data adapter. Keeping it beside the strategy context removes a compute-to-data
dependency without adding another implementation.

**Behavior:** The window keeps the same bar limit, order, and dataframe fields.

**Code:** `src/forward/data/aggregator.py`, `src/forward/strategy/base.py`, and
`src/forward/strategy/windows.py`.

## 2026-11-03: Signal context uses storage and enrichment ports

**Status:** Accepted

**Decision:** `SignalContextService` receives ports for SQL storage, cached
market inputs, and live market intelligence, plus its scoring engine.
`LocalSignalContextMarketDataAdapter` reuses the shared daily-bar cache and
learning artifact adapter. Backtests and the paper runner receive the use case
through `SignalContextEnrichmentPort`. SQL sessions and repository calls stay
in `SqlSignalContextStorage`.

**Reason:** Signal scoring should not own database sessions, choose cache paths,
or duplicate parquet and CSV readers. The same enrichment use case serves
paper signals, backtest trades, analytics routes, and learning services.

**Behavior:** Signal keys, scoring, persistence, and outcome calculations stay
the same for available market frames. The equity-series cache file is checked
before the bare-symbol file. The service does not retry a missed cache read
through a second parquet reader. The runner and backtest service require the
enrichment port when they are built.

**Code:** `src/forward/core/ports.py`,
`src/forward/appdb/signal_context_storage.py`,
`src/forward/data/signal_context_market_data.py`,
`src/forward/signal_context/service.py`, `src/forward/services/runner.py`,
`src/forward/services/backtests.py`, `src/forward/api/composition.py`, and
the signal-context and paper-runner tests.

## 2026-11-03: Self-learning state uses an explicit storage port

**Status:** Accepted

**Decision:** `SelfLearningEngine` receives `SelfLearningStateStoragePort`.
`JsonSelfLearningStateAdapter` owns the configured state file, and application
composition owns the shared engine. Trade-signal outcomes use that composed
engine.

**Reason:** Research logic should not choose a file path or hide storage errors.
The application should create one engine with the active research-data setting.

**Behavior:** Missing state starts the initial model. Invalid stored fields,
invalid JSON, and read or write failures surface to the caller. Trade-signal
outcome updates surface errors instead of silently skipping learning.

**Code:** `src/forward/core/ports.py`,
`src/forward/data/self_learning_state.py`,
`src/forward/research/self_learning.py`,
`src/forward/trade_signals.py`, `src/forward/api/composition.py`,
`src/forward/api/legacy/signals.py`, and `tests/conftest.py`.

## 2026-11-03: Options dataset paths belong to the CSV adapter

**Status:** Accepted

**Decision:** `OptionsRunService` passes the configured dataset directory and
owner ID through `OptionsDatasetPort`. `OptionsCsvDatasetAdapter` resolves the
path and returns stored paths as strings.

**Reason:** The use case should coordinate dataset import and metadata storage
without resolving paths or constructing filesystem objects.

**Behavior:** The owner-specific folder layout and cleanup after a failed
metadata write stay the same. Relative paths resolve from the repository root
inside the adapter; absolute configured paths stay absolute.

**Code:** `src/forward/core/ports.py`, `src/forward/data/options_feed.py`,
`src/forward/services/options_runs.py`, `src/forward/api/composition.py`, and
`src/forward/api/routers/options.py`.

## 2026-11-03: Market-intelligence snapshots use a cache port

**Status:** Accepted

**Decision:** `MarketIntelService` reads and writes its computed snapshot
through `MarketIntelSnapshotCachePort`. The local pickle adapter owns the
snapshot file. `LocalMarketIntelDataAdapter` reads universe and benchmark
history, while `LocalMarketIntelIndexRefreshAdapter` owns index refresh I/O.
Application composition owns the shared service instance. Insights receives
market intelligence and daily bars through ports.

**Reason:** The calculation service should depend on market-data ports instead
of reading local files, managing broker refreshes, or owning a process singleton.

**Behavior:** Cache fingerprints, snapshot fields, atomic writes, cache misses,
and the existing cache error behavior stay the same.

**Code:** `src/forward/core/ports.py`,
`src/forward/data/market_intel_market_data.py`,
`src/forward/data/market_intel_index_refresh.py`,
`src/forward/data/market_intel_snapshot.py`,
`src/forward/market_intel/service.py`, `src/forward/insights/service.py`,
`src/forward/api/composition.py`, and market-intelligence callers and fixtures.

## 2026-11-03: Symbol canonicalization is shared core logic

**Status:** Accepted

**Decision:** The canonical symbol rule and exchange-series suffix rule live in
`forward.core.symbols`. Instrument metadata, market-data adapters, research,
and API code use that shared core module.

**Reason:** Symbol normalization is a pure rule shared across layers. Keeping it
in the instrument service made lower layers depend on that application service.

**Behavior:** Uppercasing, whitespace removal, and the supported suffix list
stay the same. Cache filename parsing uses the same suffix rule.

**Code:** `src/forward/core/symbols.py`, `src/forward/instruments/service.py`,
`src/forward/data/`, `src/forward/research/learning_enrich.py`,
`src/forward/market_intel/service.py`, `src/forward/signal_context/service.py`,
`src/forward/screener/service.py`, and callers and tests.

## 2026-11-03: Insights settings and state use a storage port

**Status:** Accepted

**Decision:** `InsightsService` uses `InsightsStateStoragePort` for settings and
delivery state, and `InsightsHoldingsPort` to read holdings. The JSON adapters
own file paths and I/O. Application composition provides these adapters.

**Reason:** The insights use case should manage its report and delivery rules,
not choose paths, read files, or call a broker directly for holdings.

**Behavior:** Settings defaults, unreadable-state handling, and current live,
snapshot, and export holdings selection stay the same. This moves existing
storage behavior behind ports and adds no new fallback.

**Code:** `src/forward/core/ports.py`, `src/forward/data/insights_state.py`,
`src/forward/data/insights_holdings.py`, `src/forward/insights/holdings.py`,
`src/forward/insights/service.py`, `src/forward/api/composition.py`, account
report callers, and `tests/test_insights.py`.

## 2026-11-03: Resolve research data paths from configuration

**Status:** Accepted

**Decision:** `PROJECT_ROOT` is the one project-root definition. The
`research_data_root()` resolver uses it to resolve the configured research-data
directory when it is relative. Application services, data adapters, API
composition, background jobs, and CLI commands use this shared resolver.
Market intelligence no longer exports repository-specific data paths.

**Reason:** The data location is configuration. A calculation service should
not choose a repository folder, and commands should use the same configured
location no matter which directory launched them.

**Behavior:** The default remains the project `data` directory. An absolute
configured path stays absolute, and a relative configured path is based at the
project root.

**Code:** `src/forward/config/settings.py`, `src/forward/appdb/engine.py`,
`src/forward/data/history.py`, `src/forward/instruments/service.py`,
`src/forward/market_intel/service.py`, `src/forward/research/hunt.py`,
`src/forward/api/composition.py`,
`src/forward/api/legacy/startup.py`, `src/forward/cli.py`,
`src/forward/signals/session.py`, and `src/forward/services/runner.py`.

## 2026-11-03: Audit files use the configured data directory

**Status:** Accepted

**Decision:** The JSONL audit writer gets its default location from
`research_data_root()` instead of defining another repository-relative path.

**Reason:** Audit records should follow the configured data location along with
market history and research artifacts.

**Behavior:** The default remains `data/audit/audit.jsonl`. The existing
`FORWARD_AUDIT_PATH` setting still selects a separate file for isolated runs.

**Code:** `src/forward/audit/log.py`, `src/forward/audit/__init__.py`,
`src/forward/data/audit_log.py`, and `src/forward/config/settings.py`.

## 2026-11-03: The local encryption key path is configuration

**Status:** Accepted

**Decision:** `secret_key_path()` resolves `FORWARD_SECRET_KEY_FILE` from the
central settings. `SecretBox` no longer builds a repository path itself.

**Reason:** The crypto code should use the selected key location instead of
owning another project-root calculation.

**Behavior:** The default remains `data/.app_secret`. The existing priority of
`FORWARD_SECRET_KEY` over the local key file, and key-file generation when no
environment key or file exists, stay unchanged.

**Code:** `src/forward/config/settings.py`, `src/forward/auth/crypto.py`,
`src/forward/auth/service.py`, and `.env.example`.

## 2026-11-03: Strategy symbol selection reads the shared market cache

**Status:** Accepted

**Decision:** `ResearchedStrategyUniverse` uses the shared `stock_universe`
selector with `CachedMarketFramesPort`, then confirms each symbol in the IIFL
instrument master.

**Reason:** Strategy authoring and research need the same long-history symbol
rule. One selector prevents the two lists from drifting, while the port keeps
cache reads outside the selector.

**Behavior:** The NSE equity segment, 1,500-bar minimum, `-EQ` exclusion, and
exact broker-contract check stay the same.

**Code:** `src/forward/instruments/strategy_universe.py`,
`src/forward/research/hunt.py`, and `src/forward/data/cached_market_frames.py`.

## 2026-11-03: Research calculations receive market frames through a port

**Status:** Accepted

**Decision:** Research calculations use `CachedMarketFramesPort` for daily
frames. The research harness cleans and shapes the returned frames; the cache
adapter owns cache lookup. Scheduled trackers and the paper runner receive the
same port through composition.

**Reason:** Research rules should not know about Parquet files or the cache
directory. The application already has a cached-frame port and adapter, so a
second history interface would duplicate the same boundary.

**Behavior:** The equity and ETF segments, minimum-history rules, data cleaning,
and returned series stay the same. Callers now provide the history port
explicitly.

**Code:** `src/forward/core/ports.py`, `src/forward/research/hunt.py`,
`src/forward/research/forward_tracker.py`, `src/forward/research/gap_plan.py`,
`src/forward/signals/session.py`, `src/forward/services/runner.py`,
`src/forward/instruments/strategy_universe.py`, application composition, and
research scripts.

## 2026-11-03: Insights reads watchlists through a port

**Status:** Accepted

**Decision:** `InsightsService` receives a watchlist-symbol port. The existing
`WatchlistService` implements the query, and application composition supplies
it to Insights.

**Reason:** The Insights use case should not resolve an API composition
function while building a report. The watchlist query belongs to the watchlist
use case and can be reused directly.

**Behavior:** A missing user still has no watchlist symbols. The current
watchlist-error handling remains unchanged.

**Code:** `src/forward/core/ports.py`, `src/forward/services/watchlists.py`,
`src/forward/insights/service.py`, `src/forward/api/composition.py`, and
`tests/test_insights.py`.

## 2026-11-03: Intelligent alerts receive market, broker, and storage ports

**Status:** Accepted

**Decision:** The intelligent-alert monitor receives its market frames, held
symbols, alert store, notification channels, and config storage through its
constructor. The IIFL holdings adapter, JSON adapters, and channel setup are
created at the application composition boundary.

**Reason:** A scheduled application service must not import API route helpers or
read cache files on its own. The same holdings adapter can serve the scan route,
and the configured data root gives alert files the same location as other data.

**Behavior:** Alert rules, signal scoring, broker field handling, notification
format, and configured alert data location stay the same. The monitor reads
market frames through the shared cache port. A second direct-Parquet read path
was removed.

**Code:** `src/forward/alerts/intelligent.py`,
`src/forward/brokers/iifl/alert_holdings.py`,
`src/forward/data/intelligent_alert_config.py`,
`src/forward/api/composition.py`, `src/forward/core/ports.py`, and the alert
and signal routes.

## 2026-11-03: Price alerts evaluate through ports

**Status:** Accepted

**Decision:** The alert evaluator accepts quote, daily-history, storage, and
notification ports. API routes and the CLI provide the IIFL quote adapter and
the configured daily-history adapter.

**Reason:** The rule evaluator is application logic. It should not resolve
broker contracts or open Parquet files itself, and both entry points should use
the same method contract.

**Behavior:** Rules are checked in their stored order with the same cooldown,
message, and event updates. Quote batching and forming-bar construction stay in
their existing IIFL and daily-history adapters.

**Code:** `src/forward/alerts/engine.py`,
`src/forward/brokers/iifl/alert_quotes.py`,
`src/forward/data/alert_history.py`, `src/forward/api/legacy/alerts.py`, and
`src/forward/cli.py`.

## 2026-11-03: Setup statistics are an injected use case

**Status:** Accepted

**Decision:** Setup-stat replay receives market frames, intelligent-alert
settings, and persistent cache storage through ports. Signal creation receives
the setup-statistics reader as an input.

**Reason:** The replay should not import API composition, scan cache paths, or
hide its data requirements in a global singleton. API, startup, and alert
evaluation now use the same composed service.

**Behavior:** The replay still samples the first 300 sorted symbols, uses the
same enabled rules and stop/target math, refreshes in the background, and keeps
the persistent report under the configured data directory. Its cache key now
uses the actual frames, config, and calculation source.

**Code:** `src/forward/signals_stats.py`, `src/forward/core/ports.py`,
`src/forward/data/cached_market_frames.py`,
`src/forward/data/setup_stats_cache.py`, `src/forward/api/composition.py`,
`src/forward/trade_signals.py`, and the alert, signal, and startup routes.

## 2026-11-03: Briefing builds from supplied market frames

**Status:** Accepted

**Decision:** The briefing builder accepts its settings and market frames as
inputs. `BriefingService` coordinates data reads, config storage, and message
delivery through ports; the JSON adapter owns briefing files.

**Reason:** Message selection is reusable pure logic. It should not read local
Parquet or JSON files, and API and CLI should use the same service.

**Behavior:** Ranking, filters, message text, send order, and last-send details
stay the same. Briefing files now follow the configured data directory.

**Code:** `src/forward/briefing.py`, `src/forward/services/briefing.py`,
`src/forward/data/briefing_storage.py`, `src/forward/core/ports.py`,
`src/forward/api/composition.py`, and the briefing API and CLI.

## 2026-11-03: Trade signal state uses a storage adapter

**Status:** Accepted

**Decision:** The trade signal queue and its settings use a JSON storage port.
Composition creates one shared queue and settings service using the configured
data directory. The queue itself no longer creates files or owns a module
global.

**Reason:** Queue rules should not depend on a repository-relative path, and
API routes and the alert monitor should share the same queue through the
composition boundary.

**Behavior:** Queue ordering, expiry, status updates, settings fields, and the
current malformed-file recovery behavior stay the same. The default files
remain `data/trade_signals/queue.json` and `settings.json`.

**Code:** `src/forward/core/trade_signal_models.py`,
`src/forward/services/trade_signal_factory.py`,
`src/forward/data/trade_signal_storage.py`, `src/forward/core/ports.py`,
`src/forward/api/composition.py`, and the risk, signal, and alert modules.

## 2026-11-03: Composition belongs at the application root

**Status:** Accepted

**Decision:** Application adapters use `forward.composition` to build shared
services. The composition root is outside the API package so the API and CLI
can use the same wiring.

**Reason:** Application construction is shared infrastructure for entry
points, not part of the HTTP adapter.

**Behavior:** Service factories and their singleton lifetimes stay the same.
Imports in the API, CLI, scripts, and tests now point to the shared module.

**Code:** `src/forward/composition.py` and all callers that previously
imported `forward.api.composition`.

## 2026-11-03: Paper prices use an explicit live-tick port

**Status:** Accepted

**Decision:** The paper price adapter requires a live-feed port with explicit
price, timestamp, and quote methods. It does not discover methods at runtime.

**Reason:** Paper fills must use a known feed contract. Runtime method probing
can hide an incomplete adapter and silently drop timestamp or quote data.

**Behavior:** The adapter calls the declared feed methods directly instead of
probing for alternate method names. A missing observation or feed error still
produces no price, so the paper venue cannot fill from an absent tick.

**Code:** `src/forward/core/market_data_ports.py`, `src/forward/services/paper_prices.py`,
and `src/forward/api/price_sources.py`.

## 2026-11-03: Backtest ports live together

**Status:** Accepted

**Decision:** Historical feed, backtest market-data, and backtest storage
contracts live in `forward.core.backtest_ports`.

**Reason:** These interfaces form one workflow and should be easy to find
without loading unrelated application ports.

**Behavior:** Feed data, strategy version lookup, and stored run results keep
the same contracts. Their imports now point directly to the backtest module.

**Code:** `src/forward/core/backtest_ports.py` and its callers.

## 2026-11-03: Paper runner wiring belongs in composition

**Status:** Accepted

**Decision:** The application composition root creates and owns the process
paper runner. The runner service contains the runner behavior, not its singleton
factory.

**Reason:** Service code should receive its adapters. API startup is the
application boundary that assembles those adapters and starts the process loop.

**Behavior:** Startup still creates one runner before starting the loop. Health
and deployment monitoring share a read-only runner port instead of reaching
into a service global.

**Code:** `src/forward/composition.py`,
`src/forward/core/paper_runner_ports.py`, `src/forward/core/ports.py`,
`src/forward/services/runner.py`,
`src/forward/services/paper_health.py`, and API startup and paper routes.

## 2026-11-03: Remove the unused in-memory event bus

**Status:** Accepted

**Decision:** Remove the core event classes and synchronous event bus because
the application does not use them.

**Reason:** The backtest and paper execution paths use direct service calls and
stored order events. A second, unused event system added concepts with no
working callers. The older architecture assessment describes an event bus as a
future proposal, not current runtime behavior.

**Behavior:** No runtime path changes. The stored order event log remains the
source of paper order state.

**Code:** Removed `src/forward/core/events.py` after checking source, tests,
and scripts for imports and calls.

## 2026-11-03: Ports follow application workflows

**Status:** Accepted

**Decision:** Keep each application port beside the workflow it serves. Split the
large core port module into focused modules for broker access, live market data,
paper execution, orders, risk, research, alerts, strategy authoring, and related
areas. Consumers import the port from its owning module. The old combined module
has been removed, with no re-export layer.

**Reason:** A single port file hid which boundary each contract belonged to and
made unrelated services depend on one large interface list. Workflow-level
modules make those boundaries visible and reusable.

**Behavior:** Port method contracts are preserved. Live paper strategies share
the tick snapshot contract, while market subscription and opening-data methods
stay on the market-data port.

**Code:** `src/forward/core/*_ports.py` and imports across `src/forward`,
`tests`, and `scripts`.

## 2026-11-03: Broker access follows ports and composition

**Status:** Accepted

**Decision:** The broker access use case applies session and environment rules
through `BrokerAccessProviderPort`. The composition root wires that use case to
the IIFL client and broker adapter. API routes and the CLI request broker access
from composition instead of creating clients in a service or transport module.

**Reason:** Session policy belongs to the application. IIFL client construction,
connection reuse, and broker objects belong to the broker adapter.

**Behavior:** Read access still works in development when a session exists. Live
order access remains blocked in development. Each worker thread keeps its own
reusable IIFL client.

**Code:** `src/forward/core/broker_ports.py`,
`src/forward/services/broker_access.py`,
`src/forward/brokers/iifl/access.py`, `src/forward/composition.py`, and API and
CLI call sites.

## 2026-11-03: Startup and preview backtests use application services

**Status:** Accepted

**Decision:** The composition root builds and starts the paper runner with its
storage, broker, history, and live market-feed adapters. The HTTP startup adapter
only starts it and reports the existing startup result. The backtest service owns
strategy selection and engine execution for preview runs; the HTTP route selects
input and formats the response.

**Reason:** HTTP lifecycle code should not construct broker adapters or run the
backtest engine itself. The composition root is the place that knows which
adapters belong in the running application.

**Behavior:** Runner startup keeps its existing best-effort handling. Preview
backtests keep their existing strategy, fill, and response rules.

**Code:** `src/forward/composition.py`, `src/forward/services/backtests.py`,
`src/forward/api/legacy/startup.py`, and `src/forward/api/legacy/backtest.py`.

## 2026-11-03: Live feed access goes through a market-feed port

**Status:** Accepted

**Decision:** The IIFL adapter owns session checks, bridge construction,
contract resolution, quote normalization, and listener fan-out. The API
broadcaster and options paper service receive the shared feed through
`LiveMarketFeedPort`.

**Reason:** WebSocket transport and paper-run workflows need market events, but
they should not build broker clients, read IIFL contract files, or parse broker
packet types. One shared port also keeps option quotes on the same connection as
the market stream.

**Behavior:** A single process feed still serves browser subscriptions, paper
deployments, index quotes, and options simulation. Existing session refresh,
reconnect delay, and subscription behavior stay in the IIFL adapter. Orders
from options forward runs remain simulated.

**Code:** `src/forward/core/models.py`, `src/forward/core/live_feed_ports.py`,
`src/forward/brokers/iifl/live_feed.py`, `src/forward/api/stream.py`,
`src/forward/brokers/iifl/feeds.py`, `src/forward/core/tick_candle_ports.py`,
`src/forward/services/tick_candles.py`, and options forward wiring.

The broadcaster also sends tick-candle requests through
`TickCandleService` and `TickCandleAggregationPort`. The DuckDB provider is
resolved on the calling thread because its connections are thread-local. The
WebSocket module uses the required JSON encoder and one candle aggregation
path.

## 2026-11-03: Startup delegates scheduled work to application services

**Status:** Accepted

**Decision:** Startup owns task timing and process lifecycle. Backup creation
and daily market-history workflows run through application services whose
adapters own local file and archive operations.

**Reason:** API startup should schedule work without importing storage and data
implementations. The same workflows can then be assembled without FastAPI or
the local filesystem adapter.

**Behavior:** The existing backup time and retention, daily quality scan,
post-close bar refresh, held-symbol gap repair, and market-history sync keep
their current schedules and data rules.

**Code:** `src/forward/core/daily_maintenance_ports.py`,
`src/forward/data/daily_maintenance.py`,
`src/forward/services/daily_maintenance.py`,
`src/forward/core/backup_ports.py`, `src/forward/infra/backup_adapter.py`,
`src/forward/services/backup.py`, `src/forward/composition.py`, and API startup.

## 2026-11-03: Live order workflows are assembled in composition

**Status:** Accepted

**Decision:** API routes request a live execution service from composition. The
composition root wires the order service, broker portfolio, and live venue.

**Reason:** HTTP handlers should validate requests and map errors, while the
composition root owns the choice of broker and execution adapters.

**Behavior:** Manual orders and trade-signal orders keep the same risk checks,
idempotency keys, broker, and response mapping.

**Code:** `src/forward/composition.py`,
`src/forward/api/legacy/orders.py`, and `src/forward/api/legacy/signals.py`.

## 2026-11-03: Instrument routes use an application service

**Status:** Accepted

**Decision:** Instrument HTTP routes call `InstrumentDirectoryService`. The
composition root wires its local cache adapter through an instrument-directory
port.

**Reason:** Routes should not own instrument-index access or translate cache
records into API data. The adapter owns that mapping, while the application
service gives HTTP handlers a stable query interface.

**Behavior:** Search order, symbol lookup, universe membership, refresh results,
and status fields keep their current shapes.

**Code:** `src/forward/core/instrument_directory_ports.py`,
`src/forward/data/instrument_directory.py`,
`src/forward/services/instrument_directory.py`, `src/forward/composition.py`,
`src/forward/api/routers/instruments.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-03: Regime evaluation runs through an application service

**Status:** Accepted

**Decision:** The API route sends its indicator values to
`MarketRegimeService`. Composition provides the classifier through a port, and
the service returns the response fields.

**Reason:** HTTP code should not call the classifier or choose suggested
strategies. This keeps classification and response policy outside the transport
layer while leaving the existing endpoint contract in place.

**Behavior:** The classifier inputs, model label, regime confidence, and
suggested strategy names stay the same.

**Code:** `src/forward/core/market_regime_ports.py`,
`src/forward/services/market_regime.py`, `src/forward/composition.py`, and
`src/forward/api/routers/regime.py`.

## 2026-11-03: Signal-context dimensions come from the use case

**Status:** Accepted

**Decision:** The signal-context API asks `SignalContextService` for its
supported analytics dimensions before validating a request.

**Reason:** The route should not import analytics implementation details to
learn which inputs the use case accepts. The analytics engine remains the one
source for that list.

**Behavior:** The accepted dimensions and the 400 response for an unknown
dimension stay the same.

**Code:** `src/forward/signal_context/service.py` and
`src/forward/api/routers/signal_explorer.py`.

## 2026-11-03: Backtest uncertainty analysis belongs to the backtest service

**Status:** Accepted

**Decision:** `BacktestService` reads the completed equity curve, owns the
single-analysis lock, and runs the moving-block bootstrap. The API route maps
service errors to HTTP responses.

**Reason:** Compute coordination and backtest data access are application work.
Keeping them in the service leaves the route responsible for HTTP input and
error mapping.

**Behavior:** The run ownership check, busy response, input limits, optional
compute dependency response, and bootstrap output stay the same.

**Code:** `src/forward/services/backtests.py` and
`src/forward/api/routers/backtests.py`.

## 2026-11-03: Learning axis rules come from the learning service

**Status:** Accepted

**Decision:** The learning API gets its axis catalogue and accepted names from
`LearningService`.

**Reason:** Axis labels, defaults, and unavailable reasons are part of the
analysis use case. The HTTP layer should not depend on the research module to
validate or display them.

**Behavior:** The catalogue response, default axes, and unknown-axis error stay
the same.

**Code:** `src/forward/services/learning.py` and
`src/forward/api/routers/learning.py`.

## 2026-11-03: Strategy sizing previews run through the strategy service

**Status:** Accepted

**Decision:** The strategy API validates the sizing request, then calls
`StrategyService.preview_sizing` to build the sizing configuration and run the
shared position sizing engine.

**Reason:** The route should not import a strategy engine or calculate a
position size. The same use case can be called by another delivery layer.

**Behavior:** Request fields, sizing rules, and the response mapping stay the
same.

**Code:** `src/forward/services/strategies.py` and
`src/forward/api/routers/strategies.py`.

## 2026-11-03: Market-intelligence queries own filtering and ranking

**Status:** Accepted

**Decision:** The market-intelligence use case validates sort fields, filters
stock rows, orders sectors and stocks, resolves stock symbols, and shapes regime
configuration. Routes map query errors to HTTP responses and serialize results.

**Reason:** Ranking and symbol selection describe market-intelligence behavior.
Keeping them in the use case avoids a second set of rules in the transport layer.

**Behavior:** Supported sort fields, ordering, filters, limit handling, symbol
canonicalization, and response fields stay the same.

**Code:** `src/forward/core/errors.py`, `src/forward/market_intel/service.py`,
and `src/forward/api/routers/market_intel.py`.

## 2026-11-03: Analytics filters and account reads run through an application service

**Status:** Accepted

**Decision:** Analytics filtering, source and evidence-grade validation,
aggregate-size checks, account-scoped reads, trade-page totals, and report
calculations belong to `AnalyticsService`. The API maps its typed errors to HTTP
responses and serializes the result.

**Reason:** These rules define which trades a report includes and how their
results are grouped. Keeping them next to the query use case gives every
analytics endpoint one filter meaning and keeps HTTP and storage details out of
the report logic.

**Behavior:** Existing filter names and normalization, full-book total counts,
page filters, response fields, and the 200,000-row aggregate limit stay the same.
Coverage read errors surface instead of being converted into an unknown-coverage
placeholder.

**Code:** `src/forward/core/analytics_ports.py`,
`src/forward/services/analytics.py`, `src/forward/composition.py`, and
`src/forward/api/routers/analytics.py`.

## 2026-11-03: Options strategy authoring runs through an application service

**Status:** Accepted

**Decision:** Options definition construction, validation, listing, creation,
versioning, and owned version lookup belong to `OptionsStrategyService`. The HTTP
router sends editor data to that service and maps its validation errors to the
existing API responses. The options kind and engine key are declared once in the
strategy domain.

**Reason:** These rules describe the options strategy workflow and were repeated
in the route. Centralizing them keeps the saved definition consistent with the
backtest and paper runners. The editor must describe a reusable builder, not
ship one user's strategy as its preset.

**Behavior:** The route shapes, validation report fields, strategy ownership
checks, versioning flow, saved-version selection, and response payloads stay the
same. A new editor opens with an empty definition and cannot save until required
fields, stages, and legs are filled in.

**Code:** `src/forward/strategy/options.py`,
`src/forward/services/options_strategies.py`, `src/forward/composition.py`, and
`src/forward/api/routers/options.py`, `web/src/OptionsStrategiesPanel.tsx`.

## 2026-11-03: Risk routes write audit events through the audit service

**Status:** Accepted

**Decision:** Risk routes get the configurable-limit vocabulary from
`RiskStateService` and send post-change audit events to `AuditLogService`; they
do not read service internals or call the JSONL audit adapter directly.

**Reason:** Application services own the risk vocabulary and audit event
construction. A state change should not depend on a route knowing the file
format.

**Behavior:** Risk event fields, the JSONL sink, and best-effort logging behavior
stay the same. Audit errors still do not undo a committed risk-state change.

**Code:** `src/forward/services/audit_log.py`, `src/forward/services/risk.py`,
and `src/forward/api/routers/risk.py`.

## 2026-11-03: Reconciliation notifications use a delivery port

**Status:** Accepted

**Decision:** Critical reconciliation reports are formatted and sent by
`ReconciliationNotifier`. Composition supplies the configured channels through
the existing `AlertDeliveryPort`; the API passes the notifier callback only when
the request enables notifications.

**Reason:** Notification formatting and channel selection are application work.
Keeping alert channel imports out of the HTTP router makes the route independent
of Telegram, SMS, Discord, Slack, and log adapters.

**Behavior:** Only critical reports are sent. The channel order, first-success
delivery, per-channel error handling, and request-level `notify` switch stay the
same.

**Code:** `src/forward/services/reconciliation_notifications.py`,
`src/forward/composition.py`, and
`src/forward/api/routers/reconciliation.py`.

## 2026-11-03: Shared history-feed HTTP mapping lives in the API adapter

**Status:** Accepted

**Decision:** The cached-history feed helper used by backtest and research
routes lives in `api/legacy/common.py`. It calls the research-history use case
and maps cache errors to HTTP responses. Routes do not import helpers from one
another.

**Reason:** A route is a transport adapter, and another route is not a shared
application dependency. Keeping the shared HTTP mapping in the API package
removes that route-to-route link while preserving one error response for both
endpoints.

**Behavior:** Symbol uppercasing, status codes, messages, cache loading, and the
returned feed remain the same.

**Code:** `src/forward/api/legacy/common.py`,
`src/forward/api/legacy/backtest.py`, and
`src/forward/api/legacy/research.py`.

## 2026-11-03: Legacy order access checks are shared API adapters

**Status:** Accepted

**Decision:** Shared legacy API checks for order ownership, permissions, live
execution, broker access, and risk-state reads live in `api/legacy/access.py`.
Order, signal, risk, and system routes use that module instead of importing
helpers from the risk route.

**Reason:** A route module should not act as a helper library for other routes.
Keeping these HTTP-facing checks in one adapter module gives all legacy order
paths the same account and execution gates.

**Behavior:** Existing checks, response codes, messages, and risk-state reporting
remain the same. The unused `_kill_switch_engaged` helper was removed.

**Code:** `src/forward/api/legacy/access.py`,
`src/forward/api/legacy/risk.py`, `src/forward/api/legacy/orders.py`,
`src/forward/api/legacy/signals.py`, and `src/forward/api/legacy/system.py`.

## 2026-11-03: Trade-signal scanning runs as an application service

**Status:** Accepted

**Decision:** The trade-signal scan loads its settings and market data, selects
research and rule-based candidates, applies queue and holding checks, builds
signals, and sends previews through injected ports in
`TradeSignalScanService`. Composition supplies those ports. The HTTP route only
starts the scan and returns its result.

**Reason:** Candidate selection and queue updates define the signal workflow,
while channel setup, storage, and market data come from adapters. Keeping the
workflow in a service lets other entry points reuse the same scan without
depending on an HTTP route.

**Behavior:** Candidate order, active limits, pending-signal checks, sell
holding checks, notification order, response fields, and current per-source
error handling stay the same. The existing error handling was moved, not
expanded.

**Code:** `src/forward/core/trade_signal_ports.py`,
`src/forward/services/trade_signal_scan.py`, `src/forward/composition.py`, and
`src/forward/api/legacy/signals.py`.

## 2026-11-03: Signal approval uses the shared execution workflow

**Status:** Accepted

**Decision:** Signal lookup, bracket order construction, idempotency keys, audit
recording, queue state changes, and confirmation delivery belong to
`TradeSignalExecutionService`. The API keeps account and live-mode checks and
maps service errors to the current HTTP responses.

**Reason:** Approving a signal is an application use case built on the shared
order lifecycle. Keeping its order sequence out of the route makes that
workflow reusable and keeps HTTP status handling at the API edge.

**Behavior:** Signal validation still happens before the live-mode check. The
three order legs, idempotency keys, partial-order details on venue errors,
audit fields, queue updates, and response fields keep their current order and
shape. Existing notification error handling was moved without adding recovery.

**Code:** `src/forward/core/trade_signal_ports.py`,
`src/forward/services/trade_signal_execution.py`, `src/forward/composition.py`,
and `src/forward/api/legacy/signals.py`.

## 2026-11-03: P&L calendar requests use one application service

**Status:** Accepted

**Decision:** Month views, day details, manual realized-P&L changes, and their
data-source coordination belong to `PnlCalendarService`. The API routes pass
the request values and map invalid calendar dates to HTTP 400 responses.

**Reason:** The routes were coordinating paper storage, real holdings, cached
prices, official broker reports, and calendar calculations themselves. One
service now owns that workflow and reuses the existing calendar calculations.

**Behavior:** Paper and real response fields, totals, source labels, date rules,
storage calls, and error messages remain the same.

**Code:** `src/forward/services/pnl_calendar.py`, `src/forward/composition.py`,
and `src/forward/api/legacy/pnl.py`.

## 2026-11-03: Broker login accepts the broker's callback fields directly

**Status:** Accepted

**Decision:** The IIFL callback reads only `authcode` and `clientid`, which are
the field names used by the broker redirect. Session responses are read using
the dictionary contract defined by `BrokerSessionPort`. Routes depend directly
on the composed broker-session service.

**Reason:** Extra parameter spellings were duplicate compatibility paths, and
the manual login route treated a dictionary as an object. One callback shape and
one declared session response shape keep the adapter aligned with its ports.

**Behavior:** IIFL's callback fields and the login response keys stay the same.
The manual login response now returns the session fields supplied by the port.

**Code:** `src/forward/api/legacy/login.py`,
`src/forward/core/broker_ports.py`, and
`src/forward/services/broker_session.py`.

## 2026-11-03: Options paper snapshots use valid exception syntax

**Status:** Accepted

**Decision:** Catch `TypeError` and `ValueError` together with a tuple in the
options paper snapshot price conversion.

**Reason:** The comma form is invalid Python syntax and prevents the options
paper service from loading. The tuple form records the two intended conversion
errors and keeps the current behavior of skipping an unconvertible quote.

**Behavior:** Valid quotes still mark positions. Missing or unconvertible quote
prices are skipped as before. No fallback price is added.

**Code:** `src/forward/services/options_forward.py`.

## 2026-11-06: Exclude trades with unknown P&L from forward comparisons

**Status:** Accepted

**Decision:** Forward checks include a trade in performance statistics only
when its return and net P&L are known. They count closed rows excluded for
missing P&L inputs in the response.

**Reason:** Treating missing net P&L as zero changes win and profit-factor
results without evidence.

**Behavior:** Incomplete trades do not enter the comparison sample and remain
visible in the excluded-trade count.

**Code:** `src/forward/services/forward_check.py`.

## 2026-11-06: Show strategy evaluation failures in paper status

**Status:** Accepted

**Decision:** When one deployment's strategy evaluation fails, the runner marks
that deployment as `ERROR` and records the reason in its status. The paper page
shows the reason when the run is expanded.

**Reason:** Keeping one deployment from interrupting others should not make a
failed strategy look like a valid run with no signal.

**Behavior:** The runner continues processing other deployments. The failed
deployment's error and last known runner state remain visible to the operator.

**Code:** `src/forward/services/runner.py`, `web/src/paper-api.ts`, and
`web/src/PaperRuns.tsx`.

## 2026-11-06: Resolve paper rules through the configured strategy version

**Status:** Accepted

**Decision:** Every deployment loop requires an injected rule resolver. It does
not fall back to a registry entry with a matching strategy name when the saved
strategy version cannot be resolved.

**Reason:** Running a same-named built-in strategy can change the rules while
the deployment still reports its saved strategy identity.

**Behavior:** Missing or failed saved-rule resolution marks the deployment as
an error and prevents orders.

**Code:** `src/forward/services/runner.py`.

## 2026-11-06: Show paper monitor read failures

**Status:** Accepted

**Decision:** The paper monitor clears P&L data when its request fails and shows
the request error. It does not leave an old P&L value looking current. A failed
F&O detail refresh clears that snapshot and displays the error. Missing marks or
commissions show an incomplete-P&L message.

**Reason:** A failed refresh must not look like a new or unchanged result, and
an unknown balance must not look like zero.

**Behavior:** Paper-health errors, runner-status errors, P&L request errors,
F&O refresh errors, and incomplete F&O P&L are visible in the paper monitor.

**Code:** `web/src/PaperRuns.tsx`, `web/src/OptionsStrategiesPanel.tsx`,
`web/src/paper-api.ts`, and `web/src/options-api.ts`.

## 2026-11-06: Keep one paper monitoring read path

**Status:** Accepted

**Decision:** The paper UI uses the combined deployment overview for monitoring
data. Unused per-field API wrappers are removed, and the activity limit must be
provided by the caller.

**Reason:** Fetching separate views can show mismatched snapshots, and unused
wrappers invite a second path that can drift from the combined read.

**Behavior:** Paper monitor detail comes from one overview response. Callers
choose the activity limit explicitly.

**Code:** `web/src/paper-api.ts`.

## 2026-11-06: Reject backtest configs with unknown or non-finite values

**Status:** Accepted

**Decision:** Backtest payload parsing rejects unknown fields and explicit null
objects. Numeric run settings must be finite numbers, not booleans, NaN, or
infinity.

**Reason:** Ignoring a field or accepting a non-finite value can make a saved
run differ from what its request appears to say.

**Behavior:** Invalid payload values fail with a field-specific `ConfigError`.

**Code:** `src/forward/backtest/config.py`.

## 2026-11-06: Do not value a benchmark from stale marks

**Status:** Accepted

**Decision:** The hold benchmark leaves each sleeve in cash until its first
tradable bar. After entry, every requested benchmark date must have a valid
close mark; missing marks make the benchmark unavailable.

**Reason:** Forward-filling a stale close can make benchmark performance look
complete when the market value is unknown.

**Behavior:** The benchmark raises with the symbol and date of a missing mark.

**Code:** `src/forward/backtest/hold_benchmark.py`.

## 2026-11-06: Surface live tick source failures

**Status:** Accepted

**Decision:** The paper price source returns no price only when the feed has no
valid positive finite price. Exceptions from the live tick adapter propagate to
the caller.

**Reason:** Turning a feed failure into an ordinary missing quote hides the
cause of skipped paper execution and health checks.

**Behavior:** Callers can report or handle adapter errors at their own boundary.

**Code:** `src/forward/services/paper_prices.py`.

## 2026-11-06: Fail visibly on price and scan errors

**Status:** Accepted

**Decision:** Runner price reads propagate venue failures to the runner loop,
and full-market scans raise a symbol-specific error when a frame cannot be
scored or timed.

**Reason:** Treating a failed quote as no quote or dropping an unscorable
symbol makes incomplete execution and scan results look normal.

**Behavior:** Runner management reports quote errors through its existing
failure boundary. A scan fails with the symbol and original error attached.

**Code:** `src/forward/services/runner.py` and
`src/forward/services/local_scans.py`.

## 2026-11-06: Require usable trade-signal risk inputs

**Status:** Accepted

**Decision:** ATR stops require 20 valid bars and a finite positive ATR. Stop
prices, risk budgets, and quantities must be valid; the signal builder does not
switch to percentage stops or force a one-share position when those checks fail.

**Reason:** A guessed stop or quantity can make a signal's stated risk differ
from the risk used to size it.

**Behavior:** Missing ATR history, invalid stop distance, or a budget below the
one-share risk raises a clear error before a signal is queued.

**Code:** `src/forward/services/trade_signal_factory.py`.

## 2026-11-03: P&L calendar exception clauses use valid syntax

**Status:** Accepted

**Decision:** Write the multi-type exception handlers in the P&L calendar with
tuple syntax.

**Reason:** The comma form is invalid Python syntax and prevents the calendar
service from loading. The tuple form keeps the same exception types and existing
per-record skip or report behavior.

**Behavior:** Malformed settlement rows are still skipped, and the existing
optional gap-plan handling is unchanged. No fallback behavior is added.

**Code:** `src/forward/services/pnl_calendar.py`.

## 2026-11-03: The Windows launcher uses its own project folder

**Status:** Accepted

**Decision:** Resolve the project directory from the launcher's location instead
of a fixed machine path.

**Reason:** A fixed `D:\\ALGO` path ties startup to one installation location
even though the launcher already lives inside the project.

**Behavior:** Double-clicking the launcher from the project folder still starts
the same app and prepares the same configured database.

**Code:** `start-forward.bat`.

## 2026-11-03: Paper health stays pending while the runner is offline

**Status:** Accepted

**Decision:** Mark paper health as pending when the runner reports that it is not
running. Do not run deployment checks while the runner is unavailable; refresh
health after it reports that it is running.

**Reason:** A completed market-data check cannot say that a deployment is ready
while no runner is evaluating it. The paper screen already polls pending health
more often, so keeping the state pending makes the display match the runner.

**Behavior:** The page says it is waiting for the runner. It does not serve a
completed state as current while the runner is stopped. When the runner starts,
the next health request starts a refresh and can report a completed state.

**Code:** `src/forward/services/paper_health.py`, `web/src/PaperRuns.tsx`, and
`web/src/api.ts`.

## 2026-11-03: Bridge TLS settings enter through adapter construction

**Status:** Accepted

**Decision:** Pass the configured TLS verification choice into each IIFL bridge
client. The bridge adapter does not read application settings from a global
provider.

**Reason:** Broker transport security is an application setting. The
composition boundary should provide it so a bridge connection can be built and
understood from its constructor inputs.

**Behavior:** The same configured certificate verification setting controls
bridge connections created by the live feed, historical feed runner, and CLI.
The F&O options feed uses only bridge ticks. A disconnected bridge leaves its
quotes unavailable and cannot create simulated fills from REST polling.

**Code:** `src/forward/brokers/iifl/bridge.py`,
`src/forward/brokers/iifl/live_feed.py`, `src/forward/brokers/iifl/feeds.py`,
`src/forward/brokers/iifl/options_forward.py`, `src/forward/composition.py`,
and `src/forward/cli.py`.

## 2026-11-03: Options market adapter receives settings at construction

**Status:** Accepted

**Decision:** Pass the application settings into `IiflOptionsMarketAdapter`
when composition creates it. The adapter does not fetch global settings while
handling requests.

**Reason:** The adapter's session and index-contract paths depend on runtime
configuration. Explicit input makes that dependency visible and keeps settings
ownership in composition.

**Behavior:** Session storage, IIFL client setup, and the index-contract path
continue to use the same configured values.

**Code:** `src/forward/brokers/iifl/options_market.py` and
`src/forward/composition.py`.

## 2026-11-03: Live feed uses explicit session and instrument ports

**Status:** Accepted

**Decision:** Build the IIFL live-feed adapter with a session store, broker
instrument catalog, and TLS setting supplied by composition. Remove its eager
best-effort contract load.

**Reason:** The feed adapter should depend on the contract lookup behavior it
needs, not construct its own catalog or hide cache failures during startup.

**Behavior:** Contract IDs are loaded through the shared catalog when requested.
Missing or unreadable contract data is reported by that catalog at lookup time.

**Code:** `src/forward/brokers/iifl/live_feed.py`,
`src/forward/composition.py`, and `src/forward/core/broker_ports.py`.

## 2026-11-03: Broker rows require exact contracts and timestamps

**Status:** Accepted

**Decision:** Resolve broker positions, holdings, and trades only through the
loaded contract master. Reject a fill whose timestamp is missing or invalid;
do not create a synthetic instrument or use the current time.

**Reason:** A guessed contract can assign the wrong asset class or ID, and a
current-time timestamp changes the history of a real fill. Unknown broker data
must remain an explicit error.

**Behavior:** An unmatched contract or invalid fill timestamp now raises an
error instead of producing a guessed domain object or timestamp.

**Code:** `src/forward/brokers/iifl/broker.py`.

## 2026-11-03: Historical feed dates are explicit inputs

**Status:** Accepted

**Decision:** Require the IIFL historical feed caller to supply its interval,
date range, and application timeframe. Do not use a fixed example year when a
caller omits the range.

**Reason:** A historical-data adapter should fetch the period its use case
requested. A built-in date range can return valid but unrelated candles.

**Behavior:** Existing warmup calls keep their requested range. Any new caller
must choose the date range and timeframe directly.

**Code:** `src/forward/brokers/iifl/feeds.py` and
`src/forward/brokers/iifl/warmup.py`.

## 2026-11-03: Bridge topics require a known exchange segment

**Status:** Accepted

**Decision:** Build bridge topics only for exchange segments listed in the
adapter's explicit map. Reject unknown segments instead of lowercasing and
guessing a topic prefix.

**Reason:** A guessed prefix can subscribe to the wrong topic and leave a feed
quiet without a clear contract error.

**Behavior:** Supported segments keep their existing topics. An unknown segment
fails when its bridge topic is built.

**Code:** `src/forward/brokers/iifl/feeds.py`.

## 2026-11-03: IIFL rate limits require shared storage

**Status:** Accepted

**Decision:** Count IIFL requests in the shared SQLite window. If it cannot be
opened or updated, fail the request instead of switching to a process-local
counter.

**Reason:** A local counter cannot enforce one account's limit across the app
and other clients. Continuing would allow more calls than the broker permits.

**Behavior:** IIFL calls stop with a storage error while the shared limiter is
unavailable. Tests can still inject a memory window explicitly.

**Code:** `src/forward/brokers/iifl/ratelimit.py`,
`src/forward/brokers/iifl/client.py`, and `tests/test_iifl_ratelimit.py`.

## 2026-11-06: Broker venues receive contract lookup from composition

**Status:** Accepted

**Decision:** Require an application instrument-lookup port when building a
broker venue. Do not fall through to a broker adapter's internal contract
master.

**Reason:** The execution adapter needs an exact contract but should not reach
inside another adapter to find one. Composition already owns the shared
instrument catalog.

**Behavior:** Direct and deployment live orders resolve contracts through the
same application catalog. Missing catalog entries fail before broker
submission.

**Code:** `src/forward/brokers/venue.py`,
`src/forward/brokers/live_deployment.py`,
`src/forward/brokers/runner_venue_provider.py`,
`src/forward/composition.py`, and `tests/test_execution_service.py`.

## 2026-11-06: Historical chunk errors stay visible

**Status:** Accepted

**Decision:** Keep IIFL intraday date chunking, but let invalid ranges and failed
chunks raise. Do not retry the whole range as one request or return an invalid
date string unchanged.

**Reason:** A large one-shot request may silently return partial history, and a
malformed date must not become a different request shape.

**Behavior:** Successful chunks are combined as before. Any failed chunk stops
the fetch and exposes the broker or parse error.

**Code:** `src/forward/brokers/iifl/client.py`.

## 2026-11-03: Paper health owns its refresh state

**Status:** Accepted

**Decision:** Keep paper-health result and refresh state inside an injected
`PaperHealthService`. The composition layer builds it from the market, history,
storage, runner, and market-calendar dependencies. API routes ask that service
for health rather than assembling the workflow themselves.

**Reason:** Module-level cache state made the service hard to isolate, and the
route knew too much about its adapters. A service instance gives the cache one
clear owner and keeps adapter wiring in the composition layer. The service also
must not reach into the global calendar or runner helpers to find runtime state;
composition supplies the calendar it needs.

**Behavior:** Health stays pending while the runner is unavailable. Refreshes
run in the background only after the runner reports that it is running. The
health snapshot cache and in-progress refresh state belong to the service.

**Code:** `src/forward/services/paper_health.py`,
`src/forward/composition.py`, `src/forward/api/routers/paper.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-03: Track-record dates use the exchange calendar

**Status:** Accepted

**Decision:** Inject the exchange calendar into `TrackRecordService` and use it
to build the replay dates. Keep the first fill's date in the curve even if the
calendar marks that date closed.

The exchange calendar interface lives in `forward.core.calendar_ports` so
application services and signal rules do not depend on the NSE calendar adapter.
Data-status calculations also require that port from composition; they do not
construct an exchange calendar when one was not supplied.

**Reason:** The benchmark price series is not the schedule for exchange
sessions. Using it as one, then guessing weekdays when it is missing, couples
valuation dates to benchmark data and can include holidays.

**Behavior:** The track record still includes the fill date and every exchange
session through today. Missing benchmark closes remain missing benchmark
values; they do not change which dates the portfolio is valued on.

**Code:** `src/forward/core/calendar_ports.py`,
`src/forward/market_calendar.py`, `src/forward/services/track_record.py`,
`src/forward/services/paper_health.py`, `src/forward/services/data_status.py`,
`src/forward/services/vwap_reversion.py`,
`src/forward/services/sector_opening.py`, `src/forward/services/monitoring.py`,
`src/forward/services/runner.py`,
`src/forward/signals/session.py`, and `src/forward/composition.py`.

## 2026-11-03: Monitoring depends on a ledger port

**Status:** Accepted

**Decision:** Type the deployment monitor against a focused paper-ledger port
that exposes only account snapshots and fills. Do not reach into ledger storage
or private ledger methods from the monitor.

**Reason:** The monitor is an application use case. Its dependency should state
which read operations it needs and let composition provide the ledger adapter.
An unused helper that called a private ledger method was removed during the
boundary review.

**Behavior:** Monitoring still values the account from the same snapshot and
fill history. The unused helper had no callers and did not produce a response.

**Code:** `src/forward/core/paper_ports.py`,
`src/forward/services/monitoring.py`, and `src/forward/composition.py`.

## 2026-11-03: Quote-derived bars live in the data layer

**Status:** Accepted

**Decision:** Keep the quote-to-bar collector in `forward.data`. The stream
adapter writes observations into it and the market-data port exposes completed
bars to application services.

**Reason:** The collector transforms external quote events into a market-data
shape. It does not run a use case, place an order, or decide a strategy signal.

**Behavior:** The collector keeps the same session, gap, freshness, completion,
and payload rules. The API and VWAP workflow still use the same bar source.

**Code:** `src/forward/data/observed_bars.py`, `src/forward/api/stream.py`,
`src/forward/core/market_data_ports.py`, and `src/forward/services/vwap_reversion.py`.

## 2026-11-03: Saved-scan storage errors stay visible

**Status:** Accepted

**Decision:** Seed built-in scans only when the saved-scan file does not exist.
Let read and decode errors reach the caller instead of replacing saved scans
with built-ins.

**Reason:** Replacing unreadable user data with defaults hides the storage error
and makes the saved scans appear to have been deleted.

**Behavior:** A new store still starts with the built-in scan set. An existing
store is returned as saved, and an unreadable store fails visibly.

**Code:** `src/forward/services/saved_scans.py` and
`src/forward/data/saved_scans.py`.

## 2026-11-03: Empty briefing watchlists stay empty

**Status:** Accepted

**Decision:** Save the briefing watchlist exactly as supplied. Do not replace an
empty list with the full default symbol universe.

**Reason:** An empty list is an explicit choice and should not silently widen a
scan to every default symbol. Omitted fields still use the defaults declared by
`BriefingConfig`.

**Behavior:** `universe="watchlist"` with an empty watchlist produces no matches.
The service no longer needs a second default-symbol list from composition.

**Code:** `src/forward/services/briefing.py` and
`src/forward/composition.py`.

## 2026-11-03: Holding-history repair uses a market-data port

**Status:** Accepted

**Decision:** Pass `ScannerMarketPort` to the holding-history repair workflow.
Keep IIFL session and contract lookup inside `IiflScannerMarketAdapter`; the
daily-history service and local file adapter receive the market-data interface,
not the broker client.

**Reason:** A use case should depend on the operation it needs, not on a
broker-specific client. Reusing the scanner's exact-contract history port avoids
adding a second broker interface for the same daily-bar request.

**Behavior:** Held-symbol files are refreshed from public history first. When an
authenticated IIFL market adapter is available, missing files are seeded and
gaps are repaired using the same exact-contract daily candles.

**Code:** `src/forward/core/daily_maintenance_ports.py`,
`src/forward/data/daily_maintenance.py`, `src/forward/data/gap_repair.py`,
`src/forward/services/daily_maintenance.py`, and
`src/forward/api/legacy/startup.py`, and `src/forward/composition.py`.

## 2026-11-03: The quote stream receives its bar collector through a port

**Status:** Accepted

**Decision:** Inject `ObservedBarsPort` into the WebSocket broadcaster. Build
the quote-to-bar adapter in the composition root; the API transport does not
construct a data adapter.

**Reason:** The broadcaster coordinates incoming feed events and browser
subscriptions. Bar rules belong to the data adapter, and a port keeps the event
flow testable without making the transport import the data layer.

**Behavior:** Quote validation, session boundaries, gap checks, and completed
bar snapshots stay in the same collector. Only ownership and construction move.

**Code:** `src/forward/core/observed_bars_ports.py`,
`src/forward/data/observed_bars.py`, `src/forward/api/stream.py`, and
`src/forward/composition.py`.

## 2026-11-03: The composition root does not resolve API adapters

**Status:** Accepted

**Decision:** Require callers to pass the live market-data port into
`get_paper_health_service`. API startup and the paper route obtain the shared
stream and pass it to composition.

**Reason:** The composition root may assemble services from adapters, but it
must not import an API module to obtain one. The API owns the WebSocket stream
adapter and can supply it at the application boundary.

**Behavior:** Startup and paper-health requests still use the same process-wide
market stream. The health service continues to be created once and reused.

**Code:** `src/forward/composition.py`,
`src/forward/api/legacy/startup.py`, and `src/forward/api/routers/paper.py`.

## 2026-11-03: F&O endpoints live in a feature API module

**Status:** Accepted

**Decision:** Keep options strategy, dataset, backtest, and forward-run types
and requests in `web/src/options-api.ts`. Use the shared versioned API request
function from the common client.

**Reason:** The main API file mixed unrelated feature contracts. The F&O panel
and paper-run view are the only current consumers of these options contracts,
so the options workflow can be changed and understood as one feature.

**Behavior:** Request paths, payloads, response types, and error handling stay
the same. The two consuming views now import options operations from the
feature module.

**Code:** `web/src/api.ts`, `web/src/options-api.ts`,
`web/src/OptionsStrategiesPanel.tsx`, and `web/src/PaperRuns.tsx`.

## 2026-11-03: Platform-account calls live in an auth API module

**Status:** Accepted

**Decision:** Keep account setup, sign-in, profile, MFA, session, and API-key
contracts in `web/src/auth-api.ts`. Keep IIFL broker-session calls with the
broker-facing API operations.

**Reason:** Platform identity and broker authorization are separate sessions
with different owners and lifecycles. Keeping their client contracts separate
matches the server boundary and makes it harder to confuse one login with the
other.

**Behavior:** Both modules use the shared versioned or unversioned request
client as appropriate. The account login flow keeps its MFA challenge response;
the broker login flow is unchanged.

**Code:** `web/src/auth-api.ts`, `web/src/api.ts`, `web/src/AuthGate.tsx`,
`web/src/App.tsx`, and `web/src/lib/useSession.ts`.

## 2026-11-03: Watchlist and instrument contracts share a feature module

**Status:** Accepted

**Decision:** Keep watchlist operations and instrument-directory lookups in
`web/src/watchlist-api.ts`. Keep generic quote reads in the market API module.

**Reason:** The watchlist panel, chart watchlist, and symbol picker need the
same instrument and list contracts. One feature module gives these components
one source for those types and operations.

**Behavior:** Watchlist and instrument URLs, request bodies, and response
shapes stay the same. Generic quote lookup remains in the general API client.

**Code:** `web/src/watchlist-api.ts`, `web/src/WatchlistPanel.tsx`,
`web/src/lib/useChartWatchlist.ts`, and `web/src/components/ui/symbol-picker.tsx`.

## 2026-11-03: Strategy validation has separate research and structure types

**Status:** Accepted

**Decision:** Name the walk-forward result `ResearchStrategyValidation` and
the definition check `StrategyDefinitionValidation`. Keep saved-strategy
authoring and version operations in `web/src/strategy-api.ts`.

**Reason:** The API client declared two interfaces with the same name. TypeScript
merged them even though one describes statistical research and the other checks
whether a saved definition can run. Separate names stop one response from being
mistaken for the other.

**Behavior:** Endpoint paths and payloads stay the same. Consumers now import
saved-strategy contracts from the strategy module and each validation result
uses its own response shape.

**Code:** `web/src/api.ts`, `web/src/strategy-api.ts`,
`web/src/options-api.ts`, `web/src/StrategiesPanel.tsx`,
`web/src/ResearchPanel.tsx`, and other strategy consumers.

## 2026-11-03: The frontend shares one JSON transport

**Status:** Accepted

**Decision:** Keep endpoint-specific error policies in the typed API client,
but use one request helper for credentials, shared GET handling, and JSON body
parsing.

**Reason:** The legacy and versioned clients repeated transport code. Their
error shapes differ, so only the common HTTP work is shared; the versioned
client still raises `ApiError` with its machine-readable code.

**Behavior:** Requests keep their URL, credentials, and GET-sharing behavior.
The legacy client still rejects non-JSON success responses, and the versioned
client still accepts empty success bodies and preserves its structured errors.

**Code:** `web/src/lib/api-client.ts`, `web/src/api.ts`, and
`web/src/lib/shared-fetch.ts`.

## 2026-11-03: Keep analytics tone vocabularies scoped to their meaning

**Status:** Accepted

**Decision:** Keep `flat` in trade analytics and `muted` in learning and signal
context as separate view-model values, even though both use quiet colors.

**Reason:** `flat` means a measured value is neutral or zero. `muted` means a
result is unknown, insufficient, or not a claim. Treating them as one shared
value would make these different states easier to confuse in code and in the UI.

**Behavior:** Each view keeps the values that match its evidence rules. Shared
color helpers remain available for surfaces that use the common tone vocabulary.

## 2026-11-03: Keep paper endpoints in their own client module

**Status:** Accepted

**Decision:** Put deployment, runner health, challenger comparison, and deployment
monitor endpoints in `web/src/paper-api.ts`. Keep their types beside those
endpoints and update consumers to import from that module.

**Reason:** These routes form one paper-trading domain and were a large block in
the general API client. Keeping that block together makes its lifecycle and
read-only monitor contracts easier to inspect. Shared learning statistics live
in a small contract file so both API modules can use the same shape without
depending on each other at runtime.

**Behavior:** Request paths, payloads, and response types stay the same. There
are no re-exports from the general API client; callers use the paper API module.

**Code:** `web/src/paper-api.ts`, `web/src/learning-contracts.ts`,
`web/src/api.ts`, and paper deployment, monitor, strategy, and dashboard callers.

## 2026-11-03: Keep versioned backtest endpoints in their own client module

**Status:** Accepted

**Decision:** Put the versioned backtest request, run, metrics, curve, trade,
monthly, benchmark, and uncertainty contracts and endpoints in
`web/src/backtest-api.ts`. Keep the old single-call backtest transport in
`web/src/api.ts` until its remaining consumers are deliberately migrated.

**Reason:** The versioned workflow has a separate lifecycle and response model.
Keeping its contracts next to its endpoints makes that boundary visible without
changing the older endpoint's behavior. Chart points use a shared contract
because the optimization API also returns them.

**Behavior:** Request paths, payloads, and successful response shapes stay the
same. Errors from benchmark and uncertainty endpoints now use the same
structured versioned error type as the rest of this API family. Backtest screens
and pure result helpers import their contracts from the backtest module.

**Code:** `web/src/backtest-api.ts`, `web/src/curve-contracts.ts`,
`web/src/api.ts`, and the backtest UI and result helpers.

## 2026-11-03: Use Forward names for the paper UI probe

**Status:** Accepted

**Decision:** Name the probe credentials `FORWARD_PROBE_USER` and
`FORWARD_PROBE_PASSWORD`.

**Reason:** The probe targets the Forward app. An old product name in its setup
message and environment variables makes the tool harder to identify and keep.

**Behavior:** The probe reads only the Forward variable names; it does not accept
the old names as aliases.

**Code:** `scripts/probe_paper_ui.py`.

## 2026-11-03: Keep research and validation endpoints in their own client

**Status:** Accepted

**Decision:** Group walk-forward research, screening, paper validation,
Episodic Pivot, Alpha Hunt, and evidence endpoints and response contracts in
`web/src/research-api.ts`.

**Reason:** These endpoints report evidence about strategy rules. They form a
clear research surface and should not live beside generic market and account
requests. The built-in strategy catalogue stays with this surface because the
research picker and the saved-strategy editor both use it.

**Behavior:** Request paths and successful response shapes stay the same.
Timestamped equity points use the shared `CurvePoint` contract.

**Code:** `web/src/research-api.ts`, `web/src/curve-contracts.ts`,
`web/src/api.ts`, and the research, validation, and strategy screens.

## 2026-11-03: Keep scanner endpoint families in their own client

**Status:** Accepted

**Decision:** Put custom scanner and structured screener contracts and requests
in `web/src/screening-api.ts`.

**Reason:** Both endpoint families power screens that build conditions, scan a
chosen universe, and explain matching rows. They were adjacent but mixed into
the general API client, which made those screens depend on unrelated account
and market contracts.

**Behavior:** Existing endpoint paths, request bodies, and response shapes stay
the same. Each screen imports its contracts and requests from the screening
client.

**Code:** `web/src/screening-api.ts`, `web/src/api.ts`,
`web/src/CustomScannerPanel.tsx`, and `web/src/ScreenerPanel.tsx`.

## 2026-11-03: Keep alert endpoints in their own client

**Status:** Accepted

**Decision:** Put rule alerts and intelligent-alert contracts and requests in
`web/src/alerts-api.ts`.

**Reason:** The alert screen manages alert rules, event history, and the live
intelligent-alert settings as one feature. These contracts do not belong in the
general API client.

**Behavior:** Request paths, payloads, and response shapes stay the same. The
alert screen imports its contracts and requests from the alert client.

**Code:** `web/src/alerts-api.ts`, `web/src/api.ts`, and
`web/src/AlertsPanel.tsx`.

## 2026-11-03: Give paper health and monitoring narrow runner ports

**Status:** Accepted

**Decision:** Give paper health and deployment monitoring separate runner
protocols. Keep `PaperRunnerPort` as the full contract for startup and the
platform status endpoint.

**Reason:** Health needs rule resolution, running deployment rows, and process
status. Deployment monitoring needs one deployment's loop state and the active
flag. Passing the full runner to both services hides those smaller needs and
ties read-only services to unrelated runner operations.

**Behavior:** Runtime runner behavior and health results stay the same. Service
constructors now require only the runner operations they call.

**Code:** `src/forward/core/paper_runner_ports.py`,
`src/forward/services/paper_health.py`,
`src/forward/services/monitoring.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-03: Let the runner own its status projection

**Status:** Accepted

**Decision:** Include the current market-session flag in `PaperRunner.status()`
and let the paper status route return that status directly.

**Reason:** The route was importing a helper from the runner service and
mutating the returned dictionary to build one status response. The runner
already owns its process state and the calendar rule it uses to evaluate it, so
it should return the complete status shape.

**Behavior:** The response keeps the existing `in_market_hours` field and value.
The route no longer reaches into the runner service or edits its status object.

**Code:** `src/forward/services/runner.py` and
`src/forward/api/routers/paper.py`.

## 2026-11-03: Define the cash session in one core module

**Status:** Accepted

**Decision:** Keep India time, cash-market open and close, and strategy session
timeframe rules in `src/forward/core/session.py`. The market calendar, runner,
and backtest config use those shared definitions.

**Reason:** Session clocks had separate hardcoded values in the calendar,
runner, and backtest rules. The runner also listed intraday bars that its
strategy configuration rejects. Shared constants make those rules easier to
review while keeping legacy runner bar widths supported.

**Behavior:** Existing session times, supported backtest intervals, and the
runner's legacy 10, 30, and 60 minute bar widths remain unchanged.

**Code:** `src/forward/core/session.py`, `src/forward/market_calendar.py`,
`src/forward/services/runner.py`, and `src/forward/backtest/config.py`.

## 2026-11-03: Reuse the shared India time zone

**Status:** Accepted

**Decision:** Import `IST` from `src/forward/core/session.py` wherever the
application needs India-local timestamps.

**Reason:** Repeating a fixed UTC offset or constructing the same named time
zone in each service made time handling inconsistent and harder to change.

**Behavior:** All callers keep the same India-local date and wall-clock time.
Market session rules remain owned by `core/session.py`.

**Code:** Alerting, broker auth and history, market data, insights, learning,
paper health, portfolio, stream, and track-record modules.

## 2026-11-03: Put saved screener SQL behind a storage port

**Status:** Accepted

**Decision:** Let `ScreenerService` depend on `ScreenerStoragePort`. Keep SQL
sessions and `ScreenerRepository` calls in `SqlScreenerStorageAdapter`, built
by the composition root.

**Reason:** The screener use case owned both screen rules and database
transactions. That made it depend on a concrete persistence layer and made the
watch loop reach into the same repository on its own.

**Behavior:** Ownership checks, duplicate-name errors, saved-screen rows, and
watch state keep their existing behavior. The service and watch loop use the
same storage port; SQL remains in the app database adapter.

**Code:** `src/forward/core/screener_ports.py`,
`src/forward/appdb/screener_storage.py`, `src/forward/screener/service.py`,
`src/forward/screener/watch.py`, and `src/forward/composition.py`.

## 2026-11-03: Put authentication transactions behind a unit of work

**Status:** Accepted

**Decision:** Make `AuthService` use `AuthStoragePort` and
`AuthTransactionPort`. Keep account, session, API-key, and transactional audit
repository calls in `SqlAuthStorageAdapter`. Build the service in the
composition root.

**Reason:** Authentication directly opened SQL sessions and called database
repositories. The service also chose database-specific exceptions, so it could
not be used without the SQL adapter.

**Behavior:** Related account changes and their audit event still share one
transaction. Duplicate identity errors keep their current API code and status.
Authentication routes and dependencies get the same cached service from
composition.

**Code:** `src/forward/core/auth_ports.py`,
`src/forward/appdb/auth_storage.py`, `src/forward/auth/service.py`,
`src/forward/composition.py`, and the authentication API callers.

## 2026-11-03: Put screener market reads behind a market-data port

**Status:** Accepted

**Decision:** Make `ScreenerService` use `ScreenerMarketDataPort` for universe
resolution and daily frames. Keep instrument-catalog reads, Parquet access, and
the screener frame cache in `ScreenerMarketDataAdapter`.

**Reason:** The screener use case loaded files and fetched the global instrument
master itself. That tied screen rules to the local cache layout and made it hard
to use a different market-data source.

**Behavior:** Universe ordering, canonical symbol keys, frame freshness, and
screen results keep their existing rules. Unknown universes still return the
same `unknown_universe` error through the use case.

**Code:** `src/forward/core/screener_ports.py`,
`src/forward/data/screener_market.py`, `src/forward/screener/service.py`, and
`src/forward/composition.py`.

## 2026-11-03: Keep SQL audit writes in the database adapter

**Status:** Accepted

**Decision:** Keep `forward.audit.log` responsible for the append-only file
sink. Write audit table rows through `record_audit_event` in the application
database adapter.

**Reason:** The audit file writer imported the SQL repository, so an inner
module depended on an outer database layer. Authentication and watchlist
updates also need their audit rows in the same transaction as their data.

**Behavior:** Authentication and watchlist writes keep the same transaction
boundary and retain the file audit record. Watchlist deletion continues to
write its file record after the database transaction completes.

**Code:** `src/forward/audit/log.py`, `src/forward/appdb/audit_storage.py`,
`src/forward/appdb/auth_storage.py`, and
`src/forward/appdb/watchlist_storage.py`.

## 2026-11-03: Resolve strategy contracts through a broker port

**Status:** Accepted

**Decision:** Make the researched strategy universe depend on
`BrokerInstrumentCatalogPort`. Keep IIFL contract-master lookup in
`IiflInstrumentCatalogAdapter`, and build that dependency with the shared
composition root.

**Reason:** Strategy authoring reached into IIFL's contract master directly.
That tied strategy validation to one broker and caused the CLI to build a
separate copy of the strategy service.

**Behavior:** Research symbols still need enough cached history and an exact
listed NSE equity contract. A missing contract remains an error. The CLI and
HTTP routes now use the same configured strategy service.

**Code:** `src/forward/core/broker_ports.py`,
`src/forward/brokers/iifl/instrument_catalog.py`,
`src/forward/instruments/strategy_universe.py`, `src/forward/composition.py`,
and `src/forward/cli.py`.

## 2026-11-03: Inject authentication policy into the use case

**Status:** Accepted

**Decision:** Make `AuthService` receive an `AuthSettingsPort` with login
lockout, MFA, and session lifetime values. Supply the application settings in
the composition root.

**Reason:** The authentication use case read global settings itself, which
hid one of its inputs and made policy harder to change or inspect.

**Behavior:** Login lockout, privileged MFA, and session expiration use the
same configured values as before.

**Code:** `src/forward/core/auth_ports.py`, `src/forward/auth/service.py`,
`src/forward/composition.py`, and `tests/test_auth.py`.

## 2026-11-03: Reuse the shared market session in alerts

**Status:** Accepted

**Decision:** Use `is_market_session_open` and `local_datetime` from
`core.session` for alert-session state and timestamp comparisons.

**Reason:** Alert engines repeated the cash-session clock, and other modules
imported the India time zone through the alert engine.

**Behavior:** Weekday and open/close boundaries stay the same. Naive input
timestamps are treated as India-local; aware timestamps are converted to India
time before comparison. The cash session includes 09:15 and excludes 15:30.

**Code:** `src/forward/core/session.py`, `src/forward/alerts/engine.py`,
`src/forward/alerts/intelligent.py`, `src/forward/services/alerts.py`,
`src/forward/cli.py`,
`src/forward/brokers/iifl/intraday_history.py`, and
`src/forward/signals/report.py`.

## 2026-11-03: Keep the local instrument catalog in the data adapter layer

**Status:** Accepted

**Decision:** Place the filesystem-backed instrument catalog in
`forward.data.instrument_catalog`.

**Reason:** The catalog scans parquet and CSV files, fingerprints directories,
and persists a JSON snapshot. Those are data-adapter jobs, not instrument
domain rules.

**Behavior:** Catalog lookup, cache files, search, and refresh rules stay the
same. Imports now show which layer owns the filesystem work.

**Code:** `src/forward/data/instrument_catalog.py`, its composition and adapter
callers, and tests that use the catalog.

## 2026-11-03: Require exact contract data for order risk checks

**Status:** Accepted

**Decision:** Resolve order instruments from the IIFL contract catalog and pass
that lookup into the order use case. Reject missing contracts instead of
creating a generic instrument.

**Reason:** The previous lookup called a method that the local equity catalog
does not provide, swallowed the error, and used a multiplier of one. That made
risk arithmetic look valid without proving the contract details.

**Behavior:** Order validation uses the catalog's validated lot and multiplier
data. A missing or unreadable contract now stops validation with the catalog
error.

**Code:** `src/forward/core/order_ports.py`,
`src/forward/brokers/iifl/instrument_catalog.py`,
`src/forward/services/orders.py`, `src/forward/composition.py`,
`src/forward/services/runner.py`, and order test fixtures.

## 2026-11-03: Give the local instrument catalog explicit ownership

**Status:** Accepted

**Decision:** Construct `InstrumentMaster` in the composition root with an
explicit cache directory. Do not keep a global catalog or read configuration
from the data adapter.

**Reason:** A module-level singleton hid its data root, tied tests to shared
process state, and let a data worker reach back into the catalog to reset it.

**Behavior:** Catalogs still refresh when their snapshot expires. The market
history adapter no longer tries to reset a process-wide object that it does not
own.

**Code:** `src/forward/data/instrument_catalog.py`,
`src/forward/composition.py`, `src/forward/data/market_history.py`,
`tests/conftest.py`, `tests/test_screener.py`, and
`scripts/verify_paper_workflow.py`.

## 2026-11-03: Keep secret key loading outside authentication rules

**Status:** Accepted

**Decision:** Keep encryption operations in `auth.crypto`, but load key
material through an injected provider in `infra`.

**Reason:** The authentication use case should not read settings or create key
files. Those are operating-system and deployment details.

**Behavior:** Environment-key and local-key-file support remain the same. The
provider loads them only when a secret needs encryption or decryption.

**Code:** `src/forward/core/auth_ports.py`, `src/forward/auth/crypto.py`,
`src/forward/auth/service.py`, `src/forward/infra/secret_box_provider.py`,
`src/forward/composition.py`, and `tests/test_auth.py`.

## 2026-11-03: Let the composition root own the request limiter

**Status:** Accepted

**Decision:** Keep the token-bucket algorithm independent of settings. Build its
shared instance in composition and inject a provider into HTTP middleware.

**Reason:** The auth package applied settings through a hidden module singleton,
although the limiter protects the whole API.

**Behavior:** Per-minute limits and the existing fail-open behavior for limiter
errors stay the same. Test setup can clear the composed instance after changing
settings.

**Code:** `src/forward/auth/rate_limit.py`, `src/forward/composition.py`,
`src/forward/api/middleware.py`, `src/forward/api/main.py`,
`tests/conftest.py`, and `tests/test_api_v1.py`.

## 2026-11-03: Inject one adapter for the JSONL audit trail

**Status:** Accepted

**Decision:** Keep the JSONL file writer in `infra` and pass it through the
composition root to each adapter that records file audit events. Build the
shared event shape in a pure audit helper.

**Reason:** Authentication and watchlist database adapters should not reach
into a global audit module to write files. The file path belongs to runtime
configuration, and the same event fields should reach both audit sinks.

**Behavior:** The configured append-only file and SQL audit table keep their
current best-effort write behavior. The audit adapter is built lazily from the
current settings and can be cleared when test settings change.

**Code:** `src/forward/audit/events.py`, `src/forward/core/audit_ports.py`,
`src/forward/infra/audit_log.py`, `src/forward/appdb/audit_storage.py`,
`src/forward/appdb/auth_storage.py`, `src/forward/appdb/watchlist_storage.py`,
`src/forward/composition.py`, and audit tests.

## 2026-11-03: Keep option contract lookup in the core layer

**Status:** Accepted

**Decision:** Store option expiry and strike lookup in
`src/forward/core/option_catalog.py`. Broker adapters, historical data feeds,
and strategy execution use this shared catalog.

**Reason:** Contract selection is a domain rule over instrument records. It
does not belong to one strategy, broker, or file format.

**Behavior:** Expiry selection and nearest-strike tie breaking stay the same.
This change only moves the shared rule to the domain layer.

**Code:** `src/forward/core/option_catalog.py`,
`src/forward/strategy/options.py`, `src/forward/brokers/iifl/options_market.py`,
`src/forward/data/options_feed.py`, and `src/forward/services/options_forward.py`.

## 2026-11-03: Document only storage adapters that the app provides

**Status:** Accepted

**Decision:** Describe the application database and Parquet market cache as the
supported storage paths. Do not document a TimescaleDB store that is absent from
the current application.

**Reason:** The setup guide named a deleted `forward.data.store` module and a
schema command users could not run. The real database adapter is
`forward.appdb.engine` and the real schema is `forward.appdb.schema`.

**Behavior:** SQLite remains the default. PostgreSQL remains available through
`APP_DB_URL`; historical market bars continue to use the Parquet cache.

**Code:** `README.md`, `src/forward/appdb/engine.py`, and
`src/forward/appdb/schema.py`.

## 2026-11-03: Keep shared history checks outside the backtest package

**Status:** Accepted

**Decision:** Put price-jump and missing-bar checks in the pure analytics layer.
The data job and backtest validity report use the same functions.

**Reason:** A data adapter should not import a backtest module to inspect price
history. These checks need only frames and can be reused by both workflows.

**Behavior:** The jump limit, gap limit, warning text, and findings stay the
same. Only the shared function's owner changes.

**Code:** `src/forward/analytics/history_quality.py`,
`src/forward/backtest/validity.py`, `src/forward/data/quality.py`, and
`src/forward/cli.py`.

## 2026-11-03: Keep briefing settings in the core model layer

**Status:** Accepted

**Decision:** Store the briefing settings model and default scanner universe in
`forward.core`. The JSON adapter serializes the model but does not import the
brief-building workflow.

**Reason:** A storage adapter should depend on a small data contract, not on a
module that also scores market frames and builds user-facing messages.

**Behavior:** The same setting names and default symbols are used. A missing
settings file gets the normal initial settings; malformed saved JSON now raises
instead of silently replacing the user's settings with defaults.

**Code:** `src/forward/core/briefing_models.py`,
`src/forward/core/market_universe.py`, `src/forward/core/briefing_ports.py`,
`src/forward/data/briefing_storage.py`, `src/forward/briefing.py`,
`src/forward/scanner.py`, and `src/forward/services/briefing.py`.

## 2026-11-03: Keep intelligent alert settings in the core model layer

**Status:** Accepted

**Decision:** Put the intelligent alert settings model in `forward.core` and
use that typed contract in the alert service and JSON storage adapter.

**Reason:** The saved settings describe application behavior and should not be
owned by a file adapter or mixed into the alert event models. A malformed saved
file must be visible instead of silently becoming a fresh default.

**Behavior:** A missing file still creates the defined initial settings.
Malformed JSON or invalid fields now raise an error and leave the saved file
untouched.

**Code:** `src/forward/core/alert_config.py`, `src/forward/core/alert_ports.py`,
`src/forward/data/intelligent_alert_config.py`, `src/forward/alerts/intelligent.py`,
and `src/forward/signals_stats.py`.

## 2026-11-03: Keep evidence grades in the core domain layer

**Status:** Accepted

**Decision:** Put the shared evidence classes, grades, and grading functions in
`forward.core.learning_evidence`.

**Reason:** Research, paper trading, the journal, attribution, and learning all
use the same evidence rules. The shared vocabulary belongs in the core layer so
those areas do not depend on a research module for domain meaning.

**Behavior:** Evidence values and grading rules stay the same. Only the module
owner and imports change.

**Code:** `src/forward/core/learning_evidence.py` and all modules that use the
shared evidence vocabulary.

## 2026-11-03: Keep forward-tracking file access in its adapter

**Status:** Accepted

**Decision:** The file adapter returns saved tracker state. The service calls
the research summary functions to build the dashboard reports.

**Reason:** A storage adapter should read and write data, while a service owns
the application query. This keeps file access out of the service and research
summary code out of the adapter.

**Behavior:** Dashboard fields and calculations stay the same. Missing state
files still mean that tracking has not started. Invalid JSON or a non-object
state now raises instead of silently becoming empty state.

**Code:** `src/forward/core/forward_tracking_ports.py`,
`src/forward/data/forward_tracking.py`, `src/forward/services/forward_tracking.py`,
`src/forward/research/forward_tracker.py`, and `src/forward/research/gap_plan.py`.

## 2026-11-03: Keep sector catalog reads behind the data adapter

**Status:** Accepted

**Decision:** Read the symbol-to-industry catalog in `forward.data` and pass it
to portfolio reports, risk gates, and learning replay through explicit inputs.

**Reason:** A portfolio service must not load CSV files or import research code.
The same canonical catalog should serve reports, risk checks, and learning.

**Behavior:** Sector names and unknown-symbol handling stay the same. A damaged
CSV is still skipped while other catalog files are read.

**Code:** `src/forward/data/sector_catalog.py`,
`src/forward/data/learning_artifacts.py`, `src/forward/services/portfolio.py`,
`src/forward/services/runner.py`, and `src/forward/services/learning.py`.

## 2026-11-03: Keep shared cost models out of the backtest package

**Status:** Superseded by the 2026-11-07 decision above

**Decision:** Store commission, statutory fee, and slippage models in
`forward.execution.costs` and have backtests and deployments use that module.

**Reason:** The paper venue and live strategy planning use the same cost rules.
They are execution rules, so backtests must depend on them rather than own them.

**Behavior at the time:** Fee and slippage formulas and their defaults were
unchanged. Cost rules were moved out of the backtest package.

**Historical code:** `src/forward/execution/costs.py` and its consumers in the
backtest, broker, strategy, and service layers. The current owner is recorded
in the superseding decision above.

## 2026-11-03: Keep panel files beside their configured market data

**Status:** Accepted

**Decision:** Place a consolidated panel under the data root that owns the
source cache instead of a path relative to the process working directory.

**Reason:** The application can start from different folders. A shared cache
must follow its configured source folder, not whichever folder launched it.

**Behavior:** Panel contents and freshness rules stay the same. Its saved path
is now derived from the source cache path.

**Code:** `src/forward/data/panel.py` and `tests/test_panel.py`.

## 2026-11-03: Inject universe lookup into backtests

**Status:** Accepted

**Decision:** Give the backtest service the screener market-data port for its
universe list and symbol resolution.

**Reason:** A service must not call the composition root to find another
service. The composition root owns wiring; the backtest service uses its port.

**Behavior:** Universe names and membership still come from the same local
instrument data. Lookup errors now reach the backtest caller instead of turning
the universe list into an empty response.

**Code:** `src/forward/core/screener_ports.py`,
`src/forward/services/backtests.py`, and `src/forward/composition.py`.

## 2026-11-03: Unknown provenance is never forward evidence

**Status:** Accepted

**Decision:** Grade an unknown source label as `IN_SAMPLE` when deriving the
evidence class for a forward record.

**Reason:** A missing or unfamiliar source does not prove that the record was
written before its outcome. Treating it as paper-forward could overstate the
evidence behind a strategy.

**Behavior:** Known `PAPER` and `LIVE` sources keep their current classes.
Unknown sources now remain in-sample.

**Code:** `src/forward/core/learning_evidence.py` and its evidence consumers.

## 2026-11-03: Inject instrument lookup into the paper runner

**Status:** Accepted

**Decision:** Pass the exact instrument lookup into the paper runner through
its constructor.

**Reason:** The runner is an application workflow and must not reach back into
the composition root to create a dependency while handling a deployment.

**Behavior:** Order validation still resolves contracts through the configured
instrument catalog. Only dependency wiring moves to the composition root.

**Code:** `src/forward/core/order_ports.py`,
`src/forward/services/runner.py`, and `src/forward/composition.py`.

## 2026-11-03: Keep option selection in the core catalog

**Status:** Accepted

**Decision:** Keep expiry and strike selection in `OptionContractCatalog` and
remove the duplicate selectors from the IIFL contract-file adapter.

**Reason:** Broker adapters load and normalize listed instruments. Strategy and
market workflows use one shared core catalog to choose among them.

**Behavior:** Weekly expiry remains the first listed expiry on or after the
requested date. Monthly expiry remains the last listed expiry in the nearest
eligible calendar month. Nearest-strike ties still choose the lower strike and
then the first symbol alphabetically.

**Code:** `src/forward/core/option_catalog.py` and
`src/forward/brokers/iifl/contracts.py`.

## 2026-11-03: Require listed instruments for paper fills

**Status:** Accepted

**Decision:** Resolve every paper fill through the configured instrument lookup.
The paper venue no longer creates a generic instrument when lookup is missing.

**Reason:** Lot size, multiplier, exchange, and tick size affect the fill and its
costs. A made-up instrument can make simulated P&L differ from the listed
contract.

**Behavior:** Paper orders now fail at instrument lookup if their contract is not
in the configured catalog. Valid contracts keep the same matching and cost
rules.

**Code:** `src/forward/services/paper_venue.py`,
`src/forward/services/runner.py`, and `src/forward/composition.py`.

## 2026-11-03: Inject live prices into the paper ledger

**Status:** Accepted

**Decision:** Require an explicit live price source in `PaperLedger` and
`PaperRunner`. The API startup path wires the broker stream into the runner;
there is no process-wide price registry.

**Reason:** A use case should receive the data it needs through its constructor.
That keeps execution connected to the configured feed and independent of hidden
startup state.

**Behavior:** Positions and fills use live tick data. Missing quotes stay
missing; daily closes are not used as a substitute. A runner cannot be created
without an explicit price source.

**Code:** `src/forward/core/paper_models.py`,
`src/forward/services/paper_prices.py`, `src/forward/services/paper_ledger.py`,
`src/forward/services/runner.py`, `src/forward/composition.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-03: Keep paper price types in the core layer

**Status:** Accepted

**Decision:** Define the paper price measurement and its read port in
`forward.core` for use by the paper services and market adapters.

**Reason:** A service should not import a sibling service to share a value type
or interface. Core owns the contract; adapters and services depend inward.

**Behavior:** Price fields, quote-age calculation, and source results keep their
current shape.

**Code:** `src/forward/core/paper_models.py`,
`src/forward/services/paper_prices.py`,
`src/forward/services/paper_ledger.py`, and
`src/forward/services/paper_venue.py`.

## 2026-11-03: Keep paper deployment settings with the strategy

**Status:** Accepted

**Decision:** Each strategy owns the deployment settings required to run its
saved definition. Deployment creation and runner loading use one shared
strategy lookup to apply those settings.

**Reason:** The deployment service and paper config had separate engine-name
branches for the same strategy settings. Updating one copy could leave the
saved deployment and the runner with different symbols or timing.

**Behavior:** Strategy validation and the existing symbol, exchange, timing,
position, and risk settings are applied from one place. Other strategies keep
their saved config and context behavior.

**Code:** `src/forward/strategy/strategies/__init__.py`,
`src/forward/strategy/strategies/sector_opening.py`,
`src/forward/strategy/strategies/vwap_reversion.py`,
`src/forward/services/deployments.py`, and `src/forward/paper/config.py`.

## 2026-11-03: Watchlist reorder requires the complete order

**Status:** Accepted

**Decision:** The watchlist reorder request must list every current symbol once.
The repository rejects partial, duplicate, blank, and unknown symbols.

**Reason:** The API contract already describes a complete replacement order.
Appending omitted symbols made a partial request look successful and hid client
mistakes.

**Behavior:** A valid complete order remains idempotent. Invalid orders return
`invalid_order`; no symbols are silently added, dropped, or rearranged.

**Code:** `src/forward/core/errors.py`,
`src/forward/core/watchlist_ports.py`, `src/forward/appdb/repositories.py`,
`src/forward/services/watchlists.py`, `src/forward/api/routers/watchlists.py`,
and the existing watchlist API tests.

## 2026-11-03: Confirm idempotency conflicts by key

**Status:** Accepted

**Decision:** An order-intent insert handles a database integrity error as a
retry only when the exact idempotency key already exists. Inserts run in a
savepoint so the outer transaction stays usable.

**Reason:** Treating every integrity error as a duplicate can hide unrelated
database faults such as invalid references. The unique key remains the race-safe
guard, and an existing key owned by another account remains a conflict.

**Behavior:** Same-account retries return the original order. Cross-account key
reuse raises `IdempotencyConflict`. Other integrity errors propagate unchanged.

**Code:** `src/forward/appdb/repositories.py` and
`tests/test_appdb_trading.py`.

## 2026-11-03: Confirm signal-context duplicates by key

**Status:** Accepted

**Decision:** A signal-context insert is a duplicate only when a row with the
same user and signal IDs already exists after the failed insert.

**Reason:** Treating every integrity error as a duplicate can hide invalid
references or other storage errors. The database constraint still handles
simultaneous inserts without a check-before-insert race.

**Behavior:** Repeated signals return zero inserted rows. Other integrity errors
propagate to the caller.

**Code:** `src/forward/appdb/repositories.py` and
`src/forward/appdb/schema.py`.

## 2026-11-03: Surface malformed persisted JSON

**Status:** Accepted

**Decision:** Repositories let JSON decode errors propagate for system state,
learning observations, optimization recommendations, strategy experiments,
signal contexts, and trade attributions. Missing optional system-state keys
still return the caller's stated default.

**Reason:** Replacing a malformed stored value with an empty value hides data
damage and can make a report or risk setting look valid when it is not.

**Behavior:** Valid stored values decode as before. Invalid JSON stops the read
and reports the decode error to the caller.

**Code:** `src/forward/appdb/repositories.py`.

## 2026-11-03: Keep signal-context SQL in its own repository

**Status:** Accepted

**Decision:** Store signal-context SQL operations in
`forward.appdb.signal_context_repository`. The signal-context and learning
storage adapters import that repository directly.

**Reason:** Signal-context persistence is a separate adapter responsibility.
Keeping it in the shared repository module made unrelated storage areas depend
on a growing file.

**Behavior:** Query filters, ordering, duplicate handling, JSON decoding, and
transaction ownership stay the same.

**Code:** `src/forward/appdb/signal_context_repository.py`,
`src/forward/appdb/signal_context_storage.py`,
`src/forward/appdb/learning_storage.py`, and
`src/forward/appdb/repositories.py`.

## 2026-11-03: Keep audit SQL in its own repository

**Status:** Accepted

**Decision:** Put audit event queries and writes in
`forward.appdb.audit_repository`. Audit storage and reconciliation import it
directly. Shared row conversion and ID creation live in
`forward.appdb.repository_support` and are used by both repository modules.

**Reason:** Audit persistence is its own adapter responsibility. Sharing simple
SQL row and ID helpers avoids copying the same conversions into each adapter.

**Behavior:** Audit fields, query filters, ordering, page limits, transaction
ownership, and event IDs keep their current behavior.

**Code:** `src/forward/appdb/audit_repository.py`,
`src/forward/appdb/repository_support.py`,
`src/forward/appdb/audit_storage.py`,
`src/forward/appdb/reconciliation_storage.py`,
`src/forward/appdb/repositories.py`, and `tests/test_reconcile.py`.

## 2026-11-03: Keep identity SQL in its own repository

**Status:** Accepted

**Decision:** Put user, login-session, and API-key SQL in
`forward.appdb.identity_repository`. Authentication storage and callers import
these repositories from that module. The shared repository module keeps
application trading and strategy persistence.

**Reason:** Identity persistence is a separate storage responsibility. Removing
it from the shared module makes that boundary clear and keeps the file focused.

**Behavior:** Queries, field normalization, lockout updates, and transaction
ownership stay the same.

**Code:** `src/forward/appdb/identity_repository.py`,
`src/forward/appdb/auth_storage.py`, `src/forward/appdb/repositories.py`, and
callers in `tests/`.

## 2026-11-03: Keep watchlist SQL in its own repository

**Status:** Accepted

**Decision:** Put watchlist and watchlist-item SQL in
`forward.appdb.watchlist_repository`. The watchlist storage adapter imports it
directly. Repository classes are no longer exported from the `appdb` package;
callers import the repository that owns their data.

**Reason:** Watchlist persistence is a separate adapter responsibility. Moving
it out of the shared repository module also removes a package-level barrel that
hid where SQL operations live.

**Behavior:** User scoping, ordering, duplicate handling, column setup, and
transaction ownership stay the same.

**Code:** `src/forward/appdb/watchlist_repository.py`,
`src/forward/appdb/watchlist_storage.py`,
`src/forward/appdb/repositories.py`, and `src/forward/appdb/__init__.py`.

## 2026-11-03: Keep backtest SQL in its own repository

**Status:** Accepted

**Decision:** Put backtest-run lifecycle and result-artifact SQL in
`forward.appdb.backtest_repository`. Backtest and signal-context storage import
those repositories directly. Shared JSON serialization lives in
`forward.appdb.repository_support`.

**Reason:** Backtest runs and their result files have a separate storage
boundary. Keeping the terminal-status rule beside its repository also avoids
placing backtest state in the shared order and strategy module.

**Behavior:** Run ownership, lifecycle guards, pagination, artifact replacement,
serialization, and transaction ownership stay the same.

**Code:** `src/forward/appdb/backtest_repository.py`,
`src/forward/appdb/repository_support.py`,
`src/forward/appdb/backtest_storage.py`,
`src/forward/appdb/signal_context_storage.py`, and
`src/forward/appdb/repositories.py`.

## 2026-11-03: Keep screener SQL in its own repository

**Status:** Accepted

**Decision:** Put saved-screener queries and writes in
`forward.appdb.screener_repository`. The screener storage adapter imports that
repository directly.

**Reason:** Saved screener definitions are a separate persistence boundary.
Their SQL does not belong beside order lifecycle or strategy-version writes.

**Behavior:** User scoping, case-insensitive name checks, serialization, and
transaction ownership stay the same.

**Code:** `src/forward/appdb/screener_repository.py`,
`src/forward/appdb/screener_storage.py`, and
`src/forward/appdb/repositories.py`.

## 2026-11-03: Keep installation state SQL in its own repository

**Status:** Accepted

**Decision:** Put durable key/value state queries and writes in
`forward.appdb.system_state_repository`. Risk, daily-learning, and general
system-state storage adapters import it directly.

**Reason:** Installation state is shared by several use cases and has a clear
storage boundary. It should not live beside order or strategy SQL.

**Behavior:** JSON encoding, missing-key defaults, audit fields, and transaction
ownership stay the same.

**Code:** `src/forward/appdb/system_state_repository.py`,
`src/forward/appdb/system_state_storage.py`,
`src/forward/appdb/risk_state_storage.py`,
`src/forward/appdb/daily_learning_storage.py`, and
`src/forward/appdb/repositories.py`.

## 2026-11-03: Keep learning-observation SQL in its own repository

**Status:** Accepted

**Decision:** Put learning-observation inserts and queries in
`forward.appdb.learning_observation_repository`. Learning, signal-context,
strategy-research, and daily-learning adapters import it directly.

**Reason:** Learning observations are their own persisted evidence type. Their
queries and JSON columns should not be grouped with order execution or strategy
definition writes.

**Behavior:** Evidence fields, JSON decoding, filters, ordering, limits, and
transaction ownership stay the same.

**Code:** `src/forward/appdb/learning_observation_repository.py`,
`src/forward/appdb/learning_storage.py`,
`src/forward/appdb/strategy_research_storage.py`,
`src/forward/appdb/signal_context_storage.py`,
`src/forward/appdb/daily_learning_storage.py`, and
`src/forward/appdb/repositories.py`.

## 2026-11-03: Keep strategy definition SQL in its own repository

**Status:** Accepted

**Decision:** Put strategy and immutable version queries and writes in
`forward.appdb.strategy_repository`. Strategy, deployment, backtest, portfolio,
and research storage import it from that module. The strategy repository owns
its version history cleanup, including related evidence rows.

**Reason:** Strategy definitions are their own storage boundary. Keeping these
queries beside order and deployment SQL made the shared module responsible for
unrelated records.

**Behavior:** Definition canonicalization, duplicate rejection, owner scoping,
version immutability, history cleanup, and transaction ownership stay the same.
The redundant `get_version` alias was removed; callers use `version`.

**Code:** `src/forward/appdb/strategy_repository.py`,
`src/forward/appdb/repositories.py`, strategy and deployment storage adapters,
and their tests.

## 2026-11-03: Keep strategy-research SQL in its own repository

**Status:** Accepted

**Decision:** Put optimization-recommendation and strategy-experiment SQL in
`forward.appdb.strategy_research_repository`. The strategy-research storage
adapter imports those repositories directly.

**Reason:** These records support research workflows and have separate storage
tables and lifecycle operations. Their persistence should not share a module
with order execution.

**Behavior:** Existing status changes, result fields, version creation, JSON
decoding, and transaction ownership stay the same.

**Code:** `src/forward/appdb/strategy_research_repository.py`,
`src/forward/appdb/strategy_research_storage.py`, and
`src/forward/appdb/repositories.py`.

## 2026-11-03: Keep strategy-research workflows in application services

**Status:** Accepted

**Decision:** Keep approval checks, candidate transformation, version creation,
and apply-result construction in the optimization and experiment services.
Expose strategy-version creation and state updates through the unit-of-work
port so the SQL adapter still commits the workflow atomically.

**Reason:** Repositories own SQL reads and writes. They should not choose which
business rules apply or call optimization logic.

**Behavior:** Approval rules, candidate values, change notes, new version fields,
updated statuses, and transactional writes stay the same.

**Code:** `src/forward/services/optimization.py`,
`src/forward/services/experiment.py`,
`src/forward/core/strategy_research_ports.py`,
`src/forward/appdb/strategy_research_storage.py`, and
`src/forward/appdb/strategy_research_repository.py`.

## 2026-11-03: Keep strategy kinds in the core vocabulary

**Status:** Accepted

**Decision:** Define supported strategy kinds, the default kind, and the API
validation pattern in `forward.core.strategy_kinds`. The strategy service,
strategy repository, command line, API, and configured-options flow use those
shared values.

**Reason:** Separate copies of the accepted kinds can disagree and reject a
strategy in one entry point while accepting it in another.

**Behavior:** The accepted values, default, and case-insensitive API validation
stay the same; the API pattern is now generated from the shared vocabulary.

**Code:** `src/forward/core/strategy_kinds.py`,
`src/forward/appdb/strategy_repository.py`,
`src/forward/services/strategies.py`, `src/forward/strategy/options.py`,
`src/forward/services/options_strategies.py`,
`src/forward/services/options_backtest.py`, `src/forward/cli.py`, and
`src/forward/api/routers/strategies.py`.

## 2026-11-03: Keep trade-attribution SQL in its own repository

**Status:** Accepted

**Decision:** Put attribution writes, owner-scoped queries, and row decoding in
`forward.appdb.trade_attribution_repository`. Attribution and learning storage
adapters import it directly.

**Reason:** Trade attribution is its own evidence store and has a dedicated
application port. Its SQL should not share the general repository module.

**Behavior:** Fingerprint reads, insert-or-replace behavior, filters, ordering,
JSON decoding, and transaction ownership stay the same.

**Code:** `src/forward/appdb/trade_attribution_repository.py`,
`src/forward/appdb/attribution_storage.py`,
`src/forward/appdb/learning_storage.py`, and
`src/forward/appdb/repositories.py`.


## 2026-11-03: Keep trade-journal SQL in its own repository

**Status:** Accepted

**Decision:** Put trade-journal writes, owner-scoped reads, and exit-reason
categorization in `forward.appdb.trade_journal_repository`. Storage adapters and
callers import that repository directly.

**Reason:** The trade journal has its own storage adapter and use cases. Keeping
its SQL in the general repository file makes persistence boundaries harder to
see.

**Behavior:** Trade creation, close calculations, reason fields, filters,
ordering, row limits, and transaction ownership stay the same.

**Code:** `src/forward/appdb/trade_journal_repository.py`,
`src/forward/appdb/trade_journal_storage.py`, and the direct repository imports.

## 2026-11-03: Keep deployment SQL in its own repository

**Status:** Accepted

**Decision:** Put deployment creation, ownership-scoped reads, and lifecycle
updates in `forward.appdb.deployment_repository`. Callers import it directly.

**Reason:** Deployment persistence is a distinct boundary used by paper
execution, portfolio, attribution, and strategy services.

**Behavior:** Mode and capital validation, lifecycle rules, result fields,
filters, ordering, and transaction ownership stay the same.

**Code:** `src/forward/appdb/deployment_repository.py` and the direct repository imports.


## 2026-11-03: Isolate order events, idempotency, and reconciliation SQL

**Status:** Accepted

**Decision:** Keep the append-only order event log, durable order idempotency,
and reconciliation-run queries in separate SQL repositories. Storage adapters
and callers import each repository from its own module.

**Reason:** These records enforce different rules. Order state is projected
from its event log, idempotency is protected by a database key and savepoint,
and reconciliation records preserve health reports. Separate modules make
those rules easier to locate and keep SQL behind the storage boundary.

**Behavior:** Event ordering and order projection, duplicate-key handling,
reconciliation validation and reporting, and caller-owned transactions stay the
same.

**Code:** `src/forward/appdb/order_event_repository.py`,
`src/forward/appdb/order_intent_repository.py`,
`src/forward/appdb/reconciliation_repository.py`,
`src/forward/appdb/order_repository.py`, and their direct callers.


## 2026-11-03: Remove the shared trading repository module

**Status:** Accepted

**Decision:** Keep each trading persistence rule in its focused SQL repository:
orders, order events, order idempotency, deployments, reconciliation, and trade
journals. Remove `forward.appdb.repositories` and update callers to import the
focused modules directly.

**Reason:** One shared file mixed separate storage boundaries and invited new
features to add SQL in the wrong place. Direct imports make the boundary clear
and keep the project free of a compatibility module.

**Behavior:** The SQL operations, transaction ownership, order event projection,
retry rules, and repository results stay the same.

**Code:** `src/forward/appdb/order_repository.py`,
`src/forward/appdb/order_event_repository.py`,
`src/forward/appdb/order_intent_repository.py`,
`src/forward/appdb/deployment_repository.py`,
`src/forward/appdb/reconciliation_repository.py`,
`src/forward/appdb/trade_journal_repository.py`, and their direct callers.


## 2026-11-03: Keep venue ports in execution and venue adapters in brokers

**Status:** Accepted

**Decision:** Keep venue status values, the `Venue` protocol, and `VenueOutcome`
in `forward.execution.venue`. Keep broker, paper, reversion, and live-deployment
venue implementations under `forward.brokers`. Keep order placement and its
result in `forward.services.execution`.

**Reason:** Venue implementations depend on the venue contract. They should not
import that contract from an application service that also coordinates order
placement. Broker translation and broker-backed portfolio reads are adapter
work, so they belong with the broker adapters.

**Behavior:** Venue states and fill fields, order translation, matching, costs,
runner behavior, and live risk inputs stay the same. The unused
`resolve_order_type` wrapper was removed; `OrderType.parse` remains the single
order-type parser.

**Code:** `src/forward/execution/venue.py`,
`src/forward/services/execution.py`,
`src/forward/brokers/venue.py`,
`src/forward/brokers/portfolio.py`,
`src/forward/brokers/paper_venue.py`,
`src/forward/brokers/reversion_venue.py`, and
`src/forward/brokers/live_deployment.py`.


## 2026-11-03: Inject the exchange calendar into the paper runner

**Status:** Accepted

**Decision:** Require `MarketCalendarPort` when constructing `PaperRunner` and
pass that port to each `DeploymentLoop`. Runner session checks use the injected
calendar instead of a process-global calendar lookup.

**Reason:** The composition root already owns calendar construction. The runner
must use that same dependency for market-hour checks and strategy evaluation.
The old wiring passed a calendar to a constructor that rejected it, then passed
a calendar field to a loop that did not define it.

**Behavior:** Session-open decisions still come from the exchange calendar.
The startup call now accepts the dependency it already provides, and deployment
loops share the configured calendar instance.

**Code:** `src/forward/services/runner.py`,
`src/forward/composition.py`, and runner construction call sites.


## 2026-11-03: Inject runner venue profiles

**Status:** Accepted

**Decision:** Keep venue selection behind `RunnerVenueProviderPort`. The
composition root builds the broker-backed provider and registers special paper
venue builders and runner policies under each strategy's declared engine key.
The runner supplies the saved mode and engine key, then uses the selected venue,
evaluator, signal key, history, order recovery, feed, and clock policies.

**Reason:** The runner coordinates strategy evaluation and order flow. It should
not construct broker adapters or know how paper venues differ by strategy. The
provider applies registered profiles without embedding strategy names. The
composition root adapts the strategy evaluators and registers their history,
order recovery, feed, and clock policies, so the generic loop does not need
strategy-specific branches.

**Behavior:** Paper, VWAP, opening-strategy, and live deployments keep their
existing venue and portfolio behavior. No pricing or broker fallback is added.

**Code:** `src/forward/core/runner_venue_ports.py`,
`src/forward/brokers/runner_venue_provider.py`,
`src/forward/services/runner.py`, and `src/forward/composition.py`.


## 2026-11-03: Keep execution services on deployment loops

**Status:** Accepted

**Decision:** Remove the unused `PaperRunner.execution` property, its cached
service field, and the unused `_order_services` mapping. Build
`ExecutionService` only for the deployment loop that owns the current order and
portfolio state.

**Reason:** The runner property had no callers and referred to `self.orders`,
which the runner does not provide. The deployment loop already builds an
execution service bound to that deployment's portfolio and order service. The
mapping had no readers or writers.

**Behavior:** Per-deployment order placement continues through the loop's
execution service. The unused runner-level path and empty mapping are removed.

**Code:** `src/forward/services/runner.py`.

## 2026-11-03: Remove the unused deployment feature field

**Status:** Accepted

**Decision:** Remove `DeploymentLoop.features` because no runner, caller, or
consumer reads or supplies it.

**Reason:** An unused field suggests the loop has feature state that affects
strategy evaluation, but the runner builds strategy context from its market
frames and configured profile.

**Behavior:** No runtime behavior changes. Strategy frames and context remain
the inputs used by evaluation.

**Code:** `src/forward/services/runner.py`.

## 2026-11-03: Generic runner evaluation uses its injected calendar

**Status:** Accepted

**Decision:** Pass the deployment loop's `MarketCalendarPort` to each generic
evaluation step that checks session state, builds session context, or reads
completed bars.

**Reason:** The loop already receives its calendar from composition. Referring
to an undefined local name stopped generic strategy evaluation before it could
apply the configured session rules.

**Behavior:** Generic strategies now use the same injected market calendar as
profile-based strategies. No other evaluation rules change.

**Code:** `src/forward/services/runner.py`.

## 2026-11-03: Strategy IOC orders use deployment instrument settings

**Status:** Accepted

**Decision:** Build special-strategy IOC orders with the exchange and product
selected by `RunnerConfig`. Keep session-style product selection in that shared
configuration model.

**Reason:** Generic and special-strategy orders should use the same deployment
instrument settings. Copying the product rule or defaulting every special
strategy to one exchange could produce an order for the wrong market segment.

**Behavior:** Intraday deployments use the intraday product and their configured
symbol exchange. Other deployments use the delivery product and their
configured symbol exchange.

**Code:** `src/forward/paper/config.py`, `src/forward/services/runner.py`, and
`src/forward/services/paper_intents.py`.

## 2026-11-03: Reconcile every journal entry for a deployment

**Status:** Accepted

**Decision:** Let journal reconciliation request every journal row for its
deployment. Keep the 1,000-row cap for normal paged reads.

**Reason:** Reconciliation compares existing episodes with the complete fill
history. A hidden row cap could omit an old episode and cause it to be recorded
again during a later reconciliation pass.

**Behavior:** Reconciliation reads all matching journal rows. Other repository
queries keep their existing pagination limit.

**Code:** `src/forward/appdb/trade_journal_repository.py` and
`src/forward/appdb/trade_journal_storage.py`.

## 2026-11-03: Build portfolio fills from the full order history

**Status:** Accepted

**Decision:** Use `OrderRepository.for_replay` when portfolio storage reads a
user's fills. Keep the capped, paged order query for UI listings.

**Reason:** Portfolio calculations need the complete fill history. The former
50,000-order request went through a query that capped pages at 500, so the book
could leave out older orders.

**Behavior:** Portfolio folds use all of the user's recorded orders. Fill-event
reads remain grouped into bounded database batches.

**Code:** `src/forward/appdb/portfolio_storage.py` and
`src/forward/appdb/order_repository.py`.

## 2026-11-03: Scope signal outcomes to the owner and full journal

**Status:** Accepted

**Decision:** Resolve backtest outcomes only through runs owned by the
requesting account, and load every closed journal episode used for live and
paper context outcomes.

**Reason:** Trade IDs alone do not prove account ownership. Also, limiting
journal outcomes to 1,000 rows could leave older saved contexts without their
matching trades.

**Behavior:** Valid owner-scoped outcomes stay the same. Cross-account backtest
trade IDs do not resolve, and live or paper outcome matching can see the full
closed journal.

**Code:** `src/forward/appdb/signal_context_storage.py` and
`src/forward/appdb/trade_journal_repository.py`.

## 2026-11-03: Keep terminal backtest results immutable

**Status:** Accepted

**Decision:** Claim a backtest's terminal status transition before writing or
deleting its result artifacts, inside the same SQL transaction. Remove the
unconditional artifact-delete operation.

**Reason:** A late worker could previously write results after cancellation or
completion, or delete a completed run's artifacts when a delayed failure arrived.
The run repository already refuses terminal state changes; the adapter must
respect that result before touching artifacts.

**Behavior:** Only a worker that changes a non-terminal run may save completion
artifacts or clear partial artifacts for failure. A rejected late completion
leaves all artifacts untouched. An artifact-write error still rolls the status
change back with the transaction.

**Code:** `src/forward/appdb/backtest_storage.py` and
`src/forward/appdb/backtest_repository.py`.

## 2026-11-03: Import the screener duplicate-name error at its adapter boundary

**Status:** Accepted

**Decision:** Raise the shared `DuplicateScreenerName` domain error when the
saved-screen adapter finds a conflicting name.

**Reason:** The adapter already translated duplicate names into this error, but
it did not import the error class. A duplicate create or rename therefore raised
`NameError` instead of the error the screener service handles.

**Behavior:** Duplicate names reach the existing service-level error handling.

**Code:** `src/forward/appdb/screener_storage.py` and
`src/forward/core/errors.py`.

## 2026-11-03: Require an account portfolio for order risk checks

**Status:** Accepted

**Decision:** Build order services only when the caller provides the portfolio
for the order's execution mode. The versioned order API uses the user's paper
ledger for paper orders and the authenticated broker portfolio for live orders.

**Reason:** The former default portfolio represented an unknown account as an
empty one. Position-aware checks could then approve an order without checking
the real book.

**Behavior:** Missing live broker access returns its declared service error.
Order creation and validation use the portfolio for the selected mode. A paper
order tied to a deployment checks that deployment's book; an untied paper order
checks the user's whole paper book. Read-only order routes use the user's paper
portfolio because no risk decision is made. Order creation also checks deployment
ownership before storing a deployment link.

**Code:** `src/forward/core/risk_ports.py`, `src/forward/services/risk.py`,
`src/forward/services/orders.py`, `src/forward/composition.py`, and
`src/forward/api/routers/orders.py`.

## 2026-11-03: Keep unavailable watchlist values unavailable

**Status:** Accepted

**Decision:** Use the default columns only when the caller omits a column list.
Reject an explicit list that contains no supported columns. Do not substitute the
close for missing high or low bars, and do not calculate VWAP from incomplete
OHLCV bars.

**Reason:** A plausible value made from a different field is not market data.
Also, replacing an invalid explicit selection with defaults hides the caller's
mistake.

**Behavior:** Missing price fields stay null. VWAP needs complete high, low, and
volume values for its window. An empty or unsupported explicit column selection
returns the existing `no_valid_columns` service error.

**Code:** `src/forward/services/watchlists.py`.

## 2026-11-06: Runner status errors keep paper health pending

**Status:** Accepted

**Decision:** Treat a failed paper-runner status read as runner unavailability. Keep paper health pending and do not publish a completed snapshot as current.

**Reason:** A status connection error cannot prove that the runner is evaluating deployments. Reporting old or partial health as current would mislead the operator.

**Behavior:** Health remains pending until the runner status read succeeds and reports that the runner is running. Status failures are logged for diagnosis.

## 2026-11-06: Configure browser origins once

**Status:** Accepted

**Decision:** Store credentialed cross-origin browser origins in `cors_allowed_origins`. Use that setting for HTTP CORS, cookie CSRF checks, and the tick WebSocket origin check. Same-origin requests remain valid when the browser origin matches the request host.

**Reason:** Separate origin lists can drift and make the API, writes, and live ticks disagree. One setting keeps the security boundary consistent and lets operators configure their browser host without editing code.

**Behavior:** The default list keeps the local Vite and preview origins working. Operators can replace it with a JSON list in `.env` or the process environment. Missing or invalid origins remain rejected.

## 2026-11-06: Keep broker ports grouped by workflow

**Status:** Accepted

**Decision:** Split broker access, sessions, contract lookup, account reads, tradebooks, read coalescing, and live orders into separate port modules. Callers import the port from its owning module.

**Reason:** The shared broker port file mixed unrelated workflows and made the core boundary harder to scan. Small contracts keep each adapter and service coupled only to the capability it needs.

**Behavior:** Port methods and runtime behavior are unchanged. The combined module is removed; no compatibility re-export remains.

## 2026-11-06: Group market data ports by use

**Status:** Accepted

**Decision:** Split market data contracts into status, scanner, chart, dashboard history, historical data, tracking, watchlist, and live market data modules. Callers import the contract they use.

**Reason:** One market data port file had unrelated read paths and several large composite interfaces. Focused files make it clear which data source each workflow needs and reduce accidental coupling.

**Behavior:** The contract methods and runtime behavior stay the same. The old combined module is removed without a re-export.

## 2026-11-06: Group paper ports by workflow

**Status:** Accepted

**Decision:** Split paper ledger reads, deployment storage, deployment monitoring, system state, runner storage, health storage, and P&L storage into focused port modules. Each caller imports the contract it needs.

**Reason:** One paper port module combined account books, deployment lifecycle, monitor history, runner inputs, and reporting. Focused ports make storage needs clearer and stop unrelated workflows from sharing a contract file.

**Behavior:** The port methods and application behavior are unchanged. The combined module is removed without a compatibility re-export.

## 2026-11-06: Give each options workflow its own port

**Status:** Accepted

**Decision:** Separate options market access, dataset handling, forward execution, run storage, and saved-strategy lookup into their own port modules.

**Reason:** These workflows have different adapters and can change independently. A strategy service should not need the broker's market, file import, run storage, and strategy storage contracts from one shared module.

**Behavior:** The existing contracts and runtime behavior are unchanged. The combined module is removed without a compatibility re-export.

## 2026-11-06: Group domain models by market workflow

**Status:** Accepted

**Decision:** Separate market observations, instruments, orders and fills, and portfolio records into domain model modules. Imports follow the dependency direction: orders and portfolio records use instrument models; portfolio records also use fills.

**Reason:** The shared model file mixed live market data, contract rules, order lifecycle, and account books. These groups change for different reasons, and their one-way dependencies can be made clear in the module layout.

**Behavior:** Model fields, validation, and calculations are unchanged. Callers import from the focused module; the combined file is removed without a compatibility re-export.

## 2026-11-06: Keep venue failures in the core error contract

**Status:** Accepted

**Decision:** Define VenueError in core errors. Execution services, venue adapters, API routes, and callers import the same error from that inner-layer module.

**Reason:** HTTP adapters and application services should not import an error through the execution venue module. The error represents a shared order outcome, not venue implementation detail.

**Behavior:** The exception type, message, and order_id field are unchanged. The venue module no longer exports the error.

## 2026-11-06: Keep scheduled research jobs out of API startup

**Status:** Accepted

**Decision:** Define the forward-tracker and gap-plan schedules in `forward.jobs.research`. API startup supplies the data root and market-history port to that job builder.

**Reason:** HTTP startup should coordinate background work without importing individual research use cases or defining their schedules.

**Behavior:** The jobs keep their existing names, weekdays-only rule, run order, and run times. Their inputs remain explicit ports and paths.

## 2026-11-06: Give learning data its own module

**Status:** Accepted

**Decision:** Keep the learning dataset model, column contract, evidence builder, and source-specific row helpers in `forward.services.learning_dataset`. Learning analysis and reporting use that module through direct imports.

**Reason:** The learning service module had grown to combine data assembly, report models, statistical analysis, and use-case orchestration. The dataset contract and builder form one unit and can be maintained independently.

**Behavior:** Dataset fields, row-building logic, and evidence rules are unchanged. Callers import dataset types from their owning module; the old module does not define a compatibility export list.

## 2026-11-06: Separate learning analysis and reports

**Status:** Accepted

**Decision:** Keep statistical analysis and metric selection in `forward.services.learning_analysis`. Keep daily report models and report building in `forward.services.learning_reports`. The learning use case composes these modules.

**Reason:** Analysis and daily reporting have different inputs and change for different reasons. Keeping them separate makes dataset assembly, analysis, reporting, and orchestration easier to read and reuse.

**Behavior:** Analysis, report fields, and calculations are unchanged. Callers import each public model from its owning module; no compatibility export module is added.

## 2026-11-06: Separate portfolio policy validation

**Status:** Accepted

**Decision:** Keep the portfolio policy model, supported conflict modes, and payload validation in `forward.services.portfolio_policy`. Portfolio management imports the policy contract from that module.

**Reason:** Policy parsing is a focused rule set. Keeping it beside book aggregation and order gating made risk changes harder to review.

**Behavior:** Policy fields, validation, and existing defaults are unchanged. Callers import policy types and functions from their owning module.

## 2026-11-06: Separate portfolio models, risk checks, and entry context

**Status:** Accepted

**Decision:** Keep portfolio book records and shared calculations in `forward.services.portfolio_models`, order-risk checks in `forward.services.portfolio_risk`, and entry-time replay in `forward.services.portfolio_context`.

**Reason:** The portfolio module combined book folding, order decisions, risk-gate wiring, and historical context replay. These parts use different inputs and should be reviewed at their own boundaries.

**Behavior:** Book folding, risk decisions, and replay calculations are unchanged. Callers import each contract from its owning module.

## 2026-11-06: Keep paper health pending when the runner is unavailable

**Status:** Accepted

**Decision:** Report paper health as pending when the runner is missing, reports itself stopped, or cannot return status.

**Reason:** A stopped or unavailable runner cannot prove that a deployment is healthy or unhealthy. The UI should show that health is unknown until the runner can be checked.

**Behavior:** Preserve the last health snapshot for context, mark it pending, and add the stopped-runner problem when a previous snapshot exists. Do not turn a runner-status failure into a healthy result.

## 2026-11-06: Map attribution records outside the service

**Status:** Accepted

**Decision:** Build the stored attribution row in `forward.services.attribution_records`. The attribution service supplies the journal row, calculated trade, result, input fingerprint, and computation time.

**Reason:** The service coordinates journal reads, input assembly, calculation, and persistence. Mapping every calculated field into a storage record is a separate responsibility and can be reused without accessing storage or market data.

**Behavior:** Preserve every stored field. The mapper copies its supplied computation time into both timestamp fields; the repository updates `updated_at` when it writes the row. Provenance fields still come from the journal-backed trade details.

## 2026-11-06: Keep paper and journal ports with their workflows

**Status:** Accepted

**Decision:** Keep attribution storage contracts in `forward.core.attribution_ports`, journal reconciliation contracts in `forward.core.trade_journal_ports`, and paper fill reads in `forward.core.paper_ledger_ports`.

**Reason:** These are separate storage boundaries used by different workflows. Placing all three in the attribution module made paper reporting and journal reconciliation depend on an unrelated contract file.

**Behavior:** Protocol methods and adapter behavior are unchanged. Callers import each protocol from its owning workflow module; no compatibility re-exports are provided.

## 2026-11-06: Reuse account-scoped repositories for attribution reads

**Status:** Accepted

**Decision:** Route attribution's opening-order, signal-context, and closing-order reads through their owning SQL repositories. Require the account ID when reading an order.

**Reason:** The adapter should map the attribution port onto existing repository operations instead of repeating SQL. Owner-scoped order reads also make the account boundary explicit at the storage call.

**Behavior:** Keep the same order filters and ordering. A closing-order lookup now returns a row only when that row belongs to the trade's account.

## 2026-11-06: Require account scope for saved backtest strategies

**Status:** Accepted

**Decision:** Require an account ID when listing saved strategies for backtest options. Let storage and strategy-version errors reach the caller.

**Reason:** Selecting the first database account can show one user's strategies to another. Swallowing storage errors can make existing saved strategies look absent and gives the caller no way to distinguish a database failure from an empty list.

**Behavior:** Keep the authenticated route's account filter. Do not select an arbitrary account or return a partial registry when saved-strategy reads fail.

## 2026-11-06: Give trade signal models, rules, and messages separate owners

**Status:** Accepted

**Decision:** Keep signal data models in `forward.core.trade_signal_models`, risk calculations and signal creation in `forward.services.trade_signal_factory`, and Telegram message formatting in `forward.alerts.trade_signal_messages`.

**Reason:** The former `forward.trade_signals` module mixed domain data, market calculations, and alert presentation. Those responsibilities have different dependencies and should not be imported as one unit.

**Behavior:** Keep model fields, risk formulas, expiry timing, and message text unchanged. Update callers to import each responsibility from its owner. Do not keep a compatibility module.

## 2026-11-06: Keep self-learning training outside the API route

**Status:** Accepted

**Decision:** Let `SelfLearningTrainingService` read market history through `CachedMarketFramesPort` and pass it to the self-learning engine through `SelfLearningTrainingPort`. Keep self-learning state and training contracts in `forward.core.self_learning_ports`.

**Reason:** The route should start the use case without choosing a market-data source or assembling engine inputs. The state adapter, engine, and application service can share focused contracts without importing one another's implementations.

**Behavior:** Train on the same cached NSE equity frames, with the same engine and saved state. The HTTP response remains unchanged.

## 2026-11-06: Check storage boundaries for every API route module

**Status:** Accepted

**Decision:** Apply the route-to-adapter architecture check to both `forward.api.routers` and `forward.api.legacy`.

**Reason:** The previous guard covered only versioned routes. Legacy routes are still HTTP adapters and need the same rule against importing storage, data, or broker implementations.

**Behavior:** Existing route behavior is unchanged. The architecture check now scans both route packages.

## 2026-11-06: Resolve order intent keys in the order service

**Status:** Accepted

**Decision:** Keep explicit idempotency keys and strategy-derived order intent keys in `OrderService`. HTTP and execution services pass the optional key and mapped order draft to that use case.

**Reason:** Key derivation is an order rule shared by callers, while the HTTP adapter should only map request data and translate service outcomes.

**Behavior:** An explicit non-empty key still takes priority. Orders linked to a signal or strategy still receive the same derived key whether they enter through the versioned order route or the paper execution route; manual orders without either still have no derived key.

## 2026-11-06: Run saved-screen watches through an application service

**Status:** Accepted

**Decision:** Keep watch configuration, scheduling, match tracking, and notification requests in `ScreenerWatchService`. Give it saved-screen and alert ports; build the Telegram channel in application composition.

**Reason:** The old watch module mixed scan state changes with Telegram settings and channel creation, and both the API route and startup loop called it directly. The watch workflow is application behavior and should receive its dependencies from the composition root.

**Behavior:** Keep interval bounds, baseline handling, new-match detection, retry state, and message text. Preserve the scan's existing HTTP error shape by placing `ScreenerError` in the core error contract. The architecture check now treats `forward.screener` as a compute layer.

## 2026-11-06: Reject invalid persisted risk limits

**Status:** Accepted

**Decision:** Risk-limit data must use known keys and valid values. An invalid
trading window, an invalid boolean, or an unknown stored field raises an error
instead of becoming an unrestricted setting.

**Reason:** Silently dropping a bad trading window or unknown field can remove a
protection while making the risk state look valid.

**Behavior:** Empty `allowed_symbols` means no symbol is allowed. `None` remains
the explicit value for an unset optional limit. Malformed data fails closed.

## 2026-11-06: Require an execution price for paper fills

**Status:** Accepted

**Decision:** A simulated fill must carry a finite, positive execution price.
Paper execution rejects an unpriceable new order instead of substituting a
reference or zero price.

**Reason:** A substitute price can create a fill that did not come from the
configured paper venue.

**Behavior:** Invalid references are reported as unpriceable. A fill decision
without a valid price is an internal error.

## 2026-11-06: Keep public user fields with auth identity models

**Status:** Accepted

**Decision:** The auth identity model owns the shared projection of stored user
fields that may be returned to callers.

**Reason:** A single allowlist keeps secret fields out of every auth response
and avoids separate projections drifting across service methods.

**Behavior:** Auth use cases keep their current response values while reusing
one public-user projection.

## 2026-11-06: Share JSON row decoding across repositories

**Status:** Accepted

**Decision:** SQL repositories use one helper to copy a row and decode only its
declared JSON fields.

**Reason:** Several repositories repeated the same serialization boundary.

**Behavior:** Malformed stored JSON remains an error. Instrument batches use
one transaction and keep the prior last-write-wins behavior for duplicate keys.

## 2026-11-06: Keep monitoring cache data private

**Status:** Accepted

**Decision:** Public monitor queries accept identifiers and user input only.
Combined screens pass already-loaded data to a private status builder.

**Reason:** Internal cached rows and computed summaries are composition details,
not public query inputs.

**Behavior:** An overview reuses its deployment row and summaries instead of
loading them again while building status.

## 2026-11-06: Share validation report read errors

**Status:** Accepted

**Decision:** `ValidationReportService` owns the shared handling of expected
report read and JSON decode errors.

**Reason:** The report methods repeated the same storage error translation.

**Behavior:** Each report keeps its current missing-file response and aggregation
rules; malformed report files remain visible as read errors.

## 2026-11-06: Reuse parsed strategy definitions

**Status:** Accepted

**Decision:** Strategy version responses reuse a parsed definition when the
service has already loaded and validated it.

**Reason:** Parsing the same stored definition twice adds work and splits one
use case across duplicate conversions.

**Behavior:** Version response fields and validation rules remain unchanged.

## 2026-11-06: Share learning report label counts

**Status:** Accepted

**Decision:** Learning reports use one pure helper to count categorical labels.
Each report section keeps its own tie rule.

**Reason:** The count logic is reusable, while tie meaning depends on the report
question.

**Behavior:** Regime reports list all tied leaders; unusual-event summaries keep
their first-seen tie behavior.

## 2026-11-06: Parse each broker trade once for dashboard performance

**Status:** Accepted

**Decision:** The dashboard performance summary reads each trade's realized
P&L and date in one pass, then reuses them for aggregate and daily totals.

**Reason:** A second scan would parse the same broker response twice and could
let aggregate and daily results use different input handling.

**Behavior:** Existing order, daily grouping, and drawdown rules stay the same.

## 2026-11-06: Reuse market-depth row rendering

**Status:** Accepted

**Decision:** Bid and ask rows share one `DepthRow` renderer. Side-specific
classes stay in a typed style map.

**Reason:** Both sides show the same level fields and interactions; only their
color treatment differs.

**Behavior:** Price selection, row order, labels, and quantity bars stay the
same.

## 2026-11-06: Filter analytics rows before pagination

**Status:** Accepted

**Decision:** Apply every normalized trade filter in the storage query before
LIMIT and OFFSET.

**Reason:** Filtering a page in memory can return a short page and hide matching
rows that occur after that page.

**Behavior:** The full matching total and response shape remain unchanged.

## 2026-11-06: Read one fill snapshot for both attribution legs

**Status:** Accepted

**Decision:** Trade attribution loads one account fill snapshot and uses it for
both the entry and exit legs.

**Reason:** Two separate reads cost more and can observe different fill state.

**Behavior:** Entry and exit legs use the same fill snapshot for each attributed
trade.

## 2026-11-06: Reject missing backtest annotation timestamps

**Status:** Accepted

**Decision:** Shared timestamp matching raises on missing or invalid values
instead of mapping them to an empty key.

**Reason:** Empty keys can make unrelated annotations compare equal and attach
the wrong reason to a trade.

**Behavior:** Valid Python and pandas timestamps keep the same ISO matching key;
invalid data is visible to the caller.

## 2026-11-06: Validate live quote prices with one rule

**Status:** Accepted

**Decision:** The live paper-price adapter uses one finite, positive-price check
for the last price, open, bid, and ask.

**Reason:** A NaN or infinite quote is not a usable market price, and repeated
checks can drift apart.

**Behavior:** Missing or invalid quote fields remain unavailable; valid quote
values keep their existing behavior.

## 2026-11-06: Share the position risk budget calculation

**Status:** Accepted

**Decision:** Fixed-rupee and percentage-based risk sizing use one shared helper
to calculate the rupee risk amount.

**Reason:** Both risk sizing methods used the same calculation, which could drift
if maintained in two places.

**Behavior:** Existing precedence and percentage defaults remain unchanged.

## 2026-11-06: Measure insight data age in the market timezone

**Status:** Accepted

**Decision:** The daily-insights service measures cache age using the configured
IST session timezone.

**Reason:** Host-local time can differ from the market calendar date used by the
underlying data.

**Behavior:** The age calculation now follows IST consistently with scheduled
insight delivery.

## 2026-11-06: Share alert indicator calculations

**Status:** Accepted

**Decision:** RSI alerts share one latest-value calculation, and SMA alert
directions share one cross evaluation path.

**Reason:** The alert kinds differ only by comparison direction; duplicate
calculation paths can drift.

**Behavior:** Thresholds, five-bar cross window, and alert messages remain the
same.

## 2026-11-06: Surface intelligent-alert indicator errors

**Status:** Accepted

**Decision:** Golden-cross evaluation lets invalid indicator input errors reach
the alert cycle instead of silently treating them as no signal.

**Reason:** A hidden exception-to-no-signal conversion can make broken data look
like a valid absence of a trading condition.

**Behavior:** Valid crossover results keep the same three-bar check and message.

## 2026-11-06: Keep alert file storage in the data adapter layer

**Status:** Accepted

**Decision:** Alert JSON persistence lives in `forward.data` and implements the
alert storage port.

**Reason:** Alert rules and delivery logic should depend on the storage contract,
not on filesystem code inside the alert package.

**Behavior:** Composition and CLI use the new adapter. Missing files still mean
empty collections; malformed or unreadable files now raise instead of appearing
empty.

## 2026-11-06: Build daily bars through one shared shape

**Status:** Accepted

**Decision:** Exchange-history rebuild, BSE backfill, and broker-cache top-up
use one helper to construct the application's timestamp and OHLCV columns.

**Reason:** These paths write the same bar contract, so one transformation
keeps their column order and timestamp rule consistent.

**Behavior:** Each caller keeps its existing dtype conversion and persistence
behavior.

## 2026-11-06: Share custom formula evaluation in signal rules

**Status:** Accepted

**Decision:** Entry and exit rules use one helper to compile and evaluate their
saved formulas.

**Reason:** Both paths build the same indicator context and handle invalid
formulas the same way.

**Behavior:** Formula results, signal text, and scan behavior stay unchanged.

## 2026-11-06: Reuse the computed market summary for one-stock context

**Status:** Accepted

**Decision:** When building context for a symbol outside the cached universe,
reuse the summary and sectors from the first market snapshot read.

**Reason:** The second read repeats the accessor call and can observe a different
snapshot if the market configuration changes between calls.

**Behavior:** The one-stock metrics and public response remain unchanged.

## 2026-11-06: Share risk-reduction classification across order gates

**Status:** Accepted

**Decision:** Book-limit and daily-loss gates use one method to identify
risk-reducing orders.

**Reason:** Both gates depend on the same order classification, and duplicate
checks can drift.

**Behavior:** Existing classification and exception handling stay unchanged.

## 2026-11-06: Share common Indian transaction charges

**Status:** Accepted

**Decision:** Delivery and intraday cost models use one helper for brokerage,
exchange, SEBI, and GST charges.

**Reason:** Both models use the same charge formula; only their STT, stamp duty,
and delivery fees differ.

**Behavior:** Delivery and intraday fee differences remain unchanged.

## 2026-11-06: Do not allow a broken request limiter to pass traffic

**Status:** Accepted

**Decision:** Let rate-limiter errors reach the API middleware instead of
turning them into an allowed request. Reject non-positive limits and non-finite
or non-positive token costs instead of clamping them.

**Reason:** A broken security check must not quietly disable itself.

**Behavior:** Valid requests and normal 429 responses keep their existing
token-bucket rules. Invalid configuration and limiter failures are visible to
the request path.

## 2026-11-06: Use one screener leaf traversal

**Status:** Accepted

**Decision:** Screener tree validation uses the public recursive `iter_leaves`
helper; the duplicate traversal was removed.

**Reason:** One tree walk keeps validation and other screener code in agreement.

**Behavior:** Leaf order and validation results remain unchanged.

## 2026-11-06: Keep option candidate de-duplication ordered

**Status:** Accepted

**Decision:** Track seen rounded candidates in a set while emitting candidates
in their original order.

**Reason:** This avoids repeated linear membership scans without changing the
search order.

**Behavior:** Candidate values and output order remain unchanged.

## 2026-11-06: Surface malformed research and calendar data

**Status:** Accepted

**Decision:** Do not convert calculation or corrupt-file errors into empty
research and P&L results. Use the Indian market date for market-day defaults.

**Reason:** An empty result can look like a real zero when the input was broken,
and host-local dates can disagree with NSE dates.

**Behavior:** Missing optional calendar files keep their declared empty-list
meaning. Corrupt inputs now raise. Tradebook, calendar, and history-scan dates
use IST.

## 2026-11-06: Remove unused route and data helpers

**Status:** Accepted

**Decision:** Remove helpers and query parameters with no callers, and reuse
existing middleware constants and imports.

**Reason:** Unused API surface and duplicate names create maintenance work
without serving a caller.

**Behavior:** The learning dataset route keeps the parameters its web caller
uses. No caller of the removed helper or parameter was found in the project.

## 2026-11-06: Remove the unused P&L calendar client

**Status:** Accepted

**Decision:** Remove the unused P&L calendar component, its frontend API
types and calls, and the unused `TrendArrow` export.

**Reason:** No current screen imports the component or calls the API wrappers,
and no caller uses `TrendArrow`. Keeping them would leave stale frontend
surface that cannot be reached by a user.

**Behavior:** Current screens and their API calls do not change. The backend
P&L calendar routes remain available.

## 2026-11-06: Keep instrument search failures separate from no matches

**Status:** Accepted

**Decision:** `SymbolPicker` reports failed instrument search requests as search
errors. It adds a pasted symbol to the “Not found” list only after a successful
search returns no matching instrument.

**Reason:** A network or service failure does not prove that an instrument is
missing. Calling it “not found” hides an outage and can make a valid symbol look
like a typo.

**Behavior:** A failed typed search displays a search error. A failed pasted
search reports that some symbols could not be checked; successful no-match
searches remain listed as not found.

**Code:** `web/src/components/ui/symbol-picker.tsx`.

## 2026-11-06: Require explicit credentials for the paper UI probe

**Status:** Accepted

**Decision:** The paper UI probe reads its user name and password from required
`FORWARD_PROBE_USER` and `FORWARD_PROBE_PASSWORD` environment variables. It has
no built-in account or cookie override.

**Reason:** A probe must not ship an account default or bypass its login flow
with a hardcoded session value.

**Behavior:** The probe stops with a clear message when either credential is
missing and logs in with the supplied account when both are present.

## 2026-11-06: Use Bun as the frontend package manager

**Status:** Accepted

**Decision:** Frontend builds and the development server use Bun, matching the
CI workflow, `web/bun.lock`, and the architecture guide. Startup fails when Bun
is unavailable or the build fails. `--no-build` requires an existing built
frontend.

**Reason:** Trying several package managers or continuing after a failed build
can serve stale or missing assets and makes the running app differ by machine.

**Behavior:** The app starts only with a successful Bun build or an explicitly
requested existing build.

## 2026-11-06: Read chart colors from theme tokens

**Status:** Accepted

**Decision:** Canvas chart colors and surface colors come from CSS theme tokens.
Chart setup raises a clear error when a required token is missing instead of
substituting a second palette.

**Reason:** A fallback palette can make the canvas disagree with the theme and
hide a missing design token.

**Behavior:** Light and dark themes define the chart canvas colors in one place.

**Code:** `web/src/lib/chart-theme.ts`, `web/src/index.css`, and
`web/src/ChartsPanel.tsx`.

## 2026-11-06: Describe only configured exit settings

**Status:** Accepted

**Decision:** Exit summaries show configured indicator periods, confirmation
bars, and ATR values only. An enabled ATR trail with incomplete settings is
reported as incomplete.

**Reason:** A summary must not imply the strategy uses values absent from its
saved definition.

**Behavior:** Missing optional values are no longer shown as assumed defaults.

**Code:** `web/src/lib/strategy-exits.ts`.

## 2026-11-06: Remove the local WebSocket origin guess

**Status:** Accepted

**Decision:** Live tick setup uses the configured API origin or the browser's
current origin. It raises an error if neither exists; it does not guess a
localhost service.

**Reason:** A guessed local port can connect to a different service or hide a
missing deployment configuration.

**Behavior:** A malformed tick frame is logged instead of being silently
discarded.

**Code:** `web/src/lib/useLiveTicks.ts`.

## 2026-11-06: Keep the exchange with each live tick subscription

**Status:** Accepted

**Decision:** Tick subscriptions carry a symbol and its exchange. The client
groups symbols by exchange when it sends WebSocket requests and keys stored
ticks by both values.

**Reason:** One exchange default can request a BSE or SME instrument from the
wrong market and can mix same-named instruments.

**Behavior:** Screens pass exchange data from their instrument or portfolio
context. A lookup without an exchange returns a tick only when exactly one
matching subscription exists.

**Code:** `web/src/lib/useLiveTicks.ts` and its callers.

## 2026-11-06: Remove the old theme compatibility module

**Status:** Accepted

**Decision:** Import money and percentage formatters from `lib/format.ts`.
Remove the deprecated `lib/theme.ts` shim and move its one typography use to
the component.

**Reason:** A deprecated module kept duplicate display tokens and a compatibility
export path alive after the canonical formatter module existed.

**Behavior:** Formatter calls keep the same implementation and output.

**Code:** `web/src/lib/format.ts`, former `web/src/lib/theme.ts` callers, and
`web/src/PortfolioControlCenter.tsx`.

## 2026-11-06: Show paper P&L only when fees and marks are complete

**Status:** Accepted

**Decision:** Paper and portfolio P&L views use the API's `pnl_complete` and
`commission_complete` fields. Unknown net P&L stays null and renders as a dash;
it is never added as zero. P&L values retain up to two decimal places.

**Reason:** Missing commission data means the net result cannot be measured.
Treating it as zero makes gross results look net and changes portfolio totals.

**Behavior:** Aggregates show a result only when every included deployment has
complete P&L. A deployment with unknown fees shows an incomplete label, and its
position P&L is not shown as a measured amount.

**Code:** `web/src/paper-api.ts`, `web/src/api.ts`, `web/src/PaperRuns.tsx`,
`web/src/PaperDeploymentPanel.tsx`, `web/src/OverviewPanel.tsx`,
`web/src/StrategiesPanel.tsx`, `web/src/StrategyDetails.tsx`, and
`web/src/PortfolioControlCenter.tsx`.

## 2026-11-06: Require a portfolio policy before adding exposure

**Status:** Accepted

**Decision:** The order gate rejects an order that adds exposure when the
account has no stored portfolio risk policy. Orders that reduce an existing
position remain allowed. An incomplete commission record also blocks added
exposure, even when a policy is present.

**Reason:** Missing risk settings do not prove that the operator intended to
allow unlimited exposure. Treating an absent policy as approval would be a
silent fallback. Closing orders must remain possible so missing settings or
fees do not trap an existing position.

**Behavior:** The gate returns `portfolio_policy_missing` when there is no
policy and `commission_incomplete` when fees are unknown. Portfolio reporting
without a policy remains unchanged.

**Code:** `src/forward/services/portfolio_risk.py`.

## 2026-11-06: Keep CLI workflows behind application services

**Status:** Accepted

**Decision:** CLI handlers parse options, call composed application services,
and render their results. They do not read application databases or recreate
service workflows directly.

**Reason:** Direct SQL, file adapters, and duplicated workflow logic make
command behavior drift from the API and dashboard.

**Behavior:** Portfolio reads, backups, data quality checks, cached full-market
scans, alerts, and account lookup use the existing service composition. Broker
instrument search reads the local catalog and does not trigger a network sync.

**Code:** `src/forward/cli.py` and the existing service composition.

## 2026-11-06: Report alert delivery and data failures explicitly

**Status:** Accepted

**Decision:** Alert delivery uses only configured channels. Telegram sends one
Markdown message and reports rejection without retrying in another format.
An event records the channel that accepted delivery; if none accepts it, the
event records `channel="none"` and `ok=false`. Intelligent alerts use symbols
from the configured cached universe and holdings, and require valid market,
RSI, and enabled volume-breakout data before evaluating a signal.

**Reason:** Assumed data and implicit log or plain-text delivery can hide a
missing market feed or a failed notification from the operator.

**Behavior:** Missing required data raises an explicit error. A failed or
unconfigured delivery remains visible in the recorded alert event.

**Code:** `src/forward/alerts/channels.py`, `src/forward/alerts/models.py`, and
`src/forward/alerts/intelligent.py`.

## 2026-11-06: Reject invalid successful API responses

**Status:** Accepted

**Decision:** A successful versioned API response must contain valid JSON,
except for an explicit `204 No Content` response. Invalid or empty response
bodies raise a typed `ApiError`.

**Reason:** Casting an absent response body to the requested type hides server
and proxy failures until they cause confusing errors farther into the UI.

**Behavior:** Callers receive the `invalid_json_response` code and can show the
request failure where it happened.

**Code:** `web/src/lib/api-client.ts`.

## 2026-11-06: Keep missing strategy inputs unknown

**Status:** Accepted

**Decision:** Candidate portfolio strategies exclude a symbol when its ranking
value or close is missing. A missing aggregate-volatility or trend reading does
not change portfolio exposure. Paper-model evaluation errors propagate to the
caller instead of becoming a no-signal result.

**Reason:** Treating absent data or a model error as zero can add a false rank,
change portfolio exposure, or hide a broken model as an ordinary quiet day.

**Behavior:** The candidate selector uses `None` for unavailable numeric data.
It waits for configured warmup, then raises `StrategyInputUnavailable` if a
required ranking, price, or exposure input is still missing. It does not turn
missing universe returns into zero returns.

**Code:** `src/forward/strategy/strategies/alpha_candidates.py` and
`src/forward/strategy/strategies/paper_alpha.py`.

## 2026-11-06: Do not invent regime inputs

**Status:** Accepted

**Decision:** Regime strategies use calculated ADX and the configured lookback
periods. They report missing indicator inputs and skip classification until
those inputs exist. Position quantities use the shared sizing engine and the
instrument's configured quantity step.

**Reason:** Fixed ADX guesses and neutral indicator values can create a regime
signal without market evidence. A regime strategy also needs the same sizing
rules used elsewhere in the platform.

**Behavior:** No market inputs are substituted when indicators are missing.
The strategy waits for the configured warmup, then raises
`StrategyInputUnavailable` when a required value is still missing.

**Code:** `src/forward/strategy/indicators.py` and
`src/forward/strategy/strategies/regime_adaptive.py`.

## 2026-11-06: Require typed alert channel settings

**Status:** Accepted

**Decision:** Alert channel construction reads the required settings fields
directly from `Settings` instead of probing for optional attributes or filling
missing fields with empty strings.

**Reason:** The settings model owns channel configuration. A mismatched settings
object should fail at the integration boundary rather than disable delivery
without a clear reason.

**Behavior:** Only explicitly configured channels are constructed. Missing
settings fields are a configuration error.

**Code:** `src/forward/alerts/channels.py`.

## 2026-11-06: Inject trade-signal creation into alert monitoring

**Status:** Accepted

**Decision:** The intelligent-alert monitor receives a trade-signal factory at
construction time. The composition root supplies the service implementation.

**Reason:** Alert monitoring must not import an application service while it is
running. The dependency belongs at the composition boundary.

**Behavior:** Signal creation uses the same existing service, with the
dependency visible and required when the monitor is built.

**Code:** `src/forward/alerts/intelligent.py` and `src/forward/composition.py`.

## 2026-11-06: Keep options paper P&L unknown without complete live marks

**Status:** Accepted

**Decision:** Options forward-run snapshots report total P&L and equity only
when every open option has a valid live quote and every fill has known
commission. They list unpriced symbols and report price completeness separately
from commission completeness.

**Reason:** Reusing a previous mark when the feed has no current quote makes a
live P&L figure look current when it is not.

**Behavior:** Cash and realized P&L remain available when commissions are known.
Total and unrealized P&L are null while either marks or commissions are
incomplete.

**Code:** `src/forward/services/options_forward.py`.

## 2026-11-06: Surface audit file failures

**Status:** Accepted

**Decision:** The JSONL audit adapter raises file-write, file-read, and malformed
record errors. It serializes only valid JSON values and does not turn failures
into warnings or empty history.

**Reason:** Dropping an audit event or presenting a damaged file as an empty
audit trail hides an operational failure.

**Behavior:** A missing file still means no events have been written. Once a file
exists, unreadable or malformed content is reported to the caller.

**Code:** `src/forward/infra/audit_log.py` and `src/forward/services/audit_log.py`.

## 2026-11-06: Reject invalid compute device names

**Status:** Accepted

**Decision:** The compute device selector accepts only `auto`, `cpu`, or `cuda`.
An unavailable requested CUDA device raises an error; an unknown name is never
changed to `auto`.

**Reason:** A typo must not silently change where a screening job runs.

**Behavior:** The explicit `auto` option still selects CUDA when available and
CPU otherwise. `gpu` is no longer an alias for `cuda`.

**Code:** `src/forward/compute/device.py`.

## 2026-11-06: Validate compute screen inputs

**Status:** Accepted

**Decision:** Close-matrix and parameter-screen functions reject invalid windows,
limits, costs, and empty input matrices before starting tensor calculations.

**Reason:** Invalid inputs should fail at the calculation boundary instead of
producing empty or misleading screening results.

**Behavior:** Valid screens keep their calculation rules. Invalid limits and
screens with no valid fast/slow pair raise clear `ValueError` messages.

**Code:** `src/forward/compute/sweep.py` and `src/forward/compute/holdout.py`.

## 2026-11-06: Exclude incomplete trades from forward comparisons

**Status:** Accepted

**Decision:** Forward-versus-backtest comparisons use only closed trades with
known net P&L, return, entry price, and quantity. Incomplete rows are counted
and reported in the response.

**Reason:** Missing costs or prices must not be counted as zero when judging
whether paper results match the backtest.

**Behavior:** Comparison statistics use complete trades only. The output shows
how many rows could not be included.

**Code:** `src/forward/services/forward_check.py`.

## 2026-11-06: Require configured encryption keys

**Status:** Accepted

**Decision:** Secret storage uses `FORWARD_SECRET_KEY` or an existing file at
`FORWARD_SECRET_KEY_FILE`. The application does not create a replacement key.
Missing keys and unreadable encrypted secrets raise an authentication error.

**Reason:** A generated replacement key cannot decrypt existing secrets, and
turning key errors into disabled MFA can weaken account protection.

**Behavior:** Password login remains available for accounts without encrypted
secrets. Secret enrollment and MFA login report an explicit service error when
the configured key cannot be loaded or cannot decrypt the stored seed.

**Code:** `src/forward/infra/secret_box_provider.py`, `src/forward/auth/service.py`,
`src/forward/auth/crypto.py`, and `src/forward/config/settings.py`.

## 2026-11-06: Back up the configured database

**Status:** Accepted

**Decision:** Local ZIP backups receive the database file selected by the
application database adapter. They require a file-backed SQLite database and a
present database file. Other database modes report that local ZIP backup is not
supported.

**Reason:** Assuming `data/app.db` can archive the wrong database or omit the
configured database without telling the operator.

**Behavior:** Backup and restore use the configured SQLite path. Unsupported
backends, memory databases, and missing source files fail clearly. Backup
folder paths resolve from the project root when relative.

**Code:** `src/forward/appdb/engine.py`, `src/forward/core/backup_ports.py`,
`src/forward/services/backup.py`, `src/forward/infra/backup.py`,
`src/forward/composition.py`, and `src/forward/cli.py`.

## 2026-11-06: Propagate audit sink failures

**Status:** Accepted

**Decision:** JSONL serialization, append, read, and parse errors propagate to
the caller. Audit database write failures also propagate instead of returning an
empty event ID.

**Reason:** A missing audit record or damaged log must not look like a
successful action or an empty audit trail.

**Behavior:** A missing not-yet-created JSONL file still returns no events.
Once a file exists, unreadable or malformed content raises. A database audit
failure fails its transaction.

**Code:** `src/forward/infra/audit_log.py`, `src/forward/audit/events.py`,
`src/forward/appdb/audit_storage.py`, and `src/forward/api/routers/risk.py`.

## 2026-11-06: Do not turn failed optimization runs into scores

**Status:** Accepted

**Decision:** Walk-forward and parameter-neighborhood evaluation errors stop the
optimization run. They do not become zero metrics or look like ordinary failed
strategies.

**Reason:** A failed evaluation has no measured score. Scoring it with made-up
values can change candidate ranking and hide broken inputs or code.

**Behavior:** Evaluation exceptions propagate. Walk-forward also raises when it
produces no valid in-sample metrics. A proposed robustness value must be inside
the parameter's declared bounds.

**Code:** `src/forward/optimization/evaluator.py`.

## 2026-11-06: Use measured paper-strategy evidence as stored

**Status:** Accepted

**Decision:** Paper-strategy signals require recorded measured statistics, and
the evaluator registry is the single source of paper strategy membership.

**Reason:** A made-up neutral win rate or confidence value looks like research
evidence. A second hand-written evaluator list also drifts when a model is added.

**Behavior:** Missing measured fields and unknown strategy IDs raise clear
errors. Evaluating all models iterates the registry.

**Code:** `src/forward/research/papers.py`.

## 2026-11-07: Keep missing market observations unknown

**Status:** Accepted

**Decision:** Market-intelligence summaries require usable benchmark history.
Research comparisons keep incomplete benchmark and return observations
unknown instead of filling them with zero or stale marks.

**Reason:** A placeholder benchmark can turn missing input into a measured flat
market and change a research verdict.

**Behavior:** Market-intelligence returns a service-unavailable response when
the benchmark or all eligible stock history is missing. Symbols without enough
history are counted and shown. Research benchmark metrics are `null` when held
prices are incomplete; the verdict marks that comparison unknown. Evidence
helpers preserve missing return periods and reject incomplete comparison data.

**Code:** `src/forward/market_intel/models.py`,
`src/forward/market_intel/service.py`,
`src/forward/api/routers/market_intel.py`, `src/forward/research/evidence.py`,
`src/forward/research/validate.py`, and `src/forward/services/research_validation.py`.

## 2026-11-07: Require the position quantity rounding rule

**Status:** Accepted

**Decision:** A backtest configuration must carry the rule used to convert
fractional target quantities into whole shares.

**Reason:** Different rounding rules produce different position sizes and P&L.
Leaving the rule out makes a run hard to reproduce.

**Behavior:** Config validation accepts only `FLOOR`, `ROUND`, or `CEIL`. The
configuration form requires a choice and passes it to the sizing plan.

**Code:** `src/forward/backtest/config.py`, `src/forward/backtest/runner.py`,
`src/forward/services/backtests.py`, `src/forward/api/routers/backtests.py`,
`web/src/backtest-api.ts`, and `web/src/BacktestWorkflowPanel.tsx`.

## 2026-11-07: Require the available cash limit for each order

**Status:** Accepted

**Decision:** Every strategy sizing configuration states what fraction of
currently available cash a new order may use.

**Reason:** A hidden cash cap changes position quantities and makes sizing
behavior unclear. The chosen fraction is part of the strategy and backtest
configuration so the result can be repeated.

**Behavior:** The fraction must be greater than 0 and at most 1. The backtest
form requires a percentage from 0 to 100 and sends it as a fraction. The shared
sizing engine applies that limit directly; it does not add a separate cash
reserve.

**Code:** `src/forward/strategy/sizing.py`,
`src/forward/backtest/sizing.py`, `src/forward/backtest/config.py`,
`src/forward/backtest/runner.py`, `web/src/backtest-api.ts`,
`web/src/BacktestWorkflowPanel.tsx`, and `web/src/lib/test-setup.ts`.

## 2026-11-07: Require explicit strategy sizing and canonical rule blocks

**Status:** Accepted

**Decision:** A sizing configuration names both a sizing method and a rounding
rule, includes every input that method needs, and sets the allowed share of
available cash. Definitions use only the
nested `rules.entry` and `rules.exit` blocks. JSON encoding and rule evaluation
do not stringify unknown objects or accept plural and top-level aliases.

**Reason:** A method, rounding choice, or rule block guessed by a consumer can
change order size or make the saved strategy mean something different in
preview, backtest, and deployment.

**Behavior:** Missing sizing is a validation error. Missing required data during
an evaluation raises `StrategyInputUnavailable` and reaches the caller. Live
deployment sets an observable error state when sizing is invalid. Strategies
that use resting orders must have a cancellation port. Volume-confirmed rules
require actual volume data, and regime classifier failures propagate.

**Code:** `src/forward/strategy/sizing.py`,
`src/forward/strategy/definition.py`, `src/forward/backtest/sizing.py`,
`src/forward/strategy/base.py`, `src/forward/strategy/strategies/episodic_pivot.py`,
`src/forward/strategy/strategies/momentum_breakout.py`,
`src/forward/strategy/regime_laya.py`, and `src/forward/services/runner.py`.

## 2026-11-07: Require complete execution inputs

**Status:** Accepted

**Decision:** Fee calculation requires an order side. Broker reports map only
from explicit states, and a fill must include a positive quantity and price.
Risk limits use `None` to mean that no cap is configured.

**Reason:** Guessing a missing side can understate fees. Treating a cancelled or
incomplete broker response as an accepted or filled order can create a false
position in the platform.

**Behavior:** Missing fee sides fail at the call boundary. Broker rejections
need a reason. Only pending or submitted orders count as accepted; unexpected
states and incomplete fills raise errors. A configured notional cap rejects an
order when its reference price is unknown. A zero cap remains active; invalid or
infinite configured limits raise instead of disabling checks.

**Code:** `src/forward/execution/costs.py`, `src/forward/execution/risk.py`,
`src/forward/execution/oms.py`, `src/forward/services/risk.py`,
`src/forward/brokers/venue.py`, and `src/forward/brokers/base.py`.

## 2026-11-07: Seed daily risk from recorded equity

**Status:** Accepted

**Decision:** Daily-loss checks use an explicit equity baseline set before the
session's first market update and reset from the prior session's equity.

**Reason:** Seeding the baseline during a risk check happens after the first
mark and can hide losses. Leaving it unset or stale can also make the daily
limit cumulative or impossible to trigger.

**Behavior:** Backtest and live runners set the opening baseline explicitly.
Every live date change resets it before processing that day's snapshot. A
missing baseline with an active daily-loss limit raises an error.

**Code:** `src/forward/execution/risk.py`, `src/forward/backtest/engine.py`,
and `src/forward/live/runner.py`.

## 2026-11-07: Reject future paper quote timestamps

**Status:** Accepted

**Decision:** Quote age is measured against a caller-supplied current time. A
future timestamp, invalid supplied age, or missing age when freshness limits are
active cannot be treated as fresh.

**Reason:** Clamping a future quote to age zero can make a clock-skewed or
malformed quote appear fresh and allow a paper fill.

**Behavior:** Callers must supply the comparison time. Reported ages must be
finite and non-negative. Timestamp-derived ages raise when the timestamp is in
the future. Paper orders with a configured age limit are rejected when a quote
has no measurable age, and quote-source errors propagate.

**Code:** `src/forward/core/paper_models.py`,
`src/forward/brokers/paper_venue.py`, and `src/forward/services/paper_prices.py`.

## 2026-11-07: Preserve missing volume and observed alert bars

**Status:** Accepted

**Decision:** A market bar keeps missing volume as `None`. Alert indicator
history receives the current observed `Bar`; it does not build one from LTP or
reuse an earlier volume value.

**Reason:** Zero volume is a measurement, while missing volume is not. Reusing
prior volume or inventing OHLC values can change liquidity assumptions and
indicator results.

**Behavior:** IIFL alert quotes must contain a valid timestamp and OHLC prices.
Traded volume remains `None` only when the broker omits it. Missing quote rows
or invalid prices raise errors. Alert history appends the supplied bar as-is.
Participation-capped backtest fills require measured volume; a measured zero
volume produces no fill.

**Code:** `src/forward/core/market_models.py`,
`src/forward/core/alert_ports.py`, `src/forward/alerts/engine.py`,
`src/forward/data/alert_history.py`, and
`src/forward/brokers/iifl/alert_quotes.py`, and
`src/forward/backtest/broker.py`.

## 2026-11-07: Require an explicit holdings source

**Status:** Accepted

**Decision:** Each holdings read names `LIVE`, `SNAPSHOT`, or `EXPORT` at the
call site.

**Reason:** Choosing a saved snapshot after a live broker read fails can hide
stale account data and make the report appear current.

**Behavior:** `LIVE` requires an authenticated broker client. `SNAPSHOT` and
`EXPORT` read only their selected file and raise when it is missing or invalid.
No source is selected automatically.

**Code:** `src/forward/core/insights_ports.py`,
`src/forward/data/insights_holdings.py`, `src/forward/insights/service.py`,
`src/forward/services/pnl_calendar.py`, and
`src/forward/api/legacy/startup.py`.

## 2026-11-07: Keep each market-data source separate

**Status:** Accepted

**Decision:** Read only the market-data source named by the caller. Keep IIFL
history, public exchange history, and Yahoo history in separate cache roots.
Do not repair broker history with public bars or join bars from different
exchanges. Record provenance only when the source is known. Keep missing volume
as unknown, and reject malformed OHLCV rows.

**Reason:** Combining sources can hide a missing broker bar or change a price
series without making that change visible. Replacing missing volume with zero
changes a missing measurement into a real measurement.

**Behavior:** IIFL history stays under `iifl_daily`. Public NSE and BSE history
is written under `public_daily` with a source manifest. Explicit Yahoo imports
use `public_daily/YAHOO`. Index contract source and candle interval are selected
by the caller; index sync makes no alternate-source or alternate-interval
request. P&L reads the exact IIFL symbol file. A missing cache stays missing,
and invalid rows raise an error.

**Code:** `src/forward/data/base.py`, `src/forward/data/alert_history.py`,
`src/forward/data/data_status.py`, `src/forward/data/eod_refresh.py`,
`src/forward/data/indices.py`, `src/forward/data/market_history.py`,
`src/forward/data/pnl_calendar.py`, `src/forward/data/quality.py`, and
`src/forward/cli.py`.

## 2026-11-07: Require measured liquidity for paper fills

**Status:** Accepted

**Decision:** Paper fills require a valid average-volume measurement. A market
order with a live quote must use the observed bid or ask. Configured quote-age
limits require a measurable age.

**Reason:** Filling without volume skips participation limits and impact. Using
last price when the spread is missing can give a market order an unreal price.

**Behavior:** Average volume must be positive and finite. Invalid venue settings
raise at construction. Broker price-source exceptions propagate. With an age
limit enabled, missing timestamp or age rejects submission; market orders with
an invalid two-sided quote are rejected.

**Code:** `src/forward/brokers/paper_venue.py`,
`src/forward/services/paper_prices.py`, and `src/forward/composition.py`.

## 2026-11-07: Reject paper-ledger reads for missing deployments

**Status:** Accepted

**Decision:** A paper-ledger replay with a deployment ID requires that deployment
to belong to the requested account. The SQL adapter raises when it cannot find
that owned deployment.

**Reason:** Treating a missing allocation as zero cash makes a bad deployment ID
look like a valid empty account and hides an ownership or persistence error.

**Behavior:** The deployment cash port returns the saved allocation for an owned
deployment. Missing or unowned deployments raise `LookupError`. Ad-hoc replays
that explicitly provide starting cash remain supported by the application service.

**Code:** `src/forward/appdb/paper_ledger_storage.py` and
`src/forward/services/paper_ledger.py`.

## 2026-11-07: Persist reconciliation reports with their audit events

**Status:** Accepted

**Decision:** Store a reconciliation report and its audit event in the same
database transaction. Propagate persistence errors to the caller.

**Reason:** A missing audit event or report must not be hidden behind a
successful reconciliation response.

**Behavior:** Both records commit together, or neither does. The storage port
returns a run ID only after a successful commit.

**Code:** `src/forward/core/reconciliation_ports.py` and
`src/forward/appdb/reconciliation_storage.py`.

## 2026-11-07: Keep schema changes out of application startup

**Status:** Accepted

**Decision:** `AppDatabase.prepare()` creates tables from current SQLAlchemy
metadata only. It does not alter existing tables or rewrite stored rows.

**Reason:** Hidden startup migrations make database behavior depend on which
schema happened to be present and can hide failed data changes. Schema changes
must be explicit and reviewable.

**Behavior:** Existing databases must match the current schema. Adding,
renaming, or transforming fields requires a separately reviewed migration.

**Code:** `src/forward/appdb/engine.py` and `src/forward/appdb/schema.py`.

## 2026-11-07: Require instrument metadata for file-backed backtests

**Status:** Accepted

**Decision:** CSV and Parquet backtests load instrument definitions from an
explicit JSON catalog. Each row must provide its symbol, asset class, currency,
exchange, multiplier, tick size, minimum quantity, and quantity step.

**Reason:** A bar file describes prices, not what contract those prices belong
to. Guessing equity metadata can produce incorrect sizing and risk calculations.

**Behavior:** The CLI rejects missing, duplicate, incomplete, or unknown catalog
fields. Synthetic runs continue to use their configured generator instruments.

**Code:** `src/forward/data/instrument_file.py` and `src/forward/cli.py`.

## 2026-11-07: Build validation from the panel that was actually loaded

**Status:** Accepted

**Decision:** The cross-sectional validation feed and its reported universe size
use symbols in the loaded panel, whether the panel was fetched or read from cache.

**Reason:** Cached validation did not define the fetch-only `frames` variable,
and constructing feed instruments from the current liquidity screen could differ
from the symbols in an older cached panel.

**Behavior:** The panel's non-empty `symbol` values define instruments and the
reported universe count. Both fetch and cache paths use the same source of truth.

**Code:** `scripts/validate_cross_sectional.py`.

## 2026-11-07: Require live data on live quote and holdings requests

**Status:** Accepted

**Decision:** When a route requests live holdings or live watchlist quotes, the
route requires the authenticated broker source and does not replace missing
quotes with cached values. Cached watchlist data is available only when the
caller requests it explicitly.

**Reason:** A live-data request that quietly returns cached holdings or prices
can look current while describing a different source.

**Behavior:** Broker authentication and quote errors reach the caller as errors.
Live watchlist requests fail when any symbol has no valid last-traded price.
Learning endpoints also propagate storage errors instead of returning an empty
observation list.

**Code:** `src/forward/api/routers/insights.py`,
`src/forward/api/routers/watchlists.py`,
`src/forward/api/routers/learning.py`, and
`src/forward/api/routers/paper.py`.

## 2026-11-07: Fingerprint all execution-relevant price fields

**Status:** Accepted

**Decision:** A backtest data fingerprint includes each symbol's full timestamp
sequence and available OHLCV values.

**Reason:** Close-only hashes can match even when changed highs, lows, opens, or
volume alter a signal, fill, stop, or participation-capped execution. Recording
the bars that can affect execution makes result provenance more reliable.

**Behavior:** Identical price frames produce the same fingerprint. A change to
an execution-relevant timestamp or OHLCV value changes it.

**Code:** `src/forward/backtest/config.py::fingerprint_series`.

## 2026-11-07: Keep missing derivative margin unknown

**Status:** Accepted

**Decision:** Derivative margin is available only when the instrument supplies a
positive per-unit value. The portfolio reports unknown margin as `None`; it does
not estimate F&O margin from notional. The notional ratio applies to cash
equities only; other asset classes without a margin rule remain unknown.

**Reason:** Missing historical or contract margin data must not create a made-up
capital requirement, nor imply that margin is zero. P&L can still be calculated
without that capital figure.

**Behavior:** `margin_required`, `margin_for_order`, `margin_used`, and
`buying_power` return `None` when an open derivative's margin is unavailable.

**Code:** `src/forward/execution/portfolio.py`.

**Backtest behavior:** Margin-enforced runs reject orders when margin is
unknown, but still allow orders that reduce or close an existing position.
P&L-only options runs set `enforce_margin=False` explicitly, so they can
calculate trade P&L while recording that capital feasibility was not modeled.
Strategies that need buying power cannot size positions when the portfolio
reports unknown margin.

**Code:** `src/forward/backtest/broker.py`,
`src/forward/backtest/engine.py`, and `src/forward/services/options_backtest.py`.

## 2026-11-07: Keep CUDA dependencies opt-in

**Status:** Accepted

**Decision:** The GPU dependency group is not part of the default `uv sync`.
Install it explicitly with `uv sync --group gpu` on a machine configured for
CUDA.

**Reason:** The project declares GPU sweeps optional and says the application
request path does not need PyTorch. Installing the GPU group by default makes
normal setup and CI depend on the CUDA package source without using it.

**Behavior:** The default environment includes application and development
dependencies. GPU sweeps require the named GPU group.

**Code:** `pyproject.toml`.

## 2026-11-07: Configure the Vite backend target explicitly

**Status:** Accepted

**Decision:** Vite development proxies use `VITE_API_URL` for every backend
route. Development startup fails with a clear message when the value is missing.
`forward dev` supplies the URL; `forward serve` does not need a proxy.

**Reason:** Repeating a local host in every proxy rule makes the dev setup hard
to change and disagrees with the existing environment setting. A required
development value keeps the target configurable without hiding a default.

**Behavior:** The proxy target comes from one environment value, and the route
prefixes share the same proxy builder. Test and production configurations do
not enable the development proxy.

**Code:** `web/vite.config.ts`, `web/.env.example`, and `src/forward/cli.py`.

## 2026-11-07: Surface portfolio risk-status failures

**Status:** Accepted

**Decision:** The portfolio control view requires both portfolio and risk-status
responses to load successfully before it displays the combined view.

**Reason:** The view presents emergency order controls and broker risk status.
Replacing a failed risk request with `null` can leave the user looking at stale
or incomplete safety state.

**Behavior:** A risk-status request error reaches the existing view error state;
the component does not keep an old kill-switch value as if it were current.

**Code:** `web/src/PortfolioControlCenter.tsx`.

## 2026-11-07: Do not convert experiment request failures into empty results

**Status:** Accepted

**Decision:** Experiment and optimization panels keep API request failures as
errors. They do not replace failed list or parameter requests with empty
collections, zeroes, or fabricated comparison values.

**Reason:** Empty research output and failed data retrieval mean different
things. Showing them the same way can hide a broken request or imply evidence
that was never returned.

**Behavior:** The panels clear values from the previous selection while loading.
They show a dash when the backend explicitly reports an unknown comparison.

**Code:** `web/src/StrategyExperimentLab.tsx` and
`web/src/OptimizationPanel.tsx`.

## Market strategy guidance

**Decision:** The market overview does not label a strategy style as a good or
poor fit using frontend-only thresholds. Strategy suitability must come from
recorded strategy results shown by the existing Strategy Context feature.

**Reason:** UI thresholds were not versioned with the market regime model and
were not backed by measured outcomes. Presenting them as advice overstated the
evidence.

**Effect:** The overview describes where to inspect measured strategy results.
It does not invent a replacement score when no result is available.

## Direct runtime dependencies

**Decision:** Keep a package in the direct runtime dependency list only when
Forward imports it directly or needs it to support a declared runtime adapter.

**Reason:** Direct dependencies affect install and upgrade work. A transitive
dependency should not be pinned as a Forward requirement when Forward does not
use its API.

**Effect:** `python-dateutil` is provided by pandas and is no longer declared as
a separate Forward dependency. PostgreSQL's psycopg driver remains direct because
it enables SQLAlchemy's PostgreSQL database adapter.

## Audit service wiring

**Decision:** Audit service and JSONL adapter construction live in
`forward.wiring.audit`. API routes import those providers from the wiring module.

**Reason:** Audit persistence is one infrastructure boundary. Keeping its
providers beside unrelated backtest, market, and paper providers made the main
composition module harder to change safely.

**Effect:** The audit providers have one owner. The main composition module may
use the audit log provider when it builds other services, without owning it.

## Market-intelligence wiring

**Decision:** Market-intelligence service and data-adapter caches live in
`forward.wiring.market_intel`. Shared daily-bar cache construction lives in
`forward.wiring.market_data` so every adapter uses the same cache instance.

**Reason:** Market intelligence is one provider family, while daily bars are
shared by backtests, signal context, charts, and market intelligence. Each cache
needs one owner.

**Effect:** API routes, startup, and CLI code use the market-intelligence wiring
module directly. The test setup clears market and insights providers separately.

## Broker access wiring

**Decision:** The broker access service and its authenticated, live, and
read-only providers live in `forward.wiring.broker_access`.

**Reason:** Broker session and environment rules belong to one provider. Keeping
the cache in general service composition made callers depend on an unrelated
module and caused market-intelligence wiring to import the composition root.

**Effect:** Broker consumers import the provider directly. Market-intelligence
wiring now uses the same broker provider without importing the composition root.

## Authentication wiring

**Decision:** Authentication and request-rate-limiter providers live in
`forward.wiring.auth`, including their cache reset functions.

**Reason:** Both providers are configured from application settings and shared
across requests. API dependencies, routes, middleware, and the CLI should use
one provider owner.

**Effect:** Consumers import the providers directly. Their single-entry caches
and settings-based construction stay unchanged.

## Signal-context wiring

**Decision:** Signal-context service construction and cache reset live in
`forward.wiring.signal_context`. The shared learning-artifacts adapter has one
owner in `forward.wiring.learning_artifacts`.

**Reason:** Signal context, learning, portfolio, and execution all read the same
learning artifacts. The service should reuse that adapter rather than reaching
back into the general composition module or making a second copy.

**Effect:** Signal-context consumers import its provider directly. Test setup
resets signal context, backtests, and daily learning separately, with the same
overall cache cleanup as before.

## Broker account-data wiring

**Decision:** Broker session, account-read, tradebook-capture, and position
providers live in `forward.wiring.broker_data`. The shared account-read coalescer
has the same owner.

**Reason:** These providers use IIFL adapters and passed-in broker clients. They
do not need the general service composition module.

**Effect:** Consumers import the providers directly. Broker-session setup stays
eager, and account reads keep one coalescer per process.

## Shared market frames and self-learning wiring

**Decision:** The cached market-frames adapter lives in
`forward.wiring.market_data`. Self-learning engine and training providers live in
`forward.wiring.self_learning` and use that shared adapter.

**Reason:** Strategy, scanning, research, learning, and portfolio services read
the same market-frame cache. Self-learning training must use the same instance.

**Effect:** Composition and startup use the shared provider directly. Resetting
self-learning clears its engine and training service without replacing the
market-frame cache used by other services.

## Backtest and research CLI boundary

**Decision:** Backtest and walk-forward research command handlers live in
`forward.commands.backtest`. `forward.cli:main` remains the CLI entry point and
owns argument parsing and command dispatch.

**Reason:** The backtest and research runners depend on the same selected market
feed, while the main CLI also contains unrelated broker and maintenance tasks.
This gives the related runners one clear owner without changing the command
interface.

**Effect:** The backtest and research handlers share one feed builder and can be
changed independently from the other CLI commands.

## Broker CLI boundary

**Decision:** The quote, login, and instrument commands live in
`forward.commands.broker`. Shared table output lives in `forward.commands.console`.
The CLI parser and `forward.cli:main` continue to own the command interface.

**Reason:** These commands all work with IIFL access or instrument data. The
authenticated-client helper was also used by other CLI commands, so it has one
shared owner instead of being tied to the main CLI module.

**Effect:** Broker command behavior can change without growing the main CLI file.
Commands that share the same terminal table format use one formatter.

## Market intelligence strategy-context boundary

**Decision:** Keep strategy-context analytics and its axis/evidence UI in
`web/src/market-intelligence/StrategyContextPanel.tsx`. Keep the market-data
error notice shared by the intelligence panels in the same feature folder.

**Reason:** The strategy-context panel owns a separate query and view model from
the overview, sector, and stock panels. Keeping it in its own feature module
makes those responsibilities easier to change while reusing the existing API,
design-system components, and error notice.

**Effect:** The market intelligence shell selects and mounts the strategy
context panel, while its request behavior and rendered controls remain the same.

## Backup and market-data maintenance CLI boundary

**Decision:** Backup, restore, and data-check handlers live in
`forward.commands.maintenance`. The CLI entry point keeps argument parsing and
dispatch.

**Reason:** These handlers perform local data maintenance through existing
services and do not need the broker-facing command code.

**Effect:** The maintenance commands can change independently from the main CLI
without changing their command names or output behavior.

## Learning report formatting boundary

**Decision:** Performance, drift, and daily learning report formatting lives in
`forward.commands.learning_report`. The learning command keeps loading data and
selecting actions in `forward.commands.learning`.

**Reason:** Report layout is terminal presentation work. Keeping it apart from
data access makes the learning command easier to read and leaves each formatter
available as a focused module function.

**Effect:** The learning command delegates only text formatting; report values and
output rules remain unchanged.

## Learning CLI command boundary

**Decision:** The read-only learning command handler lives in
`forward.commands.learning`; `forward.cli:main` owns only parser and dispatch
work for the command.

**Reason:** Learning data access and action selection belong with the learning
feature. Keeping them out of the main CLI makes the command smaller without
changing its service calls or output.

**Effect:** The command reuses `forward.commands.learning_report` for terminal
formatting and keeps its read-only behavior.

## Strategy authoring CLI boundary

**Decision:** Strategy authoring and account selection live in
`forward.commands.strategy`. The `forward strategy seed` output uses an explicit
capital placeholder instead of showing a suggested amount.

**Reason:** Account selection and strategy lifecycle actions form one command
family. A sample capital value can be mistaken for a recommended allocation,
even though capital must be chosen by the user.

**Effect:** `forward.cli:main` keeps the same command dispatch contract, and the
seed command tells users to replace the placeholder with their chosen numeric
capital before sending the deployment request.

## Signal scanning CLI boundary

**Decision:** The signal scan, buy, sell, validation, and config-init handler
lives in `forward.commands.signals`. It reuses the shared IIFL session helper
from `forward.commands.broker`.

**Reason:** Signal scanning owns several related user actions and already has a
service boundary. Keeping its command flow together avoids coupling it to the
main CLI file and avoids a second broker-session implementation.

**Effect:** The parser options, order/session choice, read and write behavior,
and terminal output stay the same; only the handler's module changes.

## Alert and trade-signal wiring boundary

**Decision:** Alert, intelligent-alert, setup-statistics, and trade-signal
providers live in `forward.wiring.alerts`. API and CLI consumers import them
from that module directly.

**Reason:** These providers share alert-specific adapters and cached use cases.
Keeping their singleton state in one wiring module preserves object identity
while separating this feature from general application composition.

**Effect:** The provider factories no longer live in `forward.composition`, and
there is no compatibility façade. The alert and trade-signal behavior remains
unchanged.

## F&O wiring boundary

**Decision:** Options market, dataset, run, backtest, and forward paper
providers live in `forward.wiring.options`. The shared instrument catalog and
strategy authoring service live in their own wiring modules, and feature
consumers import providers directly.

**Reason:** These providers form one F&O feature boundary. Options backtests
reuse the strategy authoring service, and charting, order checks, catalog
routes, and strategies reuse the same broker instrument catalog.

**Effect:** The options forward service remains one process-wide instance, so
active paper runs keep their state for the lifetime of the app process. Moving
these providers removes their ownership from `forward.composition` without a
compatibility façade.

## Paper runner construction boundary

**Decision:** The paper runner builder lives in `forward.wiring.paper_runner`
and receives all adapters and services as explicit arguments.

**Reason:** Venue setup, evaluator closures, and feed subscriptions belong
together, while process state belongs to the composition layer.

**Effect:** `forward.composition` still guards the one-runner lifecycle and
clears health state after creation. The builder has no singleton state and
keeps the same runner profiles and execution behavior.

## 2026-11-07: Keep the current application stack during the refactor

**Status:** Accepted

**Decision:** Keep Python, FastAPI, SQLAlchemy, React, TypeScript, Vite,
Tailwind, and the current package lockfiles for this refactor. The application
database defaults to SQLite and also supports PostgreSQL through
`APP_DB_URL`. Improve module boundaries and dependency ownership before
considering a stack migration.

**Reason:** The current stack already supports the product's API, database,
browser UI, market-data work, and paper execution. The main costs found in this
work are oversized composition and CLI modules, not a missing framework
capability. A stack change would add migration work before proving that it fixes
those costs.

**Effect:** Refactoring can continue without changing runtime or deployment
requirements. Reconsider a stack change only when a measured product need cannot
be met cleanly with the current stack.

## 2026-11-07: Keep research and scanner wiring in its feature module

**Status:** Accepted

**Decision:** Research-history, scanner, strategy-catalog, validation, and
validation-report providers live in `forward.wiring.research`. API callers
import these providers from that module directly.

**Reason:** These providers assemble one research workflow and share its
catalog, history adapter, and report service. Keeping them together preserves
shared service identity without making feature routes depend on the general
composition module.

**Effect:** The research providers no longer live in
`forward.composition`, and there is no compatibility façade. Provider behavior
and API response handling stay the same.

**Related provider:** The same module owns the lazy shared local-scan service.
API routes and the history CLI use it directly, so cached full-market scans do
not need to route through the general composition root.

**Research input:** Synthetic walk-forward runs require an explicit symbol
list. The API does not invent a US-symbol universe when the request omits one.

## 2026-11-07: Keep paper storage adapters in paper wiring

**Status:** Accepted

**Decision:** Paper ledger, order, runner, system-state, P&L storage, and live
paper price-source providers live in `forward.wiring.paper`. API and script
consumers import those providers directly.

**Reason:** These adapters belong to the paper feature and are shared across
paper routes, paper workflows, and application composition. Putting them in a
feature wiring module removes general composition as an unnecessary hop while
keeping one shared storage adapter per process.

**Effect:** `forward.composition` keeps runner lifecycle and providers that
depend on cross-feature state. Deployment service construction reuses the
shared paper ledger rather than creating a second ledger instance.

## 2026-11-07: Keep learning feature explanations in research logic

**Status:** Accepted

**Decision:** Build dataset-level missing-feature explanations in
`forward.research.learning_missing_features`; keep dataset loading and row
assembly in `forward.services.learning_dataset`.

**Reason:** The explanation is a deterministic calculation over dataset rows.
It does not need storage, broker, or application state, so it belongs beside
the other learning research calculations.

**Effect:** The learning service delegates the explanation calculation to a
focused function. Trend history requirements use the shared indicator window,
and sector explanations no longer contain a stale file path or cached-symbol
count.

## 2026-11-07: Separate the learning dataset from its builder

**Status:** Accepted

**Decision:** `LearningDataset`, its stable column and metric definitions, and
its pure summary helpers live in `forward.services.learning_dataset_model`.
`LearningDatasetBuilder` remains in `forward.services.learning_dataset`.

**Reason:** The dataset is the shared read model used by learning analysis,
reports, daily learning, and champion comparisons. Its shape should not depend
on the adapters that assemble it.

**Effect:** Consumers import the model from its canonical module. The builder
continues to assemble the same rows and returns the same dataset shape; there is
no re-export from the builder module.

## 2026-11-07: Keep market-context statistics outside the learning service

**Status:** Accepted

**Decision:** Market-context bucket calculations live in
`forward.services.learning_context`. `LearningService` loads the dataset and
resolves the requested metric, then calls that calculation.

**Reason:** Bucket selection and statistics are report logic. They do not need
the learning service's storage or dataset cache, so a focused function can
handle them with explicit inputs.

**Effect:** The API method and response shape stay the same. The calculation
reuses the shared strategy matcher and evidence/statistics helpers. Confidence
intervals come from the `mean_ci` value provided by the statistics result.

## 2026-11-07: Keep one deployment loop per service module

**Status:** Accepted

**Decision:** `PaperRunner` coordinates deployments; each `DeploymentLoop` and
its per-deployment state and clock helpers live in `forward.services.deployment_loop`.

**Reason:** The runner's process-level coordination and one deployment's
evaluation lifecycle are separate responsibilities. Keeping them in separate
modules makes the paper execution flow easier to understand and change.

**Effect:** `PaperRunner` keeps its orchestration and shared adapters. The
extracted loop keeps the same per-deployment evaluation behavior, and internal
imports use its canonical module directly.

## 2026-11-07: Keep history command behavior in a feature module

**Status:** Accepted

**Decision:** The history command handler lives in `forward.commands.history`;
`forward.cli` owns its parser and command dispatch.

**Reason:** History loading and its terminal output form one command workflow.
Keeping the handler out of the parser module makes both responsibilities easier
to change without duplicating the history service.

**Effect:** The command name, parser arguments, and service behavior stay the
same while the CLI module delegates the handler.

## 2026-11-07: Keep alert, briefing, and GPU command handlers out of the parser

**Status:** Accepted

**Decision:** Alert, briefing, and GPU command handlers live in
`forward.commands.alerts`, `forward.commands.briefing`, and
`forward.commands.gpu`. The main CLI keeps parser setup and dispatch.

**Reason:** These command flows have separate service and presentation work.
Keeping them in feature modules lets each change without growing the parser
module or duplicating its argument definitions.

**Effect:** Existing command names, arguments, and terminal output stay the
same. The CLI delegates each selected command to its feature handler.

## Portfolio CLI boundary

**Decision:** Broker portfolio display and export handling lives in
`forward.commands.portfolio`. It uses the shared broker-session helper, table
formatter, and broker-data wiring provider.

**Reason:** Portfolio output is a separate read-only command flow. Its JSON, CSV,
status, and section rendering do not belong in the main CLI dispatcher.

**Effect:** `forward.cli:main` only dispatches portfolio requests; the command
keeps its existing output and return codes.

## Dashboard metric calculation boundary

**Decision:** Broker-trade performance and market-breadth calculations live in
`forward.analytics.dashboard_metrics`. `DashboardSummaryService` loads broker
and cached-history data, applies its request cache, and passes snapshots to
these pure calculations. Breadth sample and history thresholds are owned by the
analytics module and shared with the history request.

**Reason:** These calculations do not need broker or storage access. Keeping
them in the analytics layer makes the service an adapter coordinator and lets
the same calculations be reused without creating another broker-facing path.

**Effect:** The dashboard response fields and formulas stay the same. The
exchange-local current date is passed into the performance calculation so the
calculation does not read the system clock on its own. One set of breadth
thresholds controls both the data request and the calculation.

## Options database schema ownership

**Decision:** Options dataset, backtest, and forward-run tables live in
`forward.appdb.options_schema`. The database engine imports that schema module
when it assembles the shared metadata.

**Reason:** F&O persistence is one feature area and should not be mixed into the
general control-plane schema file. The repository imports those tables from
their owning module.

**Effect:** Table names, columns, and constraints stay the same. All 31 tables
remain registered in the shared metadata before `create_all` runs.

## Options execution capability in definition validation

**Decision:** A structurally valid `kind: options` definition reports both
paper execution and backtesting as resolvable. The validation report names the
shared `configured_options` engine.

**Reason:** The options backtest and forward-paper services are already wired
to the same configuration-driven strategy. Dataset, broker session, and live
quote availability are checked when a run starts, not by schema validation.

**Effect:** Strategy validation no longer says that connected F&O workflows are
missing. Run-time readiness checks and execution behavior stay in their
existing services.

## Scope learning caches by account

**Decision:** Cache each learning dataset by its optional `user_id` instead of
sharing one process-wide dataset across all account requests.

**Reason:** Dataset builds pass the account ID to storage ports, which return
account-scoped journal and backtest evidence. A cache key must use the same
scope as the data it stores.

**Effect:** Existing cache intervals and refresh behavior stay the same, while
one account's cached evidence cannot be returned to another account.

## Explicit flat targets always close a position

**Decision:** The backtest sizing wrapper preserves `target(symbol, 0)` and
`close(symbol)` as flat-position requests, even when short positions are enabled.
An intentional short entry must use a negative non-zero target.

**Reason:** A zero target means no position. Inferring direction from the signed
delta turns a close from a long position into a new short position when
shorting is enabled.

**Effect:** Flat targets submit only the quantity needed to close the current
position. Non-zero negative targets keep their short-entry behavior.

## Protect broker data and escape login callback text

**Decision:** The live broker positions route requires the existing
`order:read` permission and a signed-in account. Values inserted into the broker
login callback HTML are escaped before rendering.

**Reason:** Live account positions are private, and broker errors or client IDs
can contain markup that must not become executable page content.

**Effect:** Authenticated users with order-read access keep the same positions
response. Login errors remain visible as text, without being interpreted as
HTML.

## Authenticate the live tick stream with the existing session

**Decision:** The WebSocket tick route resolves the same bearer token, API key,
or session cookie as HTTP routes and requires `market:read` before subscribing.
Origin validation remains an additional browser boundary.

**Reason:** A WebSocket can be opened by a non-browser client without an Origin
header. Origin checks alone do not identify the caller.

**Effect:** The existing same-origin browser uses its session cookie. No token
query parameter or alternate credential path is added.

## Restrict audit history to audit readers

**Decision:** The legacy audit endpoint requires the existing `audit:read`
permission.

**Reason:** Audit history includes account actions and its local file path, so
it is not public market data.

**Effect:** Users with the audit permission retain the same response. Other
callers are refused by the shared permission dependency.

## Keep paper limit fills inside their price boundary

**Decision:** Apply slippage and liquidity impact, then cap a simulated limit
or stop-limit fill at the submitted limit. Report `PARTIAL` until the cumulative
fill reaches the order quantity.

**Reason:** A price-matched order cannot execute worse than its limit. The
execution engine stores cumulative fill quantity and derives the OMS state from
that quantity, so the venue status should describe the same progress.

**Effect:** Paper fills still include modeled market friction, but never cross a
buy limit upward or a sell limit downward. Resting orders retain their existing
partial-fill and cancellation flow.

## Give each database schema to its feature

**Decision:** Store account, watchlist, audit, execution, trade, strategy and
backtest, screener, portfolio, and research tables in feature-owned schema
modules. A schema registry imports each owner before the engine calls
`create_all`.

**Reason:** The former schema module combined unrelated database features and
made every repository depend on one shared file. SQLAlchemy can resolve string
foreign keys after each feature registers its tables in shared metadata.

**Effect:** Table definitions and their registration order stay the same.
Repositories and storage adapters import each table from its owning feature;
the former shared schema module has been removed.

## Keep net return separate from gross price return

**Decision:** Return no net-return value when recorded net P&L or a positive
entry position value is missing. Do not substitute gross price movement.

**Reason:** Gross price movement does not include costs and cannot be labeled as
net performance. Missing source data must stay visible as missing.

**Effect:** Attribution summaries omit trades without usable net inputs from net
return statistics. Gross return remains available through its separate field.

## Reject non-finite prices in excursion metrics

**Decision:** Excursion and execution quality inputs must be finite positive
prices.

**Reason:** NaN and infinity are not valid market prices and can corrupt reports
without raising an error.

**Effect:** Invalid prices are treated as unavailable by the existing metric
input validation path.

## Keep learning quality reports running on malformed values

**Decision:** Normalize trade references before deduplication, and report
malformed or non-finite numeric quality inputs as issues.

**Reason:** A data-quality report must not fail while inspecting the bad data it
is meant to find.

**Effect:** Mixed text and numeric references deduplicate consistently. Invalid
price, quantity, and commission values appear in the existing abnormal-data
issues.

## Preserve strategy settings during walk-forward validation

**Decision:** Walk-forward validation starts from the strategy definition's
saved constructor parameters, then applies each tested parameter value on top.

**Reason:** Testing one adaptive value must not silently reset the strategy's
other configured values to constructor defaults.

**Effect:** Every fold uses the configured baseline, with the candidate value
overriding only its named parameter.

## Order tick aggregation by market time

**Decision:** Build each candle's open and close from ticks ordered by epoch and
timestamp.

**Reason:** Aggregate input order is not a market-time guarantee; an unordered
first or last tick can reverse the candle body.

**Effect:** Both in-memory and stored-tick DuckDB queries use the same time order
for open, close, and candle timestamp.

## Do not invent chart values before a series begins

**Decision:** Align equity curves only from each series' first observed point,
and apply toast limits to both initial and newly added items.

**Reason:** Carrying a value backward creates data that was never observed, and
zero is a valid limit that should show no toasts.

**Effect:** The equity chart begins each curve at its first timestamp. A zero
toast limit keeps the stack empty.

## Use keyboard controls for market-depth price selection

**Decision:** Render selectable depth rows as buttons, and disable the control
when no price-selection action is provided.

**Reason:** A clickable non-button row cannot be used reliably from a keyboard
or assistive technology.

**Effect:** Enabled rows keep their current price-selection action and work with
standard button keyboard behavior.

## Release shared live-tick subscriptions by reference count

**Decision:** Track how many mounted hook consumers need each live subscription.
Send unsubscribe only when the last consumer releases it.

**Reason:** The WebSocket and subscription map are shared by views, so one view
must not stop another view from receiving the same symbol.

**Effect:** Unmounted views release their symbols, active views keep theirs, and
reconnect restores only subscriptions that still have consumers.

## Treat chart scripts as code written by the signed-in user

**Decision:** The current Pine-style chart editor accepts user-authored or pasted
code stored in that browser. It does not load executable scripts from shared
strategy records.

**Reason:** The current evaluator uses JavaScript execution in the app origin,
which can access the signed-in user's same-origin browser session.

**Effect:** Users should apply only code they trust. If chart code later comes
from another user or an external source, replace the evaluator with a restricted
parser and interpreter before enabling that source.

## Keep unavailable position P&L visibly unavailable

**Decision:** Show an unavailable mark when no live price or recorded
unrealized P&L exists. Do not calculate P&L from a zero price. Keep null values
at the end when sorting a table.

**Reason:** Zero P&L is a real result; it must stay distinct from a missing
price or missing calculation.

**Effect:** Position rows show a dash until a valid mark or recorded P&L exists.
Table sorting supports missing numeric values without changing their display.

## Keep screener row expansion keyboard-operable

**Decision:** Put the screener disclosure action in a labeled button and key each
row pair by its symbol.

**Reason:** A clickable table row has no native keyboard behavior, and the
fragment wrapping its result/detail rows needs the stable key.

**Effect:** Users can expand or collapse evidence with keyboard controls, and
React can track each row pair by symbol.

## Require an explicit candle date range

**Decision:** Candle-history requests must provide both start and end dates.

**Reason:** A fixed default date range becomes stale and can silently return an
old chart.

**Effect:** Callers choose the history window they need, and requests cannot
fall back to a baked-in period.

## Surface incomplete backtest result requests

**Decision:** Treat a failed monthly-results request like any other failed
backtest result request.

**Reason:** An empty monthly matrix can look like a real result when the request
actually failed.

**Effect:** The result view shows the existing error state instead of drawing a
partial result as if it were complete.

## Keep kill-switch orders blocked

**Decision:** A kill switch blocks every order. The live runner stops and leaves
open positions for manual closure. Daily-loss halts still allow risk-reducing
orders. Reset daily-loss state and order counts at the next session rollover.

**Reason:** The user chose a strict kill switch that does not send any orders.
Automatic exits during a safety stop would violate that policy.

**Effect:** The runner does not flatten or cancel orders as part of kill-switch
shutdown. Daily-loss halts remain separate and can still reduce existing risk.

## Keep paper P&L data within the signed-in account

**Decision:** The paper P&L calendar reads only closed trades owned by the
signed-in user. It does not mix in shared strategy research settlements.

**Reason:** The calendar is authenticated and users should only see their own
trade history.

**Effect:** The API passes the principal's user ID through the use case and SQL
adapter, which checks the trade, deployment, and strategy owners. Shared
research settlements and their virtual ticket value are not reported as
account P&L.

## Reject unknown screener sort fields

**Decision:** Return a validation error when a caller supplies a sort field
outside the screener catalog.

**Reason:** Replacing a typo with the default sort hides invalid input and can
make a result look correct while using a different order.

**Effect:** The API reports the invalid field before loading the market data.

## Do not carry a previous close through a missing session bar

**Decision:** Value a position from the close for that exact market date. If the
bar is missing, mark the point unpriced and show the cost basis only as an
incomplete estimate.

**Reason:** A stale close can make a missing market-data session look like a
measured result.

**Effect:** The track record does not carry yesterday's close forward and does
not publish that point as complete equity.

## Reject non-finite market and order values at the domain boundary

**Decision:** Instrument sizes, order quantities and prices, and fill values
must be finite and valid before they enter portfolio calculations.

**Reason:** NaN and infinity can bypass ordinary comparisons and contaminate
positions, risk checks, and reported P&L.

**Effect:** Invalid contract metadata and execution values raise validation
errors instead of becoming portfolio state.

## Require explicit signal configuration setup

**Decision:** Signal commands require an existing config file. The
`init-config` action writes the starter config explicitly.

**Reason:** A missing file must not silently turn into a run with default
thresholds that the user did not select.

**Effect:** Scan and validation commands report the missing file; users can
create it with the explicit setup command.

## Reject malformed exchange overrides

**Decision:** A deployment's per-symbol exchange overrides must be a mapping.
Malformed values raise a configuration error.

**Reason:** Ignoring a bad override can route a contract through the wrong
exchange and hide a broken deployment setup.

**Effect:** The runner does not replace an invalid override map with an empty
map.

## Declare FastAPI principal aliases as annotations

**Decision:** Use `principal: CurrentPrincipal` when a route needs the signed-in
principal. Do not assign `CurrentPrincipal` as the default value of a parameter
annotated as `Principal`.

**Reason:** `CurrentPrincipal` is an `Annotated` alias that carries FastAPI's
`Depends(get_principal)` metadata. As a default value, it is not interpreted as
that dependency, so the route can receive the alias object instead of an
authenticated principal.

**Effect:** Optimization routes that act on behalf of a user resolve identity
through the same declared dependency as the other versioned routes.

## Protect private legacy reads with the existing permission checks

**Decision:** Require the matching read permission on legacy risk, strategy,
alert, scanner and broker-account routes that return private application state.

**Reason:** These older routes bypass the authentication dependencies used by the
versioned API. With authentication enabled, a route without a dependency remains
public even though the rest of the app requires a signed-in account.

**Effect:** Private reads use the current permission matrix. The legacy custom scanner, saved-scan list, and saved-scan mutations require `screener:run`, matching the versioned screener surface. Public health and market-data-only routes remain outside this decision.

## 2026-11-07: Keep symbol-level screener errors visible in the use case

**Status:** Accepted

**Decision:** `ScreenerService.run` owns condition evaluation and adds each symbol's evaluation error to the scan response. It does not hide that failure inside the market-data adapter or convert it into a non-match.

**Reason:** A screen may still show valid matches when one symbol has bad data, while the caller can see that symbol was not evaluated successfully.

**Effect:** The service reports symbol-level evaluation errors separately from unmatched symbols. Unmeasurable sort values remain explicitly ordered last. Malformed saved JSON raises a decoding error, and a decoded value that is not an object raises a validation error instead of being replaced with an empty definition.

## Remove the unused SMA crossover stop setting

**Decision:** Keep `SmaCrossover` limited to settings it uses. Remove the
unread `atr_stop_multiple` field from that strategy.

**Reason:** The field never affected entry, exit, sizing, or stop behavior. A
setting that appears active but has no effect makes strategy configuration
misleading.

**Effect:** `SmaCrossover` keeps its existing signals and position sizing. The
separate ORB strategy retains its own working ATR stop setting.

## Surface custom scan evaluation errors

**Decision:** Custom scans stop with a clear error when a condition or symbol
cannot be evaluated. They do not skip that symbol or replace a failed indicator
with `NaN`.

**Reason:** A partial result without an error can look like every omitted symbol
was checked and did not match.

**Effect:** The custom scan route returns invalid condition errors as HTTP 422.
Valid scans keep the same match and ranking rules.

## Require explicit adaptive parameter rules

**Decision:** Adaptive parameters use one list format. Every enabled parameter
declares its current value, minimum, maximum, step, and block. Values must match
the strategy definition and remain inside the declared bounds.

**Reason:** Guessing bounds or writing a parameter into a default block can
change the tested strategy without the strategy definition saying so.

**Effect:** Missing or invalid specifications fail with a clear error. Applying
a candidate never creates a missing block, clamps a proposed value, or updates
a different block by guesswork.

## Hide attribution statistics below the sample floor

**Decision:** Attribution buckets below ``min_sample`` keep their trade counts
and evidence counts, but return ``null`` for outcome and cost statistics.

**Reason:** A visible ``suppressed`` flag is not enough if the same row still
publishes a number that looks reliable.

**Effect:** Small-bucket counts remain available for context. Performance values
become visible only when the bucket reaches the configured sample floor.

## Use the market-intelligence sector ranker for all sorted sector reads

**Decision:** `MarketIntelService.get_sectors` delegates ordering to
`ranked_sectors`, which validates the requested field. Missing and non-finite
measurements sort last in either direction.

**Reason:** A second unchecked sort path silently returned unsorted rows for
valid fields and ignored invalid fields.

**Effect:** Existing supported sector order remains consistent across service
callers; invalid sort fields raise the same validation error.

## Keep attribution context thresholds neutral at equality

**Decision:** A context score equal to the supportive threshold is neutral.
Supportive and unsupportive classifications use strict comparisons.

**Reason:** The classifier already documents equality as neither supportive nor
unsupportive. Its inclusive comparison contradicted that rule.

**Effect:** A score of 40 under the current default thresholds has no direction
code. Higher scores are supportive; lower scores are unsupportive.

## Reject unknown learning axes and omit missing strategy keys

**Decision:** An explicitly requested unknown learning axis raises a validation
error. A strategy row with no usable key or ID has no strategy bucket.

**Reason:** Silently replacing a typo with the default axis set changes the
analysis. An empty-string bucket claims that rows share a strategy identity when
the identity was not recorded.

**Effect:** Direct service callers get a clear error for unknown axes, matching
the API's existing 400 response. Rows without a strategy identity are counted
as excluded from that axis.

## Gate drift comparisons on measured metric samples

**Decision:** Each drift metric uses the number of rows that actually contain
that measurement for its minimum-sample gate and reported sample size.

**Reason:** A count of closed trades does not prove that every return,
slippage, or duration value was recorded.

**Effect:** A metric with fewer than the required measured observations is
reported as insufficient. The report keeps total closed-trade counts separately
for trade-frequency analysis.

## Use a seven-day window for the seven-day evidence count

**Decision:** `last_7d` counts evidence from the preceding seven days.

**Reason:** The previous ten-day cutoff mislabeled a longer window and
overstated recent evidence.

**Effect:** Recent-evidence counts now match the field name and date-window
description.

## Fail a research portfolio run on a missing held-asset return

**Decision:** Portfolio research does not replace missing returns with zero.
When a non-zero holding has no finite session return, the run fails with the
symbol and date.

**Reason:** A missing price does not mean the asset was flat. Zeroing the gap
changes portfolio P&L and the sample used for risk statistics.

**Effect:** Missing prices in unheld assets do not affect the run. Missing
prices in held assets stop the result before a misleading equity curve is made.

## Leave signal outcomes unresolved when P&L is missing

**Decision:** A backtest or journal outcome with no recorded net P&L remains
unresolved. A present but malformed or non-finite value raises an error.

**Reason:** Missing P&L is not a zero-result trade. Turning it into zero changes
learning statistics and can make missing records look like flat outcomes.

**Effect:** Signal-context analysis omits unresolved outcomes from outcome
statistics and surfaces invalid recorded values to its existing error boundary.

## Keep advance-decline ratio unmeasured when no stocks declined

**Decision:** A market date with zero declining stocks has no advance-decline
ratio.

**Reason:** Dividing by a made-up denominator of one invents a finite breadth
measurement for a market where that ratio is undefined.

**Effect:** Historical signal-context snapshots keep the ratio absent rather
than report a fabricated value.

## Treat corrupt scheduled-job state as an error

**Decision:** A missing job-state file means the job has not run. Any existing
state file must contain a valid state object; malformed JSON, missing required
fields, invalid values, and read errors are surfaced to the caller.

**Reason:** Treating a broken file as first-run state can repeat a completed job
or hide an inability to read its run history.

**Effect:** The scheduler starts cleanly when state is absent and fails visibly
when saved state is unreadable or invalid. Each due check reads its state once.

## Require complete saved paper deployment settings

**Decision:** A deployment must persist a non-empty symbol list, exchange,
timeframe, positive position limit, and explicit signal basis. Invalid JSON,
missing fields, unknown fields, and malformed values fail deployment creation
or runner loading with an error.

**Reason:** A runner must not turn incomplete saved settings into a different
deployment through guessed defaults or ignored fields.

**Effect:** Both deployment forms save `closed_bar` explicitly. Stops and sizing
remain optional when omitted. Runner pass cadence and history warmup remain
documented platform defaults (1 second and 400 calendar days) because they are
operational settings, not strategy rules. The unused `order_value` field is
removed.

## Cancel accepted option entries after a later-leg rejection

**Decision:** When an option stage rejects a leg, cancel every earlier order in
that stage that is still active before closing the positions that have filled.

**Reason:** An accepted entry order can still fill after another leg is rejected.
Leaving it active could reopen exposure after the strategy has started to stop.

**Effect:** Backtests cancel queued or resting accepted orders before flattening.
The quote-driven paper broker fills or rejects orders immediately, so it has no
active entry orders to cancel. Any partial fill remains accounted for and is
closed using the existing position-flattening behavior.

## Do not confirm chart breakouts without measured volume

**Decision:** Volume-confirmed chart-pattern flags are unavailable when the
volume column or its 20-bar baseline is missing or invalid. Short frames return
the full pattern-key set with unavailable values.

**Reason:** A breakout cannot be described as volume-confirmed when volume was
not measured. Returning zero would report a measured absence; returning one
would invent confirmation.

**Effect:** The formula context receives `None` for unavailable pattern readings.
Comparisons against those readings fail explicitly instead of treating missing
volume or short history as a non-match.

## Propagate custom formula evaluation failures

**Decision:** A custom entry or exit formula that cannot be prepared or
evaluated raises an explicit formula error. The scanner, deployment runner, and
backtest report that failure through their existing error paths; they do not
convert it to a false condition.

**Reason:** A missing indicator reading is not evidence that a condition is
false. This matters most for exits: treating an unavailable exit condition as
false can leave a position open while showing no problem.

**Effect:** Live deployments record an error state and reason for the failed
evaluation. Backtest workers record a failed run. The existing runner cycle
continues to isolate other deployments; this decision adds no recovery policy.

## Require canonical trade sides in attribution

**Decision:** Attribution calculations accept only the domain's canonical
``BUY`` and ``SELL`` sides. Missing or unknown sides raise an error instead of
being interpreted as a long position.

**Reason:** A fabricated direction changes returns, maximum adverse excursion,
and maximum favourable excursion while still producing ordinary-looking
numbers.

**Effect:** Journal episodes continue to be normalized and validated by
``episode_identity``. Direct callers of the pure attribution functions receive
the same strict validation through ``Side.parse``.

## Scope strategy research to its owner

**Decision:** Optimization recommendations and experiment records are visible
and changeable only through the account that owns their strategy. Every
strategy version, recommendation, and experiment read or write carries that
account ID through the service and storage ports. SQL reads join the strategy
owner, and updates apply the same owner condition.

**Reason:** A role permission says what kind of action an account may perform;
it does not grant access to another account's strategy. Research records inherit
their ownership from the strategy they evaluate.

**Effect:** Foreign strategy and research IDs return the same not-found result
as missing IDs. The existing strategy ownership column is the source of truth,
so this rule needs no duplicate owner field or data migration.

## Require canonical portfolio order sides

**Decision:** Portfolio order checks and fill folding accept only ``BUY`` and
``SELL``. Missing and unknown sides raise an error instead of being treated as
buys.

**Reason:** Guessing a side changes exposure and can make an invalid order
appear to pass portfolio limits.

**Effect:** The portfolio layer uses the shared ``Side.parse`` rule. Invalid
order data cannot reach exposure or cash calculations.

## Reject non-finite portfolio policy and order values

**Decision:** Portfolio limits, priorities, warning thresholds, order quantity,
and order price must be finite numbers.

**Reason:** ``NaN`` and infinity can bypass ordinary limit comparisons.

**Effect:** Invalid numbers are rejected before portfolio exposure decisions.

## Report holdings P&L only with complete daily prices

**Decision:** A daily holdings P&L value is calculated only when every current
holding has a price change for that session. Price changes use a shared session
index, so a symbol missing a bar cannot contribute a multi-session move to one
day.

**Reason:** A partial-book total or a delayed price move looks like exact daily
P&L even though it does not describe the full holdings book for that day.

**Effect:** Incomplete dates are omitted from the calculated holdings series.
Daily detail can still list the prices that arrived and name missing holdings,
but its total is unavailable until all holdings have a measured change.

## Reject invalid execution and journal measurements

**Decision:** The order service accepts a cumulative fill only when its quantity
and price are finite and positive, and the stored order and previous fill
quantities are valid. The trade journal also requires canonical ``BUY`` or
``SELL`` sides and finite positive fill prices and quantities.

**Reason:** Non-finite values can pass ordinary comparisons and then corrupt
the fill ledger and profit calculations.

**Effect:** Invalid execution reports fail before the order event is recorded.
Invalid persisted journal rows fail before profit, loss, or excursion values
are calculated.

## Preserve unavailable chart volume

**Decision:** Candle volume is nullable from the cached chart service through
the chart API model. Volume charts omit bars with unavailable volume, and Pine
VWMA and MFI results remain unavailable when any required volume is missing.

**Reason:** Replacing unknown volume with zero invents market data and can
change volume-based signals.

**Effect:** Users can distinguish unavailable volume from a measured zero.

## Partition IIFL history by interval and requested date range

**Decision:** Store every IIFL history result under a deterministic cache path
that includes its canonical interval and exact requested start and end dates.
The flat exchange-level daily files remain derived views assembled from the
daily range partitions. Exact duplicate candles collapse. Different candles
with the same timestamp make daily materialization fail with a cache conflict.

**Reason:** A symbol-only cache filename let one interval or date range replace
another, and a file modified today could incorrectly skip a different request.

**Effect:** A request can skip only its exact interval and range partition.
Daily readers keep using one flat file per symbol, while intraday and other
interval partitions stay out of the daily universe scans. Both range partitions
and daily views are written atomically, so incomplete writes are not exposed as
cache files.

## Stream complete journal history for portfolio P&L

**Decision:** Portfolio realized P&L reads every matching journal episode from
one ordered database cursor. The cursor is scoped to the signed-in account and
orders rows by entry time and trade ID.

**Reason:** A fixed 1,000-row query silently omitted older trades while the
portfolio reported a complete total. Separate offset pages can shift when rows
are added during a read.

**Effect:** Realized P&L has no row cutoff, and database iteration stays
streamed instead of loading every episode into memory at once.

## Preserve last-known execution and risk state

**Decision:** Failed health, execution-mode, and risk refreshes keep their last
confirmed values and mark them as unavailable. Initial reads show a pending
state. Order confirmation blocks when the current risk state is unavailable or
the kill switch is engaged.

**Reason:** Clearing known status during a transient error can falsely show
that the broker is disconnected, risk is clear, or execution mode is unknown.
A stale "open" state must not authorize an order.

**Effect:** The interface labels last-known values, disables risk-changing
controls until a fresh risk read succeeds, and blocks order confirmation when
risk status is not current. Engaging the kill switch blocks every order,
including exits, and leaves open positions for manual closure.

## Keep learning observation counts complete and bucket-linked

**Decision:** Readiness uses a grouped database count of every stored PAPER or
LIVE forward observation, with the existing global strategy-level ownership
semantics. Each new observation stores only source trade references whose rows
contributed to that exact bucket and outcome metric.

**Reason:** A 500-row list cap undercounts older observations, while attaching
the first strategy trades to every bucket mislabels evidence.

**Effect:** Readiness counts no longer depend on a page limit. Observation
source references map to the bucket that produced them. The observation table
has no owner column; whether observations should become account-owned remains
an open data-model decision, and this change does not infer ownership.

## Report dashboard holdings P&L only when prior prices are complete

**Decision:** Calculate holdings ``day_pnl`` only from a valid prior close
included in the broker position row. If any held symbol lacks one, holdings
``day_pnl``, ``day_pnl_pct``, and combined ``total_day_pnl`` are unavailable;
the response reports ``day_pnl_complete: false`` and the incomplete count.

**Reason:** A partial holdings sum can look like the complete account result.

**Effect:** Missing broker baselines stay unknown. The dashboard does not
substitute a cached close or reject a price move using a fixed threshold. The
frontend contract in ``web/src/api.ts`` allows nullable P&L and reports the
incomplete count; no current UI component renders these fields.

## Resolve the launcher runtime from the project or PATH

**Decision:** ``start-forward.bat`` uses the repository virtual environment
when it exists. Otherwise, it requires ``uv`` to be available on ``PATH``.
The launcher does not search machine-specific install locations.

**Reason:** A launcher should use the runtime the user explicitly put on
``PATH`` or the runtime installed for this project. Guessing other locations
adds machine-specific lookup behavior.

**Effect:** Installing ``uv`` on ``PATH`` or creating the project virtual
environment is a visible setup requirement. The launcher still prepares the
configured database before starting the API.

## Keep synthetic demo inputs in explicit configuration

**Decision:** The demo seeder reads its sample account, synthetic price model,
trade generation inputs, and paper fill assumptions from
``config/demo_seed.json``. The demo owner password must come from the command
line or ``FORWARD_DEMO_PASSWORD`` and has no source-code default.

**Reason:** Demo data must be clearly identified as synthetic, and users must
be able to review its assumptions without searching through Python code. A
fixed demo password can also be mistaken for a safe account credential.

**Effect:** The seeder refuses to run without a supplied password and continues
to refuse a non-empty data directory. Its generated records stay marked as
demo data.

## Keep production-rule research inputs in a reviewable config file

**Decision:** The standalone production-rule walk-forward script reads its
universe, exchange, splits, cost model, backtest settings, and validation gates
from ``config/backtest_production_rules.json``.

**Reason:** These are research assumptions that change reported performance.
Keeping them outside Python makes each run's inputs visible and editable
without changing strategy code.

**Effect:** The checked-in config preserves the prior experiment values. The
script passes each field explicitly to the existing backtest and validation
objects.

## Make historical-data download assumptions explicit

**Decision:** The ETF history downloader reads its source URL, symbol list,
date range, timezone, interval, bar time, request timeout, worker count, and
output directory from ``config/fetch_etf_history.json``. The long-history IIFL
downloader requires its exchange, history length, and request pause as explicit
CLI values.

**Reason:** These inputs affect which market data is requested, how it is
timestamped, and how quickly requests reach a broker or data provider.

**Effect:** Changing the history window or request behavior no longer requires
editing the downloader source.

## Share IIFL minute-history inputs and candle parsing

**Decision:** The sequential and parallel IIFL minute downloaders read the
same explicit settings from ``config/fetch_iifl_minute_history.json``. They
share universe and contract loading, and both use the broker candle adapter to
parse responses. A corrupt cached parquet file is reported as an error instead
of being silently ignored and downloaded again.

**Reason:** Separate fetch scripts should not use different symbols, dates,
cache rules, or response parsing for the same dataset.

**Effect:** The request window and broker pacing are changed in one config, and
unexpected cache or response shapes fail visibly.

## Configure single-symbol IIFL history probes

**Decision:** Additional-symbol and NIFTYBEES history probes read their
symbols, exchanges, intervals, date windows, cache rules, and output paths from
named JSON files under ``config/``. Both parse candles through the shared IIFL
feed adapter and use the application broker settings.

**Reason:** These probes use the same broker API and data format as the larger
history jobs. Machine paths and alternate response-shape parsing made them
harder to repeat and review.

**Effect:** A probe can run from any project checkout with its selected config;
it does not change the process working directory or embed a Windows path.

## Use the broker session adapter for the manual OAuth fetch tool

**Decision:** The manual IIFL callback-and-fetch script uses the existing
``IiflSessionAdapter`` to create sessions, the documented ``authcode`` and
``clientid`` callback fields, and the shared candle parser. Callback and
history settings live in ``config/iifl_auth_fetch.json``.

**Reason:** The script remains a documented-in-code manual workflow, but it
should share the application's session rules and exact broker contract instead
of maintaining a second session exchange implementation.

**Effect:** Its sample symbols, dates, exchange, interval, chunk size, callback
port, wait time, and pacing are visible in one config. Alternate callback key
spellings and a separate candle parser are removed.

## Keep one-off diagnostic inputs outside source code

**Decision:** Diagnostic scripts that select research dates, indicators,
thresholds, sample sizes, response preview limits, and output examples read
those values from named JSON files under ``config/``. Read-only database
diagnostics use the required ``APP_DB_URL`` application setting and do not
create an implicit local database URL.

**Reason:** Diagnostic output depends on these choices. A hidden threshold or
database location makes the result difficult to repeat and can point a tool at
the wrong data store.

**Effect:** Each diagnostic prints its selected config path and fails visibly
when the config or required database setting is unavailable.

## Keep market-research assumptions in named experiment configs

**Decision:** Standalone study scripts keep their data windows, indicator
periods, signal thresholds, cost models, sample sizes, split rules, and report
paths in named JSON files under ``config/``. Related weekly target, search,
and cost studies share the target-study config and pass it into their helpers.

**Reason:** Research results depend on these assumptions. They need to be
reviewable and repeatable without editing study source or changing global app
settings.

**Effect:** Each study prints the config it used. The checked-in files preserve
the values used by the prior experiments; incomplete inputs now fail visibly
instead of being silently skipped or substituted.

## Show only sourced claims and server evaluation criteria

**Decision:** The validation screen no longer displays unsourced literature
win-rate claims or treats a fixed two-standard-deviation value as the verdict
threshold. It displays the measured comparison to the random-selection control
and the evaluation checks returned with each report.

**Reason:** A literature claim without its source cannot be checked, and a
frontend-only threshold can disagree with the configured evaluation that
produced the server verdict.

**Effect:** The screen reports measured values and server-provided verdict
checks. Literature claims can return when the report API includes their source
and citation.

## Require operational configuration outside source code

**Decision:** Behavior-changing application settings have no Python defaults.
The checked-in ``.env.example`` names each setting, and each local installation
must provide the corresponding value in its local ``.env`` or environment.
Nullable values remain required inputs when their field is declared; a blank
credential is accepted only where the owning integration can report that the
feature is not configured.

**Reason:** Source defaults hide operational choices and make it easy for two
installations to behave differently without either operator seeing why.

**Effect:** Missing configuration fails during settings construction. Audit
logging also uses its explicit configured path rather than choosing a path at
runtime. Existing local ``.env`` entries are preserved when new keys are added.

## Build transaction-cost models from required settings

**Decision:** Commission, statutory charge, and fixed slippage values are
required environment settings. Domain cost models have no numeric constructor
defaults; the config layer builds them from ``Settings`` and per-run values may
override only fields that the run explicitly supplies.

**Reason:** A cost estimate changes backtest and paper results, so a hidden
Python constructor value must not silently decide reported performance.

**Effect:** Missing cost settings fail while loading application configuration.
The checked-in example and local environment carry the previous explicit fee
assumptions. The unused random slippage mode was removed.
# Decision: Keep research and signal thresholds in named configuration

Standalone research scripts use checked-in, named JSON files for historical study
assumptions. The live holding insight uses required typed settings passed to the
detector at the service boundary. The detector does not choose missing values or
replace missing volume with fabricated data; it requires volume input. This keeps
research runs repeatable and lets deployments change operating thresholds without
editing the algorithm.

## Decision: Keep chart-pattern search assumptions beside the experiment

The pattern-combination study reads its occurrence limits, combination sizes,
shuffle settings, and reporting thresholds from `config/study_pattern_search.json`.
It reads pattern definitions and trading costs from the existing chart-pattern
configuration. The study reports its selected config paths and writes to the
configured output location.

This keeps related experiments on the same pattern and cost definitions and makes
the search repeatable without editing Python code.

## Decision: Keep the ML walk-forward experiment reproducible

`study_ml_walkforward.py` reads its date range, signal horizon, feature periods,
model parameters, sample gates, costs, and output path from
`config/study_ml_walkforward.json`. This keeps the hand-picked historical run
available while making each assumption visible and editable outside the script.

## Decision: Require insight and screener policies at the composition boundary

Insight cache age, stale-data age, initial user preferences, and holding-analysis
thresholds come from required Settings. A failed state or preference read is an
error; it is not replaced with an empty state or built-in preferences. If the
market return needed to classify buy ideas is missing, the response marks ideas
unavailable rather than calculating them from zero.

Screener limits and request defaults are read from the configured screener policy
and passed into the service. The service has no built-in leaf, group, history,
worker, market, or sort values. The frame-TTL constant was removed because the
service did not use it; the adapter owns frame caching.

## Decision: Configure runner timing, quote age, and reconciliation tolerances

Runner cadence, lookback, minimum sleep, closed-market wait, feed-subscription
retry time, and maximum quote age come from required Settings and reach the runner
and venue through composition. Saved deployments may set their own cadence and
lookback; omitted values use the explicit platform configuration. The quote-age
check reads the configured venue field directly and does not invent a time limit.

Position and cash comparisons receive their tolerances from the reconciliation
service. The pure comparison functions require those values as arguments so they
cannot select a hidden precision rule.

Parameter-screen request bounds and the evaluated fast/slow period lists, chunk
size, candidate count, and displayed cost assumption come from the required
`PARAMETER_SCREEN_CONFIG` object. The screen service receives a typed config and
does not own those numeric choices.

## Decision: Keep paper momentum tracking paths and score rules configured

`track_momentum_paper.py` reads its source files, append-only ledger path, rule
name, target return, settlement window, and backfill size from
`config/track_momentum_paper.json`. Invalid ledger rows and missing required pick
prices stop the run with an error instead of being skipped.

## Decision: Keep the model walk-forward study's settings visible

The historical ML experiment stores its feature windows, training gates, classifier
parameters, target return, costs, and output path in
`config/study_ml_walkforward.json`. If no test year meets the configured sample
requirements, the script reports an error instead of producing an empty summary.

## Decision: Keep the startup diagnostic tunable

The server startup diagnostic reads its port, timeout, polling intervals, and watch
duration from `config/time_startup.json`. Its process is always stopped and waited
for after measurement, including when a request fails.

## Decision: Load phase-two research choices at run time

The phase-two research runner reads its history range, universe files, result path,
and progress log path from `config/run_phase2.json`. It opens the log only while
the command runs, so importing the script has no file-writing side effect.

## Decision: Make the gap-and-run paper monitor explicit

The manual paper monitor reads its data window, universe, benchmark, signal gates,
fees, comparison runs, and log path from `config/paper_trade.json`. Its documented
`--loop` option was removed because the command never implemented a loop. Missing
benchmark data and unreadable history now stop the run instead of disabling the
regime filter or clearing the prior log.

## Decision: Keep weekly momentum hypotheses pre-registered in config

The weekly momentum study keeps the historical date window, universe files, data
eligibility gates, momentum grid, random-control seeds, trading costs, return
thresholds, execution lag, and output path in
`config/research_weekly_momentum.json`. Required universe files and symbol history
must be readable; the study does not turn missing data into a smaller, silently
different experiment.

## Decision: Keep weekly target feasibility inputs explicit

`research_weekly_target.py` reads its targets, historical window, instrument and
stock universes, data eligibility, bootstrap settings, portfolio sizes, and output
path from `config/research_weekly_target.json`. Missing required universe files,
instrument data, or a usable portfolio sample stop the report with a clear error.

## Decision: Keep weekly stock-pick filters and data gates configured

`research_weekly_stock_picks.py` reads its filter definitions, stock eligibility,
history windows, panel cache, and report destination from
`config/research_weekly_stock_picks.json`. The panel cache must be explicitly
selected; selecting it when the file is missing is an error. A rebuild remains an
explicit command option and reads every eligible daily file.

## Decision: Make survivorship comparisons reproducible

`research_survivorship.py` reads the cache directory, selected panel, minimum
history, annualization, and report labels from
`config/research_survivorship.json`. Unreadable parquet files and empty or
unaligned panels fail the comparison instead of being omitted.


## Decision: Evaluate volatility scaling after its configured warmup

`research_honest_verdicts.py` reads its data paths, history gates, annualization,
factor parameters, trial counts, split points, and output path from
`config/research_honest_verdicts.json`. Unreadable data files fail visibly. The
volatility overlay starts only after its configured rolling window has enough
history, and its benchmark uses the same evaluation dates. Missing volatility
inside that window is an error rather than a zero-weight substitute.

## Decision: Keep volatility-factor stress assumptions explicit

`research_vol_factor_stress.py` reads the source panel, forward horizon, factor
lookbacks, quantile count, sample gates, monthly rebalance, and cost model from
`config/research_vol_factor_stress.json`. The historical cost assumption is
preserved as 25 basis points per side. Panel read errors stop the study.

## Decision: Make factor-regime checks reproducible

`research_vol_factor_regime.py` reads its panel, rebalance interval, volatility
window, warmup, cross-section gate, quantile count, annualization, and worst-month
count from `config/research_vol_factor_regime.json`. Missing panels and samples
that do not meet the configured gates stop with an error.

## Decision: Configure momentum-versus-volatility study parameters

`research_momentum_or_vol.py` reads its panel, forward horizon, momentum
formation window, volatility windows, bucket counts, and annualization from
`config/research_momentum_or_vol.json`. It no longer turns the missing first
market return into zero; periods without complete factor inputs are excluded by
the study's row eligibility step.
# 2026-11-08: Shared interaction values come from UI config

Small interaction rules belong in the shared UI configuration too. Toast limits,
keyboard and number animation behavior, timestamp age bands, symbol-picker limits,
live-tick reconnect delay, and visible market-depth rows now come from required
Settings fields and `/api/v1/ui-config`. The browser must not recreate them in
component defaults. IIFL's ten-level depth payload stays with the broker protocol;
the five rows per side shown by the browser are a display setting.


## Decision: Keep episodic-pivot premise inputs pre-registered

`research_episodic_pivot.py` reads universe paths, requested universes, required
history columns, sample gates, gap and volume thresholds, forward horizons,
quiet-volatility rules, tail windows, and output path from
`config/research_episodic_pivot.json`. Every requested universe file and every
requested symbol's cache file is required; a missing file stops the run. Symbols
that exist but do not meet the configured history gate remain ineligible. Missing
forward outcomes are reported as unavailable when there are no catalyst samples.

## Decision: Keep factor-scan hypotheses and horizons in config

`research_factor_scan.py` reads its panel, forward horizon, quantile count,
formation and lookback windows, factor names, and survivor threshold from
`config/research_factor_scan.json`. The historical hypothesis grid is preserved
there. A missing or empty panel now stops the scan.

## Decision: Reuse the weekly momentum config in its deflated validation

`validate_momentum_deflated.py` now reads the weekly study's dates, universes,
formation grid, execution lag, and data rules from
`config/research_weekly_momentum.json`. Its fold count, fold sample gates, and
report path are isolated in `config/validate_momentum_deflated.json`. This keeps
the pre-registered strategy grid in one source of truth without adding a
compatibility layer.

## Decision: Evaluate risk overlays only with known inputs

`research_overlay_honest.py` reads its daily cache, history gate, annualization,
split point, and each overlay parameter from
`config/research_overlay_honest.json`. Unreadable cache files stop the study.
Unknown indicator values remain outside that overlay's measured window rather
than becoming zero exposure, and each benchmark comparison uses the same dates.

## Decision: Source polling intervals from required UI config

Polling intervals come from `/api/v1/ui-config`; browser components do not own
copies of those values. Every visible polling task has an error handler owned by
its section. A failed refresh keeps the last successful value visible and marks
it stale or unavailable, so a temporary connection error cannot look like a
real zero, empty result, or cleared safety state.
# 2026-11-08: Saved-screen watch intervals are explicit

Scheduled screen watches require an interval when they are enabled. The worker
does not invent an interval for an old or incomplete saved watch. Minimum and
maximum intervals and notification row count come from required Settings. The
P&L calendar backfill window also comes from Settings and is passed into the
calculation call instead of a function default.

## Decision: Keep legacy walk-forward study assumptions in an explicit config

`hunt_walkforward.py` requires a named JSON config path on its command line.
Dates, universes, selected strategy variants, costs, annualization, volatility
targeting, and sample gates live in `config/hunt_walkforward.json`. The study
stops when the selected data range has no usable panel instead of reporting an
empty result as a valid run.

## Decision: Preserve tighter-stop grids in a named study config

`hunt_wf_tighter.py` requires a JSON config path and reads its study range,
universes, regime windows, gap filters, folds, transaction costs, and every
registered stop/target/holding/position combination from that file. Missing
NIFTYBEES dates stop the run instead of silently carrying an earlier close.

## Decision: Keep local task scheduling user-managed

Forward does not own a Windows Task Scheduler configuration. The Briefing page
therefore explains that scheduling is user-managed instead of presenting fixed
machine paths and run times as ready-to-run commands.

## Decision: Reject missing symbols and bars in shared legacy research panels

`hunt_final3.load_stocks` no longer catches symbol file errors, skips symbols
with short histories, or forward-fills panel gaps. Its callers pass an explicit
minimum bar count from their study config. A missing or unreadable requested
file, a short history, a panel gap, or missing benchmark dates stops the run
with a visible error so incomplete data cannot look like a full-universe result.

## 2026-11-08: Analytics reports carry their effective slippage threshold

**Decision:** Analytics row responses expose the threshold stored with that
trade's attribution. Execution distributions expose the configured threshold
used for current aggregate warnings. The service receives its page, aggregate,
and sample limits from required Settings during composition.

**Reason:** The UI must explain why it marks slippage as high without repeating
a business threshold in TypeScript. Stored row thresholds remain authoritative
for historical classifications, while the aggregate uses the current configured
threshold.

**Effect:** Missing attribution branches or reason-code lists now raise an
explicit data error instead of being replaced with empty objects or lists.
Analytics reads return an error rather than presenting incomplete saved data as
a valid report.

## 2026-11-08: Alert requests carry their complete rule policy

**Decision:** Manual alert requests must provide the exchange, rule kind,
threshold, cooldown, and armed state. The API exposes the supported rule-kind
catalog. Manual editor values and its display limits are required UI
configuration.

**Reason:** A request must not create a rule whose behavior depends on model
defaults that the operator never saw. The UI can still start with the current
editor values, but it reads them from `/ui-config` and gets allowed rule kinds
from the alert API.

**Effect:** Missing policy fields fail validation. Alert-event page bounds and
display limits come from named configuration values.

## 2026-11-08: Backtest result caps come from configuration

**Decision:** Backtest and F&O services receive their result-curve cap and
trade-row cap from configuration. Both paths use the configured saved-trade
limit from the backtest form profile; curve points use the required
`BACKTEST_MAX_CURVE_POINTS` setting.

**Reason:** Storage and response-size limits change which results are kept, so
the compute layer must not hide those limits in module constants.

**Effect:** Missing cap configuration fails during Settings loading, and both
backtest paths report truncation against the same configured policy.
# 2026-11-08: Runtime and broker policy has one configured source

IIFL client transport and order policy, bridge routing, saved-screen watch
limits, P&L calendar history length, and experiment output size/precision are
required configuration. Adapters receive these values through composition;
they do not carry their own operational defaults. Saved-screen watches require
an explicit interval when enabled, so an incomplete saved row cannot silently
run at an assumed cadence. Old unused Settings fields were removed when their
behavior had already moved to database or shared page-size policy.

## 2026-11-08: Frontend number and currency formatting uses server settings

**Decision:** Frontend number and money formatters use locale, currency, and
default decimal precision from `/api/v1/ui-config`. The app waits for this
required configuration before it renders pages that format values. Money
formatters do not choose a local currency or locale when the API is unavailable.

**Reason:** Different pages must show the same financial values, and a display
choice must not be hidden in a shared helper or copied across components.

**Effect:** Missing UI configuration is shown as an application error. P&L
values use the configured maximum precision, with explicit precision kept only
where a particular measurement needs a different display rule.

## 2026-11-08: F&O runner policy comes from a required named config

**Decision:** F&O feed frequency and live-runner settings are loaded from
`config/options_forward.json`, whose path is required in environment settings.
Margin mode and positive-P&L exit policy are required request or strategy
fields; they have no implicit choices.

**Reason:** These fields decide which contracts and execution path a simulation
uses. A missing value must be rejected instead of quietly selecting a policy.

**Effect:** The existing paper-run policy is now visible in one config file.
Invalid or missing config prevents runner composition. The API uses its
configured maximum in validation messages.

## 2026-11-08: Order preview does not invent margin or fill-protection estimates

**Decision:** The order preview shows market value only when a current price is
available. It does not estimate required margin or market-order protection
without a broker result or an explicit configured model.

**Reason:** A fixed percentage is not an account- or contract-specific margin
requirement. Showing it as a real estimate could mislead order decisions.

**Effect:** Margin fields show unavailable until the broker API or a named
operator configuration supplies the calculation. Missing quotes also remain
unknown instead of being treated as zero.

## 2026-11-08: Require deployment inputs instead of prefilled values

**Decision:** Paper deployment capital, exchange, timeframe, and maximum open
positions start empty. The operator must choose or enter each value, and the
form validates them before sending the request. Position sizing previews also
wait for the required inputs instead of inserting example prices, risk values,
or account capital.

**Reason:** A prefilled number can look like a recommendation or an account
setting even when it is only a UI convenience.

**Effect:** New deployments require explicit values. The summary and preview
show no result until the required inputs are present. Optional stop loss stays
blank unless the operator enters a value.

## 2026-11-08: Require measured inputs for regime evaluation

**Decision:** The regime evaluation API requires the symbol and every indicator
value from its caller. It does not supply sample NIFTY values when a field is
missing.

**Reason:** Sample numbers can look like current market measurements and lead
to a false regime classification.

**Effect:** An incomplete request is rejected by request validation. The caller
must send measured values before the classifier runs.

## 2026-11-08: Resolve a paper universe before deployment

**Decision:** Paper deployment requires a non-empty list of resolved symbols.
Selecting a universe name does not count as a symbol list.

**Reason:** The paper runner evaluates `config.symbols`; it does not resolve a
universe name. Accepting a label with no resolved symbols can create a run that
shows as running but never evaluates a strategy.

**Effect:** The form enables deployment only after symbols are available. A
selected universe with no symbols clears any previous universe selection.

## 2026-11-08: Parse timestamps through one shared helper

**Decision:** Browser code parses timestamps through `parseTimestamp` in
`lib/time.tsx`. A timestamp without a timezone is read as UTC; an explicit
offset is preserved; invalid input returns `null`.

**Reason:** Separate date helpers handled offsets differently and could turn a
valid timestamp into an invalid date.

**Effect:** Relative-time labels and the trade ledger share one parser. The
ledger shows a dash for invalid timestamps instead of passing them to the date
formatter.


## Decision: Put the optimizer search and sample gates in its own config

`hunt_optimize.py` requires `config/hunt_optimize.json` (or another explicit
config path). The config keeps the original stop/target grid, signals, market
regimes, fees, volatility assumptions, sample gates, walk-forward references,
and output path. Requested stock data and benchmark dates must be complete; a
missing file, short history, or panel gap stops the run. The recommendation step
raises an error when no configuration meets its stated sample and return gates.

## Decision: Preserve final walk-forward comparisons in a named config

`hunt_walkforward_final.py` requires an explicit JSON config path. Its date
range, universe, benchmark, signal thresholds, folds, strategy grids, costs,
filter thresholds, and optional intraday report file are config inputs. A null
intraday report path explicitly disables that optional report; missing required
price or volume history stops the study.

## Decision: Keep BacktestEngine hunt inputs in a required config

`hunt_bt_engine.py` now requires an explicit config path. The config holds its
source data, universe, date split, all search grids, account size, risk limits,
execution assumptions, sample gates, reporting thresholds, and output path.
The script uses the configured Indian delivery cost service for fee values.
Missing files, insufficient bars, or misaligned symbol dates stop the run;
per-symbol exceptions are no longer swallowed. A recommendation run fails when
no training or OOS configuration meets its configured trade-count gate.

## 2026-11-08: Keep behavior choices outside implementation code

**Decision:** Strategy parameters, operating limits, feed settings, and display
preferences must be supplied by a persisted strategy definition, an API, an
environment variable, or a named configuration file. Runtime services receive
these values explicitly. Source code keeps only reusable execution rules and
protocol definitions.

**Reason:** A user should be able to build and change strategies in Forward
without editing or adding a Python strategy file. Hidden defaults make a run
difficult to review and repeat.

**Effect:** Required behavior settings fail validation when absent. The app does
not invent replacement values or market data when a source is missing. Any
proposed fallback needs explicit approval before implementation.

## 2026-11-08: F&O backtests require execution assumptions per run

**Decision:** Each F&O backtest request must declare fill timing, whether short
positions are allowed, margin treatment, and transaction costs. The result
stores those exact assumptions with its metrics.

**Reason:** Fill timing and short exposure change strategy results. A hidden
engine default makes runs hard to compare and review.

**Effect:** The F&O builder requires an operator choice before starting a
backtest. The service passes those choices to the shared backtest engine and
records them in the saved result.

## 2026-11-08: Weekly and monthly expiry require source metadata

**Decision:** Option contracts must include an explicit expiry type of
`WEEKLY` or `MONTHLY`. Contract selection filters on that field and fails when
it is missing or when no contract of the requested type is available.

**Reason:** Inferring a weekly/monthly category from the date pattern can select
the wrong contract when exchange schedules change.

**Effect:** IIFL contract files that omit expiry type cannot be used to choose
weekly or monthly legs. The operator must provide source contract metadata that
includes the classification before those strategy stages can run.

## 2026-11-08: Option stages declare expiry exit behavior

**Decision:** Every options stage must say whether expiry closes all open
strategy legs. This choice is saved with the strategy version and is applied by
the shared backtest and forward strategy engine.

**Reason:** Expiry handling changes the lifetime and risk of the whole position;
it must be visible in the strategy definition rather than supplied by a
strategy-specific code path.

**Effect:** Strategy authors must choose the expiry action for every stage. When
an enabled expiry arrives, the strategy closes its open positions and requests
the live paper runner to stop.

## 2026-11-08: Monitoring pagination rejects invalid limits

**Decision:** Monitoring routes validate page limits against configured bounds
and reject values outside those bounds. They do not silently clamp a request.

**Reason:** Clamping can hide client mistakes and return a different page size
than the caller requested.

**Effect:** Timeline, signal, and trade limits use configured defaults and
maximums as API validation constraints; accepted values pass unchanged to the
monitoring service.

## 2026-11-08: Learning API data is account-scoped

**Decision:** Authenticated learning routes pass the signed-in account through
trade reads, saved observations, and daily-cycle history. The shared file-based
paper ledger has no account owner, so account-scoped datasets do not include it.

**Reason:** The learning service already supports account-filtered trade data.
Omitting the account caused authenticated routes to read all users' records.
Unowned files and global cycle keys cannot safely be shown as a personal report.

**Effect:** Observation queries join the owning strategy, and cycle reports use
per-account state keys. Existing global cycle records and unowned file-ledger
rows are not returned by personal learning routes.

## 2026-11-08: Forward readiness reads require an account

**Decision:** Forward-trade readiness and live-deployment gates require an
account ID. They do not support an implicit all-account query.

**Reason:** An optional account ID let a caller count another account's paper
trades toward its own readiness decision.

**Effect:** The core port and SQL adapter always filter forward evidence by the
owning account before the gate evaluates it.

## 2026-11-08: Shared alert state requires system configuration access

**Decision:** Routes that read or change global alert rules, events, or
intelligent-alert settings require `system:configure`.

**Reason:** The current alert store, monitor, broker session, and delivery
channels are process-wide. A regular account could otherwise read or change
another operator's shared alert state.

**Effect:** Only system administrators and owners can access the shared alert
workflow; market readers can still read the static alert-rule kind catalog.

## 2026-11-08: Analytics date filters normalize to UTC

**Decision:** Timezone-aware analytics filters are converted to UTC before
removing timezone metadata for comparison with naive-UTC stored timestamps.

**Reason:** Dropping an offset without converting the clock shifts the filter
window and can include or exclude trades on the wrong side of the boundary.

**Effect:** Date filters from non-UTC clients compare against stored timestamps
at the correct instant.

## 2026-11-08: Async screens accept only the newest request

**Decision:** A screen that starts overlapping validation or detail requests
tracks which request currently owns its state. Older responses are ignored, and
closing, changing filters, or unmounting invalidates the pending request.

**Reason:** A slower response for an earlier selection must not replace the
result for the current selection.

**Effect:** Screener validation and signal detail panels display only responses
for the current draft or selected signal. Changing signal filters also closes
the detail panel so a hidden row cannot remain selected.

## 2026-11-08: API versions use the matching request helper

**Decision:** Versioned API routes use `apiV1Request` with a path that starts
after `/api/v1`. Unversioned routes use `apiRequest`.

**Reason:** The request helper owns version-prefix handling and structured API
errors. Duplicating the prefix bypassed that shared contract and could build a
double-prefixed URL.

**Effect:** Screener endpoints share the centralized versioned request path;
custom scanner endpoints keep using the unversioned helper.

## 2026-11-08: Clearing a search invalidates its pending response

**Decision:** Every symbol-picker query or configuration change advances the
request sequence, including an empty query or unavailable configuration.

**Reason:** A response for a query that was cleared must not repopulate hidden
results or change loading state.

**Effect:** Only the latest active symbol search can update the picker state.

## 2026-11-08: API request models reject unknown fields

**Decision:** Portfolio policy updates and sizing previews reject fields
outside their request models.

**Reason:** Silently ignoring a misspelled risk limit can make an operator
believe a limit changed when the saved policy stayed the same. The same rule
keeps sizing previews from silently dropping inputs.

**Effect:** Unknown fields return a request validation error and are not
silently discarded.

## 2026-11-08: Regime evaluation requires strategy read access

**Decision:** Market regime evaluation requires the `strategy:read` permission.

**Reason:** The route runs model inference and is part of the authenticated
strategy research API. Leaving it public allowed anonymous callers to consume
model resources when sign-in was required elsewhere.

**Effect:** The route uses the shared permission dependency and returns the same
authentication and access errors as other research endpoints.

## 2026-11-08: Formula rules set their own history requirement

**Decision:** Every entry, exit, or rank formula must set a positive integer
`min_history_bars` in its own rule block.

**Reason:** A missing requirement previously fell back to a separate literal
that did not match the entry and exit execution defaults. The value belongs in
the strategy definition so validation and execution use the same rule.

**Effect:** Validation and deployment reject formula rules with no explicit
history requirement, and compare each formula against the value saved beside it.

## 2026-11-08: Strategy validation reads canonical exit fields

**Decision:** Definition validation uses the same exit field names as the
strategy model. It does not accept aliases for obsolete names.

**Reason:** A stale field name made an enabled ATR chandelier exit look absent,
which could incorrectly block a valid strategy or report that it had no exit.

**Effect:** The no-exit check recognizes `atr_chandelier_enabled`, matching the
persisted strategy model.

## 2026-11-08: Portfolio exposure uses an explicit fraction

**Decision:** Maximum portfolio exposure is stored as a fraction from zero to
one. The sizing engine does not guess whether a value such as `20` means 20%
or whether `0.2` means 20%.

**Reason:** Unit inference can turn a valid looking configuration into a much
larger or smaller position cap than the author intended.

**Effect:** The configuration field is named
`max_portfolio_exposure_fraction`, rejects values above one, and is applied as
the supplied fraction.

## 2026-11-08: Only valid strategy definitions become versions

**Decision:** The strategy API does not allow callers to force-save a
structurally invalid definition as an immutable version.

**Reason:** A version cannot be edited after saving, and deployment needs valid
entry and exit rules to run. Draft validation gives authors a safe place to
correct the definition before publishing it.

**Effect:** The API, service, and frontend payload no longer accept a `force`
flag. Invalid definitions return validation errors before any version is saved.

## 2026-11-08: Backtests load saved strategies through the account owner

**Decision:** Backtest version reads require the requesting account ID, and the SQL query joins each version to its owning strategy. Backtest submission checks ownership before saving the run; readiness and result detail use the same owner scope.

**Reason:** A valid strategy ID and version number are not proof that the caller owns that strategy. An unscoped read could let a user run another account's saved rules.

**Effect:** Missing and foreign-owned versions produce the same not-found response. Version lists, runs, readiness checks, and trade details stay inside the signed-in account boundary.

## 2026-11-08: Journal metrics use fills from one closed position episode

**Decision:** When closing a previously journalled position, select the fill sequence for that position's flat-to-flat episode before calculating exit price, costs, and excursions.

**Reason:** Filtering only by symbol and entry time can pull in a later round trip for the same symbol and attach its exit or costs to an earlier trade.

**Effect:** A journal entry closes only when its own completed episode can be identified. Later fills from another episode cannot change its reported P&L.

## 2026-11-08: Portfolio realized P&L is grouped by deployment

**Decision:** The control center groups journal P&L and open P&L by deployment, while keeping strategy names as labels.

**Reason:** One strategy can have more than one deployment. Repeating strategy-wide P&L on every deployment row duplicates totals and gives each row a misleading return and contribution.

**Effect:** Each deployment row shows only its own realized and unrealized P&L; the portfolio total sums those deployment results once.

## 2026-11-09: Signal context links to one chronological trade episode

**Decision:** Learning evidence is matched in entry-time order, each opening order can link to one journal episode, and deployment IDs must agree when both sides have one.

**Reason:** Reusing the same order for repeated trades can assign one signal's context to multiple outcomes. Matching only by strategy, symbol, and direction can also cross-link two deployments.

**Effect:** Repeated trades consume distinct matching orders, and rows from different deployments do not share context when both records identify their deployment.

## 2026-11-09: Order results and deployment sizing require complete records

**Decision:** OMS results must contain the persisted order fields the execution
service reports. A filled venue result must carry its own fill price and a
rejected result must carry its reason. Deployment sizing reads only the saved
deployment sizing block and required deployment fields.

**Reason:** Empty rows, zero quantities, default reasons, and sizing copied from
another stored shape can make a broken record look like a valid order or
deployment.

**Effect:** Missing required state raises at the service boundary. The runner
does not infer sizing from an alternate strategy payload.

## 2026-11-09: Formula errors stop evaluation explicitly

**Decision:** A formula that divides by zero raises a formula error. Indicator
periods use the validated period supplied by the strategy definition.

**Reason:** Treating an invalid calculation as a false signal can hide a broken
strategy rule, and silently changing its period changes the rule the author
wrote.

**Effect:** The deployment can report the formula error and stop that
evaluation instead of treating the bad expression as a valid non-signal.

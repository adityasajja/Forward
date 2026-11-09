"""Create trade signals from research candidates and intelligent rules."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from forward.core.alert_ports import (
    AlertDeliveryPort,
    AlertHoldingsPort,
    AlertStorePort,
    IntelligentAlertConfigPort,
)
from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.learning_ports import SetupStatisticsPort
from forward.core.trade_signal_models import TradeSignal
from forward.core.trade_signal_ports import (
    ResearchSignalScannerPort,
    TradeSignalQueuePort,
    TradeSignalSettingsPort,
)

logger = logging.getLogger(__name__)

SignalEvaluator = Callable[[str, Any, Any, dict[str, Any] | None], Sequence[Any]]
SignalBuilder = Callable[..., TradeSignal]
SignalFormatter = Callable[[TradeSignal], tuple[str, str]]
ChannelFactory = Callable[[], Sequence[AlertDeliveryPort]]


class TradeSignalScanService:
    """Build, queue, and notify trade signals from available market frames."""

    def __init__(
        self,
        *,
        alert_config: IntelligentAlertConfigPort,
        alert_store: AlertStorePort,
        holdings: AlertHoldingsPort,
        market_frames: CachedMarketFramesPort,
        queue: TradeSignalQueuePort,
        research_scanner: ResearchSignalScannerPort,
        settings: TradeSignalSettingsPort,
        setup_statistics: SetupStatisticsPort,
        channel_factory: ChannelFactory,
        default_symbols: Sequence[str],
        evaluate_signals: SignalEvaluator,
        build_signal: SignalBuilder,
        format_preview: SignalFormatter,
    ) -> None:
        """Create the signal workflow with its required ports.

        Args:
            alert_config: Saved rules for intelligent alerts.
            alert_store: Saved rules that add symbols to the scan.
            holdings: Current held symbols, used to validate sell signals.
            market_frames: Cached daily bars for the configured exchange.
            queue: Store for pending and active signals.
            research_scanner: Source of quantitative research candidates.
            settings: Position sizing and active-signal limits.
            setup_statistics: Historical results for signal setups.
            channel_factory: Creates the configured alert channels.
            default_symbols: Symbols that the scan evaluates by default.
            evaluate_signals: Evaluates intelligent buy and sell rules.
            build_signal: Creates a queued signal from a candidate.
            format_preview: Formats a signal for channel delivery.
        """
        self._alert_config = alert_config
        self._alert_store = alert_store
        self._holdings = holdings
        self._market_frames = market_frames
        self._queue = queue
        self._research_scanner = research_scanner
        self._settings = settings
        self._setup_statistics = setup_statistics
        self._channel_factory = channel_factory
        self._default_symbols = tuple(default_symbols)
        self._evaluate_signals = evaluate_signals
        self._build_signal = build_signal
        self._format_preview = format_preview

    def run(self) -> dict[str, Any]:
        """Scan cached market bars and queue any new signal candidates.

        Returns:
            Scan size and the signals added to the queue.
        """
        alert_config = self._alert_config.load()
        settings = self._settings.load()
        queue = self._queue
        frames = self._market_frames.frames("NSEEQ")
        channels = self._channel_factory()
        held = self._holdings.held_symbols()
        setup_statistics = self._setup_statistics
        new_signals: list[dict[str, Any]] = []

        self._add_research_signals(
            frames=frames,
            settings=settings,
            setup_statistics=setup_statistics,
            added=new_signals,
        )
        symbols = set(self._default_symbols)
        try:
            symbols.update(rule.symbol for rule in self._alert_store.rules())
        except Exception as error:
            logger.warning("alert rules load failed: %s", error)

        for symbol in symbols:
            if queue.active_count() >= settings.max_active:
                break
            frame = frames.get(symbol)
            if frame is None or len(frame) < 30:
                continue
            try:
                candidates = self._evaluate_signals(symbol, frame, alert_config, held.get(symbol))
                for candidate in candidates:
                    if candidate.action == "SELL" and symbol not in held:
                        continue
                    existing = [
                        signal
                        for signal in queue.pending()
                        if signal.symbol == symbol and signal.action == candidate.action
                    ]
                    if existing or queue.active_count() >= settings.max_active:
                        continue

                    signal = self._build_signal(
                        symbol=symbol,
                        action=candidate.action,
                        setup=candidate.metric,
                        reason=candidate.reason,
                        entry_price=candidate.price,
                        df=frame,
                        settings=settings,
                        setup_stats=setup_statistics,
                        rsi_val=candidate.rsi,
                    )
                    queue.add(signal)
                    new_signals.append(signal.model_dump(mode="json"))
                    self._send_preview(channels, signal)
            except Exception as error:
                logger.warning("trade scan failed for %s: %s", symbol, error)

        return {
            "scanned": len(symbols) + len(frames),
            "new_signals": len(new_signals),
            "signals": new_signals,
        }

    def _add_research_signals(
        self,
        *,
        frames: Mapping[str, Any],
        settings: Any,
        setup_statistics: SetupStatisticsPort,
        added: list[dict[str, Any]],
    ) -> None:
        """Queue eligible research candidates before intelligent-rule signals.

        Args:
            frames: Cached daily bars keyed by symbol.
            settings: Active position sizing and signal limits.
            setup_statistics: Historical setup statistics for signal details.
            added: Response rows already created by this scan.
        """
        queue = self._queue
        try:
            candidates = self._research_scanner.scan_for_quant_signals(
                dict(frames), max_candidates=10
            )
            for candidate in candidates:
                if queue.active_count() >= settings.max_active:
                    break
                existing = [
                    signal
                    for signal in queue.pending()
                    if signal.symbol == candidate.symbol and signal.action == candidate.action
                ]
                if existing:
                    continue
                frame = frames.get(candidate.symbol)
                if frame is None or len(frame) < 20:
                    continue

                signal = self._build_signal(
                    symbol=candidate.symbol,
                    action=candidate.action,
                    setup=candidate.setup,
                    reason=candidate.thesis,
                    entry_price=candidate.price,
                    df=frame,
                    settings=settings,
                    setup_stats=setup_statistics,
                    paper_citation=candidate.paper_citation,
                    thesis=candidate.thesis,
                    confidence_score=candidate.confidence_score,
                    expected_value=candidate.expected_value,
                    regime_fit=candidate.regime_fit,
                )
                queue.add(signal)
                added.append(signal.model_dump(mode="json"))
        except Exception as error:
            logger.warning("Quant alpha scan failed: %s", error)

    def _send_preview(self, channels: Sequence[AlertDeliveryPort], signal: TradeSignal) -> None:
        """Send one signal preview through the configured channels.

        Args:
            channels: Channels that can deliver the preview.
            signal: Newly queued trade signal.
        """
        header, body = self._format_preview(signal)
        for channel in channels:
            try:
                if channel.send(header, body):
                    break
            except Exception as error:
                logger.warning("Telegram channel send failed: %s", error)
        else:
            if channels:
                logger.warning("no Telegram channel sent preview for %s", signal.id)

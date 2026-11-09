"""Coordinate self-learning with the shared market history source."""

from __future__ import annotations

from typing import Any

from forward.core.historical_market_data_ports import CachedMarketFramesPort
from forward.core.self_learning_ports import SelfLearningTrainingPort


class SelfLearningTrainingService:
    """Train the learning engine with the configured daily market history."""

    def __init__(
        self,
        market_frames: CachedMarketFramesPort,
        engine: SelfLearningTrainingPort,
    ) -> None:
        """Create the use case with market history and learning ports.

        Args:
            market_frames: Shared source of cached daily bars.
            engine: Learning engine that updates and saves its model state.
        """
        self._market_frames = market_frames
        self._engine = engine

    def train(self) -> dict[str, Any]:
        """Run one training cycle over the current NSE equity history.

        Returns:
            The updated market regime, strategy weights, and training count.
        """
        frames = self._market_frames.frames("NSEEQ")
        return self._engine.train_on_history(dict(frames))

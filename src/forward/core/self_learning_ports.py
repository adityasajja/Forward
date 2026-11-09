"""Ports used by the self-learning workflow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class SelfLearningStateStoragePort(Protocol):
    """Load and store the self-learning model state."""

    def load_state(self) -> Mapping[str, Any] | None:
        """Load saved model state.

        Returns:
            The saved state, or ``None`` when no state has been saved.
        """

    def save_state(self, state: Mapping[str, Any]) -> None:
        """Persist the current model state.

        Args:
            state: Serializable model state.
        """


class SelfLearningTrainingPort(Protocol):
    """Train the self-learning model from supplied market frames."""

    def train_on_history(self, frames: dict[str, Any]) -> dict[str, Any]:
        """Update the model from daily bars keyed by symbol.

        Args:
            frames: Daily market bars keyed by symbol.

        Returns:
            The updated regime, strategy weights, and training count.
        """

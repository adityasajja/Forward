"""Build self-learning services with shared market-history access."""

from __future__ import annotations

from forward.config.settings import research_data_root
from forward.data.self_learning_state import JsonSelfLearningStateAdapter
from forward.research.self_learning import SelfLearningEngine
from forward.services.self_learning_training import SelfLearningTrainingService
from forward.wiring.market_data import get_cached_market_frames_port

_SELF_LEARNING_ENGINE: SelfLearningEngine | None = None
_SELF_LEARNING_TRAINING_SERVICE: SelfLearningTrainingService | None = None


def get_self_learning_engine() -> SelfLearningEngine:
    """Return the self-learning engine with configured JSON state storage."""
    global _SELF_LEARNING_ENGINE
    if _SELF_LEARNING_ENGINE is None:
        data_root = research_data_root()
        storage = JsonSelfLearningStateAdapter(data_root / "self_learning" / "state.json")
        _SELF_LEARNING_ENGINE = SelfLearningEngine(storage)
    return _SELF_LEARNING_ENGINE


def get_self_learning_training_service() -> SelfLearningTrainingService:
    """Return the training use case with shared market-history access."""
    global _SELF_LEARNING_TRAINING_SERVICE
    if _SELF_LEARNING_TRAINING_SERVICE is None:
        _SELF_LEARNING_TRAINING_SERVICE = SelfLearningTrainingService(
            get_cached_market_frames_port(),
            get_self_learning_engine(),
        )
    return _SELF_LEARNING_TRAINING_SERVICE


def reset_self_learning_engine() -> None:
    """Clear self-learning providers after settings or data-root changes."""
    global _SELF_LEARNING_ENGINE, _SELF_LEARNING_TRAINING_SERVICE
    _SELF_LEARNING_ENGINE = None
    _SELF_LEARNING_TRAINING_SERVICE = None

"""Provide the shared learning data adapter."""

from forward.config.settings import research_data_root
from forward.data.learning_artifacts import LocalLearningArtifactsAdapter

LEARNING_ARTIFACTS = LocalLearningArtifactsAdapter(research_data_root())


def get_learning_artifacts() -> LocalLearningArtifactsAdapter:
    """Return the shared learning input and report adapter."""
    return LEARNING_ARTIFACTS

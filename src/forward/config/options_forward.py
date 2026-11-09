"""Load the configured live-data policy for F&O paper runs."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class OptionsForwardRunnerSettings(BaseModel):
    """Settings for the shared live runner used by F&O paper runs."""

    model_config = ConfigDict(extra="forbid")

    poll_timeout: float = Field(gt=0)
    square_off_time: time
    warmup_bars: int = Field(ge=0)
    max_history_bars: int = Field(gt=0)
    max_runtime_seconds: float | None = Field(gt=0)
    flatten_on_stop: bool
    square_off_enabled: bool
    max_snapshot_age_seconds: float | None = Field(gt=0)


class OptionsForwardSettings(BaseModel):
    """Market, feed, and runner settings read from options configuration."""

    model_config = ConfigDict(extra="forbid")

    frequency: str = Field(min_length=1)
    index_exchange: str = Field(min_length=1)
    options_exchange: str = Field(min_length=1)
    runner: OptionsForwardRunnerSettings


def load_options_forward_settings(path: Path) -> OptionsForwardSettings:
    """Load and validate the required F&O paper-run settings.

    Args:
        path: The configured JSON file path.

    Returns:
        The validated feed and runner settings.

    Raises:
        FileNotFoundError: If the required configuration file is missing.
        ValueError: If the file is not valid JSON or does not match the schema.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    return OptionsForwardSettings.model_validate(payload)

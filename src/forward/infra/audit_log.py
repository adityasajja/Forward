"""Append and read the local JSONL audit file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forward.core.audit_ports import AuditLogPort


class JsonlAuditLogAdapter(AuditLogPort):
    """Store audit events in one configured append-only JSONL file.

    Args:
        file_path: Configured location for the audit file.
    """

    def __init__(self, file_path: Path) -> None:
        self._file_path = Path(file_path)

    def append(self, record: dict[str, Any]) -> None:
        """Append one event to the configured file.

        Args:
            record: Normalized audit event to write.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Read the newest complete events first.

        Args:
            limit: Maximum number of records to return.

        Returns:
            Newest audit events, up to ``limit`` rows.
        """
        if limit <= 0:
            return []
        try:
            lines = self._file_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

        events: list[dict[str, Any]] = []
        first_line = max(1, len(lines) - limit + 1)
        recent_lines = enumerate(lines[-limit:], start=first_line)
        for line_number, line in reversed(list(recent_lines)):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"audit record on line {line_number} is malformed") from exc
            if not isinstance(event, dict):
                raise ValueError(f"audit record on line {line_number} is not an object")
            events.append(event)
            if len(events) == limit:
                break
        return events

    def path(self) -> str:
        """Return the configured file path for the status screen."""
        return str(self._file_path)

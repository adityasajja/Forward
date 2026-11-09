"""JSON adapter for alert rules and delivery events."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from forward.alerts.models import AlertEvent, AlertRule
from forward.core.alert_ports import AlertStorePort


def _load_models[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    """Load a JSON list and validate each row against its domain model.

    Args:
        path: File that contains a JSON array.
        model: Model used to validate each array item.

    Returns:
        Validated rows, or an empty list when the file does not exist.

    Raises:
        ValueError: If the file does not contain a JSON array.
        pydantic.ValidationError: If an array item does not match the model.
        OSError: If the file cannot be read.
    """
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [model.model_validate(row) for row in rows]


class JsonAlertStoreAdapter(AlertStorePort):
    """Store alert rules and events as JSON files.

    Args:
        directory: Folder used for the rules and event files.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _rules_path(self) -> Path:
        """Return the path for alert rules."""
        return self.directory / "rules.json"

    def _events_path(self) -> Path:
        """Return the path for alert events."""
        return self.directory / "events.json"

    def rules(self) -> list[AlertRule]:
        """Load and validate every saved alert rule.

        Returns:
            Rules in the order stored in the file.
        """
        return _load_models(self._rules_path(), AlertRule)

    def save_rules(self, rules: list[AlertRule]) -> None:
        """Write the full alert-rule collection.

        Args:
            rules: Rules to save.
        """
        self._rules_path().write_text(
            json.dumps([rule.model_dump(mode="json") for rule in rules], indent=2),
            encoding="utf-8",
        )

    def upsert(self, rule: AlertRule) -> AlertRule:
        """Add or replace a rule by its ID.

        Args:
            rule: Rule to store.

        Returns:
            The stored rule.
        """
        rules = [saved for saved in self.rules() if saved.id != rule.id] + [rule]
        self.save_rules(rules)
        return rule

    def remove(self, rule_id: str) -> bool:
        """Remove a rule by its ID.

        Args:
            rule_id: ID of the rule to remove.

        Returns:
            Whether a matching rule existed.
        """
        rules = self.rules()
        remaining = [rule for rule in rules if rule.id != rule_id]
        if len(remaining) == len(rules):
            return False
        self.save_rules(remaining)
        return True

    def events(self, limit: int = 100) -> list[AlertEvent]:
        """Load recent events, newest first.

        Args:
            limit: Maximum number of events to return.

        Returns:
            The newest stored events, in reverse chronological file order.
        """
        return _load_models(self._events_path(), AlertEvent)[-limit:][::-1]

    def log(self, event: AlertEvent) -> None:
        """Append an event and keep the newest 500 records.

        Args:
            event: Event to append.
        """
        events = _load_models(self._events_path(), AlertEvent) + [event]
        self._events_path().write_text(
            json.dumps([item.model_dump(mode="json") for item in events[-500:]], indent=2),
            encoding="utf-8",
        )

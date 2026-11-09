"""Run watched saved screens and send alerts for new matches."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from forward.core.errors import ScreenerError
from forward.core.screener_ports import (
    SavedScreenerPort,
    ScreenerWatchNotificationsPort,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class ScreenerWatchConfig:
    """Operator-owned limits for scheduled saved screens."""

    minimum_interval_minutes: int
    maximum_interval_minutes: int
    notification_line_limit: int


def _watch_of(definition: dict[str, Any]) -> dict[str, Any]:
    """Return the watch settings in a saved scan definition.

    Args:
        definition: Saved scan definition.

    Returns:
        Watch settings, or an empty mapping when no watch is configured.
    """
    watch = definition.get("watch")
    return watch if isinstance(watch, dict) else {}


class ScreenerWatchService:
    """Manage scheduled saved screens through screen and alert ports."""

    def __init__(
        self,
        screens: SavedScreenerPort,
        notifications: ScreenerWatchNotificationsPort,
        config: ScreenerWatchConfig,
    ) -> None:
        """Create the watch workflow with its outbound dependencies.

        Args:
            screens: Reads, updates, and runs saved screens.
            notifications: Sends watch alerts to the configured channel.
        """
        self._screens = screens
        self._notifications = notifications
        self._config = config

    def configure(
        self,
        user_id: str,
        scan_id: str,
        *,
        enabled: bool,
        every_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Turn a saved screen watch on or off.

        Enabling a disabled watch resets its baseline so existing matches are
        recorded without being announced as new.

        Args:
            user_id: Owner of the saved screen.
            scan_id: Screen whose watch settings should change.
            enabled: Whether the scheduler should run the screen.
            every_minutes: Required interval when enabling, or the saved interval when disabling.

        Returns:
            The updated saved screen.

        Raises:
            ScreenerError: If the interval is invalid or the screen is missing.
        """
        saved = self._screens.get_saved(user_id, scan_id)
        definition = dict(saved["definition"])
        old = _watch_of(definition)
        minutes = every_minutes if every_minutes is not None else old.get("every_minutes")
        if enabled and minutes is None:
            raise ScreenerError("every_minutes is required when enabling a watch", code="bad_interval")
        if minutes is not None:
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                raise ScreenerError(
                    "every_minutes must be a whole number", code="bad_interval"
                ) from None
            if not self._config.minimum_interval_minutes <= minutes <= self._config.maximum_interval_minutes:
                raise ScreenerError(
                    "every_minutes must be between "
                    f"{self._config.minimum_interval_minutes} and {self._config.maximum_interval_minutes}",
                    code="bad_interval",
                )

        watch = {"enabled": bool(enabled), "last_error": None}
        if minutes is not None:
            watch["every_minutes"] = minutes
        if enabled and not old.get("enabled"):
            watch.update(seen=None, last_run=None)
        else:
            watch.update(seen=old.get("seen"), last_run=old.get("last_run"))
        definition["watch"] = watch
        return self._screens.update_saved(user_id, scan_id, definition=definition)

    def run_one(
        self,
        scan: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Run one watch and persist its latest matches.

        Args:
            scan: Saved screen row with its owner and watch definition.
            now: Clock value used by tests and scheduled callers.

        Returns:
            Match counts, notification state, and baseline state.
        """
        now = time.time() if now is None else now
        watch = dict(_watch_of(scan["definition"]))
        try:
            result = self._screens.run_saved(scan["user_id"], scan["scan_id"], limit=0)
        except Exception as exc:  # noqa: BLE001 - one broken screen must not stop others
            logger.warning("watched scan %s failed: %s", scan.get("name"), exc)
            watch.update(last_run=now, last_error=str(exc)[:200])
            self._save_state(scan, watch)
            return {
                "matched": 0,
                "new": 0,
                "notified": False,
                "baseline": False,
                "error": str(exc)[:200],
            }

        rows = result["rows"]
        current = [row["symbol"] for row in rows]
        previous = watch.get("seen")
        baseline = previous is None
        previous_symbols = set() if baseline else set(previous)
        new_rows = (
            [] if baseline else [row for row in rows if row["symbol"] not in previous_symbols]
        )

        notified = False
        if baseline:
            self._send(
                f"Screener: {scan['name']} - watching",
                f"{len(current)} stocks match right now. You will be told when a new one appears.",
            )
        elif new_rows:
            title, body = self._format(scan["name"], new_rows, result)
            notified = self._send(title, body)

        if new_rows and not notified:
            retained = set(current) - {row["symbol"] for row in new_rows}
            watch["seen"] = sorted(retained)
        else:
            watch["seen"] = sorted(current)
        watch.update(last_run=now, last_error=None)
        self._save_state(scan, watch)
        return {
            "matched": len(current),
            "new": len(new_rows),
            "notified": notified,
            "baseline": baseline,
        }

    def run_due(self, *, now: float | None = None) -> int:
        """Run every enabled watch whose interval has elapsed.

        Args:
            now: Clock value used by tests and scheduled callers.

        Returns:
            The number of watches that ran.
        """
        now = time.time() if now is None else now
        ran = 0
        for scan in self._screens.list_all():
            watch = _watch_of(scan["definition"])
            if not watch.get("enabled"):
                continue
            last = watch.get("last_run")
            interval = watch.get("every_minutes")
            if interval is None:
                raise ScreenerError(
                    f"enabled saved screen {scan['scan_id']} has no every_minutes value",
                    code="bad_interval",
                )
            if (
                last is not None
                and now - float(last) < int(interval) * 60
            ):
                continue
            self.run_one(scan, now=now)
            ran += 1
        return ran

    def _save_state(self, scan: dict[str, Any], watch: dict[str, Any]) -> None:
        """Save watch state inside its screen definition.

        Args:
            scan: Saved screen whose state changed.
            watch: Updated watch state to persist.
        """
        definition = dict(scan["definition"])
        definition["watch"] = watch
        self._screens.update_saved(scan["user_id"], scan["scan_id"], definition=definition)

    def _send(self, title: str, body: str) -> bool:
        """Send one alert and report missing Telegram settings as not sent.

        Args:
            title: Alert title.
            body: Alert text.

        Returns:
            Whether Telegram accepted the alert.
        """
        if not self._notifications.configured:
            logger.warning("watched scan has new matches but Telegram is not configured")
            return False
        return self._notifications.send(title, body)

    def _format(
        self,
        scan_name: str,
        new_rows: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> tuple[str, str]:
        """Format new screen matches for the notification channel.

        Args:
            scan_name: Name shown in the alert title.
            new_rows: Newly matched screen rows.
            result: Full scan result with match count and data date.

        Returns:
            The notification title and body.
        """
        title = f"Screener: {scan_name} - {len(new_rows)} new"
        lines = []
        for row in new_rows[: self._config.notification_line_limit]:
            parts = [str(row["symbol"])]
            if row.get("ltp") is not None:
                parts.append(f"{row['ltp']:.2f}")
            if row.get("change_pct") is not None:
                parts.append(f"{row['change_pct']:+.2f}%")
            if row.get("rel_volume") is not None:
                parts.append(f"vol x{row['rel_volume']:.1f}")
            lines.append("  ".join(parts))
        if len(new_rows) > self._config.notification_line_limit:
            lines.append(
                f"...and {len(new_rows) - self._config.notification_line_limit} more"
            )
        lines.append(f"{result['matched']} match now - data as of {result.get('as_of')}")
        return title, "\n".join(lines)

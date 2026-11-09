from forward.services.screener_watch import ScreenerWatchConfig, ScreenerWatchService


class FakeService:
    def __init__(self, rows):
        self.rows = rows
        self.saved_definition = None

    def run_saved(self, user_id, scan_id, **kw):
        return {
            "rows": [{"symbol": symbol, "ltp": 10.0} for symbol in self.rows],
            "matched": len(self.rows),
            "as_of": "2026-10-01",
        }

    def update_saved(self, user_id, scan_id, *, definition=None, **kw):
        self.saved_definition = definition
        return {"user_id": user_id, "scan_id": scan_id, "definition": definition}


class FakeNotifications:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.messages = []
        self.configured = True

    def send(self, title, body):
        self.messages.append((title, body))
        return self.accepted


def _scan(seen):
    return {
        "user_id": "u",
        "scan_id": "s",
        "name": "n",
        "definition": {"watch": {"enabled": True, "every_minutes": 5, "seen": seen}},
    }


def _run(rows, seen, *, accepted=True):
    screens = FakeService(rows)
    notifications = FakeNotifications(accepted)
    service = ScreenerWatchService(
        screens,
        notifications,
        ScreenerWatchConfig(
            minimum_interval_minutes=1,
            maximum_interval_minutes=1440,
            notification_line_limit=25,
        ),
    )
    result = service.run_one(_scan(seen), now=100.0)
    return result, notifications.messages, screens.saved_definition["watch"]


def test_baseline_does_not_announce_existing():
    result, messages, saved = _run(["A", "B"], None)
    assert result["baseline"] and result["new"] == 0
    assert len(messages) == 1
    assert saved["seen"] == ["A", "B"]


def test_only_new_symbols_announced():
    result, messages, saved = _run(["A", "B", "C"], ["A", "B"])
    assert result["new"] == 1 and "C" in messages[0][1]
    assert "A" not in messages[0][1].split("\n")[0]
    assert saved["seen"] == ["A", "B", "C"]


def test_failed_send_retries_next_run():
    _, _, saved = _run(["A", "C"], ["A"], accepted=False)
    assert saved["seen"] == ["A"]

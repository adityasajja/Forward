from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep

import pytest

from forward.services.read_coalescer import ReadCoalescer


def test_overlapping_reads_share_work_and_copy_results():
    reads = ReadCoalescer()
    entered, release = Event(), Event()
    calls = []

    def read():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return {"rows": [1]}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(reads.run, "session/holdings", read)
        assert entered.wait(2)
        second = pool.submit(reads.run, "session/holdings", read)
        sleep(0.05)
        release.set()
        a, b = first.result(), second.result()
    assert len(calls) == 1
    a["rows"].append(2)
    assert b == {"rows": [1]}
    reads.run("session/holdings", read)
    assert len(calls) == 2


def test_sessions_do_not_share_reads():
    reads = ReadCoalescer()
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        assert release.wait(2)
        return "first"

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(reads.run, ("session1", "limits"), blocked)
        assert entered.wait(2)
        assert reads.run(("session2", "limits"), lambda: "second") == "second"
        release.set()
        assert first.result() == "first"


def test_failures_are_not_cached():
    reads = ReadCoalescer()

    def fail():
        raise ValueError("unavailable")

    with pytest.raises(ValueError, match="unavailable"):
        reads.run("limits", fail)
    assert reads.run("limits", lambda: 100) == 100

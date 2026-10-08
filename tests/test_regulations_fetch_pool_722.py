"""Bounded fetch pool + throttle for the regulations refresh (#722)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from estleg import generate_regulations, riigiteataja_common
from estleg.generate_regulations import (
    FetchThrottle,
    bounded_ordered_map,
    make_throttled_fetch,
)


class TestBoundedOrderedMap:
    def test_results_come_back_in_input_order(self):
        def slow_reverse(i: int) -> int:
            time.sleep(0.002 * (10 - i))  # later items finish first
            return i * i

        out = list(bounded_ordered_map(slow_reverse, range(10), workers=4))
        assert out == [(i, i * i) for i in range(10)]

    def test_never_more_than_workers_in_flight(self):
        lock = threading.Lock()
        live = 0
        peak = 0

        def fn(_i: int) -> None:
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.005)
            with lock:
                live -= 1

        list(bounded_ordered_map(fn, range(30), workers=3))
        assert 1 <= peak <= 3

    def test_window_bounds_submitted_work(self):
        submitted: list[int] = []

        def items():
            for i in range(100):
                submitted.append(i)
                yield i

        gen = bounded_ordered_map(lambda i: i, items(), workers=2, window=4)
        first = next(gen)
        assert first == (0, 0)
        # Only the window (+1 refill) has been pulled from the source.
        assert len(submitted) <= 6
        gen.close()

    def test_single_worker_runs_inline(self):
        thread_names: list[str] = []
        list(bounded_ordered_map(
            lambda i: thread_names.append(threading.current_thread().name), range(3), workers=1
        ))
        assert set(thread_names) == {threading.main_thread().name}

    def test_worker_exception_propagates_at_its_item(self):
        def fn(i: int) -> int:
            if i == 2:
                raise RuntimeError("boom")
            return i

        seen: list[int] = []
        with pytest.raises(RuntimeError, match="boom"):
            for item, _ in bounded_ordered_map(fn, range(5), workers=2):
                seen.append(item)
        assert seen == [0, 1]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestFetchThrottle:
    def test_start_rate_is_capped(self):
        fc = FakeClock()
        throttle = FetchThrottle(max_rps=4.0, sleep=0.0, clock=fc.clock, sleeper=fc.sleep)
        for _ in range(5):
            throttle.before_request()
        # 5 starts at 4 req/s need 4 gaps of 0.25 s.
        assert fc.now == pytest.approx(1.0)
        assert throttle.requests == 5

    def test_politeness_sleep_after_each_request(self):
        fc = FakeClock()
        throttle = FetchThrottle(max_rps=0, sleep=0.3, clock=fc.clock, sleeper=fc.sleep)
        throttle.before_request()
        throttle.after_request()
        assert fc.sleeps == [0.3]

    def test_uncapped_never_sleeps_before(self):
        fc = FakeClock()
        throttle = FetchThrottle(max_rps=None, sleep=0.0, clock=fc.clock, sleeper=fc.sleep)
        for _ in range(3):
            throttle.before_request()
        assert fc.sleeps == []


class TestThrottledFetchWithMockedRT:
    def _fake_fetch(self, calls: list[tuple[str, bool]]):
        lock = threading.Lock()

        def fetch(url, *, cache_name, cache_subdir, refresh):
            with lock:
                calls.append((url, refresh))
            return {"url": url, "cache": cache_name}

        return fetch

    def test_pool_fetches_every_task_once_in_order(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        calls: list[tuple[str, bool]] = []
        throttle = FetchThrottle(max_rps=0, sleep=0.0)
        fetch = make_throttled_fetch(
            throttle, cache_subdir="maarus", refresh=True, fetch=self._fake_fetch(calls)
        )
        tasks = [{"url": f"/akt/{i}", "cache_name": f"reg_{i}"} for i in range(12)]
        results = list(bounded_ordered_map(fetch, tasks, workers=4))
        assert [r["url"] for _t, r in results] == [t["url"] for t in tasks]
        assert sorted(u for u, _ in calls) == sorted(t["url"] for t in tasks)
        assert all(refresh for _u, refresh in calls)
        assert throttle.requests == 12

    def test_cache_hit_bypasses_throttle(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        (tmp_path / "maarus").mkdir()
        (tmp_path / "maarus" / "reg_1.xml").write_text("<x/>", encoding="utf-8")
        calls: list[tuple[str, bool]] = []
        throttle = FetchThrottle(max_rps=0, sleep=0.0)
        fetch = make_throttled_fetch(
            throttle, cache_subdir="maarus", refresh=False, fetch=self._fake_fetch(calls)
        )
        fetch({"url": "/akt/1", "cache_name": "reg_1"})
        fetch({"url": "/akt/2", "cache_name": "reg_2"})
        assert throttle.requests == 1  # only the uncached act counted as network

    def test_backoff_is_delegated_to_fetch_xml(self, tmp_path: Path, monkeypatch):
        """A 429 then 200 is retried by riigiteataja_common, not by the pool."""
        monkeypatch.setattr(riigiteataja_common, "DATA_DIR", tmp_path)
        monkeypatch.setattr(riigiteataja_common.time, "sleep", lambda *_a, **_k: None)
        xml = (
            "<?xml version='1.0' encoding='UTF-8'?><oigusakt><metaandmed>"
            "<terviktekstiGrupiID>1</terviktekstiGrupiID></metaandmed>"
            "<sisu><paragrahv><paragrahvNr>1</paragrahvNr><tavatekst>"
            + "tekst " * 60 + "</tavatekst></paragrahv></sisu></oigusakt>"
        )
        statuses = iter([429, 200])

        class Resp:
            def __init__(self, status: int) -> None:
                self.status_code = status
                self.content = xml.encode("utf-8")
                self.text = xml
                self.headers = {"Content-Type": "application/xml"}
                self.url = "https://www.riigiteataja.ee/api/oigusakt/1/xml"
                self.history: list = []
                self.is_redirect = False
                self.encoding = "utf-8"

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    import requests

                    raise requests.HTTPError(str(self.status_code), response=self)

        hits: list[int] = []

        def fake_get(url, *args, **kwargs):
            status = next(statuses)
            hits.append(status)
            return Resp(status)

        monkeypatch.setattr(riigiteataja_common, "allowed_get", fake_get, raising=False)
        monkeypatch.setattr(riigiteataja_common.requests, "get", fake_get)
        throttle = FetchThrottle(max_rps=0, sleep=0.0)
        fetch = make_throttled_fetch(throttle, cache_subdir="maarus", refresh=True)
        root = fetch({"url": "/akt/1", "cache_name": "reg_1"})
        assert hits == [429, 200]
        assert root is not None
        assert throttle.requests == 1


def test_cli_rejects_zero_workers(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["generate_regulations", "--workers", "0"])
    with pytest.raises(SystemExit):
        generate_regulations.main()

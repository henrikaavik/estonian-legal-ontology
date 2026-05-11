from __future__ import annotations

import pytest

import riigiteataja_common


class _FailingResponse:
    def raise_for_status(self):
        raise RuntimeError("boom")


def test_fetch_acts_raises_on_source_list_failure(monkeypatch):
    def fail_get(*args, **kwargs):
        return _FailingResponse()

    monkeypatch.setattr(riigiteataja_common.requests, "get", fail_get)

    with pytest.raises(riigiteataja_common.SourceListFetchError):
        list(riigiteataja_common.fetch_acts("määrus", max_retries=0))


def test_fetch_acts_allow_partial_stops_without_results(monkeypatch):
    def fail_get(*args, **kwargs):
        return _FailingResponse()

    monkeypatch.setattr(riigiteataja_common.requests, "get", fail_get)

    assert list(
        riigiteataja_common.fetch_acts(
            "määrus",
            allow_partial=True,
            max_retries=0,
        )
    ) == []

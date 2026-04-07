from __future__ import annotations

import requests

from autobioresearch.crawlers.semantic_scholar import SemanticScholarCrawler


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def test_semantic_scholar_retries_on_429(monkeypatch):
    crawler = SemanticScholarCrawler(requests_per_second=1000)
    responses = [
        _FakeResponse(429, headers={"Retry-After": "0"}),
        _FakeResponse(200, payload={"data": []}),
    ]
    calls: list[dict] = []
    sleeps: list[float] = []

    def fake_get(url: str, params: dict, timeout: int):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(crawler._session, "get", fake_get)
    monkeypatch.setattr("autobioresearch.crawlers.semantic_scholar.time.sleep", sleeps.append)

    papers = crawler.search("tp53", max_results=5)

    assert papers == []
    assert len(calls) == 2
    assert sleeps == [0.0]


def test_semantic_scholar_returns_empty_after_exhausting_429_retries(monkeypatch):
    crawler = SemanticScholarCrawler(requests_per_second=1000)
    calls: list[dict] = []
    sleeps: list[float] = []

    def fake_get(url: str, params: dict, timeout: int):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse(429)

    monkeypatch.setattr(crawler._session, "get", fake_get)
    monkeypatch.setattr("autobioresearch.crawlers.semantic_scholar.time.sleep", sleeps.append)

    papers = crawler.search("tp53", max_results=5)

    assert papers == []
    assert len(calls) == 5
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_semantic_scholar_sets_api_key_header():
    crawler = SemanticScholarCrawler(
        requests_per_second=1000,
        semantic_scholar_api_key="test-key",
    )

    assert crawler._session.headers["x-api-key"] == "test-key"

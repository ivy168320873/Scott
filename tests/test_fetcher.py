"""fetcher：外部 API 失敗路徑測試。

fetcher 幾乎所有函式都要連外部網路。測試一律以 monkeypatch 攔截
`fetcher.requests.get`，**不會發出任何真實網路請求**。
重點在於驗證：外部服務掛掉、回傳格式異常、逾時等情況下，
函式必須優雅降級成 ok=False，而不是拋例外把整個路由打掛。
"""
from __future__ import annotations

import fetcher
import pytest


class FakeResponse:
    """模擬 requests.Response，只實作被使用到的介面。"""

    def __init__(self, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = text.encode()

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def no_network(monkeypatch):
    """預設攔截所有網路呼叫，避免測試意外對外連線。"""
    def _boom(*args, **kwargs):
        raise AssertionError("測試不應發出真實網路請求")
    monkeypatch.setattr(fetcher.requests, "get", _boom)
    return monkeypatch


# ── _resolve_ticker ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("topic", [
    "半導體 AI 科技",      # 中文主題
    "artificial intel",   # 含空白
    "TOOLONGTICKER",      # 超過 6 字元
    "nvda",               # 小寫
    "NVDA1",              # 含數字
    "",                   # 空字串
])
def test_resolve_ticker_non_ticker_returns_input_without_network(no_network, topic):
    """非 ticker 格式應直接回傳原值，完全不呼叫網路。"""
    assert fetcher._resolve_ticker(topic) == topic


def test_resolve_ticker_success_returns_longname(monkeypatch):
    payload = {"quotes": [{"longname": "NVIDIA Corporation", "shortname": "NVIDIA"}]}
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, payload))
    assert fetcher._resolve_ticker("NVDA") == "NVIDIA Corporation"


def test_resolve_ticker_falls_back_to_shortname(monkeypatch):
    payload = {"quotes": [{"shortname": "NVIDIA"}]}
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, payload))
    assert fetcher._resolve_ticker("NVDA") == "NVIDIA"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_resolve_ticker_http_error_returns_original(monkeypatch, status):
    """API 失敗時必須降級回原始 topic，不可拋例外。"""
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(status, {}))
    assert fetcher._resolve_ticker("NVDA") == "NVDA"


def test_resolve_ticker_network_exception_returns_original(monkeypatch):
    def _timeout(*a, **k):
        raise TimeoutError("connection timed out")
    monkeypatch.setattr(fetcher.requests, "get", _timeout)
    assert fetcher._resolve_ticker("NVDA") == "NVDA"


def test_resolve_ticker_invalid_json_returns_original(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, ValueError("bad json")))
    assert fetcher._resolve_ticker("NVDA") == "NVDA"


@pytest.mark.parametrize("payload", [
    {},                              # 無 quotes 欄位
    {"quotes": []},                  # 空清單
    {"quotes": [{}]},                # 無 longname/shortname
    {"quotes": [{"longname": ""}]},  # 空字串名稱
])
def test_resolve_ticker_empty_result_returns_original(monkeypatch, payload):
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, payload))
    assert fetcher._resolve_ticker("NVDA") == "NVDA"


# ── _fetch_anue_news ──────────────────────────────────────────────────────────

def test_anue_http_error_returns_not_ok(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get", lambda *a, **k: FakeResponse(500, {}))
    result = fetcher._fetch_anue_news()
    assert result["ok"] is False
    assert result["content"] == ""
    assert "500" in result["error"]


def test_anue_network_exception_returns_not_ok(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("network unreachable")
    monkeypatch.setattr(fetcher.requests, "get", _boom)
    result = fetcher._fetch_anue_news()
    assert result["ok"] is False
    assert result["content"] == ""


def test_anue_empty_payload_returns_not_ok(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, {"items": {"data": []}}))
    assert fetcher._fetch_anue_news()["ok"] is False


def test_anue_success_builds_content(monkeypatch):
    payload = {"items": {"data": [
        {"title": "台積電法說會", "summary": "營收創高", "publishAt": 1_700_000_000},
    ]}}
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, payload))
    result = fetcher._fetch_anue_news()
    assert result["ok"] is True
    assert "台積電法說會" in result["content"]


def test_anue_missing_timestamp_does_not_raise(monkeypatch):
    """publishAt 缺漏（非交易日補資料常見）不應崩潰。"""
    payload = {"items": {"data": [{"title": "無日期新聞", "summary": None}]}}
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, payload))
    result = fetcher._fetch_anue_news()
    assert result["ok"] is True


def test_anue_malformed_payload_returns_not_ok(monkeypatch):
    """回傳結構與預期不符時走例外路徑，仍需回 ok=False。"""
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, {"items": "unexpected"}))
    assert fetcher._fetch_anue_news()["ok"] is False


# ── _fetch_article_text ───────────────────────────────────────────────────────

def test_article_text_network_failure_returns_empty(monkeypatch):
    def _boom(*a, **k):
        raise TimeoutError()
    monkeypatch.setattr(fetcher.requests, "get", _boom)
    assert fetcher._fetch_article_text("https://example.com/a") == ""


def test_article_text_short_content_is_discarded(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get",
                        lambda *a, **k: FakeResponse(200, {}, text="<p>太短</p>"))
    assert fetcher._fetch_article_text("https://example.com/a") == ""


# ── _get_client ───────────────────────────────────────────────────────────────

def test_get_client_returns_none_without_api_key(monkeypatch):
    """缺少 ANTHROPIC_API_KEY 時回 None，不應拋例外或洩漏金鑰。"""
    monkeypatch.setattr(fetcher, "_client", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert fetcher._get_client() is None

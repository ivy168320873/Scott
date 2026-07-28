"""Provider Adapter 契約測試。

驗證真實的 request/response mapping 與錯誤翻譯，方式是攔截 HTTP 層並
回放真實形狀的回應 —— 不是把 Adapter 換成永遠成功的替身。

**Live integration 未執行**：本測試套件不會對外發出真實網路請求，
因為 CI 環境沒有供應商金鑰。真實金鑰下的端對端驗證尚未執行。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from studio.contracts.generation import (
    ImageRequest,
    ProviderConfig,
    TextRequest,
    VideoRequest,
)
from studio.core.errors import (
    ConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from studio.integrations import build_config, get_image_provider, get_text_provider, get_video_provider
from studio.integrations.anthropic_provider import AnthropicTextProvider, extract_json
from studio.integrations.openai_compatible import (
    OpenAICompatibleImageProvider,
    OpenAICompatibleTextProvider,
    OpenAICompatibleVideoProvider,
)
from studio.models.types import ProviderKind

CONFIG = ProviderConfig(kind="openai_compatible", api_key="test-key", base_url="https://example.test/v1")


# ── Adapter 解析 ──────────────────────────────────────────────────────────────


def test_adapter_resolution_by_kind() -> None:
    """每種供應商類型都要解析到正確的 Adapter。"""

    assert isinstance(get_text_provider(ProviderKind.anthropic), AnthropicTextProvider)
    assert isinstance(get_text_provider(ProviderKind.openai), OpenAICompatibleTextProvider)
    assert isinstance(get_text_provider(ProviderKind.openai_compatible), OpenAICompatibleTextProvider)
    assert isinstance(get_image_provider(ProviderKind.openai), OpenAICompatibleImageProvider)
    assert isinstance(get_video_provider(ProviderKind.openai), OpenAICompatibleVideoProvider)


def test_anthropic_has_no_image_adapter() -> None:
    """未支援的能力必須明確報錯，而非回傳錯誤的 Adapter。"""

    with pytest.raises(ConfigurationError):
        get_image_provider(ProviderKind.anthropic)


# ── 設定組裝 ──────────────────────────────────────────────────────────────────


def test_build_config_resolves_key_from_environment(monkeypatch) -> None:
    """金鑰只從環境變數解析，資料庫不存明文。"""

    monkeypatch.setenv("PROVIDER_TEST_KEY", "secret-value")

    class _Provider:
        kind = ProviderKind.openai
        api_key_env = "PROVIDER_TEST_KEY"
        base_url = "https://a.test"
        image_base_url = "https://img.test"
        video_base_url = ""
        timeout_seconds = 60
        max_retries = 1
        extra_config = {"region": "us"}

    text_config = build_config(_Provider(), capability="text")
    assert text_config.api_key == "secret-value"
    assert text_config.base_url == "https://a.test"

    image_config = build_config(_Provider(), capability="image")
    assert image_config.base_url == "https://img.test", "圖片能力應使用專屬 base URL"

    video_config = build_config(_Provider(), capability="video")
    assert video_config.base_url == "https://a.test", "未設定影片 base URL 時應退回通用值"


def test_missing_key_is_configuration_error() -> None:
    """金鑰未設定時必須是設定錯誤，而非供應商錯誤。"""

    empty = ProviderConfig(kind="openai", api_key="", base_url="https://example.test/v1")

    with pytest.raises(ConfigurationError):
        OpenAICompatibleTextProvider().generate_text(empty, TextRequest(model="m", prompt="p"))


# ── OpenAI 相容：文字 ─────────────────────────────────────────────────────────


def _mock_transport(handler):
    """把 httpx 換成受控的 transport，攔截真實請求。"""

    return httpx.MockTransport(handler)


def test_text_request_response_mapping(monkeypatch) -> None:
    """請求組裝與回應解析都要正確對應 Chat Completions 規格。"""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-test",
                "choices": [{"message": {"content": "產生的文字"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 22},
            },
        )

    original_post = httpx.post
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw),
    )

    result = OpenAICompatibleTextProvider().generate_text(
        CONFIG,
        TextRequest(model="gpt-test", prompt="你好", system="你是助理", max_tokens=100, json_output=True),
    )

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["messages"][0] == {"role": "system", "content": "你是助理"}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "你好"}
    assert captured["body"]["response_format"] == {"type": "json_object"}

    assert result.text == "產生的文字"
    assert result.input_tokens == 11
    assert result.output_tokens == 22

    monkeypatch.setattr(httpx, "post", original_post)


def test_empty_choices_is_retryable_error(monkeypatch) -> None:
    """供應商回傳空結果視為可重試錯誤。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    with pytest.raises(ProviderError) as exc:
        OpenAICompatibleTextProvider().generate_text(CONFIG, TextRequest(model="m", prompt="p"))
    assert exc.value.retryable is True


@pytest.mark.parametrize(
    ("status_code", "expected", "retryable"),
    [
        (429, ProviderRateLimitError, True),
        (500, ProviderError, True),
        (400, ProviderError, False),
        (401, ProviderError, False),
    ],
)
def test_http_errors_map_to_standard_types(monkeypatch, status_code, expected, retryable) -> None:
    """HTTP 錯誤必須翻譯成標準錯誤型別，並正確標記可重試性。

    可重試性決定任務系統要不要自動重試 —— 對 4xx 重試只會浪費配額。
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="upstream detail")

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    with pytest.raises(expected) as exc:
        OpenAICompatibleTextProvider().generate_text(CONFIG, TextRequest(model="m", prompt="p"))
    assert exc.value.retryable is retryable


def test_timeout_maps_to_provider_timeout(monkeypatch) -> None:
    """逾時必須是 `ProviderTimeoutError` 且標記為可重試。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    with pytest.raises(ProviderTimeoutError) as exc:
        OpenAICompatibleTextProvider().generate_text(CONFIG, TextRequest(model="m", prompt="p"))
    assert exc.value.retryable is True


def test_non_json_response_is_error(monkeypatch) -> None:
    """非 JSON 回應必須明確報錯。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    with pytest.raises(ProviderError):
        OpenAICompatibleTextProvider().generate_text(CONFIG, TextRequest(model="m", prompt="p"))


# ── OpenAI 相容：圖片 ─────────────────────────────────────────────────────────


def test_image_b64_response_mapping(monkeypatch) -> None:
    """`b64_json` 形式的回應要解碼成位元組。"""

    payload = base64.b64encode(b"fake-png-bytes").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["size"] == "512x768"
        assert body["n"] == 2
        return httpx.Response(200, json={"data": [{"b64_json": payload}, {"b64_json": payload}]})

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    result = OpenAICompatibleImageProvider().generate_image(
        CONFIG, ImageRequest(model="img", prompt="a cat", size="512x768", count=2)
    )

    assert len(result.assets) == 2
    assert result.assets[0].data == b"fake-png-bytes"
    assert result.assets[0].width == 512
    assert result.assets[0].height == 768


def test_image_url_response_mapping(monkeypatch) -> None:
    """`url` 形式的回應要保留連結供後續下載。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"url": "https://cdn.test/a.png"}]})

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    result = OpenAICompatibleImageProvider().generate_image(CONFIG, ImageRequest(model="img", prompt="p"))

    assert result.assets[0].url == "https://cdn.test/a.png"
    assert result.assets[0].data is None


def test_image_empty_data_is_error(monkeypatch) -> None:
    """沒有回傳圖片必須報錯，不可回傳空結果讓下游誤判成功。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(handler)).post(url, **kw)
    )

    with pytest.raises(ProviderError):
        OpenAICompatibleImageProvider().generate_image(CONFIG, ImageRequest(model="img", prompt="p"))


# ── OpenAI 相容：影片（建立 + 輪詢）──────────────────────────────────────────


def test_video_polls_until_complete(monkeypatch) -> None:
    """影片生成要輪詢到完成才回傳。"""

    poll_count = {"n": 0}

    def post_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["seconds"] == 8
        assert body["size"] == "9:16"
        assert "input_reference" in body, "首幀應被帶入作為視覺錨點"
        return httpx.Response(200, json={"id": "job-1", "status": "queued"})

    def get_handler(_request: httpx.Request) -> httpx.Response:
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return httpx.Response(200, json={"id": "job-1", "status": "processing"})
        return httpx.Response(
            200, json={"id": "job-1", "status": "completed", "url": "https://cdn.test/v.mp4"}
        )

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(post_handler)).post(url, **kw)
    )
    monkeypatch.setattr(
        httpx, "get", lambda url, **kw: httpx.Client(transport=_mock_transport(get_handler)).get(url, **kw)
    )

    provider = OpenAICompatibleVideoProvider()
    provider.poll_interval = 0.01  # 測試不需要真的等

    result = provider.generate_video(
        CONFIG,
        VideoRequest(model="vid", prompt="p", duration_seconds=8, ratio="9:16", first_frame=b"frame"),
    )

    assert poll_count["n"] >= 2, "應持續輪詢直到完成"
    assert result.assets[0].url == "https://cdn.test/v.mp4"
    assert result.assets[0].duration_seconds == 8


def test_video_failure_status_is_not_retryable(monkeypatch) -> None:
    """供應商回報失敗屬於內容問題，重試無意義。"""

    def post_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job-2", "status": "failed", "error": "content policy"})

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(post_handler)).post(url, **kw)
    )

    provider = OpenAICompatibleVideoProvider()
    provider.poll_interval = 0.01

    with pytest.raises(ProviderError) as exc:
        provider.generate_video(CONFIG, VideoRequest(model="vid", prompt="p"))
    assert exc.value.retryable is False


def test_video_missing_job_id_is_error(monkeypatch) -> None:
    """未回傳作業 ID 時無法輪詢，必須立即報錯。"""

    def post_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "queued"})

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: httpx.Client(transport=_mock_transport(post_handler)).post(url, **kw)
    )

    with pytest.raises(ProviderError):
        OpenAICompatibleVideoProvider().generate_video(CONFIG, VideoRequest(model="vid", prompt="p"))


# ── JSON 萃取 ─────────────────────────────────────────────────────────────────


def test_extract_json_handles_plain_json() -> None:
    """純 JSON 直接解析。"""

    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_code_fence() -> None:
    """模型常見的 ```json 圍欄要能處理。"""

    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('```\n[1, 2]\n```') == [1, 2]


def test_extract_json_handles_surrounding_prose() -> None:
    """模型在 JSON 前後加說明文字時仍要能萃取。"""

    text = '好的，以下是結果：\n{"shots": [{"title": "開場"}]}\n希望有幫助。'
    assert extract_json(text) == {"shots": [{"title": "開場"}]}


def test_extract_json_raises_on_unparseable() -> None:
    """完全無法解析時必須報錯，不可回傳空結果冒充成功。"""

    with pytest.raises(ProviderError):
        extract_json("這裡完全沒有 JSON")


# ── Anthropic ─────────────────────────────────────────────────────────────────


def test_anthropic_requires_key() -> None:
    """未設定金鑰時必須是設定錯誤。"""

    with pytest.raises(ConfigurationError):
        AnthropicTextProvider().generate_text(
            ProviderConfig(kind="anthropic", api_key=""), TextRequest(model="m", prompt="p")
        )


def test_anthropic_maps_response(monkeypatch) -> None:
    """Anthropic 回應要正確映射到標準結果。"""

    class _Block:
        type = "text"
        text = "回應內容"

    class _Usage:
        input_tokens = 5
        output_tokens = 9

    class _Message:
        content = [_Block()]
        model = "claude-test"
        usage = _Usage()
        stop_reason = "end_turn"

    class _Messages:
        def create(self, **kwargs):
            assert kwargs["model"] == "claude-test"
            assert kwargs["messages"][0]["content"] == "提示"
            return _Message()

    class _Client:
        def __init__(self, **_kw):
            self.messages = _Messages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _Client)

    result = AnthropicTextProvider().generate_text(
        ProviderConfig(kind="anthropic", api_key="k"),
        TextRequest(model="claude-test", prompt="提示"),
    )

    assert result.text == "回應內容"
    assert result.input_tokens == 5
    assert result.output_tokens == 9

"""Anthropic 文字生成 Adapter。

真實呼叫 Anthropic Messages API。所有 SDK 例外都在此翻譯成
`studio.core.errors` 的標準錯誤，讓上層不必認識任何供應商 SDK。

金鑰只從 `ProviderConfig.api_key` 取得，該值由呼叫端從環境變數解析。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from studio.contracts.generation import ProviderConfig, TextProvider, TextRequest, TextResult
from studio.core.errors import (
    ConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

logger = logging.getLogger("studio.integrations.anthropic")

DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicTextProvider(TextProvider):
    """以 Anthropic Messages API 實作文字生成。"""

    def generate_text(self, config: ProviderConfig, request: TextRequest) -> TextResult:
        """呼叫 Anthropic 產生文字。

        Raises:
            ConfigurationError: 未設定金鑰。
            ProviderRateLimitError / ProviderTimeoutError / ProviderError: 供應商端失敗。
        """

        if not config.api_key:
            raise ConfigurationError("Anthropic 供應商未設定 API 金鑰（請確認對應的環境變數已設定）")

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - anthropic 為必要相依
            raise ConfigurationError("anthropic 套件未安裝") from exc

        client = anthropic.Anthropic(
            api_key=config.api_key,
            base_url=config.base_url or DEFAULT_BASE_URL,
            timeout=float(config.timeout_seconds),
            max_retries=config.max_retries,
        )

        system = request.system
        if request.json_output:
            # 要求純 JSON 輸出；模型仍可能加上說明文字，因此下方另有萃取邏輯。
            system = (system + "\n\n只輸出合法 JSON，不要加上任何說明文字或程式碼區塊標記。").strip()

        try:
            message = client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=system or anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": request.prompt}],
            )
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitError(str(exc), provider="anthropic") from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(str(exc), provider="anthropic") from exc
        except anthropic.APIStatusError as exc:
            # 5xx 通常是暫時性的，值得重試；4xx 多半是請求本身有問題。
            retryable = exc.status_code >= 500
            raise ProviderError(
                f"Anthropic API 錯誤（HTTP {exc.status_code}）：{exc}",
                provider="anthropic",
                retryable=retryable,
            ) from exc
        except anthropic.APIError as exc:
            raise ProviderError(str(exc), provider="anthropic", retryable=True) from exc

        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")

        return TextResult(
            text=text,
            model=message.model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            raw={"stop_reason": message.stop_reason},
        )


def extract_json(text: str) -> Any:
    """從模型輸出中萃取 JSON。

    模型常會在 JSON 前後加上說明文字或 ```json 圍欄，直接 `json.loads`
    會失敗。這裡依序嘗試：直接解析 → 去除圍欄 → 取第一個完整的物件／陣列。

    Raises:
        ProviderError: 完全無法解析時拋出，附上原始輸出開頭以利診斷。
    """

    cleaned = text.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            cleaned = fenced.group(1)

    for opening, closing in (("{", "}"), ("[", "]")):
        start = cleaned.find(opening)
        end = cleaned.rfind(closing)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ProviderError(
        f"模型輸出無法解析為 JSON：{text[:200]}",
        provider="anthropic",
        retryable=True,
    )

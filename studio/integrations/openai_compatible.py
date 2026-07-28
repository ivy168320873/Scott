"""OpenAI 相容 Adapter：文字、圖片、影片。

「OpenAI 相容」涵蓋 OpenAI 本身與大量遵循同一組 REST 規格的服務
（Azure OpenAI、各家自架推論服務、多數國內供應商），因此一份實作
就能接上多個廠商 —— 只需要換 `base_url` 與模型名稱。

以 httpx 直接呼叫 REST，不綁定 openai SDK：SDK 版本演進頻繁，
而這裡用到的端點規格相對穩定。
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

from studio.contracts.generation import (
    GeneratedAsset,
    ImageProvider,
    ImageRequest,
    ImageResult,
    ProviderConfig,
    TextProvider,
    TextRequest,
    TextResult,
    VideoProvider,
    VideoRequest,
    VideoResult,
)
from studio.core.errors import (
    ConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

logger = logging.getLogger("studio.integrations.openai_compatible")

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _require_key(config: ProviderConfig) -> str:
    """取出金鑰，未設定時拋出設定錯誤。"""

    if not config.api_key:
        raise ConfigurationError("供應商未設定 API 金鑰（請確認對應的環境變數已設定）")
    return config.api_key


def _base_url(config: ProviderConfig) -> str:
    """取得 base URL。"""

    return (config.base_url or DEFAULT_BASE_URL).rstrip("/")


def _post(config: ProviderConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """發出 POST 並把 HTTP 錯誤翻譯成標準錯誤型別。"""

    url = f"{_base_url(config)}{path}"
    headers = {"Authorization": f"Bearer {_require_key(config)}", "Content-Type": "application/json"}

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=float(config.timeout_seconds))
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(str(exc), provider="openai_compatible") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(str(exc), provider="openai_compatible", retryable=True) from exc

    return _handle_response(response)


def _get(config: ProviderConfig, path: str) -> dict[str, Any]:
    """發出 GET 並翻譯錯誤（供影片生成輪詢使用）。"""

    url = f"{_base_url(config)}{path}"
    headers = {"Authorization": f"Bearer {_require_key(config)}"}

    try:
        response = httpx.get(url, headers=headers, timeout=float(config.timeout_seconds))
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(str(exc), provider="openai_compatible") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(str(exc), provider="openai_compatible", retryable=True) from exc

    return _handle_response(response)


def _handle_response(response: httpx.Response) -> dict[str, Any]:
    """把 HTTP 回應轉為資料或標準錯誤。"""

    if response.status_code == 429:
        raise ProviderRateLimitError("供應商限流", provider="openai_compatible")

    if response.status_code >= 400:
        detail = response.text[:500]
        raise ProviderError(
            f"供應商回應 HTTP {response.status_code}：{detail}",
            provider="openai_compatible",
            retryable=response.status_code >= 500,
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("供應商回應非 JSON", provider="openai_compatible", retryable=True) from exc


class OpenAICompatibleTextProvider(TextProvider):
    """以 Chat Completions 規格實作文字生成。"""

    def generate_text(self, config: ProviderConfig, request: TextRequest) -> TextResult:
        """呼叫 `/chat/completions`。"""

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.json_output:
            payload["response_format"] = {"type": "json_object"}
        payload.update(request.params)

        data = _post(config, "/chat/completions", payload)

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("供應商未回傳任何結果", provider="openai_compatible", retryable=True)

        usage = data.get("usage") or {}
        return TextResult(
            text=(choices[0].get("message") or {}).get("content", "") or "",
            model=data.get("model", request.model),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            raw={"finish_reason": choices[0].get("finish_reason", "")},
        )


class OpenAICompatibleImageProvider(ImageProvider):
    """以 Images 規格實作圖片生成。"""

    def generate_image(self, config: ProviderConfig, request: ImageRequest) -> ImageResult:
        """呼叫 `/images/generations`。

        同時支援兩種回傳形式：`b64_json`（直接是位元組）與 `url`
        （暫時性連結，由呼叫端負責下載後存入物件儲存）。
        """

        payload: dict[str, Any] = {
            "model": request.model,
            "prompt": request.prompt,
            "n": request.count,
            "size": request.size,
        }
        if request.seed:
            payload["seed"] = request.seed
        payload.update(request.params)

        data = _post(config, "/images/generations", payload)

        items = data.get("data") or []
        if not items:
            raise ProviderError("供應商未回傳任何圖片", provider="openai_compatible", retryable=True)

        width, height = _parse_size(request.size)
        assets: list[GeneratedAsset] = []
        for item in items:
            encoded = item.get("b64_json")
            assets.append(
                GeneratedAsset(
                    data=base64.b64decode(encoded) if encoded else None,
                    url=item.get("url", "") or "",
                    content_type="image/png",
                    width=width,
                    height=height,
                    seed=request.seed,
                    metadata={"revised_prompt": item.get("revised_prompt", "")},
                )
            )

        return ImageResult(assets=assets, model=data.get("model", request.model), raw={})


class OpenAICompatibleVideoProvider(VideoProvider):
    """以「建立任務 + 輪詢」規格實作影片生成。

    多數影片供應商採用非同步模式：先建立作業，再輪詢直到完成。
    本實作在內部完成輪詢，回傳時產物已就緒。
    """

    #: 輪詢間隔（秒）。影片生成通常以分鐘計，過短的間隔只會浪費配額。
    poll_interval = 5.0

    def generate_video(self, config: ProviderConfig, request: VideoRequest) -> VideoResult:
        """呼叫 `/videos` 建立作業並輪詢至完成。"""

        payload: dict[str, Any] = {
            "model": request.model,
            "prompt": request.prompt,
            "seconds": request.duration_seconds,
            "size": request.ratio,
        }
        if request.first_frame:
            payload["input_reference"] = base64.b64encode(request.first_frame).decode()
        if request.seed:
            payload["seed"] = request.seed
        payload.update(request.params)

        created = _post(config, "/videos", payload)
        job_id = created.get("id")
        if not job_id:
            raise ProviderError("供應商未回傳作業 ID", provider="openai_compatible", retryable=True)

        deadline = time.monotonic() + config.timeout_seconds
        job = created

        while True:
            status = (job.get("status") or "").lower()

            if status in {"completed", "succeeded"}:
                break
            if status in {"failed", "error", "cancelled"}:
                raise ProviderError(
                    f"影片生成失敗：{job.get('error') or status}",
                    provider="openai_compatible",
                    retryable=False,
                )
            if time.monotonic() > deadline:
                raise ProviderTimeoutError(
                    f"影片生成逾時（{config.timeout_seconds}s），作業 {job_id} 仍在 {status}",
                    provider="openai_compatible",
                )

            time.sleep(self.poll_interval)
            job = _get(config, f"/videos/{job_id}")

        url = job.get("url") or (job.get("output") or {}).get("url") or ""
        if not url:
            raise ProviderError("影片作業完成但未提供產物 URL", provider="openai_compatible", retryable=False)

        return VideoResult(
            assets=[
                GeneratedAsset(
                    url=url,
                    content_type="video/mp4",
                    duration_seconds=request.duration_seconds,
                    seed=request.seed,
                    metadata={"job_id": job_id},
                )
            ],
            model=job.get("model", request.model),
            raw={"job_id": job_id},
        )


def _parse_size(size: str) -> tuple[int, int]:
    """把 `1024x1024` 解析為寬高；無法解析時回傳 (0, 0)。"""

    try:
        width, height = size.lower().split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 0, 0

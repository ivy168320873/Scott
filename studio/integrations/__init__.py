"""供應商 Adapter 註冊表。

依 `ProviderKind` 解析出對應的 Adapter。業務層只透過這裡取得 Provider，
不直接匯入任何具體實作。
"""

from __future__ import annotations

from studio.contracts.generation import ImageProvider, ProviderConfig, TextProvider, VideoProvider
from studio.core.errors import ConfigurationError
from studio.models.types import ProviderKind


def get_text_provider(kind: ProviderKind) -> TextProvider:
    """取得文字生成 Adapter。"""

    from studio.integrations.anthropic_provider import AnthropicTextProvider
    from studio.integrations.openai_compatible import OpenAICompatibleTextProvider

    if kind is ProviderKind.anthropic:
        return AnthropicTextProvider()
    if kind in (ProviderKind.openai, ProviderKind.openai_compatible, ProviderKind.gemini):
        # Gemini 亦提供 OpenAI 相容端點，因此共用同一個 Adapter。
        return OpenAICompatibleTextProvider()

    raise ConfigurationError(f"不支援的文字供應商類型：{kind.value}")


def get_image_provider(kind: ProviderKind) -> ImageProvider:
    """取得圖片生成 Adapter。"""

    from studio.integrations.openai_compatible import OpenAICompatibleImageProvider

    if kind in (ProviderKind.openai, ProviderKind.openai_compatible, ProviderKind.gemini):
        return OpenAICompatibleImageProvider()

    raise ConfigurationError(f"不支援的圖片供應商類型：{kind.value}")


def get_video_provider(kind: ProviderKind) -> VideoProvider:
    """取得影片生成 Adapter。"""

    from studio.integrations.openai_compatible import OpenAICompatibleVideoProvider

    if kind in (ProviderKind.openai, ProviderKind.openai_compatible, ProviderKind.gemini):
        return OpenAICompatibleVideoProvider()

    raise ConfigurationError(f"不支援的影片供應商類型：{kind.value}")


def build_config(provider, *, capability: str = "text") -> ProviderConfig:
    """由資料庫的 Provider 組出執行期設定。

    金鑰在此才從環境變數解析 —— 資料庫只存變數名稱。

    Args:
        provider: `studio.models.provider.Provider` 實例。
        capability: `text` / `image` / `video`，決定使用哪個 base URL。
    """

    from studio.config import resolve_secret

    base_url = provider.base_url
    if capability == "image" and provider.image_base_url:
        base_url = provider.image_base_url
    elif capability == "video" and provider.video_base_url:
        base_url = provider.video_base_url

    return ProviderConfig(
        kind=provider.kind.value,
        api_key=resolve_secret(provider.api_key_env),
        base_url=base_url,
        timeout_seconds=provider.timeout_seconds,
        max_retries=provider.max_retries,
        extra=dict(provider.extra_config or {}),
    )


__all__ = [
    "get_text_provider",
    "get_image_provider",
    "get_video_provider",
    "build_config",
]

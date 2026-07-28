"""生成能力的共用契約。

業務邏輯只依賴這裡的介面與 DTO，**不得** 直接匯入任何供應商 SDK。
新增供應商 = 實作介面 + 註冊，不需要改動 service 或 task 層。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """執行生成任務所需的供應商設定。

    `api_key` 由呼叫端從環境變數解析後傳入（見 `studio.config.resolve_secret`），
    永遠不是從資料庫讀出來的。
    """

    kind: str
    api_key: str
    base_url: str = ""
    timeout_seconds: int = 120
    max_retries: int = 2
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TextRequest:
    """文字生成請求。"""

    model: str
    prompt: str
    system: str = ""
    max_tokens: int = 4096
    temperature: float = 1.0
    json_output: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TextResult:
    """文字生成結果。"""

    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageRequest:
    """圖片生成請求。"""

    model: str
    prompt: str
    negative_prompt: str = ""
    size: str = "1024x1024"
    count: int = 1
    seed: int = 0
    reference_images: list[bytes] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GeneratedAsset:
    """單一生成產物。

    `data` 與 `url` 至少要有一個：部分供應商直接回傳位元組，
    部分回傳暫時性 URL（需另行下載後存入物件儲存）。
    """

    data: bytes | None = None
    url: str = ""
    content_type: str = ""
    width: int = 0
    height: int = 0
    duration_seconds: int = 0
    seed: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageResult:
    """圖片生成結果。"""

    assets: list[GeneratedAsset] = field(default_factory=list)
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VideoRequest:
    """影片生成請求。"""

    model: str
    prompt: str
    duration_seconds: int = 5
    ratio: str = "9:16"
    first_frame: bytes | None = None
    last_frame: bytes | None = None
    seed: int = 0
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VideoResult:
    """影片生成結果。"""

    assets: list[GeneratedAsset] = field(default_factory=list)
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class TextProvider(ABC):
    """文字生成供應商介面。"""

    @abstractmethod
    def generate_text(self, config: ProviderConfig, request: TextRequest) -> TextResult:
        """執行文字生成。

        Raises:
            ProviderError: 任何供應商端失敗都必須翻譯成此型別。
        """


class ImageProvider(ABC):
    """圖片生成供應商介面。"""

    @abstractmethod
    def generate_image(self, config: ProviderConfig, request: ImageRequest) -> ImageResult:
        """執行圖片生成。"""


class VideoProvider(ABC):
    """影片生成供應商介面。"""

    @abstractmethod
    def generate_video(self, config: ProviderConfig, request: VideoRequest) -> VideoResult:
        """執行影片生成。

        影片生成通常是長時間的非同步作業，實作應在內部完成輪詢，
        並在回傳前確保產物已就緒。
        """

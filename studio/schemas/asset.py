"""資產（角色／演員／場景／道具／服裝）與媒體 schema。

五類資產共用大量欄位，因此以基底類別共享；差異欄位各自宣告，
讓 OpenAPI 產生的前端型別仍能區分不同資產。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from studio.models.types import AssetKind, AssetViewAngle, FileType, FileUsageKind


class _AssetBaseFields(BaseModel):
    """資產共用可寫欄位。"""

    name: str = Field(min_length=1, max_length=255, description="資產名稱")
    description: str = Field(default="", description="描述")
    appearance_prompt: str = Field(
        default="",
        description="外觀提示詞；生成時附加於鏡頭提示詞以維持一致性",
    )
    attributes: dict[str, Any] = Field(default_factory=dict, description="結構化屬性")


class _AssetReadFields(BaseModel):
    """資產共用唯讀欄位。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="資產 ID")
    name: str = Field(description="資產名稱")
    description: str = Field(description="描述")
    appearance_prompt: str = Field(description="外觀提示詞")
    attributes: dict[str, Any] = Field(description="結構化屬性")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


# ── 角色 ──────────────────────────────────────────────────────────────────────


class CharacterCreate(_AssetBaseFields):
    """建立角色。"""

    actor_id: str | None = Field(default=None, description="綁定的演員 ID")
    role_type: str = Field(default="", max_length=32, description="角色定位")
    personality: str = Field(default="", description="性格描述")


class CharacterUpdate(BaseModel):
    """更新角色；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    appearance_prompt: str | None = None
    attributes: dict[str, Any] | None = None
    actor_id: str | None = None
    role_type: str | None = Field(default=None, max_length=32)
    personality: str | None = None


class CharacterRead(_AssetReadFields):
    """角色對外表示。"""

    project_id: str = Field(description="所屬專案 ID")
    actor_id: str | None = Field(description="綁定的演員 ID")
    role_type: str = Field(description="角色定位")
    personality: str = Field(description="性格描述")


# ── 演員 ──────────────────────────────────────────────────────────────────────


class ActorCreate(_AssetBaseFields):
    """建立演員。"""

    gender: str = Field(default="", max_length=16, description="性別")
    age_range: str = Field(default="", max_length=32, description="年齡區間")
    reference_file_id: str | None = Field(default=None, description="主要參考圖檔案 ID")


class ActorUpdate(BaseModel):
    """更新演員；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    appearance_prompt: str | None = None
    attributes: dict[str, Any] | None = None
    gender: str | None = Field(default=None, max_length=16)
    age_range: str | None = Field(default=None, max_length=32)
    reference_file_id: str | None = None


class ActorRead(_AssetReadFields):
    """演員對外表示。"""

    gender: str = Field(description="性別")
    age_range: str = Field(description="年齡區間")
    reference_file_id: str | None = Field(description="主要參考圖檔案 ID")


# ── 場景 ──────────────────────────────────────────────────────────────────────


class SceneCreate(_AssetBaseFields):
    """建立場景。"""

    location_type: str = Field(default="", max_length=32, description="場地類型（室內／室外）")
    time_of_day: str = Field(default="", max_length=32, description="時間（日／夜／黃昏）")


class SceneUpdate(BaseModel):
    """更新場景；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    appearance_prompt: str | None = None
    attributes: dict[str, Any] | None = None
    location_type: str | None = Field(default=None, max_length=32)
    time_of_day: str | None = Field(default=None, max_length=32)


class SceneRead(_AssetReadFields):
    """場景對外表示。"""

    project_id: str = Field(description="所屬專案 ID")
    location_type: str = Field(description="場地類型")
    time_of_day: str = Field(description="時間")


# ── 道具 ──────────────────────────────────────────────────────────────────────


class PropCreate(_AssetBaseFields):
    """建立道具。"""

    category: str = Field(default="", max_length=64, description="道具分類")


class PropUpdate(BaseModel):
    """更新道具；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    appearance_prompt: str | None = None
    attributes: dict[str, Any] | None = None
    category: str | None = Field(default=None, max_length=64)


class PropRead(_AssetReadFields):
    """道具對外表示。"""

    project_id: str = Field(description="所屬專案 ID")
    category: str = Field(description="道具分類")


# ── 服裝 ──────────────────────────────────────────────────────────────────────


class CostumeCreate(_AssetBaseFields):
    """建立服裝。"""

    character_id: str | None = Field(default=None, description="預設穿著此服裝的角色 ID")
    season: str = Field(default="", max_length=32, description="season／場合")


class CostumeUpdate(BaseModel):
    """更新服裝；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    appearance_prompt: str | None = None
    attributes: dict[str, Any] | None = None
    character_id: str | None = None
    season: str | None = Field(default=None, max_length=32)


class CostumeRead(_AssetReadFields):
    """服裝對外表示。"""

    project_id: str = Field(description="所屬專案 ID")
    character_id: str | None = Field(description="預設角色 ID")
    season: str = Field(description="season／場合")


# ── 資產圖片 ──────────────────────────────────────────────────────────────────


class AssetImageCreate(BaseModel):
    """為資產新增參考圖。"""

    file_id: str = Field(min_length=1, max_length=64, description="圖片檔案 ID")
    view_angle: AssetViewAngle = Field(default=AssetViewAngle.front, description="視角")
    is_primary: bool = Field(default=False, description="是否為主要參考圖")
    prompt: str = Field(default="", description="產生此圖所用的提示詞")


class AssetImageRead(BaseModel):
    """資產圖片對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="資產圖片 ID")
    asset_kind: AssetKind = Field(description="資產類型")
    asset_id: str = Field(description="資產 ID")
    file_id: str = Field(description="圖片檔案 ID")
    view_angle: AssetViewAngle = Field(description="視角")
    is_primary: bool = Field(description="是否為主要參考圖")
    prompt: str = Field(description="提示詞")
    created_at: datetime = Field(description="建立時間")


# ── 媒體檔案 ──────────────────────────────────────────────────────────────────


class MediaCreate(BaseModel):
    """登記一筆媒體檔案。

    僅建立中繼資料紀錄；實際二進位內容由上傳端點寫入物件儲存。
    """

    file_type: FileType = Field(description="檔案類型")
    storage_key: str = Field(min_length=1, max_length=1024, description="物件儲存 key")
    filename: str = Field(default="", max_length=512, description="原始檔名")
    content_type: str = Field(default="", max_length=128, description="MIME type")
    size_bytes: int = Field(default=0, ge=0, description="檔案大小")
    width: int = Field(default=0, ge=0, description="影像寬度")
    height: int = Field(default=0, ge=0, description="影像高度")
    duration_seconds: int = Field(default=0, ge=0, description="影音長度")
    project_id: str | None = Field(default=None, description="所屬專案 ID")
    file_metadata: dict[str, Any] = Field(default_factory=dict, description="額外中繼資料")


class MediaUpdate(BaseModel):
    """更新媒體中繼資料；未傳入的欄位保留原值。

    刻意不允許改寫 `storage_key`：那等同把紀錄指向另一個實體檔案，
    會讓既有引用悄悄失效。需要換檔請建立新紀錄。
    """

    filename: str | None = Field(default=None, max_length=512)
    project_id: str | None = None
    file_metadata: dict[str, Any] | None = None


class MediaRead(BaseModel):
    """媒體檔案對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="檔案 ID")
    file_type: FileType = Field(description="檔案類型")
    filename: str = Field(description="原始檔名")
    content_type: str = Field(description="MIME type")
    size_bytes: int = Field(description="檔案大小")
    width: int = Field(description="影像寬度")
    height: int = Field(description="影像高度")
    duration_seconds: int = Field(description="影音長度")
    project_id: str | None = Field(description="所屬專案 ID")
    source_task_id: str | None = Field(description="產生此檔案的任務 ID")
    url: str = Field(description="可讀取的 URL；由儲存後端於執行期組出")
    file_metadata: dict[str, Any] = Field(description="額外中繼資料")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


class MediaUsageCreate(BaseModel):
    """登記媒體用途。"""

    usage_kind: FileUsageKind = Field(description="用途類型")
    owner_type: str = Field(min_length=1, max_length=32, description="擁有者類型")
    owner_id: str = Field(min_length=1, max_length=64, description="擁有者 ID")
    note: str = Field(default="", description="補充說明")


class MediaUsageRead(BaseModel):
    """媒體用途對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="用途記錄 ID")
    file_id: str = Field(description="檔案 ID")
    usage_kind: FileUsageKind = Field(description="用途類型")
    owner_type: str = Field(description="擁有者類型")
    owner_id: str = Field(description="擁有者 ID")
    note: str = Field(description="補充說明")


__all__ = [
    "CharacterCreate",
    "CharacterUpdate",
    "CharacterRead",
    "ActorCreate",
    "ActorUpdate",
    "ActorRead",
    "SceneCreate",
    "SceneUpdate",
    "SceneRead",
    "PropCreate",
    "PropUpdate",
    "PropRead",
    "CostumeCreate",
    "CostumeUpdate",
    "CostumeRead",
    "AssetImageCreate",
    "AssetImageRead",
    "MediaCreate",
    "MediaUpdate",
    "MediaRead",
    "MediaUsageCreate",
    "MediaUsageRead",
]

"""分鏡與其子資源 schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from studio.models.types import (
    AssetKind,
    CameraAngle,
    CameraMovement,
    CameraShotType,
    DialogueLineMode,
    ShotCandidateStatus,
    ShotCandidateType,
    ShotDialogueCandidateStatus,
    ShotFrameType,
    ShotStatus,
    VFXType,
)


class ShotDetailPayload(BaseModel):
    """分鏡拍攝細節（建立與更新共用）。"""

    camera_shot: CameraShotType | None = Field(default=None, description="景別")
    angle: CameraAngle | None = Field(default=None, description="機位角度")
    movement: CameraMovement | None = Field(default=None, description="運鏡方式")
    scene_id: str | None = Field(default=None, description="關聯場景 ID")
    duration_seconds: int | None = Field(default=None, ge=1, le=600, description="鏡頭時長（秒）")
    override_video_ratio: str | None = Field(default=None, max_length=16, description="影片比例覆蓋")
    description: str | None = Field(default=None, description="鏡頭整體描述")
    action_beats: list[str] | None = Field(default=None, description="動作節拍")
    mood_tags: list[str] | None = Field(default=None, description="情緒標籤")
    atmosphere: str | None = Field(default=None, description="氛圍描述")
    vfx_type: VFXType | None = Field(default=None, description="視效類型")
    vfx_note: str | None = Field(default=None, description="視效說明")
    has_bgm: bool | None = Field(default=None, description="是否包含 BGM")
    video_prompt: str | None = Field(default=None, description="影片生成提示詞")


class ShotDetailRead(BaseModel):
    """分鏡細節對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    camera_shot: CameraShotType = Field(description="景別")
    angle: CameraAngle = Field(description="機位角度")
    movement: CameraMovement = Field(description="運鏡方式")
    scene_id: str | None = Field(description="關聯場景 ID")
    duration_seconds: int = Field(description="鏡頭時長（秒）")
    override_video_ratio: str | None = Field(description="影片比例覆蓋")
    description: str = Field(description="鏡頭整體描述")
    action_beats: list[str] = Field(description="動作節拍")
    mood_tags: list[str] = Field(description="情緒標籤")
    atmosphere: str = Field(description="氛圍描述")
    vfx_type: VFXType = Field(description="視效類型")
    vfx_note: str = Field(description="視效說明")
    has_bgm: bool = Field(description="是否包含 BGM")
    video_prompt: str = Field(description="影片生成提示詞")


class ShotCreate(BaseModel):
    """建立分鏡；`index` 留空時自動指派為章節內下一個序號。"""

    title: str = Field(default="", max_length=255, description="鏡頭標題")
    index: int | None = Field(default=None, ge=1, description="鏡頭序號；留空自動指派")
    script_excerpt: str = Field(default="", description="對應的腳本段落")
    detail: ShotDetailPayload | None = Field(default=None, description="拍攝細節")


class ShotUpdate(BaseModel):
    """更新分鏡；未傳入的欄位保留原值。

    刻意不開放直接寫入 `status`：分鏡狀態由提取確認進度推導，
    請改用 `POST /shots/{id}/recompute-status`，避免狀態與實際資料脫節。
    """

    title: str | None = Field(default=None, max_length=255)
    index: int | None = Field(default=None, ge=1)
    script_excerpt: str | None = None
    skip_extraction: bool | None = Field(default=None, description="是否明確跳過提取")
    detail: ShotDetailPayload | None = None


class ShotRead(BaseModel):
    """分鏡對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="分鏡 ID")
    chapter_id: str = Field(description="所屬章節 ID")
    index: int = Field(description="鏡頭序號")
    title: str = Field(description="鏡頭標題")
    script_excerpt: str = Field(description="對應的腳本段落")
    status: ShotStatus = Field(description="資訊提取確認狀態（非執行時狀態）")
    skip_extraction: bool = Field(description="是否跳過提取")
    last_extracted_at: datetime | None = Field(description="最近一次完成提取的時間")
    thumbnail_file_id: str | None = Field(description="縮圖檔案 ID")
    generated_video_file_id: str | None = Field(description="已採用的成品影片檔案 ID")
    detail: ShotDetailRead | None = Field(default=None, description="拍攝細節")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


class ShotReadiness(BaseModel):
    """分鏡準備度。

    刻意把三種狀態分開回報，避免前端把「已確認」誤當成「可以生成影片」：
    - `status`：資訊提取確認狀態
    - `video_ready`：是否具備影片生成條件
    """

    shot_id: str = Field(description="分鏡 ID")
    status: ShotStatus = Field(description="資訊提取確認狀態")
    pending_candidate_count: int = Field(description="尚未確認的資產候選數")
    pending_dialogue_count: int = Field(description="尚未確認的對白候選數")
    video_ready: bool = Field(description="是否具備影片生成條件")
    blocking_reasons: list[str] = Field(description="無法生成影片的原因；為空表示就緒")


# ── 關鍵幀 ────────────────────────────────────────────────────────────────────


class ShotFrameCreate(BaseModel):
    """建立關鍵幀。"""

    frame_type: ShotFrameType = Field(description="幀類型")
    index: int = Field(default=0, ge=0, description="同類型內的序號")
    prompt: str = Field(default="", description="該幀的圖片生成提示詞")
    file_id: str | None = Field(default=None, description="已採用的圖片檔案 ID")


class ShotFrameUpdate(BaseModel):
    """更新關鍵幀；未傳入的欄位保留原值。"""

    prompt: str | None = None
    file_id: str | None = None


class ShotFrameRead(BaseModel):
    """關鍵幀對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="幀 ID")
    shot_id: str = Field(description="所屬分鏡 ID")
    frame_type: ShotFrameType = Field(description="幀類型")
    index: int = Field(description="序號")
    prompt: str = Field(description="圖片生成提示詞")
    file_id: str | None = Field(description="圖片檔案 ID")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


# ── 對白 ──────────────────────────────────────────────────────────────────────


class ShotDialogueCreate(BaseModel):
    """建立對白。"""

    content: str = Field(description="對白內容")
    index: int | None = Field(default=None, ge=0, description="順序；留空自動附加於最後")
    character_id: str | None = Field(default=None, description="說話角色 ID")
    speaker_name: str = Field(default="", max_length=255, description="說話者名稱")
    mode: DialogueLineMode = Field(default=DialogueLineMode.dialogue, description="對白模式")
    emotion: str = Field(default="", max_length=64, description="情緒提示")


class ShotDialogueUpdate(BaseModel):
    """更新對白；未傳入的欄位保留原值。"""

    content: str | None = None
    character_id: str | None = None
    speaker_name: str | None = Field(default=None, max_length=255)
    mode: DialogueLineMode | None = None
    emotion: str | None = Field(default=None, max_length=64)


class ShotDialogueRead(BaseModel):
    """對白對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="對白 ID")
    shot_id: str = Field(description="所屬分鏡 ID")
    index: int = Field(description="順序")
    character_id: str | None = Field(description="說話角色 ID")
    speaker_name: str = Field(description="說話者名稱")
    content: str = Field(description="對白內容")
    mode: DialogueLineMode = Field(description="對白模式")
    emotion: str = Field(description="情緒提示")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


# ── 提取候選 ──────────────────────────────────────────────────────────────────


class ShotCandidateRead(BaseModel):
    """資產提取候選對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="候選 ID")
    shot_id: str = Field(description="所屬分鏡 ID")
    candidate_type: ShotCandidateType = Field(description="候選資產類型")
    name: str = Field(description="提取出的名稱")
    description: str = Field(description="提取出的描述")
    status: ShotCandidateStatus = Field(description="確認狀態")
    linked_entity_id: str | None = Field(description="確認後連結到的資產 ID")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


class CandidateResolve(BaseModel):
    """確認或忽略一筆資產候選。"""

    action: str = Field(description="`link` 連結到既有資產，或 `ignore` 忽略", pattern="^(link|ignore)$")
    linked_entity_id: str | None = Field(default=None, description="action=link 時必填：要連結的資產 ID")


class ShotDialogueCandidateRead(BaseModel):
    """對白提取候選對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="對白候選 ID")
    shot_id: str = Field(description="所屬分鏡 ID")
    index: int = Field(description="順序")
    speaker_name: str = Field(description="提取出的說話者")
    content: str = Field(description="提取出的對白內容")
    mode: DialogueLineMode = Field(description="對白模式")
    status: ShotDialogueCandidateStatus = Field(description="確認狀態")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


class DialogueCandidateResolve(BaseModel):
    """接受或忽略一筆對白候選。"""

    action: str = Field(description="`accept` 接受並轉為正式對白，或 `ignore` 忽略", pattern="^(accept|ignore)$")
    character_id: str | None = Field(default=None, description="action=accept 時可指定說話角色")


# ── 分鏡資產關聯 ──────────────────────────────────────────────────────────────


class ShotAssetLinkCreate(BaseModel):
    """建立分鏡↔資產關聯。"""

    asset_kind: AssetKind = Field(description="資產類型")
    asset_id: str = Field(min_length=1, max_length=64, description="資產 ID")
    index: int = Field(default=0, ge=0, description="出場順序")
    note: str = Field(default="", description="此鏡頭中的特殊說明")


class ShotAssetLinkRead(BaseModel):
    """分鏡↔資產關聯對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="關聯行 ID")
    shot_id: str = Field(description="分鏡 ID")
    asset_kind: AssetKind = Field(description="資產類型")
    asset_id: str = Field(description="資產 ID")
    asset_name: str = Field(default="", description="資產名稱（便於前端顯示）")
    index: int = Field(description="出場順序")
    note: str = Field(description="特殊說明")


__all__ = [
    "ShotCreate",
    "ShotUpdate",
    "ShotRead",
    "ShotReadiness",
    "ShotDetailPayload",
    "ShotDetailRead",
    "ShotFrameCreate",
    "ShotFrameUpdate",
    "ShotFrameRead",
    "ShotDialogueCreate",
    "ShotDialogueUpdate",
    "ShotDialogueRead",
    "ShotCandidateRead",
    "CandidateResolve",
    "ShotDialogueCandidateRead",
    "DialogueCandidateResolve",
    "ShotAssetLinkCreate",
    "ShotAssetLinkRead",
]

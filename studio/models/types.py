"""Studio 領域列舉型別。

以字串值存入資料庫（而非整數），讓資料庫內容可直接閱讀，也讓新增值不需要
資料遷移重編號。

狀態語意約定（重要）：
- `ShotStatus` 只表示「資訊提取確認狀態」，只有 `pending` / `ready` 兩個值。
- 執行時狀態（生成中、失敗、取消）一律來自任務系統（`GenerationTask`），
  絕不寫回 `Shot`。因此本列舉刻意不提供 `generating`。
- 「是否具備影片生成條件」由 video-readiness 另行計算，與 `ShotStatus` 分離：
  `status == ready` 不等於可以立即生成影片。
"""

from __future__ import annotations

from enum import Enum


class ProjectVisualStyle(str, Enum):
    """畫面表現形式：用於區分真人與動漫等。"""

    live_action = "live_action"
    anime = "anime"
    mixed = "mixed"


class ProjectStatus(str, Enum):
    """專案生產狀態。"""

    draft = "draft"
    active = "active"
    archived = "archived"


class ChapterStatus(str, Enum):
    """章節生產狀態。"""

    draft = "draft"
    shooting = "shooting"
    done = "done"


class ShotStatus(str, Enum):
    """鏡頭資訊提取確認狀態。

    - `pending`：仍有提取確認工作未完成。
    - `ready`：已完成提取確認，可進入生成準備。

    刻意不含 `generating`：執行時狀態屬於任務系統的職責。
    """

    pending = "pending"
    ready = "ready"


class ShotCandidateType(str, Enum):
    """鏡頭提取候選的資產類型。"""

    character = "character"
    scene = "scene"
    prop = "prop"
    costume = "costume"


class ShotCandidateStatus(str, Enum):
    """資產候選確認狀態。"""

    pending = "pending"
    linked = "linked"
    ignored = "ignored"


class ShotDialogueCandidateStatus(str, Enum):
    """對白候選確認狀態。"""

    pending = "pending"
    accepted = "accepted"
    ignored = "ignored"


class CameraShotType(str, Enum):
    """景別。"""

    ecu = "ECU"  # 大特寫
    cu = "CU"  # 特寫
    mcu = "MCU"  # 中近景
    ms = "MS"  # 中景
    mls = "MLS"  # 中遠景
    ls = "LS"  # 遠景
    els = "ELS"  # 大遠景


class CameraAngle(str, Enum):
    """機位角度。"""

    eye_level = "EYE_LEVEL"
    high_angle = "HIGH_ANGLE"
    low_angle = "LOW_ANGLE"
    bird_eye = "BIRD_EYE"
    dutch = "DUTCH"
    over_shoulder = "OVER_SHOULDER"


class CameraMovement(str, Enum):
    """運鏡方式。"""

    static = "STATIC"
    pan = "PAN"
    tilt = "TILT"
    dolly_in = "DOLLY_IN"
    dolly_out = "DOLLY_OUT"
    track = "TRACK"
    crane = "CRANE"
    handheld = "HANDHELD"
    steadicam = "STEADICAM"
    zoom_in = "ZOOM_IN"
    zoom_out = "ZOOM_OUT"


class VFXType(str, Enum):
    """視效類型。"""

    none = "NONE"
    particles = "PARTICLES"
    volumetric_fog = "VOLUMETRIC_FOG"
    cg_double = "CG_DOUBLE"
    digital_environment = "DIGITAL_ENVIRONMENT"
    matte_painting = "MATTE_PAINTING"
    fire_smoke = "FIRE_SMOKE"
    water_sim = "WATER_SIM"
    destruction = "DESTRUCTION"
    energy_magic = "ENERGY_MAGIC"
    compositing_cleanup = "COMPOSITING_CLEANUP"
    slow_motion_time = "SLOW_MOTION_TIME"
    other = "OTHER"


class DialogueLineMode(str, Enum):
    """對白模式。"""

    dialogue = "DIALOGUE"  # 對白
    voice_over = "VOICE_OVER"  # 旁白
    off_screen = "OFF_SCREEN"  # 畫外音
    phone = "PHONE"  # 電話聲


class ShotFrameType(str, Enum):
    """分鏡幀類型。"""

    first = "first"
    last = "last"
    key = "key"


class AssetKind(str, Enum):
    """資產類型。

    角色與演員分開：角色是劇本中的人物，演員是實際呈現該角色的視覺形象；
    同一角色在不同專案可綁定不同演員，藉此維持跨鏡頭的外觀一致性。
    """

    character = "character"
    actor = "actor"
    scene = "scene"
    prop = "prop"
    costume = "costume"


class AssetViewAngle(str, Enum):
    """資產圖片視角（用於多角度描述同一資產）。"""

    front = "FRONT"
    left = "LEFT"
    right = "RIGHT"
    back = "BACK"
    three_quarter = "THREE_QUARTER"
    top = "TOP"
    detail = "DETAIL"


class FileType(str, Enum):
    """檔案類型。"""

    image = "image"
    video = "video"
    audio = "audio"


class FileUsageKind(str, Enum):
    """檔案在業務鏈上的用途。"""

    shot_frame = "shot_frame"
    generated_video = "generated_video"
    asset_image = "asset_image"
    reference_image = "reference_image"
    task_output = "task_output"
    upload = "upload"


class ProviderStatus(str, Enum):
    """供應商啟用狀態。"""

    active = "active"
    testing = "testing"
    disabled = "disabled"


class ProviderKind(str, Enum):
    """供應商協定類型。

    決定使用哪一個 Adapter，與供應商品牌名稱無關 —— 任何相容 OpenAPI 規格的
    服務都可用 `openai_compatible` 接入。
    """

    anthropic = "anthropic"
    openai = "openai"
    gemini = "gemini"
    openai_compatible = "openai_compatible"


class ModelCategory(str, Enum):
    """模型類別。"""

    text = "text"
    image = "image"
    video = "video"


class PromptCategory(str, Enum):
    """提示詞模板類別。"""

    frame_first_image = "frame_first_image"
    frame_last_image = "frame_last_image"
    frame_key_image = "frame_key_image"
    video_prompt = "video_prompt"
    storyboard_prompt = "storyboard_prompt"
    character_image = "character_image"
    actor_image = "actor_image"
    scene_image = "scene_image"
    prop_image = "prop_image"
    costume_image = "costume_image"
    script_divide = "script_divide"
    script_extract = "script_extract"
    script_optimize = "script_optimize"
    script_simplify = "script_simplify"
    script_consistency = "script_consistency"


class TaskStatus(str, Enum):
    """生成任務狀態。

    終態為 `succeeded` / `failed` / `cancelled`，其餘為進行中。
    """

    pending = "pending"
    running = "running"
    streaming = "streaming"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """是否為終態（不會再變動）。"""

        return self in {TaskStatus.succeeded, TaskStatus.failed, TaskStatus.cancelled}

    @property
    def is_active(self) -> bool:
        """是否仍在進行中。"""

        return not self.is_terminal


class TaskKind(str, Enum):
    """任務業務類型，用於執行器路由。"""

    script_divide = "script_divide"
    script_extract = "script_extract"
    script_optimize = "script_optimize"
    script_simplify = "script_simplify"
    script_consistency = "script_consistency"
    asset_image_generation = "asset_image_generation"
    frame_image_generation = "frame_image_generation"
    video_generation = "video_generation"


class TaskDeliveryMode(str, Enum):
    """任務交付方式。"""

    streaming = "streaming"  # 長連接分段輸出
    async_polling = "async_polling"  # 任務 + 輪詢


class TaskLinkStatus(str, Enum):
    """任務產物的採用狀態。"""

    todo = "todo"
    accepted = "accepted"
    rejected = "rejected"

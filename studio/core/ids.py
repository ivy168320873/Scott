"""識別碼產生。

Studio 的主鍵一律使用帶前綴的字串 ID，而非自增整數：
- 前綴讓日誌與 API 回應一眼可辨識資源類型（`prj_` / `shot_` / `task_` …）。
- 字串 ID 可在 worker 端先產生再寫入，不需要等資料庫回填。
"""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """產生帶前綴的識別碼。

    Args:
        prefix: 資源前綴，例如 `prj`。

    Returns:
        形如 `prj_3f9a1c2b4d5e6f70` 的識別碼（前綴 + 16 位 hex）。
    """

    token = uuid.uuid4().hex[:16]
    return f"{prefix}_{token}" if prefix else token


# 各資源的前綴常數，避免字面值散落各處造成不一致。
PROJECT = "prj"
CHAPTER = "cha"
SHOT = "shot"
SHOT_FRAME = "frm"
SHOT_DIALOGUE = "dlg"
CANDIDATE = "cand"
DIALOGUE_CANDIDATE = "dcand"
CHARACTER = "char"
ACTOR = "actor"
SCENE = "scene"
PROP = "prop"
COSTUME = "cost"
ASSET_IMAGE = "aimg"
FILE = "file"
FILE_USAGE = "fuse"
TASK = "task"
PROVIDER = "prov"
MODEL = "model"
PROMPT_TEMPLATE = "tpl"

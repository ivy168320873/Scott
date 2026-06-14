"""對話記憶：在記憶體中保存多輪對話，並可選擇持久化到 JSON 檔案。

Claude 的 Messages API 是無狀態的——每次請求都要送出完整的對話歷史。
這個模組負責累積 ``messages`` 串列，並在程式重啟後還原先前的對話。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Memory:
    """累積對話訊息，並可選擇性地存讀到磁碟。

    每則訊息是符合 Anthropic Messages API 格式的 dict：
    ``{"role": "user"|"assistant", "content": ...}``。
    ``content`` 可以是字串，也可以是 content block 串列（工具呼叫時）。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        # 若提供 path，對話會持久化到該檔案。
        self.path = Path(path) if path else None
        self.messages: list[dict[str, Any]] = []
        if self.path and self.path.exists():
            self.load()

    def add_user(self, content: Any) -> None:
        """加入一則使用者訊息（字串或 content block 串列）。"""
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: Any) -> None:
        """加入一則助理訊息。

        傳入完整的 ``response.content`` 以保留 thinking 與 tool_use blocks，
        這對於延續對話與工具迴圈是必要的。
        """
        self.messages.append({"role": "assistant", "content": content})

    def clear(self) -> None:
        """清空對話歷史（並更新持久化檔案）。"""
        self.messages = []
        if self.path:
            self.save()

    def load(self) -> None:
        """從 JSON 檔案還原對話歷史。"""
        try:
            with self.path.open(encoding="utf-8") as f:
                self.messages = json.load(f)
        except (json.JSONDecodeError, OSError):
            # 檔案損毀或無法讀取時，以空白歷史開始，不讓程式中斷。
            self.messages = []

    def save(self) -> None:
        """將對話歷史寫入 JSON 檔案。"""
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=2)

    def __len__(self) -> int:
        return len(self.messages)

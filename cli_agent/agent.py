"""Agent：呼叫 Claude API，處理對話與工具迴圈。

採用手動 agentic loop（而非 SDK tool runner），方便在執行 shell
之類有副作用的工具前加入確認機制，並逐字串流輸出回應。
"""

from __future__ import annotations

import sys
from typing import Callable

import anthropic

from memory import Memory
from tools import TOOL_SCHEMAS, execute_tool

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = (
    "你是一個在終端機中執行的命令列助理，同時也是投資分析助手。"
    "你可以讀檔、寫檔、執行 shell 命令來協助軟體工程任務；"
    "也可以查股價、研判大盤、跑策略回測來協助投資分析。"
    "回答精簡、直接；需要採取行動時就使用工具。"
    "執行有副作用的命令前，先簡短說明你要做什麼。"
    "提供投資相關資訊時，若資料為示範資料要明確告知，並提醒這不構成投資建議。"
)


class Agent:
    """包裝 Anthropic client、對話記憶與工具迴圈。"""

    def __init__(
        self,
        memory: Memory,
        model: str = DEFAULT_MODEL,
        effort: str = "high",
        confirm_shell: Callable[[str], bool] | None = None,
    ) -> None:
        # 不帶 api_key：SDK 會自動從 ANTHROPIC_API_KEY 環境變數解析。
        self.client = anthropic.Anthropic()
        self.memory = memory
        self.model = model
        self.effort = effort
        # 執行 shell 前的確認回呼；回傳 False 則略過該命令。
        self.confirm_shell = confirm_shell

    def send(self, user_input: str) -> str:
        """送出一則使用者訊息，跑完工具迴圈，回傳最終文字。"""
        self.memory.add_user(user_input)

        final_text = ""
        # Agentic loop：持續呼叫 API，直到 Claude 不再請求工具。
        while True:
            response = self._stream_once()
            self.memory.add_assistant(response.content)

            if response.stop_reason == "tool_use":
                tool_results = self._run_tools(response)
                self.memory.add_user(tool_results)
                continue

            # end_turn（或其他終止原因）：收集文字後結束。
            final_text = "".join(
                b.text for b in response.content if b.type == "text"
            )
            break

        self.memory.save()
        return final_text

    def _stream_once(self):
        """對 API 做一次串流請求，邊收邊印，回傳完整 Message。"""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            tools=TOOL_SCHEMAS,
            messages=self.memory.messages,
        ) as stream:
            for event in stream:
                if event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        print(
                            f"\n[工具] {event.content_block.name} ...",
                            flush=True,
                        )
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        print(event.delta.text, end="", flush=True)
            print()  # 串流結束後換行
            return stream.get_final_message()

    def _run_tools(self, response) -> list[dict]:
        """執行回應中所有 tool_use block，回傳 tool_result block 串列。"""
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            output, is_error = self._dispatch_tool(block.name, block.input)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                }
            )
        return results

    def _dispatch_tool(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """執行單一工具，必要時先請使用者確認。回傳 (輸出, 是否錯誤)。"""
        # 對有副作用的 shell 命令做確認把關。
        if name == "run_shell" and self.confirm_shell is not None:
            command = tool_input.get("command", "")
            if not self.confirm_shell(command):
                return "使用者已取消這個命令。", True

        output = execute_tool(name, tool_input)
        is_error = output.startswith("錯誤")
        return output, is_error

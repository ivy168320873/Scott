"""網頁版 Agent：非串流的對話 + 工具迴圈，給手機/瀏覽器用。

CLI 版 (agent.py) 邊串流邊印到終端機；網頁版改成一次回傳完整文字，
對話歷史由前端保存、每次請求帶上來（無狀態）。

工具沿用 tools.py（核心 + 股票工具，全部開啟）。
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

import tools

MODEL = os.environ.get("CLI_AGENT_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("CLI_AGENT_EFFORT", "medium")
MAX_ITERS = 8  # 工具迴圈上限，避免失控

SYSTEM_PROMPT = (
    "你是一個透過手機/網頁使用的 AI 助理，同時也是投資分析助手。"
    "你可以讀檔、寫檔、執行 shell 命令；也可以查股價、研判大盤、"
    "計算技術指標訊號、比較多檔股票、掃描清單找標的、跑回測、"
    "比較所有策略找最佳、驗證策略穩健度、依大盤切換策略、給完整進出場建議。"
    "回答精簡、直接，適合在手機上閱讀。"
    "提供投資相關資訊時，若為示範資料要明確告知，並提醒這不構成投資建議。"
)


def run_turn(history: list[dict], user_message: str) -> dict:
    """跑完一輪對話（含工具迴圈），回傳 {reply, messages}。

    history：先前的完整訊息陣列（含 tool_use / tool_result blocks）。
    回傳的 messages 是更新後的完整陣列，前端應存起來下次帶回。
    """
    client = anthropic.Anthropic()
    messages: list[dict[str, Any]] = list(history)
    messages.append({"role": "user", "content": user_message})

    for _ in range(MAX_ITERS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            tools=tools.TOOL_SCHEMAS,
            messages=messages,
        )

        # 保留完整 content（含 thinking / tool_use），序列化成可 JSON 的 dict。
        messages.append(
            {"role": "assistant", "content": [b.model_dump() for b in response.content]}
        )

        if response.stop_reason == "tool_use":
            results = []
            for block in response.content:
                if block.type == "tool_use":
                    output = tools.execute_tool(block.name, block.input)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                            "is_error": output.startswith("錯誤"),
                        }
                    )
            messages.append({"role": "user", "content": results})
            continue

        reply = "".join(b.text for b in response.content if b.type == "text")
        return {"reply": reply, "messages": messages}

    return {
        "reply": "（已達工具呼叫上限，請換個問法或重試）",
        "messages": messages,
    }

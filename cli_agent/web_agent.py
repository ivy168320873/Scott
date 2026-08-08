"""網頁版 Agent：非串流的對話 + 工具迴圈，給手機/瀏覽器用。

CLI 版 (agent.py) 邊串流邊印到終端機；網頁版改成一次回傳完整文字，
對話歷史由前端保存、每次請求帶上來（無狀態）。

網頁版只開放唯讀股票工具。檔案讀寫與 shell 工具只保留在需要本機確認的
CLI 版本，避免網頁提示直接控制伺服器。
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

import tools
from scott_evolution.evidence import enforce_market_evidence, tool_evidence

MODEL = os.environ.get("CLI_AGENT_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("CLI_AGENT_EFFORT", "medium")
MAX_ITERS = 6  # 工具迴圈上限，避免失控與非預期 API 費用
MAX_HISTORY_MESSAGES = 12
MAX_MESSAGE_CHARS = 2_000
MAX_TOOL_RESULT_CHARS = 20_000

SYSTEM_PROMPT = (
    "你是一個透過手機/網頁使用的唯讀投資分析助手。"
    "你不能讀寫伺服器檔案、查看環境變數或執行 shell 命令。你可以查股價、研判大盤、"
    "計算技術指標訊號、比較多檔股票、掃描清單找標的、跑回測、"
    "做動能分析（趨勢/相對強度/量能）、機構動能總評（7模組綜合決策）、"
    "AI投資委員會投票（5委員會）、共識決策（綜合4視角）、自動選股（從清單挑高信念）、"
    "產業輪動分析、強勢產業挑領頭羊、比較所有策略找最佳、"
    "驗證策略穩健度、依大盤切換策略、給完整進出場建議；"
    "還能查基本面（get_fundamentals：市值、本益比、EPS、營收、利潤率、估值），"
    "以及用蒙地卡羅模擬未來價格機率區間（montecarlo_forecast，可納入解禁/財報事件風險）。"
    "問某檔『貴不貴』、估值、本益比、EPS、營收時用 get_fundamentals；"
    "要求『預測走勢』、未來價格、目標價、上漲機率時用 montecarlo_forecast，以機率分佈回應、"
    "不保證單一價格。"
    "做綜合判斷時優先用共識決策（full_analysis）；只在多數視角一致的高信念訊號時"
    "才偏向建議進場，視角分歧時建議觀望，並提醒嚴守停損。"
    "資料誠實原則：台股可用 tw_institutional / tw_margin 查『真實』三大法人與融資融券"
    "（TWSE 開放資料、收盤後）；美股無免費法人即時資料，系統的『機構資金流』是價量"
    "推估的代理指標，回答時要照實說明，不可把推估講成真實籌碼。"
    "回答精簡、直接，適合在手機上閱讀。"
    "任何行情、估值、預測、買賣或風險結論都必須先呼叫適合的資料工具；"
    "最後清楚列出資料依據與查詢時間。工具失敗時必須說無法驗證，不可憑印象補答案。"
    "提供投資相關資訊時，若為示範資料要明確告知，並提醒這不構成投資建議。"
)


def _text_only_history(history: list[dict]) -> list[dict]:
    """Accept only bounded user/assistant text from the untrusted browser.

    Tool-use and tool-result blocks are deliberately discarded so a client
    cannot forge a prior model tool call or make the server replay one.
    """
    cleaned: list[dict] = []
    for item in history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            continue
        text = text.strip()[:MAX_MESSAGE_CHARS]
        if text:
            cleaned.append({"role": item["role"], "content": text})
    return cleaned


def run_turn(history: list[dict], user_message: str) -> dict:
    """跑完一輪對話（含工具迴圈），回傳 {reply, messages}。

    history：先前的完整訊息陣列（含 tool_use / tool_result blocks）。
    回傳的 messages 是更新後的完整陣列，前端應存起來下次帶回。
    """
    client = anthropic.Anthropic()
    clean_history = _text_only_history(history)
    safe_message = user_message.strip()[:MAX_MESSAGE_CHARS]
    messages: list[dict[str, Any]] = list(clean_history)
    messages.append({"role": "user", "content": safe_message})
    evidence: list[dict] = []

    for _ in range(MAX_ITERS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            tools=tools.SAFE_WEB_TOOL_SCHEMAS,
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
                    output = tools.execute_web_tool(block.name, block.input)
                    output = output[:MAX_TOOL_RESULT_CHARS]
                    is_error = output.startswith("錯誤")
                    evidence.append(
                        tool_evidence(block.name, block.input, output, is_error=is_error)
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                            "is_error": is_error,
                        }
                    )
            messages.append({"role": "user", "content": results})
            continue

        reply = "".join(b.text for b in response.content if b.type == "text")
        guarded = enforce_market_evidence(safe_message, reply, evidence)
        reply = guarded["reply"]
        safe_messages = clean_history + [
            {"role": "user", "content": safe_message},
            {"role": "assistant", "content": reply},
        ]
        return {
            "reply": reply,
            "messages": safe_messages[-MAX_HISTORY_MESSAGES:],
            "evidence": guarded["evidence"],
            "evidence_guard": {
                "required": guarded["required"],
                "blocked": guarded["blocked"],
            },
        }

    guarded = enforce_market_evidence(
        safe_message,
        "（已達工具呼叫上限，請換個問法或重試）",
        evidence,
    )
    return {
        "reply": guarded["reply"],
        "messages": clean_history + [{"role": "user", "content": safe_message}],
        "evidence": guarded["evidence"],
        "evidence_guard": {
            "required": guarded["required"],
            "blocked": guarded["blocked"],
        },
    }

"""CLI 入口：接收使用者輸入，驅動 Agent 對話迴圈。

用法：
    python main.py                # 互動模式
    python main.py "你的問題"     # 單次提問後結束

互動模式指令：
    /exit, /quit   離開
    /clear         清空對話記憶
    /help          顯示說明
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from agent import Agent, DEFAULT_MODEL
from memory import Memory

MEMORY_PATH = os.path.expanduser("~/.cli_agent_memory.json")

HELP_TEXT = """\
可用指令：
  /exit, /quit   離開程式
  /clear         清空對話記憶
  /help          顯示這份說明
直接輸入文字即可與助理對話。"""


def confirm_shell(command: str) -> bool:
    """執行 shell 命令前向使用者確認。"""
    print(f"\n⚠️  助理想執行 shell 命令：\n    {command}")
    answer = input("是否執行？[y/N] ").strip().lower()
    return answer in ("y", "yes")


def build_agent() -> Agent:
    """讀取設定、建立 Agent。"""
    model = os.environ.get("CLI_AGENT_MODEL", DEFAULT_MODEL)
    effort = os.environ.get("CLI_AGENT_EFFORT", "high")
    memory = Memory(MEMORY_PATH)
    return Agent(
        memory=memory,
        model=model,
        effort=effort,
        confirm_shell=confirm_shell,
    )


def main() -> None:
    load_dotenv()  # 從 .env 載入 ANTHROPIC_API_KEY

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "錯誤：未設定 ANTHROPIC_API_KEY。\n"
            "請複製 .env.example 成 .env 並填入你的金鑰。",
            file=sys.stderr,
        )
        sys.exit(1)

    agent = build_agent()

    # 單次模式：把命令列參數當成一個問題。
    if len(sys.argv) > 1:
        agent.send(" ".join(sys.argv[1:]))
        return

    # 互動模式。
    print(f"CLI Agent（模型：{agent.model}）。輸入 /help 看說明，/exit 離開。")
    while True:
        try:
            user_input = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            print("再見！")
            break
        if user_input == "/help":
            print(HELP_TEXT)
            continue
        if user_input == "/clear":
            agent.memory.clear()
            print("已清空對話記憶。")
            continue

        print("\n助理 > ", end="", flush=True)
        try:
            agent.send(user_input)
        except Exception as e:  # noqa: BLE001 — 在 CLI 中把錯誤顯示給使用者
            print(f"\n[錯誤] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

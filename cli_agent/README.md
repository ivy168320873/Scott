# CLI Agent

一個用 [Anthropic 官方 Python SDK](https://github.com/anthropics/anthropic-sdk-python) 打造的命令列 AI Agent，使用 `claude-opus-4-8` 模型，支援讀檔、寫檔與執行 shell 命令。

## 架構

| 檔案 | 職責 |
|------|------|
| `main.py` | CLI 入口，接收使用者輸入、處理指令（`/exit`、`/clear`、`/help`） |
| `agent.py` | 呼叫 Claude API，處理對話與工具 (agentic) 迴圈，串流輸出 |
| `tools.py` | 工具集：`read_file`、`write_file`、`run_shell` |
| `memory.py` | 對話記憶，持久化到 JSON |
| `.env` | 存放 `ANTHROPIC_API_KEY`（由 `.env.example` 複製而來，已被 git 忽略） |

## 安裝

```bash
cd cli_agent
pip install -r requirements.txt
cp .env.example .env      # 然後在 .env 填入你的 ANTHROPIC_API_KEY
```

## 使用

```bash
# 互動模式
python main.py

# 單次提問
python main.py "列出目前目錄下的 Python 檔案並統計行數"
```

互動模式下可用 `/help`、`/clear`、`/exit` 等指令。

## 設計重點

- **模型**：預設 `claude-opus-4-8`，啟用 adaptive thinking 與 `effort: high`。
- **串流**：使用 `messages.stream()` 逐字輸出，並用 `get_final_message()` 取得完整回應。
- **手動工具迴圈**：在執行 `run_shell` 等有副作用的命令前會請使用者確認 (`y/N`)。
- **記憶**：每輪都把完整 `response.content`（含 thinking / tool_use blocks）存入歷史，符合 Messages API 要求。

可用環境變數覆蓋設定：`CLI_AGENT_MODEL`、`CLI_AGENT_EFFORT`。

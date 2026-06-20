# CLI Agent

一個用 [Anthropic 官方 Python SDK](https://github.com/anthropics/anthropic-sdk-python) 打造的命令列 AI Agent，使用 `claude-opus-4-8` 模型，支援讀檔、寫檔與執行 shell 命令。

## 架構

| 檔案 | 職責 |
|------|------|
| `main.py` | CLI 入口，接收使用者輸入、處理指令（`/exit`、`/clear`、`/help`） |
| `agent.py` | 呼叫 Claude API，處理對話與工具 (agentic) 迴圈，串流輸出 |
| `tools.py` | 核心工具集：`read_file`、`write_file`、`run_shell` |
| `stock_tools.py` | 股票工具：`get_stock_price`、`get_fundamentals`（基本面/估值）、`get_market_state`、`analyze_signals`、`compare_stocks`、`scan_stocks`、`momentum_analysis`、`institutional_score`、`committee_vote`、`full_analysis`（共識決策）、`pick_stocks`（自動選股）、`sector_rotation`（產業輪動）、`sector_leaders`（產業領頭羊）、`regime_strategy`、`compare_strategies`、`validate_strategy`、`montecarlo_forecast`（蒙地卡羅預測）、`trade_plan`、`backtest_strategy`（接上層 Scott 投資分析系統） |
| `tw_chips.py` | 台股真實籌碼：`tw_institutional`（三大法人買賣超）、`tw_margin`（融資融券），抓 TWSE 開放資料 |
| `web_agent.py` | 網頁/手機版的非串流工具迴圈（給 `app.py` 的 `/agent` 用） |
| `memory.py` | 對話記憶，持久化到 JSON |
| `.env` | 存放 `ANTHROPIC_API_KEY`（由 `.env.example` 複製而來，已被 git 忽略） |

## 安裝

```bash
cd cli_agent
pip install -r requirements.txt
cp .env.example .env      # 然後在 .env 填入你的 ANTHROPIC_API_KEY

# （選用）要啟用股票工具，再裝上層專案的套件：
pip install -r ../requirements.txt
```

> 股票工具會接上層 Scott 投資分析系統。若沒裝上層套件（pandas、numpy 等），
> 核心功能照常運作，股票工具則會回傳安裝提示。

## 使用

```bash
# 互動模式
python main.py

# 單次提問
python main.py "列出目前目錄下的 Python 檔案並統計行數"

# 投資分析範例
python main.py "NVDA 現在多少錢？大盤偏多還偏弱？"
python main.py "分析 2330.TW 的技術面，現在適合進場嗎？"
python main.py "幫 2330.TW 用 decision_core 策略跑回測"
```

互動模式下可用 `/help`、`/clear`、`/exit` 等指令。

## 設計重點

- **模型**：預設 `claude-opus-4-8`，啟用 adaptive thinking 與 `effort: high`。
- **串流**：使用 `messages.stream()` 逐字輸出，並用 `get_final_message()` 取得完整回應。
- **手動工具迴圈**：在執行 `run_shell` 等有副作用的命令前會請使用者確認 (`y/N`)。
- **記憶**：每輪都把完整 `response.content`（含 thinking / tool_use blocks）存入歷史，符合 Messages API 要求。

可用環境變數覆蓋設定：`CLI_AGENT_MODEL`、`CLI_AGENT_EFFORT`。

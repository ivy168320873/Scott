# CLAUDE.md

本檔案提供 Claude Code 在此專案工作時的長期規則與必要背景。

## 專案概述

**Scott 投資分析系統** — 以 Python + Flask 打造的股市動能分析與投資決策輔助系統。

- 主入口：`app.py`（Flask 應用，含大量路由與頁面）
- 分析模組：根目錄的 `*_engine.py` / `*_score.py`（動能、風險、產業輪動、投資委員會、回測、壓力測試等）
- 資料來源：`fetcher.py`、`data_provider.py`（yfinance、TWSE 開放資料等）
- 排程：`scheduler.py`（APScheduler）
- 前端：Flask Jinja 模板（`templates/`）+ `static/`
- 子專案：`cli_agent/` — 以 Anthropic SDK 實作的 CLI／Web Agent

## 技術與指令

| 項目 | 內容 |
|---|---|
| 語言 | Python 3.11 |
| 框架 | Flask 3.1.3 |
| 套件管理 | pip（`requirements.txt` 執行用、`requirements-dev.txt` 開發用） |
| 安裝 | `pip install -r requirements-dev.txt` |
| 語法檢查 | `python -m compileall -q .` |
| Lint | `ruff check cli_agent`（**目前只有 `cli_agent/` 納入 lint 維護範圍**） |
| 測試 | **目前無測試套件**；`pytest` 未列入 `requirements-dev.txt` |
| 型別檢查 | **未設定** |
| Build | 無建置步驟（Railway NIXPACKS） |
| 啟動 | `python app.py`（預設 port 5000）或 `./run.sh` |
| 部署 | Railway（`railway.json`、`Procfile`） |
| CI | `.github/workflows/ci.yml`：compileall + `ruff check cli_agent` |

> 新增測試時，必須同時把 `pytest` 加入 `requirements-dev.txt` 並在 CI 增加執行步驟，否則測試不會真的被跑到。

## 長期開發原則

1. **修改前先理解專案。** 動任何檔案前先讀它，並用 Grep 找出所有呼叫端。不得在不了解現有架構的情況下重寫。優先沿用既有框架、元件、資料庫、API、設計風格與命名規則。
2. **所有功能要有驗收條件。** 驗收條件必須可被實際執行驗證，不可寫成「功能正常運作」這類無法驗證的敘述。
3. **修改後必須測試。** 實際執行適用的檢查（`compileall`、`ruff`、`pytest`、啟動驗證），不得只閱讀程式碼就宣稱完成。
4. **不得用跳過測試的方式假裝完成。** 嚴禁刪除失敗測試、用 skip/xfail 掩蓋失敗、放寬斷言直到通過、硬編碼預期結果、關閉 Lint 或型別檢查、用 `# noqa` 掩蓋真問題、`except Exception: pass`。測試失敗時要找根本原因並修正。
5. **不得將機密資訊寫入程式碼。** API Key、密碼、Token 一律走環境變數（`.env`，已被 git 忽略）。新增環境變數時同步更新 `.env.example`。機密不得出現在程式碼、Git、測試、日誌或文件中。
6. **未經允許不得部署或 git push。** 同樣需要明確同意的還有：合併主分支、正式環境部署、刪除正式資料庫、大量刪除檔案、清除正式資料、發送正式郵件、執行付款／下單、修改正式服務權限。執行破壞性指令前先說明影響並取得確認。
7. **重大功能完成後更新 `docs/project-state.md`。** 採增量更新，保留仍成立的內容。

## 專案特有注意事項

- **交易安全**：本專案整合 Alpaca。程式碼預設必須走 paper trading（`ALPACA_PAPER`），不得預設連上正式帳戶下單。
- **告警安全**：本專案有 SMTP／LINE 告警功能。測試時使用 dry-run，不要發送正式通知。
- **資料誠實原則**：台股的三大法人與融資融券為 TWSE 真實開放資料；美股的「機構資金流」是價量推估的**代理指標**，回應時必須照實說明，不得把推估講成真實籌碼。
- **邊界條件**：市場資料常見缺值。務必處理空資料、`None`/`NaN`、非交易日、時區（台股 vs 美股）、除以零、無效股票代號與極端值（漲跌停、停牌）。
- **敏感環境變數**：`ANTHROPIC_API_KEY`、`ALPACA_API_KEY`、`ALPACA_SECRET_KEY`、`SECRET_KEY`、`ACCESS_CODE`、`SMTP_PASS`、`FINNHUB_KEY`、`ALPHA_VANTAGE_KEY`、`LINE_NOTIFY_TOKEN`。

## 程式風格

- 中文註解與 docstring（與既有模組一致）
- `snake_case` 命名
- 新模組加 `from __future__ import annotations`
- 沿用既有較新模組的型別註記慣例

## 智能體

本專案的預設主控智能體為 **`claude-code-operator`**（設定於 `.claude/settings.json`）。

專業智能體位於 `.claude/agents/`：

| 智能體 | 用途 |
|---|---|
| `claude-code-operator` | 主控：理解需求 → 分析 → 計畫 → 委派 → 實作 → 驗證 → 審查 → 文件 |
| `requirements-analyst` | 需求結構化與驗收條件（唯讀） |
| `software-architect` | 架構設計與實作順序 |
| `implementer` | 撰寫／修改程式與測試 |
| `tester` | 執行測試、重現錯誤、驗證驗收條件 |
| `code-reviewer` | 唯讀審查，Critical/High/Medium/Low 分級 |
| `debugger` | 重現、根因分析、最小修復 |

新增功能相同的智能體前，先確認既有智能體是否可沿用並改善。

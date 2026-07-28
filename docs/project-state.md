# 專案狀態（project-state）

> 本文件由 `claude-code-operator` 在每次重大功能完成後增量更新。
> 最後更新：2026-07-28 — 建立 Claude Code 主控智能體體系（基線快照）

## 專案目標

**Scott 投資分析系統** — 提供股市動能分析、多維度評分、投資決策輔助與回測驗證的 Web 系統，並附帶一個可用自然語言操作的投資分析 Agent。

## 目前技術架構

| 層 | 內容 |
|---|---|
| 語言／執行環境 | Python 3.11 |
| Web 框架 | Flask 3.1.3 |
| 主入口 | `app.py`（單一大型 Flask 應用，含頁面與 API 路由） |
| 分析引擎 | 根目錄 `*_engine.py` / `*_score.py`（動能、趨勢、量能、估值、風險、產業輪動、投資委員會、機構資金流、壓力測試、部位規模、交易成本等） |
| 資料層 | `fetcher.py`、`data_provider.py`（yfinance、TWSE 開放資料）；SQLite（`user_data.db`）與 JSON 檔 |
| 回測／最佳化 | `backtest.py`、`optimizer.py`、`portfolio_optimizer.py` |
| 排程 | `scheduler.py`（APScheduler）、`monitor.py`、`alert_*.py` |
| 交易整合 | `trader.py`（Alpaca，預設 paper trading） |
| 前端 | Flask Jinja 模板 `templates/` + `static/`（含 PWA `manifest.json`、`sw.js`） |
| 子專案 | `cli_agent/`：Anthropic SDK 打造的 CLI／Web Agent，可呼叫上層分析模組 |
| 部署 | Railway（NIXPACKS，`railway.json`、`Procfile`） |

## 已完成功能（近期）

- 台股動能排行頁 `/momentum`（Top 30 HTML）
- 隔日追高風險評估，並顯示於卡片
- `cli_agent/`：股票工具、台股籌碼（三大法人、融資融券）、記憶、Web Agent
- 移除未使用的 `polymarket_bot/` 與 `tests/`，並精簡 CI
- **本次**：建立 Claude Code 主控智能體與專業智能體體系

## 本次修改過的檔案

| 檔案 | 變更 |
|---|---|
| `.claude/agents/claude-code-operator.md` | 新增 — 主控智能體 |
| `.claude/agents/requirements-analyst.md` | 新增 — 需求分析（唯讀） |
| `.claude/agents/software-architect.md` | 新增 — 架構設計 |
| `.claude/agents/implementer.md` | 新增 — 實作 |
| `.claude/agents/tester.md` | 新增 — 測試 |
| `.claude/agents/code-reviewer.md` | 新增 — 唯讀審查 |
| `.claude/agents/debugger.md` | 新增 — 除錯 |
| `.claude/settings.json` | 新增 — 設定 `agent: claude-code-operator`，並對危險指令加上 `ask` 確認 |
| `CLAUDE.md` | 新增 — 專案長期規則 |
| `.gitignore` | 修改 — 原本整個忽略 `.claude/`，改為僅忽略本機檔案，讓共用 agent 定義與 `settings.json` 進版控 |
| `.env.example` | 新增 — 根目錄環境變數範本（原本只有 `cli_agent/.env.example`） |
| `docs/project-state.md` | 新增 — 本文件 |

## 啟動方式

```bash
pip install -r requirements.txt
python app.py                 # → http://localhost:5000
# 或
./run.sh
```

CLI Agent：

```bash
cd cli_agent
pip install -r requirements.txt
cp .env.example .env          # 填入 ANTHROPIC_API_KEY
python main.py
```

## 測試方式

```bash
pip install -r requirements-dev.txt
python -m compileall -q .     # 語法檢查（全 repo）
ruff check cli_agent          # Lint（維護範圍）
```

> **目前專案沒有單元／整合測試套件。** `tests/` 曾於 commit `03793e1` 移除。
> 新增測試時必須同時把 `pytest` 加入 `requirements-dev.txt`，並在 `.github/workflows/ci.yml` 增加執行步驟。

## 環境變數

完整清單見根目錄 `.env.example`。敏感項目（**不得寫入程式碼或版控**）：

`ANTHROPIC_API_KEY`、`ALPACA_API_KEY`、`ALPACA_SECRET_KEY`、`SECRET_KEY`、`ACCESS_CODE`、`SMTP_PASS`、`FINNHUB_KEY`、`ALPHA_VANTAGE_KEY`、`LINE_NOTIFY_TOKEN`

## 已知問題

1. **無測試套件** — 目前只靠 `compileall` 與 `ruff` 把關，回歸風險高。
2. **Lint 覆蓋不足** — CI 只 lint `cli_agent/`；根目錄數十個模組未納入，貿然全面開啟會產生大量既有告警。
3. **無型別檢查** — 未設定 mypy/pyright。
4. **`app.py` 過大**（約 196KB 單檔），路由與邏輯混雜，維護成本高。
5. **`requirements.txt` 版本全數釘選** — 部署可重現，但需要人工維護升級。

## 尚未完成事項

- 建立 `tests/` 與 pytest 基礎設施，並接上 CI
- 將 lint 範圍逐步擴大到根目錄模組
- 評估導入型別檢查
- 評估 `app.py` 依功能拆分為 Blueprint

## 下一步建議

1. 先為**風險最高、最常改動**的模組（如 `fetcher.py`、`decision_engine.py`）補上單元測試，而非追求全面覆蓋。
2. 用 `ruff check --statistics .` 盤點根目錄既有告警量，再決定分批啟用的規則集。
3. `app.py` 拆分屬於大型重構，需先由 `software-architect` 提出分階段路徑與風險評估，經確認後再執行。

## 重要技術決策

| 決策 | 理由 |
|---|---|
| 主控智能體設為 `claude-code-operator`（`.claude/settings.json` 的 `agent` 欄位） | 該欄位是 Claude Code 2.1.220 中設定主執行緒 agent 的正式方式，會套用其 system prompt、工具限制與模型 |
| Agent 委派工具使用 `Agent`（非 `Task`） | 此版本中 `Task` 為舊別名，內部正規化為 `Agent`；使用正規名稱避免相依於相容層 |
| `code-reviewer` 不給 Write/Edit | 確保審查獨立性；發現問題交由 `implementer` / `debugger` 修正 |
| 危險指令採 `permissions.ask` 而非 `deny` | 保留使用者明確授權的可能，同時避免無聲執行 `git push`、部署、刪除等不可逆操作 |
| `.gitignore` 由整個忽略 `.claude/` 改為選擇性忽略 | 原設定會導致共用的 agent 定義與專案 settings 無法進版控；改為只忽略 `settings.local.json` 等本機檔案 |

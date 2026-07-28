# Scott — 現有架構盤點（改造前基準）

> 本文記錄 **改造開始前** Scott 的真實實作狀態，作為 `feature/jellyfish-parity` 的回滾基準。
> 對應 commit：`9fb1d7c`（main）。
> 文件性質：`architecture` = 現在是什麼。不寫未來計畫（未來計畫見 `jellyfish-implementation-plan.md`）。

---

## 1. 專案定位

Scott 是一套 **台股／美股動能分析與投資決策輔助平台**，不是影片生成系統。

核心能力：

- 個股動能掃描、評分與排序
- 多引擎決策（風險、估值、趨勢、量能、籌碼、輪動、總經）
- 回測與最佳化
- AI 分析師辯論（多角色 LLM）
- 排程掃描與告警推播
- 每日報告產生

---

## 2. 技術棧現況

| 層級 | 現況 | 備註 |
| --- | --- | --- |
| Web 框架 | Flask 3.1.3（WSGI，同步） | `app.py`，單一檔案 195KB |
| 前端 | Jinja2 server-side template | `templates/*.html`，無建置步驟 |
| 前端資產 | 手寫 HTML/CSS/JS，內嵌於 template | 無 npm / bundler / TypeScript |
| 資料庫 | SQLite（原生 `sqlite3`，非 ORM） | 多個 `.db` 檔，分散在各引擎模組 |
| Migration | 無 | 以 `CREATE TABLE IF NOT EXISTS` 就地建表 |
| 背景工作 | APScheduler（in-process 執行緒） | `scheduler.py`，與 web process 同生命週期 |
| 任務佇列 | 無 | 無 Redis、無 Celery |
| 物件儲存 | 無 | 無 S3／檔案上傳機制 |
| LLM | Anthropic SDK 直接呼叫 | `analyst.py` / `fetcher.py` / `analyzer.py` / `trump.py` |
| API 規格 | 無 | 無 OpenAPI／無型別產生 |
| 測試 | 無 | 無 `test_*.py` |
| CI | `compileall` + `ruff check cli_agent` | `.github/workflows/ci.yml` |
| 部署 | Railway（NIXPACKS） | `Procfile`: `web: python app.py` |
| 容器化 | 無 | 無 Dockerfile／無 compose |

---

## 3. 模組結構

### 3.1 進入點

- `app.py` — Flask app、所有 HTTP route、session/auth、SQLite 使用者資料持久化
  - 認證：`ACCESS_CODE` 環境變數 + session cookie
  - 登入限流：in-memory dict，5 次失敗鎖 15 分鐘
  - 使用者資料：`user_data.db`（SQLite），另有 legacy `user_data.json` 遷移路徑

### 3.2 資料取得層

| 檔案 | 職責 |
| --- | --- |
| `data_provider.py` | 行情資料統一入口 |
| `fetcher.py` | 外部資料抓取（含 Claude Web Search 作為 primary） |
| `demo_data.py` | 離線示範資料 |
| `sector_map.py` | 產業對照表 |

### 3.3 分析／評分引擎

`analyzer.py`、`signals.py`、`patterns.py`、`trend_score.py`、`volume_score.py`、
`valuation_score.py`、`risk_score.py`、`catalyst_score.py`、`relative_strength_score.py`

### 3.4 決策引擎

`decision_engine.py`、`final_decision_engine.py`、`top_tier_decision_engine.py`、
`top_tier_engine.py`、`investment_committee_engine.py`、`rotation_decision_engine.py`、
`rotation_engine.py`、`sell_engine.py`、`observation_engine.py`

### 3.5 風險／部位

`risk_engine.py`、`risk_manager.py`、`position_sizing_engine.py`、
`portfolio_engine.py`、`portfolio_manager_engine.py`、`portfolio_optimizer.py`、
`stress_test_engine.py`、`macro_risk_engine.py`、`market_regime_engine.py`、
`capital_filter_engine.py`、`institutional_flow_engine.py`

### 3.6 執行／監控

`trader.py`、`monitor.py`、`scheduler.py`、`alert_engine.py`、`alert_scanner.py`、
`alert_formatter.py`、`alert_history.py`、`daily_report_engine.py`、
`data_quality_engine.py`、`signal_confidence_engine.py`

### 3.7 回測

`backtest.py`（47KB）、`optimizer.py`、`trade_cost.py`

### 3.8 AI

- `analyst.py` — 三角色分析師辯論引擎（甲／乙／丙 + 主席），SSE 串流進度
- `trump.py` — 政策事件分析
- `cli_agent/` — 獨立 CLI agent（`agent.py`、`tools.py`、`stock_tools.py`、`memory.py`、`web_agent.py`）

---

## 4. 現有資料持久化細節

以原生 `sqlite3` 直接操作，**無 ORM、無 migration、無統一 schema 定義**。
各模組自帶 `_DB_PATH` 與 `CREATE TABLE IF NOT EXISTS`：

| 模組 | 持久化內容 |
| --- | --- |
| `app.py` | 使用者資料（`user_data.db`） |
| `observation_engine.py` | 觀察清單 |
| `signal_confidence_engine.py` | 訊號信心追蹤 |
| `daily_report_engine.py` | 每日報告 |
| `stress_test_engine.py` | 壓力測試結果 |
| `alert_history.py` | 告警歷史 |

並行控制以 `threading.Lock` + `check_same_thread=False` 處理。

**這些資料表與檔案在本次改造中完全不會被觸碰。**

---

## 5. 現有非同步模型

- `scheduler.py` 使用 APScheduler，在 Flask process 內以執行緒執行
- `analyst.py` 使用 `threading.Thread` + `queue.Queue`，透過 SSE 推播進度
- **問題**：任務狀態只存在記憶體，process 重啟即遺失；無法取消、無法查詢歷史、無法跨 process 恢復

---

## 6. 現有環境變數

| 變數 | 用途 |
| --- | --- |
| `SECRET_KEY` | Flask session 簽章 |
| `ACCESS_CODE` | 登入碼（空值 = 不需認證） |
| `ANTHROPIC_API_KEY` | Anthropic API |
| `USER_DATA_DB` / `USER_DATA_FILE` | 使用者資料路徑 |
| `RAILWAY_ENVIRONMENT` | 判斷是否 production（cookie secure） |

現況已符合「金鑰只從環境變數讀取」原則，程式碼中無硬編碼金鑰。

---

## 7. 啟動與驗證方式（改造前）

```bash
# 本機啟動
./run.sh                 # pip install + python app.py → http://localhost:5000

# 或
python app.py

# CI 等效驗證
python -m compileall -q .
ruff check cli_agent
```

無測試套件可執行。

---

## 8. 回滾基準

- 基準分支：`main`
- 基準 commit：`9fb1d7c`
- 改造分支：`feature/jellyfish-parity`

回滾方式：

```bash
# 放棄整個改造
git checkout main

# 或只回滾單一檔案
git checkout main -- <path>
```

改造原則保證：

1. 不修改 `main`
2. 不刪除任何既有 `.py` 檔案
3. 不修改既有 SQLite 資料表結構
4. 既有 `python app.py` 啟動路徑保持可用

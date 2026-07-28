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


---

## 9. Scott Studio 併存後的架構（Phase 3 起）

> 本節記錄 **加上 Scott Studio 之後** 的整體架構。
> 上方 §1–§8 描述的既有股票系統**完全未變**。

### 9.1 兩套系統的關係

```
uvicorn studio_server:application
  │
  ├─ /api/v1/studio/*   → FastAPI（Studio API，116 個操作）
  ├─ /studio/*          → Studio 頁面與 API 文件
  │
  └─ /  (WSGI mount)    → 既有 Flask app（112 條股票路由，路徑不變）

python app.py           → 只跑股票系統，完全不經過 Studio
```

### 9.2 Studio 分層

```
API Layer        studio/api/v1/routes/     只收參、驗證、權限、回應組合
      ↓
Service Layer    studio/services/          業務規則、狀態流轉、跨資源驗證
      ↓
Repository Layer studio/repositories/      查詢組裝、分頁、排序
      ↓
Database         studio/models/            23 張 studio_* 資料表
```

輔助層：

| 目錄 | 職責 |
| --- | --- |
| `studio/core/` | db / storage / redis / errors / security / deps / ids |
| `studio/schemas/` | Pydantic DTO（API 契約） |
| `studio/tasks/` | Celery app（執行器於 Phase 4） |
| `studio/scripts/` | 初始化與 OpenAPI 匯出 |

### 9.3 認證

Studio 與股票系統**共用同一個登入**：

- `studio/core/security.py` 以 `itsdangerous` 驗證 Flask 簽發的 session cookie
- **不 import `app.py`** —— 股票模組不存在時 Studio 仍可獨立運作
- 規則與 `app.py::_require_auth` 一致：`ACCESS_CODE` 未設定即停用認證
- Studio API 未登入一律回 **JSON 401**，絕不回 HTML 登入頁

### 9.4 OpenAPI 流程

```
studio/api/v1/routes/  ──►  /studio/openapi.json
                            │
                            ├─ python -m studio.scripts.export_openapi
                            │     └─► frontend/openapi.json（含金鑰掃描）
                            │
                            └─ pnpm run openapi:gen
                                  └─► frontend/src/services/generated/
                                        13 個 Service、157 個型別
```

後端 API 是唯一規格來源；`frontend/openapi.json` 與 generated client 皆進版控，
讓 API 變更在 code review 中可見。

### 9.5 執行方式（Phase 3 現況）

```bash
# 只跑股票系統（現行部署，未變）
python app.py

# 股票 + Studio（預設 SQLite / 本機儲存 / inline 任務，無需外部服務）
uvicorn studio_server:application --host 0.0.0.0 --port 8000

# Studio 完整堆疊
docker compose --env-file deploy/compose/.env -f deploy/compose/docker-compose.yml up --build

# 重新產生前端型別
python -m studio.scripts.export_openapi
cd frontend && pnpm run openapi:gen
```

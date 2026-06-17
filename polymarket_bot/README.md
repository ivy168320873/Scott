# Polymarket Paper Trading Bot

事件驅動（event-driven）的 **Polymarket 模擬交易系統**。它監聽新聞事件，用 LLM
判斷新聞是否會造成 Polymarket CLOB 訂單簿短暫錯價，並以 **paper trading（不實盤下單）**
的方式記錄訊號與模擬損益，用來測試「新聞 → 錯價 → 回歸」這個假設。

> ⚠️ **第一版禁止實盤下單。** 程式碼裡完全沒有送出真實訂單的程式路徑，且開機時會
> 強制檢查 `LIVE_TRADING_ENABLED=false`，否則拒絕啟動。所有交易都是模擬。

---

## A. 專案資料夾架構

```
polymarket_bot/
├── README.md                  # 本檔
├── requirements.txt           # Python 套件
├── .env.example               # 環境變數範例
├── schema.sql                 # PostgreSQL schema（開機自動套用）
├── run.sh                     # 啟動腳本
├── tests/
│   └── test_engine.py         # 離線單元測試（不需 DB / 網路）
└── app/
    ├── __init__.py
    ├── config.py              # 所有可調參數（pydantic-settings）
    ├── db.py                  # asyncpg 連線池 + schema bootstrap
    ├── models.py              # 共用 dataclass（Market / OrderbookTop / Signal…）
    ├── repository.py          # 所有 SQL（CRUD + dashboard 聚合）
    ├── clob_client.py         # Polymarket CLOB / Gamma REST（唯讀，無下單）
    ├── ws_client.py           # Polymarket WebSocket 即時訂單簿 + 記憶體快取
    ├── orchestrator.py        # 把所有模組串成一條 asyncio pipeline
    ├── main.py                # FastAPI app + dashboard API
    ├── news/
    │   ├── base.py            # NewsSource 抽象介面（未來換 Bloomberg/Reuters）
    │   ├── rss_source.py      # RSS / 公開新聞源
    │   └── ingestor.py        # 輪詢 + 去重 + 推入 queue
    ├── classifier/
    │   └── llm_classifier.py  # Claude / OpenAI / stub 分類器
    ├── engine/
    │   ├── fair_value.py      # fair probability engine（指數衰減模型）
    │   ├── signal_engine.py   # edge / confidence 判斷
    │   ├── risk_manager.py    # 風控閘門
    │   └── paper_trader.py    # 模擬成交 / 滑價 / 手續費 / 強制平倉
    └── static/
        └── dashboard.html     # 即時儀表板（純前端，3 秒輪詢 API）
```

## 資料流（pipeline）

```
RSS / 新聞源 ─► NewsIngestor ─(asyncio.Queue)─► LLM Classifier
                                                     │
                                                     ▼
                              FairValue ─► SignalEngine ─► RiskManager ─► PaperTrader
                                  ▲
Polymarket WebSocket ─► OrderbookStore（記憶體 top-of-book，供上面各層讀取）
```

- LLM **只負責分類與摘要**：回傳 `is_important / 影響哪個 market / YES-NO 方向 /
  relevance / surprise / confidence`，**永遠不決定下單金額**。
- 下單金額固定由 `config` 設定，再由 `RiskManager` 上限收斂。
- 每一筆訊號（含被風控否決的）與每一筆模擬交易都寫入 PostgreSQL → **可回測、可記錄、可解釋**。

---

## B. requirements.txt

見 `requirements.txt`（FastAPI / uvicorn / asyncpg / httpx / websockets / feedparser /
anthropic / openai / pydantic-settings…）。

## C. .env.example

見 `.env.example`，重點欄位：

| 變數 | 說明 |
|------|------|
| `LIVE_TRADING_ENABLED` | 安全開關，v1 必須 `false` |
| `DATABASE_URL` | PostgreSQL DSN |
| `CLOB_REST_URL` / `CLOB_WS_URL` / `GAMMA_API_URL` | Polymarket 端點 |
| `TRACKED_MARKETS` | 指定追蹤的 market（留空則自動抓最熱門的前 N 個）|
| `NEWS_RSS_FEEDS` | 逗號分隔的 RSS 來源 |
| `LLM_PROVIDER` | `anthropic` / `openai` / `stub`（離線關鍵字 fallback）|
| `FAIR_ALPHA` / `FAIR_LAMBDA` | fair value 衰減模型參數 |
| `MINIMUM_EDGE` / `CONFIDENCE_THRESHOLD` | 訊號門檻 |
| `PAPER_*` | 起始資金、滑價、手續費、單筆下單金額 |
| `MAX_*` | 風控上限（單筆風險、單市場曝險、每日虧損、最大持倉 90 秒）|

## D. database schema

見 `schema.sql`，共 5 張表：`markets`、`news_events`、`orderbook_snapshots`、
`signals`、`paper_trades`。開機時 `app/db.py` 會自動套用（`CREATE TABLE IF NOT EXISTS`）。

## E. 各模組 Python 檔案

見 `app/` 目錄，每個檔案開頭都有 docstring 說明職責。

## F. README 啟動教學（本節）

---

## 核心公式

**Fair probability engine**（`app/engine/fair_value.py`）：

```
fair_price = current_mid_price + alpha * news_score * exp(-lambda * seconds_since_news)
```

- `news_score ∈ [-1, 1]`：`sign(direction) * relevance_score * surprise_score`
  （YES 為正、NO 為負）。
- `exp(-lambda * t)`：時間衰減——新聞剛出來時 edge 最大，市場吸收後逐漸回到 mid。
- 結果 clamp 在 `(0, 1)` 之間（它是機率）。

**Signal engine**（`app/engine/signal_engine.py`）：

```
若 fair_price - best_ask > MINIMUM_EDGE 且 confidence > THRESHOLD  → BUY_YES
若 best_bid - fair_price > MINIMUM_EDGE 且 confidence > THRESHOLD  → SELL_YES（賣 / 等效做空）
```

**Paper trader**（`app/engine/paper_trader.py`）：模擬以 best_ask/best_bid 跨價成交，
加上 `PAPER_SLIPPAGE_BPS` 滑價與 `PAPER_FEE_BPS` 手續費；持倉超過
`MAX_HOLDING_SECONDS`（預設 90 秒）或價格收斂到 fair value 即平倉，記錄 pnl 與
holding time。

---

## 啟動教學

### 1. 安裝相依套件

```bash
cd polymarket_bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 準備 PostgreSQL

```bash
# 以 Docker 為例
docker run -d --name pm-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=polymarket_bot -p 5432:5432 postgres:16
```

schema 會在程式啟動時自動建立；若想手動套用：

```bash
psql "postgresql://postgres:postgres@localhost:5432/polymarket_bot" -f schema.sql
```

### 3. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env：填入 DATABASE_URL；若要用真 LLM，設 LLM_PROVIDER 與 API key。
# 想先離線跑通整條 pipeline，保持 LLM_PROVIDER=stub 即可。
```

### 4. 啟動

```bash
./run.sh
# 或
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 5. 打開儀表板

瀏覽器開啟 <http://localhost:8000> ，可看到：即時 market、最新 news event、
signal、open paper trades，以及 PnL / win rate / average edge / max drawdown。

### 6. API（給整合 / 回測用）

| 端點 | 說明 |
|------|------|
| `GET /api/health` | 健康檢查（含 `live_trading_enabled` 與開倉數）|
| `GET /api/markets` | 追蹤中 market + 即時 bid/ask/mid |
| `GET /api/news?limit=N` | 最新新聞與分類結果 |
| `GET /api/signals?limit=N` | 訊號（含被風控否決者）|
| `GET /api/trades` | 目前開倉中的模擬交易 |
| `GET /api/stats` | 績效聚合（PnL / win rate / avg edge / max drawdown）|

### 7. 跑測試（離線、不需 DB）

```bash
cd polymarket_bot
pip install pytest
PYTHONPATH=. pytest -q
```

---

## 設計限制（依需求落實）

1. **禁止實盤下單**：`clob_client.py` 沒有任何下單方法；開機強制斷言
   `LIVE_TRADING_ENABLED=false`。
2. **所有交易皆為 paper trading**：成交、滑價、手續費全為模擬。
3. **LLM 只做分類與摘要**：分類器 schema 不含金額欄位；下單金額由 config + 風控決定。
4. **可回測、可記錄、可解釋**：每筆 news / signal / trade 全部入庫，並另存
   `orderbook_snapshots` 以重建當下盤口；訊號附帶 fair_price、edge、news_score、
   confidence 與否決原因。

## 未來擴充

- 把 `news/` 下新增 `BloombergSource` / `ReutersSource`（實作 `NewsSource.fetch()`），
  其餘 pipeline 不需更動。
- 在 `orderbook_snapshots` 之上寫獨立 backtester，重放歷史盤口驗證訊號。
- 視覺化 equity curve / per-market 績效。

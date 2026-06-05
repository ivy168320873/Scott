# 雲端部署教學（取得公開網址）

本專案是 `Scott` repo 底下的子資料夾 `polymarket_bot/`，需要當成**獨立服務**部署，
並搭配一個 **PostgreSQL** 資料庫。以下以 **Railway** 為主（你的 repo 已在用 Railway），
另附 Render 的對照。

部署後你會得到一個像 `https://你的專案.up.railway.app` 的公開網址，打開即是儀表板。

---

## A. Railway（推薦，與既有設定一致）

### 1. 建立專案並指向這個 repo
1. 到 <https://railway.app> → **New Project** → **Deploy from GitHub repo** → 選 `ivy168320873/Scott`。
2. 進入該服務的 **Settings**：
   - **Root Directory** 設為 `polymarket_bot`
     （關鍵！否則 Railway 會部署到根目錄的股票 app，而不是這支機器人。）
   - Build 會自動偵測到 `polymarket_bot/Dockerfile`。

### 2. 加一個 PostgreSQL
1. 在同一個 Project 內 → **New** → **Database** → **Add PostgreSQL**。
2. Railway 會自動提供 `DATABASE_URL` 變數。到機器人服務的 **Variables** →
   **Add Reference** → 選 Postgres 的 `DATABASE_URL`（或手動把連線字串貼進去）。
   - schema 會在程式啟動時自動建立，不需手動跑 migration。

### 3. 設定環境變數（Variables 分頁）
最少只要這些就能跑（離線分類模式）：

| 變數 | 值 |
|------|----|
| `DATABASE_URL` | （引用上面的 Postgres）|
| `LIVE_TRADING_ENABLED` | `false` |
| `LLM_PROVIDER` | `stub` |
| `NEWS_RSS_FEEDS` | `https://feeds.bbci.co.uk/news/rss.xml,http://rss.cnn.com/rss/cnn_topstories.rss` |

要用真 LLM 分類，再加：

| 變數 | 值 |
|------|----|
| `LLM_PROVIDER` | `anthropic`（或 `openai`）|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |

> `PORT` 不用自己設，Railway 會自動注入，Dockerfile 已綁定 `$PORT`。

### 4. 部署
存檔後 Railway 會自動 build & deploy。到 **Settings → Networking →
Generate Domain** 產生公開網址，打開即可看到儀表板。

---

## B. Render（替代方案）

1. <https://render.com> → **New** → **Web Service** → 連 GitHub repo。
2. **Root Directory** 填 `polymarket_bot`；Runtime 選 **Docker**。
3. **New → PostgreSQL** 建資料庫，把它的 *Internal Database URL* 設成 Web Service
   的 `DATABASE_URL` 環境變數。
4. 其餘環境變數同上表。Render 也會自動注入 `PORT`。

---

## 部署後檢查

- `https://<你的網址>/` → 儀表板
- `https://<你的網址>/api/health` → 應回傳 `{"status":"ok","live_trading_enabled":false,...}`

若 `live_trading_enabled` 不是 `false`，程式會**拒絕啟動**（v1 安全限制）。

## 常見問題

- **打開卻是股票 app** → Root Directory 沒設成 `polymarket_bot`。
- **啟動即 crash、log 顯示連不上 DB** → `DATABASE_URL` 沒設或 Postgres 還沒起來；
  Railway 上用 Reference 引用最穩。
- **儀表板有畫面但沒有 market** → Polymarket API 需可對外連網；確認平台允許 outbound
  HTTP，稍等幾秒讓 WebSocket 接上。
- **費用** → Railway/Render 免費額度有限；這支機器人會持續連 WebSocket 與輪詢新聞，
  屬於長時間執行的服務，請留意用量。

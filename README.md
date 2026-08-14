# Scott 股市分析系統

Scott 是以 Flask 建置的美股／台股分析工具，包含技術訊號、回測、風險管理、投資組合、警報、每日報告與行動版 AI 助手。

## 市場情報中樞

登入後開啟 `/intelligence`，可把手機端同步的 `portfolio_v1` 持股與
`radarWatchlist` 觀察清單連結到 Yahoo Finance、Finnhub 與 Alpha Vantage
新聞。每則事件保留原始來源，並把「來源事實」與「AI／規則推論」分開顯示。

新聞催化劑只會在信心門檻通過時，把頂級決策分數微調最多 ±8 分；它不會取代
價格、成交量、資料品質或停損規則，也不會觸發真實下單。未設定 Claude 時仍會
使用可解釋規則；未設定付費新聞金鑰時則保留 Yahoo 無金鑰來源，並在報告標示
資料缺口。

獨立執行一次：

```bash
python -m market_intelligence.worker run --type manual --force
```

Railway 會由 `railway_start.py` 在同一個 Web Service 內監督 Web 與情報 Worker，
兩者因此共用同一個 SQLite Volume，不需要複製密鑰或建立第二份資料庫。情報 Worker
使用的命令為：

```text
python -m market_intelligence.worker daemon
```

Railway 啟動器偵測到持久化 Volume 時會自動啟用情報 Worker；需要暫停時可設定
`RAILWAY_INTELLIGENCE_WORKER_ENABLE=false`，需要在無 Volume 的測試服務啟動時則明確
設為 `true`。每日報告預設為 Asia/Taipei 08:30，
重大事件每 30 分鐘輪詢；Email 與 LINE 會使用既有 SQLite outbox 去重與重試。
啟動器會強制 `ALPACA_PAPER=true` 並移除實盤確認值，部署本身不會取得真實下單權限。

## Evolution v2 決策閉環

登入後開啟 `/evolution` 可使用新的手機版決策中樞。流程固定為：

1. 取得真實行情並執行頂級決策引擎。
2. 產生「價格、趨勢、量能、風險、市場」五類證據。
3. 以資料品質、歷史結果、證據覆蓋與市場環境建立單次訊號可信度評分卡。
4. 套用可保存的個人風險限制（持倉、產業、總曝險、單筆風險、每日損失與回撤停機線）。
5. 只有評分卡與風險閘門同時通過，才可建立持久化模擬交易。
6. 背景維護會檢查停損／目標並把平倉結果回填訊號校準資料。

通知改用 SQLite outbox：相同事件與管道會去重，失敗採指數退避重試，重新部署後仍可續送。手機 AI 對行情、估值、預測與買賣問題必須先取得工具證據；工具失敗時程式會直接阻擋無依據結論。

主要 API：

- `GET|PUT /api/evolution/risk-profile`
- `POST /api/evolution/evaluate`
- `POST /api/evolution/paper/open`
- `GET /api/evolution/paper/trades`
- `POST /api/evolution/paper/mark`
- `POST /api/evolution/paper/<trade_id>/close`
- `GET /api/evolution/notifications`
- `POST /api/evolution/notifications/retry`

`DEFAULT_RISK_PRESET` 可設為 `conservative`、`balanced` 或 `aggressive`；使用者在決策中樞儲存的設定優先。所有可信度與模擬績效均屬決策輔助，不是上漲機率、獲利保證或自動下單授權。

## 本機啟動

需要 Python 3.11。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python app.py
```

應用程式位於 `http://localhost:5000`。正式環境必須設定 `ACCESS_CODE` 與 `SECRET_KEY`；系統不會在正式環境靜默開放未登入存取。

## Railway 部署

1. 建立單一 Web Service 並連接此儲存庫。
2. 掛載 Railway Volume，建議路徑 `/data`。Railway 提供 `RAILWAY_VOLUME_MOUNT_PATH` 後，Scott 會自動把 SQLite 放在該 Volume。
3. 依 `.env.example` 設定環境變數。
4. `/healthz` 是不依賴外部行情服務的存活檢查。

Gunicorn 固定使用一個 process、八個 threads，避免 SQLite 與記憶體狀態被多個 worker 各自複製。若未來要水平擴充，需先把工作排程鎖、限流與快取移至 Redis／Postgres。

背景掃描預設關閉。若需要排程，只能在一個 Service 設定：

```text
SCHEDULER_ENABLE=true
BACKGROUND_WORKERS_ENABLE=true
```

## 資料與回測安全

- 即時來源全部失敗時預設回傳錯誤，不會冒充真實行情。只有明確設定 `ALLOW_DEMO_DATA=true` 才能使用標記過的模擬資料。
- 策略訊號在下一根 K 棒開盤成交，避免用同一根收盤價產生並成交訊號。
- 參數最佳化只用訓練集排名；測試集只用於最終樣本外評估。
- 回測結果仍不等於未來績效，也不構成投資建議。

## 通知與交易

LINE 使用 Messaging API。`LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_USER_ID` 只能放在伺服器環境變數，不會同步到瀏覽器或 SQLite。

Alpaca 預設為 paper trading。實盤需要同時設定 `ALPACA_PAPER=false`、指定的 `ENABLE_LIVE_TRADING` 確認字串，且每筆 API 請求仍需逐筆確認。

## 驗證

```bash
python -m compileall -q .
ruff check . --select E9,F63,F7,F82
pytest -q
pip-audit -r requirements.txt
```

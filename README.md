# Scott 股市分析系統

Scott 是以 Flask 建置的美股／台股分析工具，包含技術訊號、回測、風險管理、投資組合、警報、每日報告與行動版 AI 助手。

## 市場情報中樞

登入後開啟 `/intelligence`，可把手機端同步的 `portfolio_v1` 持股與
`radarWatchlist` 觀察清單連結到 Yahoo Finance、Finnhub 與 Alpha Vantage
新聞。設定合規的 `SEC_USER_AGENT` 後，還會讀取 SEC EDGAR 的 8-K、10-Q、
S-3、424B5 與 Form 4 等結構化公告，優先標示稀釋風險。每則事件保留原始來源，
並把「來源事實」與「AI／規則推論」分開顯示。

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

## Institutional Core v4 決策閉環

登入後開啟 `/evolution` 可使用新的手機版決策中樞。流程固定為：

1. 依美／台交易時區、NYSE 規則、TWSE 官方休市日曆與提早收盤辨認 PRE、OPEN、POST、CLOSED；盤中未完成、休市日或未來日 K 會先排除。
2. 行情保留 provider、抓取時間、完整 K 棒日期與 price basis；報價另標示 SIP、IEX 單一交易所或 Yahoo 參考來源。
3. 市場狀態同時檢查主要指數與 12 檔代表性橫斷面廣度；不把代理廣度冒充交易所全市場家數。
4. 取得真實行情並執行頂級決策引擎。
5. 產生「價格、趨勢、量能、風險、市場」五類證據，並套用可保存的個人風險限制。
6. 只有評分卡與風險閘門同時通過，才可建立持久化影子交易；影子單先進入 `PENDING`，下一交易日開盤才以滑價／費用後價格成交。
7. 歷史訊號也以 next-open 回填 1／3／5 日結果，記錄 gap、MFE、MAE、成本後方向報酬與相對大盤報酬。
8. 成功機率使用 Beta(1,1) 後驗分布與 95% 可信區間；同一天的相關訊號只算一個獨立樣本，並顯示 Brier score，不把綜合分數假裝成上漲機率。
9. Kelly 預設停用。只有至少 50 個獨立交易日、95% 成功率下界高於 50%、Brier ≤ 0.20、校準誤差 ≤ 10% 且成本後平均報酬為正，才用保守的 quarter-Kelly；否則只採固定風險／波動度，單檔上限 3%。
10. 每日情報成功後建立 SQLite online backup、執行 quick_check 與 SHA-256 驗證並保留 14 天；`/readyz` 阻擋資料庫損壞或正式環境無 Volume 的部署。

總經模組不再使用硬編碼事件日期：FOMC 由 Federal Reserve 官方行事曆動態解析；
設定 `FRED_API_KEY` 後會加入 CPI、就業、GDP、2Y／10Y 利率與高收益債利差。
若來源失敗，API 會回報 `DEGRADED`／`UNAVAILABLE`，不沿用過期日期。台股 `.TW`
讀取 TWSE 官方三大法人與融資融券收盤後資料；`.TWO` 改讀 TPEX 官方法人與融資融券資料，兩者不混用。

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
- `GET /api/quote/<symbol>`
- `GET /api/institutional-status/<symbol>`
- `GET /api/market-regime?market=US|TW`
- `GET /api/macro-risk`
- `GET /api/operational-readiness`

`DEFAULT_RISK_PRESET` 可設為 `conservative`、`balanced` 或 `aggressive`；使用者在決策中樞儲存的設定優先。`ALPACA_DATA_FEED=iex` 是單一交易所資料；只有帳戶具備 SIP entitlement 並改為 `sip` 時才會標記為 consolidated。所有可信度與模擬績效均屬決策輔助，不是獲利保證或自動下單授權。

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
4. `/healthz` 是不依賴外部行情服務的存活檢查；Railway 使用 `/readyz` 驗證 SQLite、Volume 與正式環境認證。

同一 Volume 內的 SQLite 備份可防資料庫檔案損壞，但不能防整個 Volume 遺失。請在 Railway 啟用平台層備份後設定 `RAILWAY_BACKUP_SCHEDULE_CONFIRMED=true`；完整缺口可在登入後查看 `/api/operational-readiness`。

GitHub Actions 另於每個交易日執行 `provider-canary.py`，直接抽查 NYSE、TWSE 與 TPEX 公開主來源；官方欄位或頁面格式改變時會讓 canary 失敗，避免系統悄悄把缺資料當成正常。

Gunicorn 固定使用一個 process、八個 threads，避免 SQLite 與記憶體狀態被多個 worker 各自複製。若未來要水平擴充，需先把工作排程鎖、限流與快取移至 Redis／Postgres。

背景掃描預設關閉。若需要排程，只能在一個 Service 設定：

```text
SCHEDULER_ENABLE=true
BACKGROUND_WORKERS_ENABLE=true
```

## 資料與回測安全

- 即時來源全部失敗時預設回傳錯誤，不會冒充真實行情。只有明確設定 `ALLOW_DEMO_DATA=true` 才能使用標記過的模擬資料。
- 日線決策只使用完整 K 棒；盤中價格必須走獨立 quote snapshot，避免用尚未收盤的日 K 產生假突破。
- 策略訊號在下一根 K 棒開盤成交，避免用同一根收盤價產生並成交訊號。
- 訊號校準扣除美／台市場預設滑價、佣金與台股交易稅；舊資料仍保留但會以 outcome model 欄位區分。
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

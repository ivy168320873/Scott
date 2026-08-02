# Scott 股市分析系統

Scott 是以 Flask 建置的美股／台股分析工具，包含技術訊號、回測、風險管理、投資組合、警報、每日報告與行動版 AI 助手。

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

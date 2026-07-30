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
- 建立 Claude Code 主控智能體與專業智能體體系
- 遠端寫入防護：`permissions.deny` + PreToolUse hook
- **本次**：測試基礎建設（199 個測試）與核心模組缺值處理修復

## 修改過的檔案

### Claude Code 智能體與安全設定

| 檔案 | 變更 |
|---|---|
| `.claude/agents/claude-code-operator.md` | 新增 — 主控智能體 |
| `.claude/agents/requirements-analyst.md` | 新增 — 需求分析（唯讀） |
| `.claude/agents/software-architect.md` | 新增 — 架構設計 |
| `.claude/agents/implementer.md` | 新增 — 實作 |
| `.claude/agents/tester.md` | 新增 — 測試 |
| `.claude/agents/code-reviewer.md` | 新增 — 唯讀審查（無 Write/Edit） |
| `.claude/agents/debugger.md` | 新增 — 除錯 |
| `.claude/hooks/block-remote-writes.py` | 新增 — PreToolUse hook，阻擋遠端寫入與破壞性指令（137 行） |
| `.claude/settings.json` | 修改 — `agent: claude-code-operator`；`git push`／PR／部署移入 `deny`；`disableBypassPermissionsMode: disable`；註冊 hook |
| `CLAUDE.md` | 新增 — 專案長期規則 |
| `.gitignore` | 修改 — 原本整個忽略 `.claude/`，改為僅忽略本機檔案，讓 agent 定義、hook 與 `settings.json` 進版控 |
| `.env.example` | 新增 — 根目錄環境變數範本（原本只有 `cli_agent/.env.example`） |

### 測試基礎建設與核心模組修復（本次）

| 檔案 | 變更 |
|---|---|
| `tests/conftest.py` | 新增 — sys.path 設定、測試資料工廠、fixtures |
| `tests/test_decision_engine.py` | 新增 — 正規化／清洗層與四個 `run_*` 入口 |
| `tests/test_risk_engine.py` | 新增 — 追價風險評分 |
| `tests/test_sell_engine.py` | 新增 — 賣出決策優先序與邊界值 |
| `tests/test_portfolio_engine.py` | 新增 — 資金效率評分 |
| `tests/test_sector_engine.py` | 新增 — 板塊領先度 |
| `tests/test_sector_map.py` | 新增 — 產業對照表一致性 |
| `tests/test_fetcher.py` | 新增 — 外部 API 失敗路徑（全 monkeypatch + autouse 網路封鎖） |
| `pytest.ini` | 新增 — `testpaths`、`--strict-markers`、`--strict-config` |
| `ohlcv_utils.py` | **新增** — `to_finite_float()` 與 `sanitize_ohlcv()` 的單一實作，四個引擎共用 |
| `decision_engine.py` | 修改 — 重寫 `normalize_list()` 與 `normalize_yahoo()` 使其逐根容忍 None／NaN／字串／非 dict 列；數值工具改用 `ohlcv_utils` |
| `risk_engine.py` | 修改 — 入口呼叫 `sanitize_ohlcv()` |
| `sell_engine.py` | 修改 — 入口呼叫 `sanitize_ohlcv()`；`cost` 非有限值／非數值防護 |
| `portfolio_engine.py` | 修改 — `cost`／`current_price`／`qty` 防護；`holding_days` 不再造假，未滿 7 天不做年化 |
| `sector_engine.py` | 修改 — 逐檔成分股呼叫 `sanitize_ohlcv()` |
| `sector_map.py` | 修改 — `get_sector()` 接受非字串回 None；`get_sector_symbols()` 回傳副本 |
| `requirements-dev.txt` | 修改 — 加入 `pytest>=8.0` |
| `.github/workflows/ci.yml` | 修改 — lint 範圍加入 `tests`；新增 `pytest` 步驟 |
| `docs/project-state.md` | 修改 — 本文件（增量更新） |

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
python -m compileall -q .        # 語法檢查（全 repo）
ruff check cli_agent tests       # Lint（維護範圍）
pytest                           # 單元測試（設定見 pytest.ini）
```

單獨執行某個模組的測試：

```bash
pytest tests/test_decision_engine.py -v
pytest -k "nan or none"          # 只跑缺值相關測試
```

**目前有 224 個測試，全部通過。** 涵蓋模組：

| 測試檔 | 受測模組 | 重點 |
|---|---|---|
| `tests/test_decision_engine.py` | `decision_engine` | 正規化與清洗層、四個 `run_*` 入口 |
| `tests/test_risk_engine.py` | `risk_engine` | 追價風險評分、RSI/SMA 除以零 |
| `tests/test_sell_engine.py` | `sell_engine` | 賣出決策優先序、停損邊界值 |
| `tests/test_portfolio_engine.py` | `portfolio_engine` | 資金效率、`buy_date` 解析 |
| `tests/test_sector_engine.py` | `sector_engine` | 板塊領先度、樣本門檻 |
| `tests/test_sector_map.py` | `sector_map` | 產業對照表一致性（無重複歸屬） |
| `tests/test_fetcher.py` | `fetcher` | 外部 API 失敗路徑（**全部 monkeypatch，不發真實請求**） |

共用測試資料工廠在 `tests/conftest.py`（`make_ohlcv`、`flat_series`、`rising_series`）。

> 測試有效性驗證：把這批測試跑在修復前的 HEAD 程式碼上得到 **85 failed / 139 passed**，
> 證明它們確實能抓到本次修掉的缺值處理 bug，而非恆真斷言。

## 環境變數

完整清單見根目錄 `.env.example`。敏感項目（**不得寫入程式碼或版控**）：

`ANTHROPIC_API_KEY`、`ALPACA_API_KEY`、`ALPACA_SECRET_KEY`、`SECRET_KEY`、`ACCESS_CODE`、`SMTP_PASS`、`FINNHUB_KEY`、`ALPHA_VANTAGE_KEY`、`LINE_NOTIFY_TOKEN`

## 已知問題

1. **測試覆蓋仍侷限於純邏輯模組** — 已覆蓋 `decision_engine`、四個評分引擎、`ohlcv_utils`、`sector_map`、`fetcher` 失敗路徑；但 `app.py` 路由、`backtest.py`、`scheduler.py`、`trader.py`（Alpaca）、`alert_*` 尚無測試。
2. **需要 pandas/numpy 的模組未測** — 本機驗證環境未安裝 pandas/numpy/flask，`analyzer.py`、`backtest.py`、`optimizer.py` 等無法在此環境執行測試；CI 會安裝完整相依，未來可補。
3. **中段缺值會讓區間標籤失準** — `sanitize_ohlcv` 丟棄無效 K 棒後序列被壓實，`risk_engine` 的「近 5 日漲幅」「RSI 14 期」名義區間可能橫跨更多日曆交易日。回傳值已附 `dropped_bars`，但引擎尚未據此在 `reasons` 中提醒使用者。
4. **Lint 覆蓋不足** — CI 現在 lint `cli_agent` 與 `tests`；根目錄數十個模組未納入（既有 E701/E702/E741 告警量大，貿然開啟會淹沒訊號）。
5. **無型別檢查** — 未設定 mypy/pyright。
6. **`app.py` 過大**（約 196KB 單檔），路由與邏輯混雜，維護成本高。
7. **`requirements.txt` 版本全數釘選** — 部署可重現，但需要人工維護升級。

## 尚未完成事項

- 為 `rotation_engine` / `top_tier_decision_engine` 的直接引擎呼叫補上清洗或測試
- 為需要 pandas 的模組（`backtest.py`、`analyzer.py`、`optimizer.py`）補測試
- `app.py` 路由層的整合測試（需要 Flask test client）
- 將 lint 範圍逐步擴大到根目錄模組
- 評估導入型別檢查
- 評估 `app.py` 依功能拆分為 Blueprint

## 下一步建議

1. 把 `sanitize_ohlcv()` 也套用到 `rotation_engine` 與 `top_tier_decision_engine` 的直接呼叫路徑，或改為統一經過 `decision_engine`。
2. 用 `ruff check --statistics .` 盤點根目錄既有告警量，再決定分批啟用的規則集。
3. `app.py` 拆分屬於大型重構，需先由 `software-architect` 提出分階段路徑與風險評估，經確認後再執行。

## 重要技術決策

| 決策 | 理由 |
|---|---|
| 主控智能體設為 `claude-code-operator`（`.claude/settings.json` 的 `agent` 欄位） | 該欄位是 Claude Code 2.1.220 中設定主執行緒 agent 的正式方式，會套用其 system prompt、工具限制與模型 |
| Agent 委派工具使用 `Agent`（非 `Task`） | 此版本中 `Task` 為舊別名，內部正規化為 `Agent`；使用正規名稱避免相依於相容層 |
| `code-reviewer` 不給 Write/Edit | 確保審查獨立性；發現問題交由 `implementer` / `debugger` 修正 |
| 不可逆的遠端寫入採 `permissions.deny` + PreToolUse hook 雙層阻擋；破壞性但可回復的操作才用 `ask` | `git push`、`gh pr create/merge`、部署等一旦執行即不可逆，不應只靠一次確認。hook（`.claude/hooks/block-remote-writes.py`）檢查完整 Bash command，命中時 stderr 輸出原因並 `exit 2` |
| 缺值清洗下沉到**各引擎入口**（`ohlcv_utils.sanitize_ohlcv`），而非只做在整合層 | 初版只在 `decision_engine.run_*` 清洗，但 `app.py:4175`、`top_tier_decision_engine:80/86/99`、`daily_report_engine:307`、`rotation_engine:415`、`stress_test_engine:351`、`cli_agent/stock_tools:948` 都直接呼叫引擎。這些路徑收到全 NaN 資料時會回 `ok=True, level="LOW", "風險可控，可依訊號介入"`——把「沒有資料」講成投資結論 |
| `sanitize_ohlcv` 只把 `closes` 列為必要序列 | `sell_engine`／`portfolio_engine` 本來就只用 closes。若要求五個序列齊全，資料源缺 volume 時停損告警會**靜默停止觸發**——不發告警是會造成實際虧損的失效模式 |
| 長度不符的輔助序列整條忽略，而非尾端對齊 | 尾端對齊會把第 2 根的 close 配上第 1 根的 open，讓「爆量長上影」算出假訊號。忽略後回退為當根 close，寧可少一個訊號也不要假訊號 |
| 缺值一律「丟棄該筆索引」而非補值 | 補值（前值填充／內插）會捏造市場資料，違反專案的資料誠實原則。丟棄後若樣本不足 20 筆，引擎會回 `ok=False` 明確告知資料不足 |
| `holding_days` 一律回報真實值（含 0 與 `None`），未滿 7 天不計算年化報酬 | 曾用「回退為 30 天」處理，但那會直接顯示在 `detail.holding_days` 上，等於**對使用者謊報持倉時間**；而且只擋了 0 天，持有 1 天的 +10% 仍會年化成 3650% 並餵進評分。改為保留真實天數、未達門檻就跳過年化區塊並在 `reasons` 說明；未來日期另以 `warning_flags` 標記為資料錯誤 |
| 測試以「行為契約」為斷言對象（`ok`／`level`／分數區間），不斷言具體分數值 | 評分權重會隨策略調整而變動，斷言具體分數會讓測試變成阻礙；斷言契約與邊界則能長期存活 |
| `.gitignore` 由整個忽略 `.claude/` 改為選擇性忽略 | 原設定會導致共用的 agent 定義與專案 settings 無法進版控；改為只忽略 `settings.local.json` 等本機檔案 |

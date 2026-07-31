# 車銷 CRM · 汽車業務客戶追蹤系統

給汽車業務使用的行動優先 CRM。可在手機瀏覽器操作，並加入主畫面像 App 一樣使用。

## 功能

| 功能 | 說明 |
|---|---|
| 客戶資料與購車需求 | 姓名、電話、來源、意向車型／等級／顏色、預算區間、購車時程、付款方式 |
| 銷售階段追蹤 | 新客戶 → 已聯繫 → 已試乘 → 已報價 → 議價中 → 已成交／已戰敗 |
| 今日跟進與逾期待辦 | 首頁優先顯示逾期（紅色）與今日待辦，一鍵撥號、一鍵完成 |
| 試乘紀錄 | 日期、車型、滿意度 1–5、客戶回饋 |
| 報價版本紀錄 | 同一客戶多版本並存（v1、v2…），保留完整議價歷史，不覆蓋舊版 |
| 舊車估價紀錄 | 品牌、車型、年份、里程、車況、估價金額 |
| 首頁儀表板 | 今天該聯絡誰、熱門客戶優先度、成交進度漏斗與成交率 |

## 在電腦上啟動

```bash
cd car_crm
pip install -r ../requirements.txt     # 只需要 Flask，已包含在其中
python app.py
```

看到這行就成功了：

```
汽車業務 CRM 啟動 → http://localhost:5001
⚠ 未設定 CAR_CRM_ACCESS_CODE，僅本機可連線（手機無法存取）。
```

用電腦瀏覽器開 <http://localhost:5001> 即可操作。

> 資料庫檔 `car_crm.db` 會在首次啟動時自動建立於當前目錄，不需要手動初始化。

## 在手機上開啟

### 先設定存取碼（必要）

本系統存放真實客戶姓名與手機號碼。**未設定存取碼時只綁定 `127.0.0.1`，手機連不上** ——
這是刻意的：讓「對整個區網開放」變成你主動的選擇，而不是預設。

```bash
CAR_CRM_ACCESS_CODE=你自己設的密碼 python app.py
```

啟動訊息會變成：

```
汽車業務 CRM 啟動 → http://localhost:5001
手機請用電腦區網 IP：http://<電腦IP>:5001
已啟用存取碼保護。
```

手機第一次開啟會要求輸入存取碼，通過後才看得到客戶資料。

### 連線步驟

手機與電腦必須連到**同一個 Wi-Fi**。

**1. 查出電腦的區網 IP**

```bash
# macOS
ipconfig getifaddr en0
# Linux
hostname -I | awk '{print $1}'
# Windows（找「IPv4 位址」）
ipconfig
```

會得到類似 `192.168.1.23` 的位址。

**2. 手機瀏覽器開啟**

```
http://192.168.1.23:5001
```

（把 IP 換成上一步查到的）

**3. 加入主畫面**

- **iPhone（Safari）**：點下方「分享」→ 選「加入主畫面」→ 「新增」
- **Android（Chrome）**：點右上角「⋮」→ 選「加入主畫面」或「安裝應用程式」

加入後從主畫面開啟會是全螢幕、沒有網址列，操作起來與一般 App 相同。

> iOS 必須用 **Safari** 才能加入主畫面，Chrome 不支援。

**關於 PWA 的誠實說明**：以區網 IP + HTTP 連線時不是瀏覽器認定的 secure context，
因此 **Service Worker 不會註冊、沒有離線快取**，Android Chrome 也只會出現
「加入主畫面」捷徑而非正式的「安裝應用程式」。全螢幕模式與主畫面圖示仍可正常運作。
要取得完整 PWA 能力需要 HTTPS。

### 連不上的排查

| 症狀 | 處理 |
|---|---|
| 手機開不了網頁 | 先確認**已設定 `CAR_CRM_ACCESS_CODE`**（未設定時只綁本機）；再確認兩台裝置在同一 Wi-Fi；必要時放行防火牆 5001 埠 |
| 只有電腦能開 | 確認啟動訊息有「已啟用存取碼保護」與 `Running on all addresses (0.0.0.0)` |
| 沒有「加入主畫面」選項 | iOS 請改用 Safari；Android 請用 Chrome |
| 一直要求輸入存取碼 | 瀏覽器需允許 cookie；無痕模式關閉後 session 會失效 |

## 設定

| 環境變數 | 預設值 | 說明 |
|---|---|---|
| `CAR_CRM_ACCESS_CODE` | 未設定 | **設定後才會綁 `0.0.0.0` 供手機連線**，並啟用登入 |
| `CAR_CRM_DB` | `./car_crm.db` | SQLite 資料庫路徑 |
| `CAR_CRM_PORT` | `5001` | 監聽埠 |
| `CAR_CRM_SECRET_KEY` | 每次啟動隨機產生 | session 簽章金鑰。不設定時重啟會需要重新登入 |

```bash
CAR_CRM_ACCESS_CODE=mycode CAR_CRM_PORT=8080 CAR_CRM_DB=/data/crm.db python app.py
```

## 測試

```bash
cd ..                                  # 回到專案根目錄
pytest tests/test_car_crm_service.py tests/test_car_crm_routes.py
```

## 架構

```
car_crm/
├── app.py          Flask 路由（HTTP 層）
├── service.py      商業邏輯（可獨立測試，不需啟動 HTTP）
├── db.py           SQLite schema 與連線
├── templates/      Jinja 模板（行動優先）
└── static/         PWA manifest、service worker、圖示
```

分層原則：路由只負責解析請求與渲染，所有規則（報價版本遞增、階段推進、熱門客戶評分）都在 `service.py`，因此能在不啟動伺服器的情況下測試。

## 安全性與目前限制

已具備：存取碼登入、CSRF token、`Cache-Control: no-store`、`X-Frame-Options`、
未設存取碼時拒絕對外綁定。客戶資料庫 `car_crm.db` 已被 `.gitignore` 的 `*.db` 涵蓋，
不會誤入版控。

**仍然不足，使用前請理解：**

- **傳輸為純 HTTP，沒有 HTTPS。** 同一個區網內的其他裝置理論上可被動側錄流量，
  包含你輸入的存取碼與客戶手機號碼。
- **只有單一存取碼，沒有個別帳號。** 無法區分是誰做的操作，也沒有操作紀錄。
- 建議只在**家用 Wi-Fi** 使用。展示間／公司 Wi-Fi 通常有訪客與非授權裝置，
  風險明顯較高；公共 Wi-Fi（咖啡廳、機場）請勿使用。
- **不要**將此服務對公開網際網路開放（勿設 port forwarding／內網穿透）。

多人共用或對外開放前，必須先加上 HTTPS 與個別帳號。

其他限制：

- 單機 SQLite，未設計多業務同時寫入
- 無資料匯出／備份功能（直接備份 `car_crm.db` 即可）
- 舊車估價為手動輸入，未串接行情資料庫
- 圖示僅提供 SVG，iOS 主畫面圖示可能顯示為網頁截圖（需補 PNG 才能完全正確）

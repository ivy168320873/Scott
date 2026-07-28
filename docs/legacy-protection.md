# 既有股票系統保護機制

> 本文記錄 Scott Studio 與既有股市分析系統的隔離設計，以及保證不回歸的驗證方式。
> 文件性質：`architecture` = 現在是什麼。

---

## 1. 核心原則

Scott Studio 是 **加法**，不是改寫。既有股票系統的程式碼、資料、路由、
啟動方式與部署設定全部維持原樣。

驗證方式不是宣告，而是 **可執行的回歸測試**（`tests/legacy/`，47 個測試），
且在 CI 中先於 Studio 測試執行 —— 失敗即代表既有系統被破壞。

---

## 2. 七層隔離

| # | 層級 | 隔離方式 |
| --- | --- | --- |
| 1 | **程式碼** | Studio 全部位於 `studio/` 套件；`app.py` 及 40 個股票模組零修改 |
| 2 | **相依** | `requirements-studio.txt` 與 `requirements.txt` 分離；股票部署不安裝 Studio 套件 |
| 3 | **資料表** | 所有 Studio 表帶 `studio_` 前綴；Alembic 只管理 `studio_*` |
| 4 | **資料庫連線** | Studio 用獨立連線字串（`STUDIO_DATABASE_URL`），不觸碰 `user_data.db` 等既有檔案 |
| 5 | **API 路徑** | Studio API 位於 `/api/v1/studio/*`；既有 `/api/*` 完全不受影響 |
| 6 | **頁面路徑** | Studio 頁面與文件收斂於 `/studio/*`；首頁 `/` 仍為股票系統 |
| 7 | **執行程序** | 股票 app 走 `python app.py`；Studio 走 `studio_server.py`，兩者可獨立執行 |

---

## 3. 併存機制

```
                    ┌─────────────────────────────┐
  studio_server.py  │  FastAPI（ASGI 主體）        │
                    │                             │
                    │  /api/v1/studio/*  → Studio │
                    │  /studio/*         → Studio │
                    │                             │
                    │  /  (mount)                 │
                    │    └─ WSGIMiddleware        │
                    │         └─ Flask (app.py)   │
                    │              /              │
                    │              /login         │
                    │              /api/health    │
                    │              ... 112 條路由  │
                    └─────────────────────────────┘
```

**關鍵性質**：

- Flask 掛在 `/`，所有既有路徑**字面不變**（不加前綴）
- Flask 匯入失敗時，Studio 仍可啟動（降級而非崩潰）
- `python app.py` 完全不經過 `studio_server.py`，不需要任何 Studio 相依

---

## 4. 啟動方式對照

| 情境 | 指令 | 需要的服務 |
| --- | --- | --- |
| 只跑股票系統（現況部署） | `python app.py` | 無 |
| 只跑股票系統（原腳本） | `./run.sh` | 無 |
| 股票 + Studio | `uvicorn studio_server:application` | 無（預設 SQLite + local 儲存 + inline 任務） |
| Studio 完整堆疊 | `docker compose ... up --build` | PostgreSQL / Redis / MinIO |

**Redis、Celery worker、PostgreSQL、MinIO、Studio 前端一律不是股票系統的
啟動必要條件。** Studio 本身在沒有它們時也能以降級模式運作。

---

## 5. 回歸測試覆蓋

`tests/legacy/test_stock_app_regression.py`（34 個）：

| 分類 | 覆蓋內容 |
| --- | --- |
| 首頁 | `GET /` 回 200；仍為股票頁面而非 Studio Dashboard |
| 登入頁 | `GET /login` 回 200 且含表單 |
| 登入成功 | 正確認識碼 → 302 + session 寫入正確 SHA-256 |
| 登入失敗 | 錯誤認識碼 → 200 重新渲染 + 不建立 session |
| 登入限流 | 連續 5 次失敗後鎖定 |
| 登出 | session 清除 + 轉回登入頁 |
| 權限 | 未登入頁面轉址；未登入 API 回 JSON 401；登入頁本身免驗證 |
| Session 設定 | SameSite=Lax、30 天有效期、secret_key 存在 |
| 路由 | 16 條核心路由存在；總數 ≥ 112；Studio 路徑不在 Flask 上 |
| 核心 API | `/api/health`、`/api/env-check`、`/api/user/data` 正常 |
| 資料庫 | `kv` 表與 key/value/ts 欄位語義不變；無 `studio_` 表混入 |
| Migration | 不引用任何股票資料表名 |
| 獨立運作 | 封鎖 redis/celery/fastapi/sqlalchemy/studio 後仍能匯入並服務首頁 |
| 模組洩漏 | 匯入 `app` 後 `sys.modules` 不含 `studio` |
| 部署 | Procfile `web:` 不變、railway.json 不變、run.sh 不變 |
| 相依 | requirements.txt 未混入 Studio 套件；app.py 無 studio 引用 |

`tests/legacy/test_mounted_coexistence.py`（13 個）：

| 分類 | 覆蓋內容 |
| --- | --- |
| 掛載後路徑 | `/`、`/login`、`/api/health`、`/api/user/data` 行為不變 |
| 同時可用 | Studio API 與股票 API 並存不互相攔截 |
| **Session 穿透** | 登入 → cookie 設定 → 帶回後可進首頁（WSGI 橋接最高風險項） |
| 掛載後權限 | 未登入 API 仍回 JSON 401；錯誤認識碼行為不變 |
| 雜湊語義 | session 認證值仍為 SHA-256 |
| 背景排程 | 既有 3 條背景執行緒仍啟動 |
| Hook 污染 | Flask `before_request` 未被外部模組註冊 |

---

## 6. CI 保護

`.github/workflows/ci.yml` 中，股票回歸測試為 **阻斷性步驟**，
且排在 Studio 測試之前：

```yaml
- name: Stock app regression tests (blocking)
  run: python -m pytest tests/legacy -q

- name: Assert migrations never touch legacy tables
  run: |
    if grep -rlnE "user_data|observation_engine|..." migrations/versions/; then
      exit 1
    fi
```

---

## 7. 回滾

Studio 為純新增，回滾不影響股票系統：

```bash
# 完全移除 Studio（股票系統不受影響）
git rm -r studio/ studio_server.py migrations/ deploy/ tests/ \
         alembic.ini requirements-studio.txt pyproject.toml
git checkout <base> -- Procfile .github/workflows/ci.yml \
         requirements-dev.txt .gitignore
```

基準 commit：`9fb1d7c`

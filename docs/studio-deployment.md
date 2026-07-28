# Scott Studio 部署指南

> **既有股票系統不受影響。** `python app.py` 仍是獨立、完整、不需要任何
> Studio 相依的啟動方式。本文只描述 Studio。

---

## 1. 本機開發（最少依賴）

預設組態不需要 PostgreSQL、Redis 或 MinIO：

```bash
pip install -r requirements-studio.txt

# 建立資料表（SQLite）
python -m alembic upgrade head

# 啟動：/ 是股票系統，/studio 是 Studio，/api/v1/studio 是 API
uvicorn studio_server:application --reload --port 8000
```

預設值：SQLite (`./studio.db`)、本機檔案儲存 (`./studio_storage/`)、
inline 任務執行（背景執行緒）。

前端開發：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173/studio/
```

前端建置後，`uvicorn studio_server:application` 會直接在 `/studio` 提供服務：

```bash
cd frontend && npm run build
```

---

## 2. Docker Compose（完整堆疊）

```bash
cp deploy/compose/.env.example deploy/compose/.env
# 編輯 .env，至少改掉密碼並填入需要的 AI 供應商金鑰

docker compose --env-file deploy/compose/.env \
  -f deploy/compose/docker-compose.yml up --build
```

服務：

| 服務 | 說明 | 埠 |
| --- | --- | --- |
| `postgres` | Studio 資料庫 | 5432 |
| `redis` | Celery broker | 6379 |
| `minio` | S3 相容物件儲存 | 9000 / 9001 |
| `studio-init` | 一次性：建 bucket + 執行 migration | — |
| `backend` | Studio API + 既有股票 app | 8000 |
| `worker` | Celery worker | — |
| `frontend` | nginx 提供前端 | 7788 |

`backend` 與 `worker` 只會在 `studio-init` 成功結束後才啟動，
避免在半初始化狀態下運作。

---

## 3. Migration

```bash
# 套用到最新
python -m alembic upgrade head

# 檢查目前版本
python -m alembic current

# 模型改動後產生新 migration
python -m alembic revision --autogenerate -m "描述"

# 回滾一版
python -m alembic downgrade -1
```

Alembic **只管理 `studio_*` 資料表**。既有股票系統的 SQLite 檔案
（`user_data.db` 等）完全不在管理範圍內，CI 有 grep 關卡防止誤觸。

---

## 4. Worker

```bash
# Celery（需要 Redis）
celery -A studio.tasks.celery_app:celery_app worker -l info

# 或不啟 worker，改用 inline 模式
export STUDIO_TASK_EXECUTION_MODE=inline
```

`inline` 模式仍會把任務狀態寫入資料庫，因此查詢、取消與重試都正常運作，
只是不具備跨 process 擴展能力。Celery 派送失敗時會自動降級為 inline，
所以 Redis 故障不會讓 API 請求失敗。

---

## 5. 供應商設定

**金鑰只透過環境變數提供，絕不寫入資料庫或程式碼。**

1. 在部署環境設定環境變數，例如 `ANTHROPIC_API_KEY`
2. 在 Studio 設定頁新增供應商，`api_key_env` 填 **變數名稱**（`ANTHROPIC_API_KEY`），不是金鑰值
3. 新增模型，指定 `model_id`（例如 `claude-opus-5`）與類別
4. 按「測試」確認設定就緒

介面只會顯示「金鑰是否已設定」，不會顯示內容。若把真正的金鑰貼進
`api_key_env`，表單會拒絕（偵測 `sk-` 前綴、空白、過長等特徵）。

目前支援：

| 類型 | 文字 | 圖片 | 影片 |
| --- | --- | --- | --- |
| `anthropic` | ✅ | — | — |
| `openai` / `openai_compatible` / `gemini` | ✅ | ✅ | ✅ |

---

## 6. 環境變數

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `STUDIO_DATABASE_URL` | `sqlite+aiosqlite:///./studio.db` | Studio 資料庫（與股票系統完全分離） |
| `STUDIO_REDIS_URL` | `redis://localhost:6379/0` | Redis |
| `STUDIO_TASK_EXECUTION_MODE` | `inline` | `celery` / `inline` |
| `STUDIO_STORAGE_BACKEND` | `local` | `s3` / `local` |
| `STUDIO_S3_ENDPOINT_URL` | — | S3 相容服務位址 |
| `STUDIO_S3_ACCESS_KEY_ENV` | `STUDIO_S3_ACCESS_KEY_ID` | S3 金鑰**變數名稱** |
| `STUDIO_CORS_ORIGINS` | localhost:5173 | 允許的前端來源 |
| `STUDIO_API_BASE_URL` | `""` | 前端使用的後端位址；同源留空 |
| `SECRET_KEY` | — | 與股票系統共用，用於驗證 session cookie |
| `ACCESS_CODE` | `""` | 與股票系統共用；留空代表不需登入 |

---

## 7. 備份與回滾

### 備份

```bash
# PostgreSQL
pg_dump -h localhost -U scott scott_studio > studio-backup.sql

# SQLite
cp studio.db studio-backup.db

# 物件儲存（MinIO）
mc mirror local/scott-studio-assets ./assets-backup
```

既有股票資料（`user_data.db` 等）請依原有方式備份 —— Studio 從不觸碰它們。

### 回滾 Studio

```bash
# 回滾 migration
python -m alembic downgrade -1

# 完全移除 Studio（股票系統不受影響）
git rm -r studio/ studio_server.py migrations/ deploy/ frontend/ tests/ \
         alembic.ini requirements-studio.txt pyproject.toml
git checkout <base> -- Procfile .github/workflows/ci.yml \
         requirements-dev.txt .gitignore
```

基準 commit：`9fb1d7c`

---

## 8. 正式部署建議

1. **不要改動既有 Railway 部署。** 目前 `railway.json` 的
   `startCommand` 仍是 `python app.py`，股票系統照常運作。
2. Studio 建議獨立部署（Docker Compose 或另一個 Railway 服務），
   兩者共用 `SECRET_KEY` 與 `ACCESS_CODE` 即可共用登入。
3. Production 應使用 PostgreSQL + Redis + S3，不要用 SQLite 與本機儲存。
4. Celery worker 至少一個獨立 process；影片生成耗時以分鐘計。
5. `STUDIO_DEBUG` 保持 `false`。
6. 供應商金鑰以部署平台的 secret 管理注入，不要放進映像檔。

### 合併前必須確認（尚未解決）

見 `CONSTRAINTS.md` §E：遠端不存在 `main`，需先確認 GitHub 預設分支、
Railway 實際部署分支，以及 `9fb1d7c` 是否為正式環境基準。

# Scott Studio 實作狀態

> **單一事實來源。** 任何新 Session 請先讀本檔，再讀 `docs/jellyfish-implementation-plan.md`。
> 分支：`feature/jellyfish-parity`
> 最後更新：Phase 0 完成

---

## 進度總覽

| Phase | 內容 | 狀態 |
| --- | --- | --- |
| 0 | 分析與保護措施 | ✅ 完成 |
| 1 | 基礎架構 | ⬜ 未開始 |
| 2 | 核心資料模型 | ⬜ 未開始 |
| 3 | API 與 OpenAPI | ⬜ 未開始 |
| 4 | 非同步任務中心 | ⬜ 未開始 |
| 5 | AI 腳本與分鏡流程 | ⬜ 未開始 |
| 6 | 圖片與影片生成 | ⬜ 未開始 |
| 7 | 前端完整工作平台 | ⬜ 未開始 |
| 8 | 測試、文件與部署 | ⬜ 未開始 |

---

## Phase 0 — 分析與保護措施 ✅

- [x] 建立 `feature/jellyfish-parity` 分支
- [x] Clone 並完整閱讀 Jellyfish
  - [x] README.md
  - [x] AGENTS.md（語意約定、頁面職責、完成標準）
  - [x] backend（models / api / services / core / chains / tasks）
  - [x] front（package.json、目錄結構、OpenAPI 產生流程）
  - [x] deploy/compose（docker-compose.yml、.env.example）
  - [x] 資料模型（Project / Chapter / Shot / 資產 / Task / Provider）
  - [x] API 路由結構
  - [x] 任務系統（GenerationTask / GenerationTaskLink / TaskExecutorRegistry）
  - [x] 模型供應商管理（Provider / Model / ModelSettings）
- [x] 盤點 Scott 現有程式
- [x] `docs/scott-architecture.md`
- [x] `docs/jellyfish-gap-analysis.md`
- [x] `docs/jellyfish-implementation-plan.md`
- [x] 確認既有啟動方式（`python app.py`）與 CI（compileall + ruff）
- [x] 回滾說明（`docs/scott-architecture.md` §8）

### 關鍵結論

Scott（股市分析）與 Jellyfish（AI 短劇）業務零重疊。
改造方式為 **加法**：在 Scott 品牌下新增 Scott Studio 平台，既有股市系統原樣保留。

---

## 下一步：Phase 1 — 基礎架構

### 精確待建檔案

| 檔案 | 內容 |
| --- | --- |
| `studio/__init__.py` | 套件入口 |
| `studio/config.py` | pydantic-settings；所有金鑰走環境變數 |
| `studio/core/db.py` | SQLAlchemy async engine / session / Base |
| `studio/core/redis_client.py` | Redis 連線 |
| `studio/core/storage.py` | 物件儲存抽象（S3 / local） |
| `studio/core/errors.py` | 統一錯誤型別 |
| `studio/tasks/celery_app.py` | Celery app + inline fallback |
| `studio/api/v1/routes/health.py` | health endpoint |
| `studio/main.py` | FastAPI app 組裝 |
| `studio_server.py` | ASGI 進入點（FastAPI + Flask via WSGIMiddleware） |
| `requirements-studio.txt` | Studio 相依（與主 app 分離） |
| `deploy/compose/docker-compose.yml` | postgres / redis / minio / backend / worker / frontend |
| `deploy/compose/.env.example` | 環境變數範本 |
| `deploy/docker/backend.Dockerfile` | 後端映像 |
| `deploy/docker/frontend.Dockerfile` | 前端映像 |

### Phase 1 驗收指令

```bash
python -m compileall -q studio studio_server.py
python -c "import studio.config"
docker compose -f deploy/compose/docker-compose.yml config -q
```

---

## 不可違反的邊界

1. 不修改 `main` 分支
2. 不刪除／改寫既有股市模組（約 40 個 `.py`）
3. 不改動既有 SQLite 資料表（`user_data.db` 等）
4. `python app.py` 必須持續可用
5. 金鑰只從環境變數讀取；DB 只存環境變數 **名稱**
6. 禁止 TODO placeholder、假 API、永遠成功的 mock

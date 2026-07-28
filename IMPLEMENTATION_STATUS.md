# Scott Studio 實作狀態

> **單一事實來源。** 任何新 Session 請先讀本檔，再讀 `docs/jellyfish-implementation-plan.md`。
> 分支：`feature/jellyfish-parity`
> 最後更新：Phase 2 完成

---

## 進度總覽

| Phase | 內容 | 狀態 |
| --- | --- | --- |
| 0 | 分析與保護措施 | ✅ 完成 |
| 1 | 基礎架構 | ✅ 完成 |
| 2 | 核心資料模型 | ✅ 完成 |
| 3 | API 與 OpenAPI | ⬜ 未開始 |
| 4 | 非同步任務中心 | ⬜ 未開始 |
| 5 | AI 腳本與分鏡流程 | ⬜ 未開始 |
| 6 | 圖片與影片生成 | ⬜ 未開始 |
| 7 | 前端完整工作平台 | ⬜ 未開始 |
| 8 | 測試、文件與部署 | ⬜ 未開始 |

---

## Phase 0 — 分析與保護措施 ✅

- [x] 建立 `feature/jellyfish-parity` 分支
- [x] Clone 並完整閱讀 Jellyfish（README / AGENTS.md / backend / front / deploy / 資料模型 / API 路由 / 任務系統 / 供應商管理 / compose / OpenAPI 流程）
- [x] 盤點 Scott 現有程式
- [x] `docs/scott-architecture.md`
- [x] `docs/jellyfish-gap-analysis.md`
- [x] `docs/jellyfish-implementation-plan.md`
- [x] 確認既有啟動方式與 CI
- [x] 回滾說明（`docs/scott-architecture.md` §8）

### 關鍵結論

Scott（股市分析）與 Jellyfish（AI 短劇）業務零重疊。
改造方式為 **加法**：在 Scott 品牌下新增 Scott Studio 平台，既有股市系統原樣保留。

---

## Phase 1 — 基礎架構 ✅

- [x] `studio/config.py` — pydantic-settings；密鑰只由環境變數解析（`resolve_secret`）
- [x] `studio/core/db.py` — SQLAlchemy 2.0 async engine / session / Base / TimestampMixin；SQLite 外鍵強制開啟
- [x] `studio/core/redis_client.py` — async Redis，延遲連線
- [x] `studio/core/storage.py` — `ObjectStorage` 抽象 + S3 / local 兩種實作，含路徑穿越防護
- [x] `studio/core/errors.py` — 統一錯誤型別 + 供應商錯誤標準化
- [x] `studio/core/ids.py` — 帶前綴字串主鍵
- [x] `studio/core/deps.py` — FastAPI 相依注入（DbSession / Paging / Storage）
- [x] `studio/schemas/common.py` — 統一回應信封 `ApiResponse` + `Page`
- [x] `studio/api/v1/routes/health.py` — 元件級健康檢查 + liveness 探針
- [x] `studio/main.py` — FastAPI 組裝 + 全部錯誤走統一信封（含 404/405/422/500）
- [x] `studio/tasks/celery_app.py` — Celery 設定 + inline 降級模式
- [x] `studio/scripts/init.py` — bucket 建立 + migration（compose 一次性服務）
- [x] `studio_server.py` — ASGI 進入點，Flask 經 `WSGIMiddleware` 掛在 `/`
- [x] Alembic：`alembic.ini` + `migrations/env.py`（async，連線字串走環境變數）
- [x] `deploy/compose/docker-compose.yml` — postgres / redis / minio / studio-init / backend / worker / frontend
- [x] `deploy/compose/.env.example`
- [x] `deploy/docker/backend.Dockerfile`（含 HEALTHCHECK）
- [x] `deploy/docker/frontend.Dockerfile` + `nginx.conf` + 執行期環境注入腳本
- [x] `requirements-studio.txt`（版本全部釘選並實際安裝驗證）
- [x] `pyproject.toml` — ruff + pytest 設定
- [x] `tests/studio/test_infrastructure.py` — 24 個測試
- [x] CI 擴充：ruff / pytest / alembic / compose config

### Phase 1 驗證結果

| 指令 | 結果 |
| --- | --- |
| `ruff check studio studio_server.py tests migrations` | ✅ All checks passed |
| `python -m pytest tests -q` | ✅ 24 passed |
| `python -m compileall -q .` | ✅ 通過 |
| `python -m alembic current` | ✅ 設定可載入 |
| `docker compose ... config -q` | ✅ 設定有效 |

### 已驗證的關鍵行為

- Studio 與既有 Flask app 併存：`/`、`/login` 路徑與內容完全不變，`/api/v1/studio/*` 同時可用
- Flask 匯入失敗時 Studio 仍可獨立啟動（降級而非崩潰）
- 所有錯誤回應（404 / 405 / 422 / 500 / Service 層錯誤）形狀一致

---

## Phase 2 — 核心資料模型 ✅

- [x] `studio/models/types.py` — 25 個列舉；`ShotStatus` 只有 `pending`/`ready`
- [x] `studio/models/project.py` — `Project`、`Chapter`
- [x] `studio/models/shot.py` — `Shot`、`ShotDetail`、`ShotFrame`、`ShotDialogue`、`ShotExtractedCandidate`、`ShotDialogueCandidate`
- [x] `studio/models/asset.py` — `Character`、`Actor`、`Scene`、`Prop`、`Costume`、`AssetImage`、`ShotAssetLink`
- [x] `studio/models/file.py` — `FileItem`、`FileUsage`
- [x] `studio/models/task.py` — `GenerationTask`、`GenerationTaskLink`
- [x] `studio/models/provider.py` — `Provider`（`api_key_env`）、`Model`、`ModelSettings`、`PromptTemplate`
- [x] `studio/core/db.py::enum_column()` — 列舉欄位型別
- [x] `migrations/versions/9b2744d7f916_initial_studio_schema.py` — 23 張表
- [x] `tests/studio/conftest.py`、`tests/studio/test_models.py` — 25 個測試

### Phase 2 驗證結果

| 指令 | 結果 |
| --- | --- |
| `ruff check studio studio_server.py tests migrations` | ✅ clean |
| `python -m pytest tests -q` | ✅ 49 passed |
| `python -m alembic upgrade head` | ✅ 23 張表建立 |
| autogenerate drift 檢查 | ✅ 0 個操作（模型與 migration 一致） |
| `python -m compileall -q .` | ✅ 通過 |

### Phase 2 修正的缺陷

列舉欄位原本宣告為 `String(n)` 但註記為 `Mapped[SomeEnum]`，
導致讀回來是 `str`，`task.status is TaskStatus.running` 永遠為 False 且無任何錯誤提示。
已新增 `enum_column()` 並轉換全部 26 個列舉欄位。

---

## 下一步：Phase 3 — API 與 OpenAPI

### 建議順序

1. Repository 層 → 2. Service 層 → 3. Schemas → 4. Routes → 5. OpenAPI 匯出

### 精確待建檔案

| 檔案 | 內容 |
| --- | --- |
| `studio/repositories/base.py` | 泛型 CRUD repository（get / list / create / update / delete / count） |
| `studio/repositories/project.py` | Project、Chapter 查詢 |
| `studio/repositories/shot.py` | Shot 與子資源查詢 |
| `studio/repositories/asset.py` | 五類資產的多型查詢 |
| `studio/repositories/task.py` | 任務查詢（依狀態、專案、task_kind） |
| `studio/repositories/provider.py` | Provider / Model / PromptTemplate |
| `studio/schemas/project.py` | ProjectCreate / Update / Read、ChapterCreate / Update / Read |
| `studio/schemas/shot.py` | Shot、ShotDetail、Frame、Dialogue、Candidate 的 DTO |
| `studio/schemas/asset.py` | 資產 DTO |
| `studio/schemas/task.py` | TaskRead（含 `elapsed_seconds`、`is_cancellable`） |
| `studio/schemas/provider.py` | ProviderRead（**必須含 `api_key_configured: bool`，不得回傳金鑰**） |
| `studio/services/project.py` | 專案／章節業務邏輯、stats 更新 |
| `studio/services/shot.py` | 分鏡 CRUD、`recompute_shot_status()`、候選確認 |
| `studio/services/asset.py` | 資產 CRUD、名稱查重 |
| `studio/services/provider.py` | 供應商 CRUD、連線測試 |
| `studio/api/v1/routes/projects.py` 等 | 各資源 route，只收參／驗證／組回應 |
| `studio/scripts/export_openapi.py` | 匯出 `frontend/openapi.json` |

### Phase 3 注意事項

1. **API 層不得含業務邏輯** —— 只收參、驗證、權限、組回應
2. `ProviderRead` 絕不可回傳 `api_key_env` 的**值**；只回傳變數名稱與
   `api_key_configured` 布林（用 `studio.config.secret_is_configured()`）
3. 所有回應用 `ApiResponse[T]` 信封
4. 分頁用 `studio.core.deps.Paging`
5. Service 層錯誤用 `studio.core.errors` 的型別，API 層不需 try/except

### Phase 3 驗收指令

```bash
ruff check studio tests
python -m pytest tests -q
python -m studio.scripts.export_openapi   # 應產出 frontend/openapi.json
```

---

## 不可違反的邊界

1. 不修改 `main` 分支
2. 不刪除／改寫既有股市模組（約 40 個 `.py`）
3. 不改動既有 SQLite 資料表（`user_data.db` 等）
4. `python app.py` 必須持續可用
5. 金鑰只從環境變數讀取；DB 只存環境變數 **名稱**
6. 禁止 TODO placeholder、假 API、永遠成功的 mock

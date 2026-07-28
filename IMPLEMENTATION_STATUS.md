# Scott Studio 實作狀態

> **單一事實來源。** 任何新 Session 請先讀本檔，再讀 `docs/jellyfish-implementation-plan.md`。
> 分支：`feature/jellyfish-parity`
> 最後更新：Phase 8 完成（MVP）
>
> **開工前必讀 `CONSTRAINTS.md`（永久約束）。**

---

## 進度總覽

| Phase | 內容 | 狀態 |
| --- | --- | --- |
| 0 | 分析與保護措施 | ✅ 完成 |
| 1 | 基礎架構 | ✅ 完成 |
| 2 | 核心資料模型 | ✅ 完成 |
| 3 | API 與 OpenAPI | ✅ 完成 |
| 4 | 非同步任務中心 | ✅ 完成 |
| 5 | AI 腳本與分鏡流程 | ✅ 完成 |
| 6 | 圖片與影片生成 | ✅ 完成 |
| 7 | 前端完整工作平台 | ✅ 完成 |
| 8 | 測試、文件與部署 | ✅ 完成 |

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

## Phase 3 — API 與 OpenAPI ✅

- [x] `studio/core/security.py` — 與股票系統 Flask session 相容的認證（不 import app.py）
- [x] `studio/repositories/` — 泛型 Repository + 23 個資源 Repository
- [x] `studio/schemas/` — project / shot / asset / provider / task DTO
- [x] `studio/services/` — project / shot / asset / media / provider / task Service
- [x] `studio/api/v1/routes/` — projects / shots / assets / media / providers / tasks
- [x] `studio/scripts/export_openapi.py` — OpenAPI 匯出 + 金鑰掃描
- [x] `frontend/` — package.json、tsconfig、openapi.json、generated client
- [x] `tests/studio/test_api.py` — 62 個 API 測試

### Phase 3 驗證結果

| 指令 | 結果 |
| --- | --- |
| `python -m pytest tests -q` | ✅ **158 passed**（47 legacy + 111 studio） |
| `python -m pytest tests/legacy -q` | ✅ **47 passed** |
| `ruff check studio studio_server.py tests migrations` | ✅ clean |
| `python -m compileall -q .` | ✅ |
| alembic autogenerate drift | ✅ 0 ops |
| `docker compose config -q` | ✅ valid |
| `frontend: tsc --noEmit` | ✅ OK |

### Phase 3 修正的缺陷（皆由測試發現）

1. **`updated_at` 用 SQL 端 `onupdate=func.now()`** → UPDATE 後欄位被標記過期，
   Pydantic 同步序列化時觸發延遲載入 → **每個 PATCH 端點都會 500**。
   改為 Python 端 callable。
2. **`Model.provider` / `Shot.detail` 延遲載入** → async session 下拋 `MissingGreenlet`。
   改為 `selectinload` eager load。
3. **422 處理器本身會 500** → 自訂 validator 拋出的 `ValueError` 被放進 `ctx`，
   無法 JSON 序列化。新增 `_sanitise_validation_errors()`。


---

## Phase 4–8 摘要（Completion Sprint）

| Phase | 內容 | 關鍵產出 |
| --- | --- | --- |
| 4 | 非同步任務系統 | `tasks/runtime.py`（生命週期）、`registry.py`、`dispatch.py`（celery + inline）、`execute.py`、兩階段取消、retry、`recover_stale_tasks` |
| 5 | 腳本與分鏡工作流 | 6 個腳本執行器 + `shot_frame_prompt`、`routes/workflow.py`、Anthropic / OpenAI 相容 Adapter |
| 6 | 圖片與影片生成 | `media_tasks.py`、產物寫入儲存並登記 FileItem、`GenerationTaskLink` 回寫、批次生成 |
| 7 | 前端 MVP | 8 個頁面（Tab 整合）、全部走 generated client、loading/empty/error/retry、防重複送出、響應式 |
| 8 | 整合與部署 | CI 加前端 typecheck/build、`docs/studio-deployment.md`、compose 驗證 |

### 最終驗證結果

| 項目 | 結果 |
| --- | --- |
| 後端測試 | ✅ **208 passed** |
| legacy 回歸（阻斷性） | ✅ **47 passed** |
| ruff | ✅ clean |
| compileall | ✅ |
| alembic upgrade + drift | ✅ 0 ops |
| docker compose config | ✅ valid |
| 前端 tsc --noEmit | ✅ clean |
| 前端 production build | ✅ 成功 |
| app.py / requirements.txt / railway.json / run.sh / templates / static | ✅ 未變更 |

### 已知未執行

- **Live provider integration**：環境無 API 金鑰，未對任何供應商發出真實網路請求。
  請求／回應映射與錯誤處理由 24 個契約測試涵蓋。
- **Docker Compose 實際啟動**：僅驗證 `config -q`，未實跑容器。
- **瀏覽器端對端**：未以真實瀏覽器操作完整流程。

---

## 不可違反的邊界

1. 不修改 `main` 分支
2. 不刪除／改寫既有股市模組（約 40 個 `.py`）
3. 不改動既有 SQLite 資料表（`user_data.db` 等）
4. `python app.py` 必須持續可用
5. 金鑰只從環境變數讀取；DB 只存環境變數 **名稱**
6. 禁止 TODO placeholder、假 API、永遠成功的 mock

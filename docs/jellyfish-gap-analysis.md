# Jellyfish ↔ Scott 差異分析

> 參考專案：https://github.com/Forget-C/Jellyfish（Apache-2.0）
> 分析基準：Jellyfish `main`（2026-07 clone）、Scott `main` @ `9fb1d7c`
> 文件性質：差異事實盤點。實作步驟見 `jellyfish-implementation-plan.md`。

---

## 0. 最重要的前提結論

**Scott 與 Jellyfish 在業務領域上完全沒有重疊。**

- Scott = 股市動能分析與投資決策平台
- Jellyfish = AI 短劇影片生成工作平台

因此本次改造 **不可能是「把 Scott 的股票功能改寫成影片功能」**。
唯一符合「保留 Scott 既有功能與資料」的作法是：

> 在 Scott 品牌下 **新增** 一套完整的 AI 影片／短劇生成平台（Scott Studio），
> 與既有股市分析系統並存，共用部署、認證與 LLM 基礎設施。

任何宣稱「重構股票引擎成為影片引擎」的說法都是不誠實的。
本文的「可重用模組」一節只列出 **真正能重用的東西**，不灌水。

---

## 1. Scott 已有功能

| 分類 | 功能 | 狀態 |
| --- | --- | --- |
| 行情資料 | 台股／美股資料抓取、快取、離線 demo | 完整 |
| 分析評分 | 趨勢、量能、估值、風險、催化、相對強度 | 完整 |
| 決策 | 多層決策引擎、投資委員會、輪動、賣出 | 完整 |
| 風險 | 部位規模、壓力測試、總經風險、市場狀態 | 完整 |
| 回測 | 回測、參數最佳化、交易成本模型 | 完整 |
| 執行 | 下單介接（Alpaca）、監控 | 完整 |
| 告警 | 掃描、格式化、歷史、推播 | 完整 |
| 排程 | APScheduler in-process | 完整（但非分散式） |
| AI | 多角色分析師辯論、SSE 串流、CLI agent | 完整 |
| 認證 | ACCESS_CODE + session + 登入限流 | 完整 |
| 前端 | Jinja server-side 渲染 | 完整（但非 SPA） |

---

## 2. Jellyfish 有、Scott 沒有的功能

### 2.1 業務功能（全部缺席）

| 功能 | Jellyfish 實作位置 | Scott |
| --- | --- | --- |
| Project 專案管理 | `models/studio_projects.py::Project` | ❌ 無 |
| Chapter 章節管理 | `models/studio_projects.py::Chapter` | ❌ 無 |
| Script 腳本編輯 | `Chapter.raw_text` / `condensed_text` | ❌ 無 |
| AI 腳本拆分鏡 | `chains/agents/script_divider_agent.py` | ❌ 無 |
| 腳本優化／簡化 | `script_optimizer_agent.py` / `script_simplifier_agent.py` | ❌ 無 |
| 一致性檢查 | `consistency_checker_agent.py` | ❌ 無 |
| Shot 分鏡 | `models/studio_shots.py::Shot` / `ShotDetail` | ❌ 無 |
| 實體提取 | `element_extractor_agent.py` | ❌ 無 |
| 候選確認流程 | `ShotExtractedCandidate` + `shot_extracted_candidates.py` | ❌ 無 |
| 對白候選 | `ShotExtractedDialogueCandidate` | ❌ 無 |
| Character／Actor | `models/studio_assets.py` | ❌ 無 |
| Scene／Prop／Costume | `models/studio_assets.py` | ❌ 無 |
| 資產圖片管理 | `models/studio_asset_images.py` | ❌ 無 |
| 關鍵幀 | `ShotFrame`（`ShotFrameType`: first/last/key） | ❌ 無 |
| 圖片生成 | `services/studio/generation/asset_image/`、`frame/` | ❌ 無 |
| 影片生成 | `services/studio/generation/video/` | ❌ 無 |
| 影片準備度 | `services/studio/shot_video_readiness.py` | ❌ 無 |
| 批次生成 | `image_tasks.py` 批次預檢 | ❌ 無 |
| 媒體庫 | `models/studio_prompts_files_timeline.py::FileItem` | ❌ 無 |
| 檔案用途追蹤 | `models/studio_file_usages.py` | ❌ 無 |
| 時間線 | `routes/studio/timeline.py` | ❌ 無 |
| Provider 管理 | `models/llm.py::Provider` | ❌ 無（金鑰寫死在 env，無 UI） |
| Model 管理 | `models/llm.py::Model` / `ModelSettings` | ❌ 無 |
| Prompt 模板 | `PromptTemplate` + `PromptCategory` | ❌ 無 |
| 統一任務系統 | `models/task.py::GenerationTask` | ❌ 無 |
| 任務關聯 | `models/task_links.py::GenerationTaskLink` | ❌ 無 |
| 任務中心 UI | `front/src/pages/aiStudio/` | ❌ 無 |

### 2.2 基礎設施（全部缺席）

| 能力 | Jellyfish | Scott |
| --- | --- | --- |
| ORM | SQLAlchemy 2.0 async + `Mapped[]` | ❌ 原生 sqlite3 |
| Migration | `backend/sql/00X-*.sql` 依序套用 | ❌ 無 |
| 關聯式資料庫 | MySQL 9.0 | ❌ SQLite |
| 快取／Broker | Redis 7 | ❌ 無 |
| 分散式任務 | Celery worker | ❌ APScheduler in-process |
| 物件儲存 | RustFS（S3 相容） | ❌ 無 |
| API 規格 | FastAPI 自動 OpenAPI | ❌ 無 |
| 前端型別產生 | `openapi-typescript-codegen` → `src/services/generated/` | ❌ 無 |
| SPA 前端 | React 18 + Vite + TS + antd + zustand | ❌ Jinja template |
| i18n | i18next（zh-CN / en-US） | ❌ 無 |
| 容器化 | Dockerfile + compose（6 服務） | ❌ 無 |
| 健康檢查 | `routes/health.py` + compose healthcheck | ❌ 無 |
| 測試 | 60+ pytest 檔 | ❌ 無 |
| 分層架構 | api / service / repository / integrations / tasks | ❌ 單檔 monolith |
| 共用契約 | `core/contracts/` | ❌ 無 |
| Provider 適配 | `core/integrations/{openai,volcengine}/` | ❌ SDK 直接呼叫 |

---

## 3. 可以直接重用的 Scott 模組

誠實列表 —— 真正能用於影片平台的只有這些：

| Scott 資產 | 重用方式 | 重用程度 |
| --- | --- | --- |
| `analyst.py` 的 LLM 呼叫模式 | 單例 client、環境變數取 key、Thread+Queue 進度推播 | **模式重用**（改寫為 Provider Adapter + Celery） |
| `analyst.py` 的多角色 agent 設計 | system prompt 分角色、多輪編排 | **模式重用**（對應 Jellyfish `chains/agents/`） |
| `app.py` 認證層 | `ACCESS_CODE` + session + 登入限流 | **直接重用**（Studio API 沿用同一 session） |
| `app.py` SSE 端點模式 | 串流進度回前端 | **模式重用**（任務進度串流） |
| 環境變數慣例 | `os.environ.get` + 無硬編碼金鑰 | **直接重用** |
| Railway 部署設定 | `Procfile` / `railway.json` | **擴充重用**（新增 worker process） |
| `.github/workflows/ci.yml` | compileall + ruff | **擴充重用**（加 pytest / tsc / build） |
| `requirements.txt` 釘選慣例 | 版本釘選以確保重現 | **直接重用** |
| `static/manifest.json` / `sw.js` | PWA 設定 | **可重用**（Studio 前端沿用） |

**不可重用**：所有股票分析／決策／回測／風險引擎（約 40 個模組、~700KB）。
這些與影片生成無任何關係，將 **原樣保留、不修改**。

---

## 4. 需要重構的模組

| 模組 | 重構內容 | 破壞性 |
| --- | --- | --- |
| `app.py` | 不改寫既有 route；僅在檔案末端新增 ASGI 掛載點，使 Flask 可與 FastAPI 併存 | 非破壞 |
| `Procfile` | `web:` 改為同時服務 Flask + Studio API；新增 `worker:` | 非破壞（既有 route 路徑不變） |
| `requirements.txt` | 新增 FastAPI / SQLAlchemy / Celery / boto3 等相依 | 非破壞 |
| `.github/workflows/ci.yml` | 新增 pytest、前端 typecheck、compose config 驗證 | 非破壞 |
| `railway.json` | 新增 worker 服務設定 | 非破壞 |

**沒有任何既有股票模組需要重構。**

---

## 5. 需要新增的資料表

全部為新表，**不改動任何既有 SQLite 表**。

### 5.1 專案域

| 表 | 說明 |
| --- | --- |
| `studio_projects` | 專案 |
| `studio_chapters` | 章節（`project_id + index` 唯一） |

### 5.2 分鏡域

| 表 | 說明 |
| --- | --- |
| `studio_shots` | 分鏡主表（`chapter_id + index` 唯一） |
| `studio_shot_details` | 分鏡細節（與 shots 共享主鍵，1:1） |
| `studio_shot_frames` | 關鍵幀（first / last / key） |
| `studio_shot_dialogues` | 對白 |
| `studio_shot_extracted_candidates` | 資產提取候選（pending/linked/ignored） |
| `studio_shot_dialogue_candidates` | 對白提取候選（pending/accepted/ignored） |
| `studio_shot_character_links` | 分鏡↔角色關聯（含出場順序） |

### 5.3 資產域

| 表 | 說明 |
| --- | --- |
| `studio_characters` | 角色 |
| `studio_actors` | 演員 |
| `studio_scenes` | 場景 |
| `studio_props` | 道具 |
| `studio_costumes` | 服裝 |
| `studio_asset_images` | 資產圖片（多視角 FRONT/LEFT/…） |
| `studio_project_actor_links` | 專案/章節/分鏡 ↔ 演員 |
| `studio_project_scene_links` | 專案/章節/分鏡 ↔ 場景 |
| `studio_project_prop_links` | 專案/章節/分鏡 ↔ 道具 |
| `studio_project_costume_links` | 專案/章節/分鏡 ↔ 服裝 |

### 5.4 媒體域

| 表 | 說明 |
| --- | --- |
| `studio_files` | 檔案（image / video） |
| `studio_file_usages` | 檔案用途追蹤（shot_frame / generated_video / …） |

### 5.5 任務域

| 表 | 說明 |
| --- | --- |
| `studio_generation_tasks` | 統一任務表（status / progress / payload / result / error / cancel / 時間戳） |
| `studio_generation_task_links` | 任務 ↔ 業務實體關聯（resource_type + relation_type + relation_entity_id） |

### 5.6 模型基礎設施域

| 表 | 說明 |
| --- | --- |
| `studio_providers` | 供應商（base_url / image_base_url / video_base_url / 金鑰參照） |
| `studio_models` | 模型（category: text/image/video） |
| `studio_model_settings` | 全域預設（單例表） |
| `studio_prompt_templates` | 提示詞模板 |

> 所有表統一加 `studio_` 前綴，避免與 Scott 既有表名衝突。

---

## 6. API 差異

### 6.1 Scott 現況

- Flask route 散落於 `app.py`，無版本前綴、無統一回應信封
- 無 OpenAPI、無型別、無自動產生 client

### 6.2 Jellyfish 結構

```
/api/v1/
  health
  llm/                      供應商、模型、設定
  script-processing/        腳本 AI 處理（非同步）
  studio/projects           專案 CRUD
  studio/chapters           章節 CRUD
  studio/shots              分鏡 CRUD + 子資源
  studio/entities           角色/場景/道具/服裝
  studio/files              檔案上傳/查詢
  studio/prompts            提示詞模板
  studio/image-tasks        圖片生成任務
  studio/shot-character-links
  studio/timeline
  film/task-status          任務狀態查詢
  film/video-request        影片生成請求
  film/generated-video      成品影片
  film/tasks-images
```

### 6.3 Scott 需新增

全部 —— 新增 `/api/studio/v1/*` 命名空間，與既有 Flask route 完全不衝突。
統一回應信封（Jellyfish 有 `tests/test_api_response_envelopes.py` 驗證此契約）。

---

## 7. 前端頁面差異

| 頁面 | Jellyfish | Scott |
| --- | --- | --- |
| Dashboard | ✅ | ❌ |
| Project List | ✅ | ❌ |
| Project Workspace | ✅ `ProjectWorkbench/` | ❌ |
| Chapter Editor | ✅ `chapter/` | ❌ |
| Script Editor | ✅ `editor/` | ❌ |
| Storyboard List | ✅ `shots/` | ❌ |
| Shot Editor（準備） | ✅ `chapter/prep/` | ❌ |
| Shot Generation Studio（生成） | ✅ `shots/components/` | ❌ |
| Character / Scene / Prop / Costume | ✅ `assets/tabs/` | ❌ |
| Media Library | ✅ `files/` | ❌ |
| Task Center | ✅ | ❌ |
| Provider Settings | ✅ `models/` | ❌ |
| Model Settings | ✅ `models/` | ❌ |
| Prompt Template Settings | ✅ `prompts/` | ❌ |

Scott 既有頁面（`index.html` / `agent.html` / `admin.html` / `login.html`）
為股市功能，**保留不動**。

技術差異：Scott 無 npm 生態，需從零建立 `frontend/`（React + Vite + TS）。

---

## 8. 非同步任務差異

| 面向 | Jellyfish | Scott |
| --- | --- | --- |
| 執行載體 | Celery worker（獨立 process） | APScheduler 執行緒（同 process） |
| Broker | Redis | 無 |
| 狀態持久化 | `generation_tasks` 表 | 記憶體（重啟即失） |
| 狀態機 | pending/running/streaming/succeeded/failed/cancelled | 無正式狀態機 |
| 進度 | `progress` 0-100 整數欄位 | Queue 事件（不可查詢） |
| 取消 | `cancel_requested` + `cancel_requested_at` + `cancel_reason` + `cancelled_at` | 不支援 |
| 耗時 | `started_at` / `finished_at` | 不記錄 |
| 重試 | 支援 | 不支援 |
| 恢復 | 重啟後可由 DB 查詢 | 不可能 |
| 路由 | `TaskExecutorRegistry` by `task_kind` | 無 |
| 供應商分派 | `register_task_adapter(task_kind, provider_key)` 雙鍵 | 無 |
| 業務關聯 | `GenerationTaskLink` 分表強外鍵 | 無 |
| 前端 | 全域 Task Center，可回跳業務頁 | 僅 SSE 單次串流 |

**這是最大的架構落差**，也是 Phase 4 的核心。

---

## 9. 部署方式差異

| 面向 | Jellyfish | Scott |
| --- | --- | --- |
| 方式 | Docker Compose（6 服務） | Railway NIXPACKS 單 process |
| 服務 | mysql / redis / rustfs / backend / celery-worker / front | web |
| 初始化 | `backend-init-db` + `mysql-init-sql` 依序 | 無（就地建表） |
| 健康檢查 | mysql / redis 皆有 healthcheck | 無 |
| 啟動相依 | `depends_on: service_healthy` / `service_completed_successfully` | 無 |
| Volume | `mysql_data` / `rustfs_data` | 無（SQLite 檔在容器內） |
| 前端埠 | 7788 | — |
| 後端埠 | 8000 | 5000 |
| 環境檔 | `deploy/compose/.env.example` | 無 |

---

## 10. 潛在相容性與資料遷移風險

| # | 風險 | 等級 | 緩解 |
| --- | --- | --- | --- |
| R1 | 引入 FastAPI 與 Flask 併存，可能造成路由衝突 | 中 | Studio 全部收斂在 `/api/studio/v1` 與 `/studio` 前綴；Flask 掛在 root，經 ASGI `WSGIMiddleware` 代理 |
| R2 | 新增大量 Python 相依可能破壞 Railway 建置 | 中 | 全部釘選版本；CI 加建置驗證；Studio 相依以 `requirements-studio.txt` 分離，主 app 不強制安裝 |
| R3 | SQLite 與 PostgreSQL 行為差異（JSON、時區、autoincrement） | 中 | Studio 統一用 SQLAlchemy；dev 用 SQLite、prod 用 PostgreSQL；避免 DB 專屬語法 |
| R4 | 既有 SQLite 表被誤改 | **高** | Studio 使用 **獨立資料庫連線與獨立表名前綴 `studio_`**；不共用 `user_data.db` |
| R5 | `python app.py` 既有啟動方式失效 | **高** | 保留 `app.py` 的 `__main__` 區塊不動；Studio 走新的 `studio_server.py` 進入點 |
| R6 | Celery worker 在 Railway 需額外 process，成本上升 | 低 | 提供 `TASK_EXECUTION_MODE=inline` fallback，單機可不啟 worker |
| R7 | 物件儲存在 dev 無 S3 | 低 | `STORAGE_BACKEND=local` fallback，寫入 `./studio_storage/` |
| R8 | 前端 build 產物體積影響部署 | 低 | 前端獨立容器；Railway 部署可只跑後端 |
| R9 | API 金鑰若存 DB 有外洩風險 | **高** | Provider 表只存 **環境變數名稱參照**（`api_key_env`），不存明文金鑰 |
| R10 | 既有 CI 因新增檔案而失敗 | 中 | `compileall` 涵蓋全 repo，新檔案必須語法正確；CI 先跑再合併 |

### 資料遷移結論

**本次改造無既有資料需要遷移。**
所有 Studio 資料表皆為新建，既有 SQLite 檔案（`user_data.db` 等）不讀、不寫、不改。
`sql/` 或 Alembic migration 只針對 `studio_*` 表。

---

## 11. 關鍵設計約定（採用自 Jellyfish AGENTS.md）

以下語意約定將被 Scott Studio 沿用：

1. `shot.status` **只表示資訊提取確認狀態**：`pending` / `ready`
   - 不使用 `generating` 作為 shot 狀態
2. 執行時狀態一律來自 **任務系統**，不寫回 shot
3. `video-readiness` **獨立**表示是否具備影片生成條件
   - `shot.status = ready` ≠ 可立即生成影片
4. 頁面職責分離：
   - 分鏡編輯頁 = **準備**（提取、確認、修正）
   - 分鏡工作室 = **生成**（關鍵幀、參數、執行）
   - 任務中心 = **通用狀態面板**（不承載業務細節）
5. API 層只負責收參／驗證／權限／回應組合；Service 層負責業務邏輯與狀態流轉
6. 跨模組 DTO 與 Provider 契約放在 `contracts/`，`tasks` 不定義跨層 DTO

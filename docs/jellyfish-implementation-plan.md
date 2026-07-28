# Scott Studio 實作計畫（Jellyfish parity）

> 文件性質：`plans` = 接下來做什麼。
> 目前進度以 `IMPLEMENTATION_STATUS.md` 為準（該檔為單一事實來源）。
> 分支：`feature/jellyfish-parity`

---

## 0. 目標與邊界

### 目標

在 Scott 品牌下建立一套完整的 **AI 影片／短劇生成工作平台（Scott Studio）**，
功能等級對齊 Jellyfish：專案／腳本／分鏡／資產一致性／多供應商模型／統一非同步任務／
完整前端工作平台／Docker Compose 一鍵部署。

### 硬性邊界（不可違反）

1. 不修改 `main` 分支
2. 不刪除、不改寫任何既有股市分析模組
3. 不改動既有 SQLite 資料表
4. `python app.py` 既有啟動方式必須持續可用
5. 所有金鑰只從環境變數讀取；DB 只存環境變數 **名稱**，不存明文
6. 不使用 TODO placeholder、假 API、永遠成功的 mock 冒充完成

---

## 1. 技術選型決策（已定案）

| 決策點 | 選擇 | 理由 |
| --- | --- | --- |
| Studio 後端框架 | **FastAPI**（與 Flask 併存） | OpenAPI 為需求 #8 的硬性要求，Flask 無原生支援；Jellyfish 同選型 |
| Flask 併存方式 | FastAPI 為 ASGI 主體，Flask 經 `WSGIMiddleware` 掛在 `/` | 既有 route 路徑完全不變 |
| ORM | **SQLAlchemy 2.0**（`Mapped[]` 風格） | 與 Jellyfish 一致，型別友善 |
| Migration | **Alembic** | 標準工具，支援 SQLite 與 PostgreSQL |
| 資料庫 | **PostgreSQL**（prod）／**SQLite**（dev fallback） | Scott 既有生態為 SQLite；PostgreSQL 較 MySQL 更適合 JSON 欄位 |
| 快取／Broker | **Redis** | 與 Jellyfish 一致 |
| 任務執行 | **Celery**；另提供 `inline` fallback | Railway 單 process 環境可降級 |
| 物件儲存 | **S3 相容（MinIO）**；`local` fallback | dev 無需起 MinIO |
| 前端 | **React 18 + Vite + TypeScript** | 與 Jellyfish 一致 |
| 前端 UI | **Ant Design 5** | 與 Jellyfish 一致，元件齊全 |
| 前端狀態 | **zustand** | 與 Jellyfish 一致 |
| API client | **openapi-typescript-codegen** 產生 | 需求 #8 |
| 表名前綴 | `studio_` | 避免與 Scott 既有表衝突 |
| API 前綴 | `/api/studio/v1` | 避免與 Flask route 衝突 |

---

## 2. 目標目錄結構

```
Scott/
├─ app.py                      # 既有 Flask（不動）
├─ <40 個既有股市模組>          # 不動
├─ studio_server.py            # 新：ASGI 進入點（FastAPI + Flask 掛載）
├─ studio/                     # 新：Studio 後端
│  ├─ main.py                  # FastAPI app 組裝
│  ├─ config.py                # pydantic-settings，全部走環境變數
│  ├─ api/
│  │  └─ v1/
│  │     ├─ router.py
│  │     └─ routes/            # 只收參／驗證／回應
│  ├─ services/                # 業務邏輯／狀態流轉
│  ├─ repositories/            # 資料存取
│  ├─ models/                  # SQLAlchemy 模型
│  ├─ schemas/                 # Pydantic DTO
│  ├─ contracts/               # 跨層 DTO + Provider 契約
│  ├─ integrations/            # Provider Adapter 實作
│  ├─ tasks/                   # Celery 任務封裝與 registry
│  ├─ agents/                  # LLM agent（腳本分析等）
│  └─ core/                    # db / storage / redis / errors
├─ migrations/                 # Alembic
├─ frontend/                   # 新：React + Vite
├─ deploy/
│  ├─ docker/
│  └─ compose/
├─ tests/studio/               # 新：pytest
└─ docs/
```

---

## 3. 分階段計畫

### Phase 0 — 分析與保護措施 ✅

- [x] 建立 `feature/jellyfish-parity` 分支
- [x] 完整閱讀 Jellyfish（README / AGENTS.md / backend / front / deploy / models / routes / tasks / providers / compose / OpenAPI 流程）
- [x] 盤點 Scott 現有程式
- [x] `docs/scott-architecture.md`
- [x] `docs/jellyfish-gap-analysis.md`
- [x] `docs/jellyfish-implementation-plan.md`
- [x] 確認既有啟動方式與 CI 驗證方式
- [x] 回滾說明（見 `scott-architecture.md` §8）

### Phase 1 — 基礎架構

- 設定層：`studio/config.py`（pydantic-settings，全部環境變數）
- DB：SQLAlchemy async engine + session，PostgreSQL / SQLite 雙支援
- Redis 連線
- Celery app + `inline` fallback
- 物件儲存抽象：S3 / local 雙後端
- FastAPI app 組裝 + health endpoint
- `studio_server.py`：FastAPI 掛 Flask
- Docker Compose（postgres / redis / minio / backend / worker / frontend）
- `.env.example`
- Health check + startup dependency

**驗收**：`python -c "import studio.main"`、`docker compose config` 通過、`/api/studio/v1/health` 可回應

### Phase 2 — 核心資料模型

- 型別 enum（`studio/models/types.py`）
- Project / Chapter
- Shot / ShotDetail / ShotFrame / ShotDialogue
- ShotExtractedCandidate / ShotDialogueCandidate / ShotCharacterLink
- Character / Actor / Scene / Prop / Costume / AssetImage
- Project*Link（actor/scene/prop/costume）
- FileItem / FileUsage
- GenerationTask / GenerationTaskLink
- Provider / Model / ModelSettings / PromptTemplate
- Alembic initial migration

**驗收**：`alembic upgrade head` 在 SQLite 成功、模型 import 無誤

### Phase 3 — API 與 OpenAPI

- Pydantic schemas（request / response）
- 統一回應信封 + 錯誤處理
- Repository 層
- Service 層
- REST routes（projects / chapters / shots / entities / files / prompts / providers / models / tasks）
- OpenAPI 匯出腳本
- 前端 generated client

**驗收**：`/openapi.json` 產生、`pnpm run openapi:gen` 成功、pytest API 測試通過

### Phase 4 — 非同步任務中心

- `GenerationTask` 生命週期服務（建立／排程／進度／完成／失敗／取消）
- `TaskExecutorRegistry`（by `task_kind`）
- Provider adapter registry（by `task_kind` + `provider_key`）
- Celery worker 任務封裝
- retry / cancel / progress 回寫
- 任務查詢與恢復 API
- 前端 Task Center

**驗收**：pytest 任務生命週期測試、取消測試、重啟恢復測試

### Phase 5 — AI 腳本與分鏡流程

- Provider Adapter：Anthropic / OpenAI / Gemini / OpenAI-compatible
- Agent：script_divider / element_extractor / script_optimizer / script_simplifier / consistency_checker
- 分鏡拆解服務
- 實體提取 → 候選產生
- 候選確認（accept / ignore / link）
- Shot readiness 判定

**驗收**：pytest agent 解析測試（以錄製回應驗證，非永遠成功的 mock）

### Phase 6 — 圖片與影片生成

- 圖片供應商 adapter
- 影片供應商 adapter
- 參考圖 / 關鍵幀管理
- 圖片生成任務
- 影片生成任務
- video-readiness 檢查
- 批次生成
- 生成結果寫回 media 系統

**驗收**：adapter 單元測試、任務整合測試

### Phase 7 — 前端完整工作平台

- Dashboard / Project List / Project Workspace
- Chapter Editor / Script Editor
- Storyboard List / Shot Editor / Shot Generation Studio
- Character / Scene / Props / Costume 管理
- Media Library / Task Center
- Provider / Model / Prompt Template 設定
- 響應式 + Loading / Empty / Error / Retry
- 防重複送出
- 移除 mock data

**驗收**：`pnpm exec tsc --noEmit`、`pnpm build` 通過

### Phase 8 — 測試、文件與部署

- 後端單元測試 + 整合測試
- 前端 type check + build
- Docker Compose 啟動測試
- Migration 測試
- README 更新
- 架構文件更新
- 部署指南

**驗收**：CI 全綠

---

## 4. 每階段完成回報格式

每個 Phase 結束時提供：

1. 完成內容
2. 修改檔案
3. 新增檔案
4. 資料庫變更
5. 執行過的指令
6. 測試結果
7. 尚未完成內容
8. 下一階段工作

---

## 5. 跨 Session 續作機制

`IMPLEMENTATION_STATUS.md` 為單一事實來源，記錄：

- 各 Phase 勾選狀態
- 下一步應修改的 **精確檔案路徑**
- 已知未完成項與阻塞點
- 驗證指令清單

任何新 Session 應先讀 `IMPLEMENTATION_STATUS.md`，再讀本計畫。

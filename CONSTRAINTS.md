# 永久約束（Phase 3～8 全程適用）

> **這些約束優先於任何實作便利性。** 每個 Phase 開始前與結束後都必須重讀本檔。
> 違反任何一條 = 該 Phase 不得標示完成。

---

## A. 既有股票系統保護

| # | 約束 | 驗證方式 |
| --- | --- | --- |
| A1 | `app.py`、`requirements.txt`、`railway.json`、`run.sh`、原股票模組、`templates/`、`static/` **非必要不得修改** | `git diff <base> -- <files>` 必須為空 |
| A2 | 若真的必須修改原股票檔案，**必須先停止並向使用者說明**，不得自行修改 | 人工把關 |
| A3 | 股票系統必須在**沒有 Redis、Celery、FastAPI、Studio DB、Studio frontend** 的情況下獨立啟動 | `tests/legacy/test_stock_app_regression.py::test_stock_app_serves_home_without_studio_dependencies` |
| A4 | **不得更換首頁**，`GET /` 必須維持股票系統 | `test_home_page_is_still_the_stock_dashboard` |
| A5 | **不得更改 Railway 的 `python app.py` 啟動方式** | `test_railway_start_command_unchanged`、`test_procfile_web_entrypoint_unchanged` |

## B. Studio 邊界

| # | 約束 | 驗證方式 |
| --- | --- | --- |
| B1 | Studio 頁面統一維持在 `/studio/*` | `test_studio_docs_live_under_studio_prefix` |
| B2 | Studio API 統一維持在 `/api/v1/studio/*` | `test_studio_api_available_alongside_stock_app` |
| B3 | Studio 資料表統一使用 `studio_` 前綴，或使用獨立 `STUDIO_DATABASE_URL` | `test_all_tables_use_studio_prefix` |
| B4 | Studio migration **永遠不得操作原股票資料表** | `test_studio_migration_never_touches_legacy_tables` + CI grep 關卡 |

## C. 流程

| # | 約束 |
| --- | --- |
| C1 | 每個 Phase 完成後都必須**重新執行全部 legacy regression tests** |
| C2 | 任一 legacy test 失敗，該 Phase **不得標示完成** |
| C3 | 暫時**不建立 Pull Request**，不合併 |
| C4 | 不修改 Railway 正式部署設定 |

## D. 機密

| # | 約束 | 驗證方式 |
| --- | --- | --- |
| D1 | **不得提交** `studio.db`、`.env`、API Key、Token 或其他 Secret | `.gitignore` + `tests/studio/test_secret_safety.py` |
| D2 | `ProviderRead` **絕不回傳任何金鑰內容**；只回傳 `api_key_configured: bool` | `test_provider_read_never_exposes_key` |
| D3 | 金鑰不得出現在 API response、log、exception、OpenAPI example、test snapshot | `test_openapi_contains_no_secret_values` |
| D4 | 未傳入新 API Key 時保留舊值；只有明確傳入新值才更新 | `test_provider_update_preserves_key_when_omitted` |

---

## E. 合併前待確認事項（尚未解決）

> **Phase 3 不需要變更正式部署分支。** 以下為未來合併前必須釐清的項目。

1. **遠端不存在 `main` 分支。** 目前遠端分支清單中沒有 `main`，
   本次改造的基準是 `9fb1d7c`（等同 `claude/confident-hamilton-Ha8bP`）。
2. 合併前必須確認：
   - GitHub 的**預設分支**究竟是哪一個
   - **Railway 實際部署的分支**是哪一個
   - commit `9fb1d7c` 是否確實為**目前正式環境的基準**
3. 在上述三點確認前，**不得合併、不得建立 PR、不得變更部署分支**。

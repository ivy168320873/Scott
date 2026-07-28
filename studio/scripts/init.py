"""Studio 初始化：建立物件儲存 bucket 並執行資料庫遷移。

由 Docker Compose 的 `studio-init` 一次性服務執行，backend / worker 會等它
成功結束後才啟動，避免多個 process 同時跑 migration 造成競態。

本腳本是冪等的，可安全重複執行。
"""

from __future__ import annotations

import logging
import sys

from alembic import command
from alembic.config import Config

from studio.config import StorageBackend, get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s [studio-init] %(message)s")
logger = logging.getLogger(__name__)


def ensure_bucket() -> None:
    """確保 S3 bucket 存在（local 後端則確保目錄存在）。"""

    settings = get_settings()
    if settings.storage_backend is not StorageBackend.s3:
        settings.local_storage_path.mkdir(parents=True, exist_ok=True)
        logger.info("local storage ready at %s", settings.local_storage_path)
        return

    from studio.core.storage import S3ObjectStorage

    storage = S3ObjectStorage(settings)
    storage.ensure_bucket()
    logger.info("s3 bucket ready: %s", settings.s3_bucket_name)


def run_migrations() -> None:
    """執行 Alembic migration 至最新版本。"""

    config = Config("alembic.ini")
    command.upgrade(config, "head")
    logger.info("database migrations applied")


def main() -> int:
    """初始化進入點。

    Returns:
        0 表示成功；非 0 會讓 compose 的 `service_completed_successfully`
        條件失敗，進而阻止 backend / worker 在半初始化狀態下啟動。
    """

    try:
        ensure_bucket()
        run_migrations()
    except Exception as exc:  # noqa: BLE001 — 初始化失敗必須讓容器以非零碼結束
        logger.error("initialisation failed: %s", exc, exc_info=True)
        return 1

    logger.info("studio initialisation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

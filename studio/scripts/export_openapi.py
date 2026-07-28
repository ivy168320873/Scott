"""匯出 OpenAPI 規格到 `frontend/openapi.json`。

後端 API 是前端型別的**唯一規格來源**。這個腳本讓前端不需要啟動後端伺服器
就能重新產生型別與 client（`pnpm run openapi:update` 會先呼叫它）。

同時執行一項安全檢查：規格中不得出現任何看起來像金鑰的字串。
OpenAPI 會被提交進版控並發布給前端，是機密外洩的高風險出口。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "frontend" / "openapi.json"

# 看起來像實際金鑰的樣式。刻意不比對「api_key」這類欄位**名稱** ——
# 名稱出現在規格中是正常的，值才是機密。
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"xoxb-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
)


def build_spec() -> dict[str, Any]:
    """產生 OpenAPI 規格。"""

    from studio.main import create_app

    return create_app().openapi()


def assert_no_secrets(spec: dict[str, Any]) -> None:
    """確認規格中不含任何金鑰值。

    Raises:
        SystemExit: 偵測到疑似金鑰時中止，避免機密被寫入檔案並提交。
    """

    text = json.dumps(spec, ensure_ascii=False)
    for pattern in _SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            raise SystemExit(
                f"OpenAPI 規格中偵測到疑似金鑰（樣式 {pattern.pattern}），已中止匯出。"
            )


def export(output: Path = DEFAULT_OUTPUT) -> Path:
    """產生規格並寫入檔案。

    Args:
        output: 輸出路徑。

    Returns:
        實際寫入的路徑。
    """

    spec = build_spec()
    assert_no_secrets(spec)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    """腳本進入點。"""

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    path = export(target)

    spec = json.loads(path.read_text(encoding="utf-8"))
    operations = sum(
        1
        for item in spec["paths"].values()
        for detail in item.values()
        if isinstance(detail, dict) and "operationId" in detail
    )
    print(f"OpenAPI 已寫入 {path}（{len(spec['paths'])} 個路徑、{operations} 個操作）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

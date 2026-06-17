"""pytest 共用設定：把專案根目錄與 cli_agent 子目錄加進匯入路徑。

測試刻意設計成不需網路：凡會對外抓資料的函式，一律以 monkeypatch
注入合成資料，確保在 CI 與離線環境都能穩定、確定性地通過。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for p in (_ROOT, _ROOT / "cli_agent"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def make_ohlcv(n: int = 120, start: float = 100.0, step: float = 0.5) -> list[dict]:
    """產生一段確定性的合成 OHLCV（鋸齒上行），供回測等測試使用。"""
    rows = []
    price = start
    for i in range(n):
        price = price + (step if i % 2 == 0 else -step * 0.4)
        close = round(price, 4)
        rows.append(
            {
                "date": f"2025-01-{(i % 28) + 1:02d}",
                "open": round(close * 0.99, 4),
                "high": round(close * 1.02, 4),
                "low": round(close * 0.98, 4),
                "close": close,
                "volume": 1_000_000 + i * 1000,
            }
        )
    return rows

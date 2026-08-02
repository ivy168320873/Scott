"""Shared deterministic test fixtures; the test suite never needs the network."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "cli_agent"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def make_ohlcv(n: int = 120, start: float = 100.0, step: float = 0.5) -> list[dict]:
    rows = []
    price = start
    for i in range(n):
        price += step if i % 2 == 0 else -step * 0.4
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

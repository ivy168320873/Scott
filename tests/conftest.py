"""pytest 共用設定與測試資料工廠。

專案模組都放在 repo 根目錄（`decision_engine.py`、`fetcher.py` 等），
因此把根目錄插進 sys.path，讓 `tests/` 底下可以直接 import。
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """全域封鎖對外連線。

    測試若忘記 monkeypatch 外部呼叫，會在 CI 上打到真實 API（配額、flaky、
    甚至誤觸告警）。這裡讓任何未預期的連線直接失敗，而不是安靜地成功。
    """
    def _no_connect(*args, **kwargs):
        raise AssertionError("測試不得建立真實網路連線——請 monkeypatch 外部呼叫")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_connect)
    monkeypatch.setattr(socket, "create_connection", _no_connect)

NAN = float("nan")
INF = float("inf")


def make_ohlcv(
    closes: list,
    opens: list | None = None,
    highs: list | None = None,
    lows: list | None = None,
    volumes: list | None = None,
) -> dict:
    """由收盤價產生一組合法的正規化 OHLCV dict。

    未指定的序列會由收盤價推導，確保長度一致（引擎會取各序列的最小長度）。
    """
    n = len(closes)

    def _derive(values, factor: float):
        if values is not None:
            return values
        return [
            (c * factor if isinstance(c, (int, float)) and c == c else c)
            for c in closes
        ]

    return {
        "closes":     list(closes),
        "opens":      _derive(opens, 0.99),
        "highs":      _derive(highs, 1.01),
        "lows":       _derive(lows, 0.98),
        "volumes":    list(volumes) if volumes is not None else [1_000_000.0] * n,
        "timestamps": [],
    }


def flat_series(n: int = 30, price: float = 100.0) -> list[float]:
    """完全持平的價格序列——不應觸發任何追價／賣出訊號。"""
    return [price] * n


def rising_series(n: int = 30, start: float = 100.0, step: float = 2.0) -> list[float]:
    """穩定上漲序列。"""
    return [start + step * i for i in range(n)]


@pytest.fixture
def flat_ohlcv() -> dict:
    return make_ohlcv(flat_series(30))


@pytest.fixture
def rising_ohlcv() -> dict:
    return make_ohlcv(rising_series(30))


@pytest.fixture
def empty_ohlcv() -> dict:
    return {"closes": [], "opens": [], "highs": [], "lows": [], "volumes": [], "timestamps": []}

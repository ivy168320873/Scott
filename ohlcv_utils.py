"""OHLCV 數值清洗共用工具。

市場資料常見缺值（停牌、非交易日、資料源回傳 null／字串、配額用盡回空值）。
若讓這些值直接進入評分引擎，會出現兩種失敗：

1. 拋 TypeError（`None > 0`、`None - None`）把整條路由打掛；
2. **更危險**——NaN 會靜默通過所有比較（`NaN > x` 恆為 False），
   讓引擎算出 0 分並回報 `ok=True`「低風險，可介入」，
   把「沒有資料」包裝成投資結論。

因此清洗必須做在**各引擎入口**，而不是只做在整合層 `decision_engine`——
`app.py`、`top_tier_decision_engine`、`daily_report_engine`、`rotation_engine`、
`stress_test_engine` 與 `cli_agent/stock_tools` 都有直接呼叫引擎的路徑。
"""
from __future__ import annotations

import math

__all__ = ["to_finite_float", "sanitize_ohlcv"]

_SERIES_KEYS = ("closes", "opens", "highs", "lows", "volumes")


def to_finite_float(value) -> float | None:
    """轉成有限的 float；None／NaN／inf／非數值一律回 None。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def sanitize_ohlcv(ohlcv: dict | None) -> dict | None:
    """清洗正規化後的 OHLCV，丟掉無效的 K 棒並保持各序列逐棒對齊。

    契約：
      * 只有 `closes` 是必要序列；`opens`/`highs`/`lows` 缺漏或長度不符時
        回退為當根收盤價，`volumes` 回退為 0（與 `normalize_list` 一致）。
        這是刻意的——`sell_engine` 與 `portfolio_engine` 本來就只需要 closes，
        若要求五個序列都齊全，會讓停損告警在資料源缺 volume 時靜默失效。
      * 收盤價無效（None／NaN／非數值／<= 0）的那一根整根丟棄。
      * **長度不符的輔助序列直接視為不存在**，而不是用尾端對齊硬湊，
        否則會把第 2 根的 close 配上第 1 根的 open，算出假的長上影訊號。
      * `timestamps` 與其他 metadata（如 `is_demo`、`source`）予以保留。

    回傳 None 代表清洗後已無可用資料——呼叫端應據此回報「資料不足」，
    不得繼續產生評分。
    """
    if not isinstance(ohlcv, dict):
        return None

    closes_raw = ohlcv.get("closes")
    if not isinstance(closes_raw, (list, tuple)) or not closes_raw:
        return None
    n = len(closes_raw)

    def _aligned(key: str) -> list | None:
        """長度與 closes 相同才視為可用，避免跨棒錯配。"""
        series = ohlcv.get(key)
        if isinstance(series, (list, tuple)) and len(series) == n:
            return list(series)
        return None

    opens_raw = _aligned("opens")
    highs_raw = _aligned("highs")
    lows_raw = _aligned("lows")
    volumes_raw = _aligned("volumes")
    timestamps_raw = _aligned("timestamps")

    # ignored_series 記錄「有提供但無法使用」的序列（長度與 closes 不符）。
    # 空 list 代表「未提供」而非資料錯誤，不列入。
    ignored_series = [
        key for key, series in (
            ("opens", opens_raw), ("highs", highs_raw),
            ("lows", lows_raw), ("volumes", volumes_raw),
            ("timestamps", timestamps_raw),
        )
        if series is None and ohlcv.get(key)
    ]

    # has_volume 讓下游能區分「沒有成交量資料」與「成交量為 0」。
    # 若不區分，缺量會被當成 volume=0，使引擎憑空產出「量能萎縮」的結論，
    # 同時讓「爆量長上影」這類風險訊號靜默消失。
    #
    # 各正規化層（data_provider._normalize、normalize_list、normalize_yahoo）
    # 都會把缺漏的 volume 補成 0，所以「序列存在」不足以證明有量資料——
    # 必須看是否至少有一根為正值。整段視窗成交量全為 0 不是真實市場資料，
    # 而是缺值；單日 0 量（停牌）則不影響判定。
    has_volume = volumes_raw is not None and any(
        (to_finite_float(v) or 0.0) > 0 for v in volumes_raw
    )

    out: dict[str, list] = {key: [] for key in _SERIES_KEYS}
    out_timestamps: list = []

    for i in range(n):
        close = to_finite_float(closes_raw[i])
        if close is None or close <= 0:
            continue                      # 缺值／非正數的 K 棒整根丟棄

        def _or_close(series, _i=i, _close=close) -> float:
            if series is None:
                return _close
            value = to_finite_float(series[_i])
            return value if value is not None else _close

        volume = to_finite_float(volumes_raw[i]) if volumes_raw is not None else None

        out["closes"].append(close)
        out["opens"].append(_or_close(opens_raw))
        out["highs"].append(_or_close(highs_raw))
        out["lows"].append(_or_close(lows_raw))
        out["volumes"].append(volume if volume is not None else 0.0)
        if timestamps_raw is not None:
            out_timestamps.append(timestamps_raw[i])

    if not out["closes"]:
        return None

    # 保留呼叫端附加的 metadata（例如 data_provider 的 is_demo / source），
    # 否則「示範資料」標記會在清洗過程中無聲消失。
    result = {
        key: value for key, value in ohlcv.items()
        if key not in _SERIES_KEYS and key != "timestamps"
    }
    result.update(out)
    result["timestamps"] = out_timestamps
    # 累加而非覆寫——同一份資料可能被多層重複清洗（例如 sector_engine 逐檔清洗）
    result["dropped_bars"] = (
        int(to_finite_float(ohlcv.get("dropped_bars")) or 0) + (n - len(out["closes"]))
    )
    # has_volume=False 代表「沒有成交量資料」，volumes 內的 0.0 只是佔位值，
    # 引擎必須據此跳過量能相關判斷，不得解讀成「成交量為 0」。
    result["has_volume"] = has_volume and bool(ohlcv.get("has_volume", True))
    # 聯集而非覆寫——重複清洗時第一次偵測到的序列問題不可遺失
    result["ignored_series"] = sorted(
        set(ignored_series) | set(ohlcv.get("ignored_series") or [])
    )
    return result

"""
Data Quality Engine — Phase 13B
Validates OHLCV data reliability before allowing trade decisions.

Public API
----------
run_data_quality(ohlcv: dict) -> dict

Statuses:
  OK      — data reliable, trade decisions allowed
  WARNING — minor issues detected, use with caution
  BAD     — data unreliable, only WATCH/AVOID allowed
  DEMO    — synthetic demo data, no trade decisions
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


# ── Public API ────────────────────────────────────────────────────────────────

def run_data_quality(ohlcv: dict) -> dict:
    """
    ohlcv: normalized dict from _get_ohlcv_norm / data_provider.get_ohlcv

    Returns:
      data_quality_score  : int 0-100
      data_status         : "OK" | "WARNING" | "BAD" | "DEMO"
      can_trade_decision  : bool
      warnings            : list[str]
      source              : str
      is_demo             : bool
      bar_count           : int
      last_price          : float | None
      last_date           : str | None
    """
    if not ohlcv or not isinstance(ohlcv, dict):
        return _empty("OHLCV 資料缺失")

    warns:  list[str] = []
    score   = 100
    status  = "OK"
    is_demo = bool(ohlcv.get("is_demo", False))
    source  = str(ohlcv.get("source", "unknown") or "unknown")

    closes  = ohlcv.get("closes",  []) or []
    opens   = ohlcv.get("opens",   []) or []
    highs   = ohlcv.get("highs",   []) or []
    lows    = ohlcv.get("lows",    []) or []
    volumes = ohlcv.get("volumes", []) or []
    timestamps = ohlcv.get("timestamps", []) or []
    dates = ohlcv.get("dates", []) or []
    quality_flags = [str(item) for item in ohlcv.get("quality_flags", []) or []]

    bar_count  = len(closes)
    last_price = closes[-1] if closes else None
    last_date  = (
        str(ohlcv.get("last_bar_date") or "")[:10]
        or (str(dates[-1])[:10] if dates else None)
        or (_ts_to_date(timestamps[-1]) if timestamps else None)
    )

    # ── Rule 1: demo data ─────────────────────────────────────────────────────
    if is_demo:
        return {
            "ok":                 True,
            "data_quality_score": 20,
            "data_status":        "DEMO",
            "can_trade_decision": False,
            "warnings":           ["使用合成 Demo 資料，不可用於真實交易決策"],
            "source":             source,
            "is_demo":            True,
            "bar_count":          bar_count,
            "last_price":         last_price,
            "last_date":          last_date,
            "quality_flags":      quality_flags,
            "fetched_at":         ohlcv.get("fetched_at"),
            "market":             ohlcv.get("market"),
        }

    # ── Rule 2: missing / empty closes ───────────────────────────────────────
    if bar_count == 0:
        return _empty("無收盤價資料（closes 為空）")

    # ── Rule 2B: completed-bar and provenance contract ───────────────────────
    if ohlcv.get("last_bar_complete") is False:
        warns.append("最新日 K 尚未完成，禁止用未完成收盤價產生交易訊號")
        score -= 45
    if ohlcv.get("excluded_incomplete_bar"):
        warns.append("已排除盤中尚未完成的日 K；技術訊號使用上一根完整日 K")
    if "FUTURE_BAR" in quality_flags:
        warns.append("行情包含未來日期，資料時間軸異常")
        score -= 50

    # ── Rule 3: last close invalid ────────────────────────────────────────────
    if not last_price or last_price <= 0:
        return {
            "ok":                 True,
            "data_quality_score": 0,
            "data_status":        "BAD",
            "can_trade_decision": False,
            "warnings":           ["最新收盤價為空值或 ≤ 0，資料異常"],
            "source":             source,
            "is_demo":            False,
            "bar_count":          bar_count,
            "last_price":         None,
            "last_date":          last_date,
            "last_bar_complete":  ohlcv.get("last_bar_complete"),
            "excluded_incomplete_bar": bool(ohlcv.get("excluded_incomplete_bar")),
            "quality_flags":      quality_flags,
            "fetched_at":         ohlcv.get("fetched_at"),
            "market":             ohlcv.get("market"),
            "price_basis":        ohlcv.get("price_basis"),
        }

    # ── Rule 4: bar count check ───────────────────────────────────────────────
    if bar_count < 20:
        return {
            "ok":                 True,
            "data_quality_score": 10,
            "data_status":        "BAD",
            "can_trade_decision": False,
            "warnings":           [f"K線資料不足（{bar_count} 根，需至少 20 根）"],
            "source":             source,
            "is_demo":            False,
            "bar_count":          bar_count,
            "last_price":         last_price,
            "last_date":          last_date,
            "last_bar_complete":  ohlcv.get("last_bar_complete"),
            "excluded_incomplete_bar": bool(ohlcv.get("excluded_incomplete_bar")),
            "quality_flags":      quality_flags,
            "fetched_at":         ohlcv.get("fetched_at"),
            "market":             ohlcv.get("market"),
            "price_basis":        ohlcv.get("price_basis"),
        }

    if bar_count < 60:
        warns.append(f"K線資料偏少（{bar_count} 根，建議 60+ 根以上）")
        score -= 15
    elif bar_count < 100:
        warns.append(f"K線資料略少（{bar_count} 根，建議 100+）")
        score -= 8

    # ── Rule 5: OHLC sanity check ─────────────────────────────────────────────
    n = min(len(opens), len(highs), len(lows), bar_count)
    if n >= 5:
        bad_ohlc = 0
        for i in range(max(0, n - 20), n):
            h = highs[i]  if i < len(highs)  else None
            l = lows[i]   if i < len(lows)   else None
            c = closes[i] if i < len(closes) else None
            if h and l and c:
                if h < c or l > c or h < l:
                    bad_ohlc += 1
        if bad_ohlc > 3:
            warns.append(f"OHLC 資料有 {bad_ohlc} 根異常（H<C 或 L>C），可能資料損壞")
            score -= 20
        elif bad_ohlc > 0:
            warns.append(f"OHLC 偶有小異常（{bad_ohlc} 根），影響較小")
            score -= 5

    # ── Rule 6: volume check ──────────────────────────────────────────────────
    if volumes:
        recent_vols = [v for v in volumes[-20:] if v is not None]
        zero_count  = sum(1 for v in recent_vols if v == 0)
        if zero_count == len(recent_vols):
            warns.append("近 20 日成交量全為 0，資料異常")
            score -= 20
        elif zero_count > len(recent_vols) // 2:
            warns.append(f"近 20 日有 {zero_count} 天成交量為 0，可能資料缺失")
            score -= 10
        elif recent_vols and max(recent_vols) == 0:
            warns.append("成交量資料異常（最大值為 0）")
            score -= 10
    else:
        warns.append("缺少成交量資料，追高風險等計算精度下降")
        score -= 12

    # ── Rule 7: timestamp freshness ───────────────────────────────────────────
    if timestamps:
        last_ts = timestamps[-1]
        try:
            now_ts  = time.time()
            age_days = (now_ts - last_ts) / 86400
            # Allow up to 5 calendar days (weekends + 1 extra day)
            if age_days > 7:
                warns.append(f"資料最後更新於 {age_days:.0f} 天前（超過 7 天），可能已過時")
                score -= 15
            elif age_days > 4:
                warns.append(f"資料已 {age_days:.0f} 天未更新，請確認是否為假日")
                score -= 5
        except Exception:
            pass

    # ── Rule 8: NaN / None in recent closes ──────────────────────────────────
    recent = closes[-10:]
    none_count = sum(1 for v in recent if v is None or v != v)  # nan check
    if none_count > 2:
        warns.append(f"近 10 日收盤價有 {none_count} 個缺失值")
        score -= 15
    elif none_count > 0:
        warns.append(f"近 10 日收盤價有 {none_count} 個缺失值（影響小）")
        score -= 5

    # ── Determine status ──────────────────────────────────────────────────────
    score = max(0, min(100, score))
    if score >= 75:
        status = "OK"
    elif score >= 45:
        status = "WARNING"
    else:
        status = "BAD"

    can_trade = (status == "OK") or (status == "WARNING" and score >= 55)

    return {
        "ok":                 True,
        "data_quality_score": score,
        "data_status":        status,
        "can_trade_decision": can_trade,
        "warnings":           warns,
        "source":             source,
        "is_demo":            is_demo,
        "bar_count":          bar_count,
        "last_price":         round(last_price, 4) if last_price else None,
        "last_date":          last_date,
        "last_bar_complete":  ohlcv.get("last_bar_complete"),
        "excluded_incomplete_bar": bool(ohlcv.get("excluded_incomplete_bar")),
        "quality_flags":      quality_flags,
        "fetched_at":         ohlcv.get("fetched_at"),
        "market":             ohlcv.get("market"),
        "price_basis":        ohlcv.get("price_basis"),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts_to_date(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _empty(reason: str) -> dict:
    return {
        "ok":                 True,
        "data_quality_score": 0,
        "data_status":        "BAD",
        "can_trade_decision": False,
        "warnings":           [reason],
        "source":             "unknown",
        "is_demo":            False,
        "bar_count":          0,
        "last_price":         None,
        "last_date":          None,
        "last_bar_complete":  None,
        "excluded_incomplete_bar": False,
        "quality_flags":      [],
        "fetched_at":         None,
        "market":             None,
        "price_basis":        None,
    }

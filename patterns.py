"""Candlestick pattern recognition (pure Python, operates on OHLCV list)."""
from __future__ import annotations
import math


def _body(o, c): return abs(c - o)
def _upper(o, h, c): return h - max(o, c)
def _lower(o, l, c): return min(o, c) - l
def _range(h, l): return h - l or 0.0001
def _bull(o, c): return c > o


def _patterns_for(bars: list[dict]) -> list[dict]:
    """Detect patterns in the last 3 bars. Returns list of {name, type, bar_idx}."""
    found = []
    n = len(bars)
    if n < 1:
        return found

    def b(i): return bars[n - 1 - i]  # b(0)=last, b(1)=prev, b(2)=2nd prev

    o0,h0,l0,c0 = b(0)['open'],b(0)['high'],b(0)['low'],b(0)['close']
    body0  = _body(o0, c0)
    upper0 = _upper(o0, h0, c0)
    lower0 = _lower(o0, l0, c0)
    rng0   = _range(h0, l0)

    # ── Single-bar patterns ────────────────────────────────────────────────

    # Doji (十字星)
    if body0 < rng0 * 0.1:
        found.append({"name": "十字星 Doji", "type": "neutral",
                      "desc": "開收盤價幾乎相同，市場猶豫，趨勢可能反轉"})

    # Hammer / Hanging Man (錘子 / 上吊線)
    if lower0 > body0 * 2 and upper0 < body0 * 0.5 and body0 > 0:
        if _bull(o0, c0):
            found.append({"name": "錘子線 Hammer", "type": "bullish",
                          "desc": "下影線長，低點被強力買盤支撐，反彈訊號"})
        else:
            found.append({"name": "上吊線 Hanging Man", "type": "bearish",
                          "desc": "下影線長但收黑，頂部警示訊號"})

    # Shooting Star (流星線)
    if upper0 > body0 * 2 and lower0 < body0 * 0.5 and body0 > 0:
        found.append({"name": "流星線 Shooting Star", "type": "bearish",
                      "desc": "上影線長，高點遭賣壓壓制，回調警示"})

    # Marubozu (光頭光腳)
    if body0 > rng0 * 0.9:
        t = "bullish" if _bull(o0, c0) else "bearish"
        found.append({"name": "光頭光腳 Marubozu", "type": t,
                      "desc": "幾乎無影線，多空力量單方面主導"})

    if n < 2:
        return found

    o1,h1,l1,c1 = b(1)['open'],b(1)['high'],b(1)['low'],b(1)['close']
    body1 = _body(o1, c1)

    # ── Two-bar patterns ──────────────────────────────────────────────────

    # Bullish Engulfing (多頭吞噬)
    if (not _bull(o1, c1) and _bull(o0, c0)
            and o0 <= c1 and c0 >= o1 and body0 > body1):
        found.append({"name": "多頭吞噬 Bullish Engulfing", "type": "bullish",
                      "desc": "紅K完全吞噬前根黑K，強烈反彈訊號"})

    # Bearish Engulfing (空頭吞噬)
    if (_bull(o1, c1) and not _bull(o0, c0)
            and o0 >= c1 and c0 <= o1 and body0 > body1):
        found.append({"name": "空頭吞噬 Bearish Engulfing", "type": "bearish",
                      "desc": "黑K完全吞噬前根紅K，強烈賣出訊號"})

    # Bullish Harami (多頭孕線)
    if (not _bull(o1, c1) and _bull(o0, c0)
            and o0 > c1 and c0 < o1 and body0 < body1 * 0.5):
        found.append({"name": "多頭孕線 Bullish Harami", "type": "bullish",
                      "desc": "小紅K包在大黑K內，下跌力道衰竭"})

    # Bearish Harami (空頭孕線)
    if (_bull(o1, c1) and not _bull(o0, c0)
            and o0 < c1 and c0 > o1 and body0 < body1 * 0.5):
        found.append({"name": "空頭孕線 Bearish Harami", "type": "bearish",
                      "desc": "小黑K包在大紅K內，上漲力道衰竭"})

    # Tweezer Bottom
    if (not _bull(o1, c1) and _bull(o0, c0)
            and abs(l0 - l1) < rng0 * 0.05):
        found.append({"name": "平底夾子 Tweezer Bottom", "type": "bullish",
                      "desc": "兩根K線低點相近，支撐明確"})

    # Tweezer Top
    if (_bull(o1, c1) and not _bull(o0, c0)
            and abs(h0 - h1) < rng0 * 0.05):
        found.append({"name": "平頭夾子 Tweezer Top", "type": "bearish",
                      "desc": "兩根K線高點相近，壓力明確"})

    if n < 3:
        return found

    o2,h2,l2,c2 = b(2)['open'],b(2)['high'],b(2)['low'],b(2)['close']
    body2 = _body(o2, c2)
    mid_c = (o1 + c1) / 2

    # ── Three-bar patterns ────────────────────────────────────────────────

    # Morning Star (晨星) – bullish reversal
    if (not _bull(o2, c2) and body2 > 0
            and body1 < body2 * 0.3
            and _bull(o0, c0) and c0 > mid_c):
        found.append({"name": "晨星 Morning Star", "type": "bullish",
                      "desc": "三K底部反轉形態，強烈買入訊號"})

    # Evening Star (暮星) – bearish reversal
    if (_bull(o2, c2) and body2 > 0
            and body1 < body2 * 0.3
            and not _bull(o0, c0) and c0 < mid_c):
        found.append({"name": "暮星 Evening Star", "type": "bearish",
                      "desc": "三K頂部反轉形態，強烈賣出訊號"})

    # Three White Soldiers (三白兵)
    if all(_bull(bars[n-1-i]['open'], bars[n-1-i]['close']) for i in range(3)):
        if c0 > c1 > c2:
            found.append({"name": "三白兵 Three White Soldiers", "type": "bullish",
                          "desc": "連續三根上漲K線，趨勢強勁"})

    # Three Black Crows (三黑鴉)
    if all(not _bull(bars[n-1-i]['open'], bars[n-1-i]['close']) for i in range(3)):
        if c0 < c1 < c2:
            found.append({"name": "三黑鴉 Three Black Crows", "type": "bearish",
                          "desc": "連續三根下跌K線，空頭強勁"})

    return found


def detect(ohlcv: list[dict]) -> list[dict]:
    """Detect candlestick patterns from OHLCV list. Returns list of patterns."""
    if len(ohlcv) < 3:
        return []
    return _patterns_for(ohlcv[-10:])  # use recent bars

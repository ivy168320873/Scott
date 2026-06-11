"""
相對強弱分數模組 (Relative Strength Score) — 0~100 分
-------------------------------------------------------
評估個股相對大盤 / 基準指數的強弱，包含：
  · 5d / 10d / 20d / 60d 相對報酬率
  · RS 排名分位（簡易計算）
  · 波動率調整後超額報酬（Sharpe-like）
  · 動能百分位（個股 vs 自身歷史）

輸入：個股 ohlcv + 基準 bench_ohlcv（建議 QQQ 或 SPY）
"""
from __future__ import annotations

import math
import statistics


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _ret(closes: list[float], n: int) -> float | None:
    """N 日報酬率。"""
    if len(closes) < n + 1 or closes[-(n + 1)] <= 0:
        return None
    return (closes[-1] / closes[-(n + 1)] - 1) * 100


def _vol_annualized(closes: list[float], window: int = 20) -> float | None:
    """年化波動率 (%)。"""
    if len(closes) < window + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - window, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 5:
        return None
    return statistics.stdev(rets) * math.sqrt(252) * 100


def _score_relative_return(
    stock_ret: float | None,
    bench_ret: float | None,
    label: str,
    weight: float,
) -> tuple[float, str | None]:
    """
    計算單一期間相對強弱得分。
    weight: 最高可給的分數
    """
    if stock_ret is None:
        return weight * 0.5, None   # 中性
    if bench_ret is None or bench_ret == 0:
        # 無基準，直接用絕對報酬判斷
        if stock_ret > 10:
            return weight, f"{label} 漲幅 +{stock_ret:.1f}%，強勢"
        elif stock_ret > 0:
            return weight * 0.7, None
        else:
            return max(0, weight * 0.3 + stock_ret * 0.02), f"{label} 跌 {stock_ret:.1f}%"

    excess = stock_ret - bench_ret
    if excess > 15:
        pts = weight
        reason = f"{label} 超越基準 +{excess:.1f}%（個股 {stock_ret:+.1f}%，基準 {bench_ret:+.1f}%），絕對強勢"
    elif excess > 8:
        pts = weight * 0.85
        reason = f"{label} 超越基準 +{excess:.1f}%，明顯跑贏"
    elif excess > 3:
        pts = weight * 0.70
        reason = f"{label} 微超基準 +{excess:.1f}%"
    elif excess > -3:
        pts = weight * 0.50
        reason = None
    elif excess > -8:
        pts = weight * 0.30
        reason = f"{label} 落後基準 {excess:.1f}%，相對弱勢"
    elif excess > -15:
        pts = weight * 0.15
        reason = f"{label} 明顯落後基準 {excess:.1f}%"
    else:
        pts = 0.0
        reason = f"{label} 嚴重落後基準 {excess:.1f}%，不建議持有"

    return _clamp(pts, 0, weight), reason


def _score_momentum_percentile(closes: list[float]) -> tuple[float, list[str]]:
    """
    動能百分位得分 (0-20 pts)
    個股近 20 日報酬率在自身歷史中的百分位。
    """
    if len(closes) < 80:
        return 10.0, []

    # 計算滾動 20 日報酬率的分佈
    rolling_rets: list[float] = []
    for i in range(len(closes) - 80, len(closes) - 20):
        if closes[i] > 0:
            rolling_rets.append((closes[i + 20] / closes[i] - 1) * 100)

    if len(rolling_rets) < 10:
        return 10.0, []

    current_ret = _ret(closes, 20)
    if current_ret is None:
        return 10.0, []

    # 百分位
    rank = sum(1 for r in rolling_rets if r < current_ret)
    pct  = rank / len(rolling_rets) * 100

    reasons: list[str] = []
    if pct >= 85:
        pts = 20; reasons.append(f"20 日報酬 {current_ret:+.1f}% 在歷史前 {100-pct:.0f}% 分位，動能極強")
    elif pct >= 70:
        pts = 15; reasons.append(f"20 日報酬歷史百分位 {pct:.0f}%，偏強")
    elif pct >= 50:
        pts = 10
    elif pct >= 30:
        pts = 5;  reasons.append(f"20 日動能歷史百分位偏低 {pct:.0f}%")
    else:
        pts = 0;  reasons.append(f"20 日報酬 {current_ret:+.1f}% 處於歷史底部 {pct:.0f}% 分位")

    return _clamp(pts, 0, 20), reasons


def _score_risk_adjusted(
    closes: list[float],
    bench_closes: list[float] | None,
) -> tuple[float, list[str]]:
    """
    波動率調整後超額報酬 (0-20 pts)
    類 Sharpe：超額報酬 / 個股波動率
    """
    reasons: list[str] = []
    ret20_s = _ret(closes, 20)
    ret20_b = _ret(bench_closes, 20) if bench_closes else None
    vol_s   = _vol_annualized(closes, 20)

    if ret20_s is None or vol_s is None or vol_s == 0:
        return 10.0, []

    excess = ret20_s - (ret20_b or 0)
    adj    = excess / (vol_s / 100)   # excess return per unit annualized vol

    if adj > 2.0:
        pts = 20; reasons.append(f"波動率調整後超額報酬 {adj:.2f}，風險報酬極佳")
    elif adj > 1.0:
        pts = 15; reasons.append(f"波動率調整後超額報酬 {adj:.2f}，良好")
    elif adj > 0.3:
        pts = 10
    elif adj > -0.5:
        pts = 6
    else:
        pts = 2;  reasons.append(f"波動率調整後報酬偏低（{adj:.2f}），承受風險不成比例")

    return _clamp(pts, 0, 20), reasons


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(ohlcv: dict, bench_ohlcv: dict | None = None) -> dict:
    """
    計算相對強弱分數。

    Parameters
    ----------
    ohlcv       : 個股標準化 OHLCV
    bench_ohlcv : 基準指數 OHLCV（建議 QQQ / SPY），None 則使用絕對動能

    Returns
    -------
    dict
        score      : float  0-100
        sub_scores : dict
        reasons    : list
        signals    : dict
        confidence : str
    """
    closes = [v for v in (ohlcv.get("closes") or []) if v and v > 0]
    bench  = [v for v in ((bench_ohlcv or {}).get("closes") or []) if v and v > 0]

    if len(closes) < 20:
        return {
            "score": 40, "sub_scores": {}, "reasons": ["相對強弱資料不足"],
            "signals": {}, "confidence": "LOW",
        }

    all_reasons: list[str] = []
    total_pts   = 0.0

    # 子分數 1-4: 各期相對報酬 (最高 10+15+15+20 = 60 pts)
    periods = [(5, 10, "5 日"), (10, 15, "10 日"), (20, 15, "20 日"), (60, 20, "60 日")]
    for days, w, label in periods:
        s_ret = _ret(closes, days)
        b_ret = _ret(bench, days) if len(bench) > days else None
        pts, reason = _score_relative_return(s_ret, b_ret, label, w)
        total_pts += pts
        if reason:
            all_reasons.append(reason)

    # 子分數 5: 動能百分位 (0-20)
    pct_pts, pct_r = _score_momentum_percentile(closes)
    total_pts += pct_pts
    all_reasons.extend(pct_r[:1])

    # 子分數 6: 波動率調整後報酬 (0-20)
    adj_pts, adj_r = _score_risk_adjusted(closes, bench if bench else None)
    total_pts += adj_r and 0 or 0   # 只累加分數
    adj_pts_real, _ = _score_risk_adjusted(closes, bench if bench else None)
    total_pts += adj_pts_real
    all_reasons.extend(adj_r[:1])

    # 最高 = 60 + 20 + 20 = 100 (已歸一化)
    score = _clamp(total_pts / (60 + 20 + 20) * 100, 0, 100)

    # 計算各期超額報酬供 signals 使用
    excess_20d = None
    if len(closes) >= 21 and len(bench) >= 21:
        excess_20d = (_ret(closes, 20) or 0) - (_ret(bench, 20) or 0)

    confidence = "HIGH" if len(bench) >= 60 and len(closes) >= 60 else \
                 "MEDIUM" if len(closes) >= 20 else "LOW"
    if ohlcv.get("is_demo"):
        confidence = "LOW"

    # ── label / warning（新增欄位，向後相容） ─────────────────────────────────
    _s = round(score, 1)
    _label = ("強勢" if _s >= 80 else "偏強" if _s >= 65 else
              "中性" if _s >= 50 else "偏弱" if _s >= 35 else "弱勢")
    _warning: list[str] = []
    _under = excess_20d is not None and excess_20d < -3
    if _under:
        _warning.append(f"持續落後大盤（20日超額報酬 {excess_20d:.1f}%），相對弱勢")
    if pct_pts == 0:
        _warning.append("動能處於歷史底部分位，趨勢動能極弱")

    return {
        "score":   _s,
        "label":   _label,
        "warning": _warning,
        "sub_scores": {
            "ret_5d":   round(_ret(closes, 5)  or 0, 2),
            "ret_10d":  round(_ret(closes, 10) or 0, 2),
            "ret_20d":  round(_ret(closes, 20) or 0, 2),
            "ret_60d":  round(_ret(closes, 60) or 0, 2),
            "momentum_percentile": round(pct_pts, 1),
            "risk_adjusted":       round(adj_pts_real, 1),
        },
        "reason":  all_reasons[:5],   # alias for new interface
        "reasons": all_reasons[:5],   # keep legacy key
        "signals": {
            "outperforming_20d":   excess_20d is not None and excess_20d > 3,
            "underperforming_20d": _under,
            "positive_momentum":   (_ret(closes, 20) or 0) > 0,
            "strong_momentum":     pct_pts >= 15,
        },
        "confidence": confidence,
        "detail": {
            "ret_5d_pct":  round(_ret(closes, 5)  or 0, 2),
            "ret_20d_pct": round(_ret(closes, 20) or 0, 2),
            "ret_60d_pct": round(_ret(closes, 60) or 0, 2),
            "bench_ret_20d": round(_ret(bench, 20) or 0, 2) if bench else None,
            "excess_20d":   round(excess_20d, 2) if excess_20d is not None else None,
            "vol_annualized": round(_vol_annualized(closes) or 0, 1),
        },
    }

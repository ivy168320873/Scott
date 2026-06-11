"""
估值分數模組 (Valuation Score) — 0~100 分
------------------------------------------
評估個股估值合理性。當有基本面資料時使用 PE / PB / 成長率；
沒有時以「動能品質」（趨勢一致性 + 波動穩定性）作為代理。

注意：估值分析不是動能系統的核心，此模組僅提供補充視角。
高估值不一定不能買，但高估值 + 高動能 = 追高風險上升。

輸入：
  ohlcv      : 標準化 OHLCV
  fundamentals: 選填，dict，含 pe, pb, revenue_growth_yoy, eps_beat,
                forward_pe, price_to_sales, debt_to_equity 等
"""
from __future__ import annotations

import statistics


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _sma(lst: list[float], n: int) -> float | None:
    valid = [v for v in lst[-n:] if v and v > 0]
    if len(valid) < n // 2:
        return None
    return sum(valid) / len(valid)


# ── 基本面評分 ────────────────────────────────────────────────────────────────

def _score_pe(pe: float | None, sector_pe: float | None = None) -> tuple[float, str | None]:
    """
    PE 評分 (0-25 pts)
    相對 sector PE 或絕對 PE 判斷。
    注意：高成長股高 PE 是正常的，需搭配 forward PE。
    """
    if pe is None:
        return 12.0, None   # 中性

    reasons = None
    if pe < 0:
        return 3.0, f"PE 為負（{pe:.1f}），公司虧損，估值無意義"

    ref = sector_pe if sector_pe and sector_pe > 0 else 25

    ratio = pe / ref
    if ratio < 0.7:
        pts = 25; reasons = f"PE {pe:.1f}× 低於參考 {ref:.0f}×（比值 {ratio:.2f}），估值具吸引力"
    elif ratio < 0.9:
        pts = 20; reasons = f"PE {pe:.1f}× 略低於參考，合理偏低"
    elif ratio < 1.2:
        pts = 15
    elif ratio < 1.6:
        pts = 8;  reasons = f"PE {pe:.1f}× 高於參考 {ratio:.1f}× 倍，估值偏貴"
    elif ratio < 2.5:
        pts = 4;  reasons = f"PE {pe:.1f}× 顯著偏高（{ratio:.1f}× 參考），需強勁成長支撐"
    else:
        pts = 1;  reasons = f"PE {pe:.1f}× 嚴重偏高（{ratio:.1f}×），高估值泡沫風險"

    return _clamp(pts, 0, 25), reasons


def _score_growth(revenue_growth_yoy: float | None, eps_beat: bool | None) -> tuple[float, str | None]:
    """
    成長品質評分 (0-30 pts)
    高估值 + 高成長 = 合理；高估值 + 低成長 = 危險。
    """
    if revenue_growth_yoy is None:
        return 15.0, None

    reasons = None
    g = revenue_growth_yoy   # 百分比

    if g > 50:
        pts = 30; reasons = f"營收年增率 {g:.1f}%，超高速成長，估值溢價合理"
    elif g > 25:
        pts = 24; reasons = f"營收年增率 {g:.1f}%，高速成長"
    elif g > 10:
        pts = 18
    elif g > 0:
        pts = 10
    elif g > -10:
        pts = 5;  reasons = f"營收年增率 {g:.1f}%，成長停滯"
    else:
        pts = 1;  reasons = f"營收年增率 {g:.1f}%，衰退中，估值風險大"

    # EPS 超預期加分
    if eps_beat is True:
        pts = min(30, pts + 6)
        reasons = (reasons or "") + "，EPS 超越預期（Earnings Beat）"

    return _clamp(pts, 0, 30), reasons


def _score_pb(pb: float | None) -> tuple[float, str | None]:
    """PB 評分 (0-15 pts)"""
    if pb is None:
        return 7.0, None
    if pb < 0:
        return 2.0, f"PB 為負（{pb:.1f}），股東權益為負"
    if pb < 1.0:
        pts = 15; reason = f"PB {pb:.1f}× 低於 1 倍，資產保護性強"
    elif pb < 2.0:
        pts = 12; reason = f"PB {pb:.1f}×，合理"
    elif pb < 4.0:
        pts = 8
    elif pb < 8.0:
        pts = 4;  reason = f"PB {pb:.1f}×，偏高"
    else:
        pts = 1;  reason = f"PB {pb:.1f}×，非常高"
    return _clamp(pts, 0, 15), reason if pb < 1.0 or pb > 4.0 else None


# ── 動能品質代理（無基本面時使用）─────────────────────────────────────────────

def _score_trend_consistency(closes: list[float]) -> tuple[float, list[str]]:
    """
    趨勢一致性得分 (0-40 pts)
    計算過去 60 日中上漲日 / 下跌日比例，及連續漲跌。
    作為「無基本面時」的估值代理：
    穩定向上 = 質量好；鋸齒上漲 = 不穩 = 潛在假突破。
    """
    reasons: list[str] = []
    if len(closes) < 20:
        return 20.0, []

    window = closes[-min(60, len(closes)):]
    up_days   = sum(1 for i in range(1, len(window)) if window[i] > window[i - 1])
    down_days = len(window) - 1 - up_days
    up_ratio  = up_days / (len(window) - 1) if len(window) > 1 else 0.5

    if up_ratio >= 0.65:
        pts = 40; reasons.append(f"近期 {up_days}/{len(window)-1} 日上漲，趨勢高度一致")
    elif up_ratio >= 0.55:
        pts = 28; reasons.append(f"近期 {up_ratio*100:.0f}% 上漲日，趨勢偏多")
    elif up_ratio >= 0.45:
        pts = 20
    elif up_ratio >= 0.35:
        pts = 10; reasons.append(f"近期 {up_ratio*100:.0f}% 上漲日，多空拉鋸")
    else:
        pts = 2;  reasons.append(f"近期僅 {up_ratio*100:.0f}% 上漲日，空頭格局")

    return _clamp(pts, 0, 40), reasons


def _score_volatility_quality(closes: list[float]) -> tuple[float, list[str]]:
    """
    波動穩定性得分 (0-30 pts)
    穩定上漲（低波動率）= 高分；劇烈震盪 = 低分。
    """
    reasons: list[str] = []
    if len(closes) < 15:
        return 15.0, []

    import math
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, min(len(closes), 61))
            if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 5:
        return 15.0, []

    try:
        vol = statistics.stdev(rets) * math.sqrt(252) * 100
    except Exception:
        return 15.0, []

    if vol < 20:
        pts = 30; reasons.append(f"年化波動率 {vol:.1f}%，低波動穩健上升")
    elif vol < 35:
        pts = 22; reasons.append(f"年化波動率 {vol:.1f}%，適中")
    elif vol < 50:
        pts = 14; reasons.append(f"年化波動率 {vol:.1f}%，偏高，需承受較大波動")
    elif vol < 70:
        pts = 6;  reasons.append(f"年化波動率 {vol:.1f}%，高波動，投機性強")
    else:
        pts = 1;  reasons.append(f"年化波動率 {vol:.1f}%，極高波動，風險極大")

    return _clamp(pts, 0, 30), reasons


def _score_price_quality(closes: list[float]) -> tuple[float, list[str]]:
    """
    價格品質代理（最後補充，0-30 pts）
    計算 R-squared of linear trend fit （趨勢擬合度）。
    """
    reasons: list[str] = []
    n = min(len(closes), 60)
    if n < 10:
        return 15.0, []

    window = closes[-n:]
    xs = list(range(n))
    ys = window

    # 簡單線性回歸
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (y_mean + (sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) /
                                  max(1e-9, sum((x - x_mean) ** 2 for x in xs))) * (x - x_mean))) ** 2
                 for x, y in zip(xs, ys))

    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    r2 = max(0, min(1, r2))

    # 趨勢方向
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / max(
        1e-9, sum((x - x_mean) ** 2 for x in xs)
    )
    trend_up = slope > 0

    if r2 >= 0.85 and trend_up:
        pts = 30; reasons.append(f"趨勢擬合度 {r2:.2f}（高度線性上漲），品質最佳")
    elif r2 >= 0.65 and trend_up:
        pts = 22; reasons.append(f"趨勢擬合度 {r2:.2f}，穩定上升")
    elif r2 >= 0.45:
        pts = 14
    elif r2 >= 0.25:
        pts = 7
    else:
        pts = 2;  reasons.append(f"趨勢擬合度 {r2:.2f}（震盪，無明確方向）")

    return _clamp(pts, 0, 30), reasons


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(ohlcv: dict, fundamentals: dict | None = None) -> dict:
    """
    計算估值分數。

    Parameters
    ----------
    ohlcv        : 標準化 OHLCV
    fundamentals : 選填 dict，可含以下欄位：
        pe                 : float  本益比
        forward_pe         : float  未來 PE
        pb                 : float  淨值比
        revenue_growth_yoy : float  營收年增率（百分比）
        eps_beat           : bool   EPS 是否超預期
        sector_pe          : float  同業 PE
        price_to_sales     : float  P/S ratio
        debt_to_equity     : float  負債比

    Returns
    -------
    dict
        score         : float  0-100
        sub_scores    : dict
        reasons       : list
        signals       : dict
        confidence    : str
        has_fundamentals : bool
    """
    closes = [v for v in (ohlcv.get("closes") or []) if v and v > 0]

    if len(closes) < 10:
        return {
            "score": 40, "sub_scores": {}, "reasons": ["資料不足"],
            "signals": {}, "confidence": "LOW", "has_fundamentals": False,
        }

    fund = fundamentals or {}
    has_fund = bool(fund)
    all_reasons: list[str] = []

    if has_fund:
        # ── 基本面模式 ──────────────────────────────────────────────────────
        pe_pts, pe_r = _score_pe(
            fund.get("pe"),
            fund.get("sector_pe"),
        )
        if pe_r:
            all_reasons.append(pe_r)

        growth_pts, growth_r = _score_growth(
            fund.get("revenue_growth_yoy"),
            fund.get("eps_beat"),
        )
        if growth_r:
            all_reasons.append(growth_r)

        pb_pts, pb_r = _score_pb(fund.get("pb"))
        if pb_r:
            all_reasons.append(pb_r)

        # 負債加扣
        d2e = fund.get("debt_to_equity")
        d2e_pts = 0.0
        if d2e is not None:
            if d2e > 3.0:
                d2e_pts = -10
                all_reasons.append(f"負債/股東權益 {d2e:.1f}，高財務槓桿風險")
            elif d2e > 1.5:
                d2e_pts = -4
            else:
                d2e_pts = 5   # 健全財務加分

        raw = pe_pts + growth_pts + pb_pts + d2e_pts  # max = 25+30+15+5 = 75
        score = _clamp(raw / 75 * 100)
        sub_scores = {
            "pe":             round(pe_pts, 1),
            "growth":         round(growth_pts, 1),
            "pb":             round(pb_pts, 1),
            "leverage_adj":   round(d2e_pts, 1),
        }
        confidence = "HIGH"
    else:
        # ── 動能品質代理模式 ────────────────────────────────────────────────
        con_pts, con_r = _score_trend_consistency(closes)
        all_reasons.extend(con_r[:1])

        vq_pts, vq_r = _score_volatility_quality(closes)
        all_reasons.extend(vq_r[:1])

        pq_pts, pq_r = _score_price_quality(closes)
        all_reasons.extend(pq_r[:1])

        raw = con_pts + vq_pts + pq_pts  # max = 40+30+30 = 100
        score = _clamp(raw)
        sub_scores = {
            "trend_consistency":   round(con_pts, 1),
            "volatility_quality":  round(vq_pts, 1),
            "price_quality_r2":    round(pq_pts, 1),
        }
        confidence = "MEDIUM" if len(closes) >= 60 else "LOW"

    if ohlcv.get("is_demo"):
        confidence = "LOW"

    # ── label / warning（新增欄位，向後相容） ─────────────────────────────────
    _s = round(score, 1)
    _label = ("強勢" if _s >= 80 else "偏強" if _s >= 65 else
              "中性" if _s >= 50 else "偏弱" if _s >= 35 else "弱勢")
    _warning: list[str] = []
    if not has_fund:
        _warning.append("無基本面資料：估值以動能品質代理，精確度有限")
    if has_fund:
        _pe = fund.get("pe") or 0
        if _pe > 50:
            _warning.append(f"本益比偏高 (PE={_pe:.1f})，估值風險存在")
        _de = fund.get("debt_to_equity") or 0
        if _de > 1.5:
            _warning.append(f"負債權益比偏高 ({_de:.1f}x)，財務槓桿風險")

    _sig_val = {
        "undervalued":   has_fund and sub_scores.get("pe", 12) >= 20,
        "high_growth":   has_fund and sub_scores.get("growth", 15) >= 24,
        "quality_trend": not has_fund and sub_scores.get("trend_consistency", 20) >= 28,
    }

    return {
        "score":   _s,
        "label":   _label,
        "warning": _warning,
        "sub_scores":       sub_scores,
        "reason":  all_reasons[:4],   # alias for new interface
        "reasons": all_reasons[:4],   # keep legacy key
        "signals": _sig_val,
        "confidence":       confidence,
        "has_fundamentals": has_fund,
        "detail": {
            "pe":                  fund.get("pe"),
            "pb":                  fund.get("pb"),
            "revenue_growth_yoy":  fund.get("revenue_growth_yoy"),
            "sector_pe":           fund.get("sector_pe"),
        } if has_fund else {},
    }

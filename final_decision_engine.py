"""
最終決策引擎 (Final Decision Engine) — 整合 6 個子模組
---------------------------------------------------------
彙整：
  · trend_score        趨勢分數  (權重 30%)
  · volume_score       量能分數  (權重 20%)
  · relative_strength  相對強弱  (權重 20%)
  · catalyst_score     催化劑    (權重 10%)
  · valuation_score    估值分數  (權重 10%)
  · risk_score         風險分數  (權重 10%，使用 100-risk 使方向一致)

輸出：total_score / grade / signal_status / action / stop_loss /
       target_range / invalidation / component_scores / reasons /
       risk_warnings / confidence / disclaimer
"""
from __future__ import annotations

import traceback
from typing import Any

# ── 子模組匯入 ─────────────────────────────────────────────────────────────────
try:
    import trend_score as _trend
    import volume_score as _volume
    import relative_strength_score as _rs
    import catalyst_score as _catalyst
    import valuation_score as _valuation
    import risk_score as _risk
    _ALL_MODULES_OK = True
except ImportError as _e:
    _ALL_MODULES_OK = False
    _IMPORT_ERROR = str(_e)


# ── 常數 ──────────────────────────────────────────────────────────────────────

_WEIGHTS = {
    "trend":     0.30,
    "volume":    0.20,
    "rs":        0.20,
    "catalyst":  0.10,
    "valuation": 0.10,
    "safety":    0.10,   # = (100 - risk_score) / 100 * 100
}

# 評級閾值
_GRADE_TABLE = [
    (88, "A+"),
    (76, "A"),
    (63, "B"),
    (50, "C"),
    (0,  "D"),
]

# 訊號狀態對照（依 total_score + signals 判斷）
_SIGNAL_MAP = [
    # (min_score, trend_ok, vol_ok, label)
    (78, True,  True,  "強勢突破"),
    (65, True,  True,  "健康上升"),
    (65, True,  False, "量能待確認"),
    (55, False, False, "震盪整理"),
    (45, False, True,  "高檔過熱"),
    (35, False, False, "弱勢反彈"),
    (0,  False, False, "空頭結構"),
]

# 行動建議對照（score閾值, action_code, action_label, 說明）
_ACTION_MAP = [
    (80, "BUY",   "買進", "可追，設停損後積極建倉"),
    (68, "BUY",   "買進", "突破確認後可買入，等回測更佳"),
    (55, "WATCH", "觀望", "僅觀察，不建議主動追價"),
    (42, "WATCH", "觀望", "等待更強訊號，暫不進場"),
    (0,  "EXIT",  "出場", "不建議進場，風險過高"),
]


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _grade(score: float) -> str:
    for threshold, label in _GRADE_TABLE:
        if score >= threshold:
            return label
    return "D"


def _signal_status(
    total: float,
    trend_sig: dict,
    volume_sig: dict,
    risk_sig: dict,
) -> str:
    trend_ok  = trend_sig.get("strong_uptrend") or trend_sig.get("in_uptrend", False)
    vol_ok    = volume_sig.get("high_volume_5d") or volume_sig.get("vol_expanding", False)
    dist_warn = volume_sig.get("distribution_warning", False)
    crash     = risk_sig.get("crash_risk", False)

    if crash:
        return "崩盤風險"
    if dist_warn and total < 60:
        return "高檔過熱"
    if total >= 78 and trend_ok and vol_ok:
        return "強勢突破"
    if total >= 65 and trend_ok and vol_ok:
        return "健康上升"
    if total >= 65 and trend_ok:
        return "量能待確認"
    if total >= 55:
        return "震盪整理"
    if total >= 35:
        return "弱勢反彈"
    return "空頭結構"


def _action(total: float, risk_lvl: str, is_demo: bool) -> tuple[str, str, str]:
    """Returns (action_code, action_label, action_detail)."""
    if is_demo:
        return ("WATCH", "觀望", "示範資料，請勿作為實際投資依據")
    if risk_lvl == "EXTREME":
        return ("EXIT", "出場", "風險極高，不建議進場")
    if risk_lvl == "HIGH" and total < 55:
        return ("TRIM", "減碼", "高風險且動能偏弱，建議降低持倉")
    for threshold, code, label, detail in _ACTION_MAP:
        if total >= threshold:
            return (code, label, detail)
    return ("EXIT", "出場", "不建議進場，風險過高")


def _position_sizing(total: float, risk_lvl: str, is_demo: bool) -> dict:
    """建議倉位比例（佔可投資資金）。"""
    if is_demo or risk_lvl == "EXTREME":
        return {"pct": 0, "label": "不進場", "note": "風險過高或示範資料"}
    tbl = [
        # (min_score, risk_lvl_allow, pct_lo, pct_hi, label)
        (75, "LOW",    10, 15, "積極建倉"),
        (65, "LOW",    7,  10, "標準建倉"),
        (55, "LOW",    5,   7, "試探性建倉"),
        (65, "MEDIUM", 5,   8, "適量建倉"),
        (50, "MEDIUM", 3,   5, "輕倉觀察"),
        (40, "MEDIUM", 2,   3, "極輕倉試水"),
        (0,  "HIGH",   0,   2, "高風險僅極輕倉"),
    ]
    for min_s, lvl, lo, hi, label in tbl:
        if total >= min_s and (risk_lvl == lvl or risk_lvl == "LOW"):
            return {"pct": hi, "pct_range": [lo, hi], "label": label,
                    "note": f"建議佔投資資金 {lo}~{hi}%"}
    return {"pct": 0, "label": "暫不建倉", "note": "條件不符，等待更佳訊號"}


def _pick_confidence(
    trend_c: str, volume_c: str, rs_c: str,
    catalyst_c: str, val_c: str, risk_c: str,
) -> str:
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    vals = [order.get(c, 1) for c in [trend_c, volume_c, rs_c, catalyst_c, val_c, risk_c]]
    avg = sum(vals) / len(vals)
    if avg >= 2.5:
        return "HIGH"
    if avg >= 1.5:
        return "MEDIUM"
    return "LOW"


def _build_risk_warnings(risk_result: dict, volume_result: dict, trend_result: dict) -> list[str]:
    warnings: list[str] = []
    sig_r = risk_result.get("signals", {})
    sig_v = volume_result.get("signals", {})
    sig_t = trend_result.get("signals", {})

    if sig_r.get("kill_signal"):
        warnings.append("⛔ 觸發 Kill Signal：技術面與風控雙重警示，強制降低倉位")
    if sig_r.get("crash_risk"):
        warnings.append("🔴 崩盤風險：近期急跌幅度超出正常區間，建議退場觀望")
    if sig_v.get("distribution_warning"):
        warnings.append("🟠 偵測到主力出貨訊號：爆量長上影 / 跌日高量，謹慎追高")
    if sig_r.get("extreme_chase"):
        warnings.append("🟡 追高風險過高：當前價格已大幅偏離均線，入場勝率下降")
    if sig_t.get("death_cross"):
        warnings.append("📉 死亡交叉：短均線已跌破長均線，趨勢轉空")
    if sig_r.get("high_atr_risk"):
        warnings.append("⚠️ ATR 波動率偏高：單日波動超出正常水準，需縮小倉位")

    return warnings


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(
    ohlcv: dict,
    bench_ohlcv: dict | None = None,
    news_items: list[dict] | None = None,
    fundamentals: dict | None = None,
    holdings: list[dict] | None = None,
    risk_profile: str = "balanced",
) -> dict:
    """
    計算最終決策分數。

    Parameters
    ----------
    ohlcv         : 個股標準化 OHLCV
    bench_ohlcv   : 基準指數 OHLCV（建議 QQQ / SPY）
    news_items    : [{sentiment, headline, ...}]
    fundamentals  : {pe, forward_pe, pb, revenue_growth_yoy, ...}
    holdings      : 持倉列表（目前保留參數，未來擴充用）
    risk_profile  : "conservative" | "balanced" | "aggressive"

    Returns
    -------
    dict
        total_score      : float  0-100
        grade            : str    A+/A/B/C/D
        signal_status    : str    強勢突破/健康上升/...
        action           : str    行動建議
        stop_loss        : float | None
        target_range     : [float, float] | None
        invalidation     : float | None
        component_scores : dict   各子模組原始分數
        weighted_scores  : dict   加權後貢獻分
        reasons          : list   主要理由（最多 8 條）
        risk_warnings    : list   風險警告
        confidence       : str    HIGH/MEDIUM/LOW
        is_demo          : bool
        disclaimer       : str
    """
    if not _ALL_MODULES_OK:
        return {
            "total_score": 0, "grade": "D",
            "signal_status": "模組載入失敗", "action": "系統錯誤",
            "stop_loss": None, "target_range": None, "invalidation": None,
            "component_scores": {}, "weighted_scores": {}, "reasons": [f"模組匯入失敗: {_IMPORT_ERROR}"],
            "risk_warnings": [], "confidence": "LOW",
            "is_demo": ohlcv.get("is_demo", False), "disclaimer": "",
        }

    is_demo = bool(ohlcv.get("is_demo"))
    all_reasons: list[str] = []
    errors: list[str] = []

    # ── 各子模組計算 ──────────────────────────────────────────────────────────

    def _safe(fn, *args, fallback_score=50, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            errors.append(f"{fn.__module__}: {traceback.format_exc(limit=1).strip()}")
            return {"score": fallback_score, "sub_scores": {}, "reasons": [],
                    "signals": {}, "confidence": "LOW"}

    trend_r   = _safe(_trend.compute,     ohlcv)
    volume_r  = _safe(_volume.compute,    ohlcv)
    rs_r      = _safe(_rs.compute,        ohlcv, bench_ohlcv)
    catalyst_r = _safe(_catalyst.compute, ohlcv, news_items)
    val_r     = _safe(_valuation.compute, ohlcv, fundamentals)
    risk_r    = _safe(_risk.compute,      ohlcv)

    # ── 原始分數 ──────────────────────────────────────────────────────────────
    t_score  = float(trend_r.get("score",    50))
    v_score  = float(volume_r.get("score",   50))
    rs_score = float(rs_r.get("score",       50))
    ca_score = float(catalyst_r.get("score", 50))
    va_score = float(val_r.get("score",      50))
    ri_score = float(risk_r.get("score",     50))
    safety   = _clamp(100.0 - ri_score)          # 高安全 = 低風險

    component_scores = {
        "trend":     round(t_score, 1),
        "volume":    round(v_score, 1),
        "rs":        round(rs_score, 1),
        "catalyst":  round(ca_score, 1),
        "valuation": round(va_score, 1),
        "risk":      round(ri_score, 1),
        "safety":    round(safety, 1),
    }

    # ── 加權計算 ──────────────────────────────────────────────────────────────
    w = _WEIGHTS
    wt = {
        "trend":     round(t_score  * w["trend"],     2),
        "volume":    round(v_score  * w["volume"],    2),
        "rs":        round(rs_score * w["rs"],        2),
        "catalyst":  round(ca_score * w["catalyst"],  2),
        "valuation": round(va_score * w["valuation"], 2),
        "safety":    round(safety   * w["safety"],    2),
    }
    raw_total = sum(wt.values())

    # ── Hard Gates ────────────────────────────────────────────────────────────
    gate_notes: list[str] = []
    r_signals = risk_r.get("signals", {})
    v_signals = volume_r.get("signals", {})

    if r_signals.get("kill_signal") and risk_r.get("confidence") in ("HIGH", "MEDIUM"):
        raw_total = min(raw_total, 35.0)
        gate_notes.append("Kill Signal → 分數上限 35")

    if r_signals.get("crash_risk"):
        raw_total = min(raw_total, 30.0)
        gate_notes.append("崩盤風險 → 分數上限 30")

    if v_signals.get("distribution_warning") and raw_total > 60:
        raw_total = min(raw_total, 58.0)
        gate_notes.append("出貨訊號 → 分數上限 58")

    if is_demo:
        raw_total = min(raw_total, 55.0)
        gate_notes.append("示範資料 → 分數上限 55")

    # risk_profile 調整：保守扣分、積極加分
    if risk_profile == "conservative":
        raw_total = _clamp(raw_total - 5)
        gate_notes.append("保守型 → -5")
    elif risk_profile == "aggressive":
        raw_total = _clamp(raw_total + 3)
        gate_notes.append("積極型 → +3")

    total_score = _clamp(raw_total)

    # ── 理由彙整（最重要的 8 條） ─────────────────────────────────────────────
    for r in trend_r.get("reasons", [])[:2]:
        all_reasons.append(f"[趨勢] {r}")
    for r in volume_r.get("reasons", [])[:2]:
        all_reasons.append(f"[量能] {r}")
    for r in rs_r.get("reasons", [])[:1]:
        all_reasons.append(f"[強弱] {r}")
    for r in catalyst_r.get("reasons", [])[:1]:
        all_reasons.append(f"[催化] {r}")
    for r in val_r.get("reasons", [])[:1]:
        all_reasons.append(f"[估值] {r}")
    for r in risk_r.get("reasons", [])[:1]:
        all_reasons.append(f"[風控] {r}")
    all_reasons = all_reasons[:8]

    if gate_notes:
        all_reasons.append("⚡ 硬性規則：" + "；".join(gate_notes))

    # ── 風控欄位 ──────────────────────────────────────────────────────────────
    stop_loss    = risk_r.get("stop_loss")
    invalidation = risk_r.get("invalidation")
    target_range = risk_r.get("target_zone")

    # ── 綜合信心度 ─────────────────────────────────────────────────────────────
    confidence = _pick_confidence(
        trend_r.get("confidence", "LOW"),
        volume_r.get("confidence", "LOW"),
        rs_r.get("confidence", "LOW"),
        catalyst_r.get("confidence", "LOW"),
        val_r.get("confidence", "LOW"),
        risk_r.get("confidence", "LOW"),
    )

    # ── 訊號 / 行動 ───────────────────────────────────────────────────────────
    _risk_lvl  = risk_r.get("risk_level", "MEDIUM")
    sig_status = _signal_status(total_score, trend_r.get("signals", {}),
                                volume_r.get("signals", {}), risk_r.get("signals", {}))
    action_code, action_label, action_detail = _action(total_score, _risk_lvl, is_demo)
    grade_str   = _grade(total_score)
    pos_sizing  = _position_sizing(total_score, _risk_lvl, is_demo)

    # ── 風險警告（合併子模組 warning） ────────────────────────────────────────
    risk_warnings = _build_risk_warnings(risk_r, volume_r, trend_r)
    # 匯集子模組自帶的 warning 欄位
    for mod_r in [trend_r, volume_r, rs_r, catalyst_r, val_r, risk_r]:
        for w in mod_r.get("warning", []):
            if w and w not in risk_warnings:
                risk_warnings.append(w)
    if errors:
        risk_warnings.append(f"系統警告：部分子模組發生錯誤（{len(errors)} 個），分數可能偏低")

    disclaimer = (
        "本分析純屬量化模型輸出，不構成投資建議。示範資料僅供系統測試用途。"
        if is_demo else
        "本分析純屬量化模型輸出，不構成投資建議。投資人應自行評估風險。"
    )

    return {
        # ── 主要決策欄位 ─────────────────────────────────────────────────────
        "total_score":   round(total_score, 1),
        "final_score":   round(total_score, 1),   # alias，與 total_score 相同
        "grade":         grade_str,
        "signal_status": sig_status,

        # action（新格式：code + label + detail；舊格式 action 保留為說明文字）
        "action":        action_detail,            # 舊欄位保留（說明文字）
        "action_code":   action_code,              # BUY / HOLD / WATCH / TRIM / EXIT
        "action_label":  action_label,             # 買進 / 續抱 / 觀望 / 減碼 / 出場

        # ── 風控欄位 ────────────────────────────────────────────────────────
        "stop_loss":     stop_loss,
        "target_range":  target_range,
        "invalidation":  invalidation,
        "position_sizing_suggestion": pos_sizing,

        # ── 子模組分數 ──────────────────────────────────────────────────────
        "component_scores": component_scores,
        "weighted_scores":  wt,

        # ── 理由 / 警告 ─────────────────────────────────────────────────────
        "reasons":       all_reasons,
        "reason":        all_reasons,              # alias
        "risk_warnings": risk_warnings,

        # ── 後設資訊 ────────────────────────────────────────────────────────
        "confidence":    confidence,
        "is_demo":       is_demo,
        "disclaimer":    disclaimer,
        "detail": {
            "risk_level":       _risk_lvl,
            "distribution":     volume_r.get("distribution", False),
            "has_news":         catalyst_r.get("detail", {}).get("has_news", False),
            "has_fundamentals": val_r.get("has_fundamentals", False),
            "gate_notes":       gate_notes,
            "module_errors":    errors,
        },
    }

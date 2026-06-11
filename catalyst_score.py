"""
催化劑分數模組 (Catalyst Score) — 0~100 分
-------------------------------------------
評估個股的新聞 / 事件催化強度，包含：
  · 價格加速（最近動能加速 vs 歷史均速）
  · 跳空缺口偵測（正向 / 負向）
  · 外部新聞情緒（若提供 news_items）
  · 近期異常量能爆發（可能反映未公開催化）

⚠️  無新聞資料時使用價格行為作為代理指標。
⚠️  此分數偵測的是「可能有催化」，不等於確認催化真實存在。
"""
from __future__ import annotations

import math


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _sma(lst: list[float], n: int) -> float | None:
    valid = [v for v in lst[-n:] if v is not None and v > 0]
    if len(valid) < n // 2:
        return None
    return sum(valid) / len(valid)


def _score_price_acceleration(closes: list[float]) -> tuple[float, list[str]]:
    """
    價格加速得分 (0-30 pts)
    比較近 5 日日均報酬 vs 前 20 日日均報酬。
    """
    reasons: list[str] = []
    if len(closes) < 25:
        return 15.0, []

    recent_rets = []
    for i in range(-5, 0):
        if closes[i - 1] > 0:
            recent_rets.append((closes[i] / closes[i - 1] - 1) * 100)

    older_rets = []
    for i in range(-25, -5):
        if closes[i - 1] > 0:
            older_rets.append((closes[i] / closes[i - 1] - 1) * 100)

    if not recent_rets or not older_rets:
        return 15.0, []

    avg_recent = sum(recent_rets) / len(recent_rets)
    avg_older  = sum(older_rets)  / len(older_rets)
    accel      = avg_recent - avg_older

    if accel > 2.0:
        pts = 30; reasons.append(f"動能顯著加速（近 5 日均日報 {avg_recent:+.2f}% vs 前期 {avg_older:+.2f}%），可能有催化事件")
    elif accel > 0.8:
        pts = 22; reasons.append(f"動能溫和加速（+{accel:.2f}%/日），觀察是否有消息面支撐")
    elif accel > 0.2:
        pts = 15
    elif accel > -0.5:
        pts = 10
    elif accel > -1.5:
        pts = 5;  reasons.append(f"動能減速（{accel:.2f}%/日），催化動能趨弱")
    else:
        pts = 0;  reasons.append(f"動能明顯減速（{accel:.2f}%/日），賣壓浮現")

    return _clamp(pts, 0, 30), reasons


def _score_gap(
    opens: list[float],
    closes: list[float],
    volumes: list[float],
) -> tuple[float, list[str]]:
    """
    跳空缺口得分 (0-25 pts，負向跳空為 0 且懲罰)
    近 5 日內出現跳空且有量的情況。
    """
    reasons: list[str] = []
    n = min(len(opens), len(closes), len(volumes))
    if n < 3:
        return 12.0, []

    opens  = opens[-n:]
    closes = closes[-n:]
    volumes = volumes[-n:]

    avg_vol = _sma(volumes, min(20, n - 1)) or 1
    best_pts = 12.0   # 無缺口 → 中性

    for i in range(-min(5, n), -1):
        prev_close = closes[i - 1]
        curr_open  = opens[i]
        if prev_close <= 0:
            continue

        gap_pct = (curr_open / prev_close - 1) * 100

        # 正向跳空
        if gap_pct > 2.0:
            vol_mult = volumes[i] / avg_vol if avg_vol > 0 else 1
            if vol_mult >= 1.5:
                pts = 25
                reasons.append(f"跳空上漲 +{gap_pct:.1f}%（量 {vol_mult:.1f}× 均量），強力催化訊號")
            elif vol_mult >= 1.0:
                pts = 18
                reasons.append(f"跳空上漲 +{gap_pct:.1f}%，帶有量能支撐")
            else:
                pts = 10
                reasons.append(f"跳空上漲 +{gap_pct:.1f}% 但量能不足，需確認")
            best_pts = max(best_pts, pts)

        # 跳空下跌 → 懲罰
        elif gap_pct < -2.0:
            reasons.append(f"跳空下跌 {gap_pct:.1f}%，出現負面催化或恐慌賣出")
            best_pts = min(best_pts, 2.0)
            break

    return _clamp(best_pts, 0, 25), reasons


def _score_news_sentiment(news_items: list[dict] | None) -> tuple[float, list[str]]:
    """
    新聞情緒得分 (0-30 pts)
    接受 Finnhub / 外部 API 的新聞列表，每筆需含:
      sentiment: "positive" | "neutral" | "negative"
      headline : str（選填）

    若無新聞資料，回傳中性 15 分。
    """
    if not news_items:
        return 15.0, []

    reasons: list[str] = []
    pos = sum(1 for n in news_items if n.get("sentiment") == "positive")
    neg = sum(1 for n in news_items if n.get("sentiment") == "negative")
    total = len(news_items)

    if total == 0:
        return 15.0, []

    pos_ratio = pos / total
    neg_ratio = neg / total

    if pos_ratio >= 0.7:
        pts = 30; reasons.append(f"近期新聞 {pos}/{total} 正面，消息面強力支撐")
    elif pos_ratio >= 0.5:
        pts = 22; reasons.append(f"近期新聞偏正面（{pos}/{total}）")
    elif neg_ratio >= 0.7:
        pts = 2;  reasons.append(f"近期新聞 {neg}/{total} 負面，消息面壓制")
    elif neg_ratio >= 0.5:
        pts = 8;  reasons.append(f"近期新聞偏負面（{neg}/{total}）")
    else:
        pts = 15

    # 抓出最重要的正面標題（若有）
    positive_news = [n for n in news_items if n.get("sentiment") == "positive"]
    if positive_news and positive_news[0].get("headline"):
        reasons.append(f"最新正面消息：{positive_news[0]['headline'][:60]}")

    return _clamp(pts, 0, 30), reasons


def _score_abnormal_volume_spike(volumes: list[float], closes: list[float]) -> tuple[float, list[str]]:
    """
    異常量能爆發得分 (0-15 pts)
    近 3 日內出現 3σ 以上的成交量異常（可能反映未公開消息）。
    """
    reasons: list[str] = []
    if len(volumes) < 25 or len(closes) < 3:
        return 7.0, []

    import statistics as stats
    base_vols = volumes[-25:-3]
    if len(base_vols) < 10:
        return 7.0, []

    try:
        mean_v = stats.mean(base_vols)
        std_v  = stats.stdev(base_vols)
    except Exception:
        return 7.0, []

    if std_v <= 0:
        return 7.0, []

    recent_max_vol = max(volumes[-3:])
    z_score = (recent_max_vol - mean_v) / std_v

    # 確認是否伴隨上漲（區分買入爆量 vs 賣出爆量）
    price_up = closes[-1] > closes[-4] if len(closes) >= 4 else False

    if z_score > 4.0 and price_up:
        pts = 15; reasons.append(f"量能 3 日內爆發 {z_score:.1f}σ（伴隨上漲），強力資金催化訊號")
    elif z_score > 3.0 and price_up:
        pts = 10; reasons.append(f"量能爆發 {z_score:.1f}σ，可能有未公開利多")
    elif z_score > 3.0:
        pts = 2;  reasons.append(f"量能異常爆發 {z_score:.1f}σ 但收盤下跌，警惕主力賣出")
    elif z_score > 2.0:
        pts = 7
    else:
        pts = 7   # 中性

    return _clamp(pts, 0, 15), reasons


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(
    ohlcv: dict,
    news_items: list[dict] | None = None,
) -> dict:
    """
    計算催化劑分數。

    Parameters
    ----------
    ohlcv      : 標準化 OHLCV
    news_items : 選填。格式：[{sentiment: str, headline: str, ...}, ...]
                 sentiment 值：'positive' | 'neutral' | 'negative'

    Returns
    -------
    dict
        score      : float  0-100
        sub_scores : dict
        reasons    : list
        signals    : dict
        confidence : str
    """
    closes  = [v for v in (ohlcv.get("closes")  or []) if v and v > 0]
    opens   = [v for v in (ohlcv.get("opens")   or []) if v and v > 0]
    volumes = [v for v in (ohlcv.get("volumes") or []) if v is not None and v >= 0]

    if len(closes) < 10:
        return {
            "score": 40, "sub_scores": {}, "reasons": ["催化資料不足"],
            "signals": {}, "confidence": "LOW",
        }

    all_reasons: list[str] = []

    # 子分數 1: 價格加速 (0-30)
    accel_pts, accel_r = _score_price_acceleration(closes)
    all_reasons.extend(accel_r[:1])

    # 子分數 2: 跳空 (0-25)
    gap_pts, gap_r = _score_gap(opens, closes, volumes)
    all_reasons.extend(gap_r[:1])

    # 子分數 3: 新聞情緒 (0-30)
    news_pts, news_r = _score_news_sentiment(news_items)
    all_reasons.extend(news_r[:2])

    # 子分數 4: 量能異常爆發 (0-15)
    spike_pts, spike_r = _score_abnormal_volume_spike(volumes, closes)
    all_reasons.extend(spike_r[:1])

    # 加總 / 歸一化（最高 30+25+30+15=100）
    raw = accel_pts + gap_pts + news_pts + spike_pts
    score = _clamp(raw)

    has_news = bool(news_items)
    confidence = "HIGH" if has_news and len(closes) >= 30 else \
                 "MEDIUM" if len(closes) >= 20 else "LOW"
    if ohlcv.get("is_demo"):
        confidence = "LOW"

    return {
        "score": round(score, 1),
        "sub_scores": {
            "price_acceleration": round(accel_pts, 1),
            "gap_signal":         round(gap_pts, 1),
            "news_sentiment":     round(news_pts, 1),
            "volume_spike":       round(spike_pts, 1),
        },
        "reasons": all_reasons[:5],
        "signals": {
            "accelerating":       accel_pts >= 22,
            "gap_up_detected":    gap_pts >= 18,
            "positive_news":      news_pts >= 22 if has_news else False,
            "has_news_data":      has_news,
            "volume_spike":       spike_pts >= 10,
        },
        "confidence": confidence,
        "detail": {
            "news_count": len(news_items) if news_items else 0,
            "has_news":   has_news,
        },
    }

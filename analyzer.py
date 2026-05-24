"""
Expert rule-based technical analysis engine.
Called when Claude API is not configured; produces detailed natural-language analysis.
"""
from __future__ import annotations
import os, json
from typing import Any


def _rsi_text(v: float) -> str:
    if v >= 80: return f"RSI {v:.1f} 處於嚴重超買區，短期回調壓力極大"
    if v >= 70: return f"RSI {v:.1f} 進入超買區，需警惕拉回風險"
    if v >= 60: return f"RSI {v:.1f} 偏多頭，動能尚可"
    if v >= 50: return f"RSI {v:.1f} 維持多頭偏強格局"
    if v >= 40: return f"RSI {v:.1f} 略偏弱，中性偏空"
    if v >= 30: return f"RSI {v:.1f} 接近超賣區，需留意止跌訊號"
    return f"RSI {v:.1f} 已在超賣區，逢低買進機會浮現"


def _macd_text(hist: float, macd: float, signal: float) -> str:
    cross = "MACD 上穿 Signal（黃金交叉）" if macd > signal else "MACD 下穿 Signal（死亡交叉）"
    momentum = "柱狀圖為正且放大" if hist > 0 else "柱狀圖為負且擴展"
    if hist > 0:
        return f"{cross}，{momentum}，短線多頭動能加速"
    else:
        return f"{cross}，{momentum}，短線空頭壓力增加"


def _bb_text(pct_b: float, price: float, upper: float, lower: float) -> str:
    if pct_b > 1.0:
        return f"價格突破布林上軌（{upper:.2f}），處於超買臨界，需觀察能否持續放量突破"
    if pct_b > 0.8:
        return f"價格緊貼布林上軌（{upper:.2f}），多頭氣勢強勁"
    if pct_b < 0.0:
        return f"價格跌破布林下軌（{lower:.2f}），短線超賣，可能出現技術反彈"
    if pct_b < 0.2:
        return f"價格靠近布林下軌（{lower:.2f}），支撐位參考"
    return f"布林%B = {pct_b:.2f}，價格在布林帶中部遊走，方向待確認"


def _ma_text(ma_analysis: list[dict]) -> str:
    bull = [m for m in ma_analysis if m.get("bullish")]
    bear = [m for m in ma_analysis if not m.get("bullish")]
    parts = []
    if len(bull) >= 3:
        names = "、".join(m["label"] for m in bull[:3])
        parts.append(f"價格站上 {names} 等均線，多頭排列佔優")
    elif len(bear) >= 3:
        names = "、".join(m["label"] for m in bear[:3])
        parts.append(f"價格跌破 {names}，均線空頭排列")
    else:
        parts.append("均線多空混雜，市場方向尚未明朗")
    return "；".join(parts)


def _stoch_text(k: float, d: float) -> str:
    if k > 80 and d > 80: return f"隨機指標 K={k:.1f}/D={d:.1f} 雙雙位於超買區"
    if k < 20 and d < 20: return f"隨機指標 K={k:.1f}/D={d:.1f} 雙雙位於超賣區，可能觸底反彈"
    if k > d and k < 80: return f"K 線上穿 D 線（{k:.1f} > {d:.1f}），短線買進訊號"
    if k < d and k > 20: return f"K 線下穿 D 線（{k:.1f} < {d:.1f}），短線賣出訊號"
    return f"隨機指標 K={k:.1f}/D={d:.1f}，中性"


def _adx_text(adx: float, di_plus: float, di_minus: float) -> str:
    trend = di_plus > di_minus
    if adx >= 40: strength = "強勢趨勢（ADX={:.0f}）".format(adx)
    elif adx >= 25: strength = "趨勢成形中（ADX={:.0f}）".format(adx)
    else: strength = "無明顯趨勢（ADX={:.0f}，市場盤整）".format(adx)
    direction = f"+DI={di_plus:.1f} > -DI={di_minus:.1f}，多頭方向" if trend else f"-DI={di_minus:.1f} > +DI={di_plus:.1f}，空頭方向"
    return f"{strength}；{direction}"


def _vol_text(ratio: float) -> str:
    if ratio >= 2.0: return f"成交量暴增至均量 {ratio:.1f} 倍，可能為突破確認或主力出貨"
    if ratio >= 1.4: return f"成交量放大至均量 {ratio:.1f} 倍，價量配合良好"
    if ratio <= 0.5: return f"成交量萎縮至均量 {ratio:.1f} 倍，觀望氣氛濃厚"
    return f"成交量正常（均量比 {ratio:.1f}x）"


def _support_resistance(price: float, bb_upper: float, bb_lower: float,
                         sma20: float | None, sma50: float | None) -> dict:
    resistances, supports = [], []
    if bb_upper and bb_upper > price: resistances.append(round(bb_upper, 2))
    if sma50 and sma50 > price: resistances.append(round(sma50, 2))
    if sma20 and sma20 > price: resistances.append(round(sma20, 2))
    if bb_lower and bb_lower < price: supports.append(round(bb_lower, 2))
    if sma20 and sma20 < price: supports.append(round(sma20, 2))
    if sma50 and sma50 < price: supports.append(round(sma50, 2))
    resistances.sort()
    supports.sort(reverse=True)
    return {
        "nearest_resistance": resistances[:2],
        "nearest_support": supports[:2],
    }


def _score_to_signal(score: float) -> tuple[str, str]:
    if score >= 72: return "強力買入", "bullish"
    if score >= 58: return "溫和買入", "mild-bullish"
    if score >= 42: return "中性觀望", "neutral"
    if score >= 28: return "溫和賣出", "mild-bearish"
    return "強力賣出", "bearish"


def rule_based_analysis(payload: dict) -> dict:
    ind = payload.get("indicators", {})
    ma = payload.get("ma_analysis", [])
    symbol = payload.get("symbol", "")
    price = payload.get("price", 0)
    change_pct = payload.get("change_pct", 0)
    vol_ratio = payload.get("volume_ratio", 1)
    score = payload.get("momentum_score", 50)
    roc = payload.get("roc", 0)
    pct_from_high = payload.get("pct_from_high", 0)

    rsi_v = ind.get("rsi") or 50
    macd_h = ind.get("macd_hist") or 0
    macd_v = ind.get("macd") or 0
    sig_v = ind.get("macd_signal") or 0
    stoch_k = ind.get("stoch_k") or 50
    stoch_d = ind.get("stoch_d") or 50
    bb_pct = ind.get("bb_pct_b") or 0.5
    bb_upper = ind.get("bb_upper") or price * 1.05
    bb_lower = ind.get("bb_lower") or price * 0.95
    adx_v = ind.get("adx") or 0
    di_plus = ind.get("di_plus") or 0
    di_minus = ind.get("di_minus") or 0

    sma20 = next((m["value"] for m in ma if "20" in m["label"]), None)
    sma50 = next((m["value"] for m in ma if "50" in m["label"]), None)

    signal, signal_class = _score_to_signal(score)
    levels = _support_resistance(price, bb_upper, bb_lower, sma20, sma50)

    bullish_count = sum(1 for m in ma if m.get("bullish"))
    total_ma = len(ma)

    # Build detailed narrative
    change_dir = "上漲" if change_pct >= 0 else "下跌"
    sign = "+" if change_pct >= 0 else ""
    summary = (
        f"{symbol} 今日{change_dir} {sign}{change_pct:.2f}%，"
        f"當前動能分數 {score:.0f}/100，信號為【{signal}】。"
        f"距 52 週高點 {pct_from_high:.1f}%，"
        f"{bullish_count}/{total_ma} 條均線呈多頭站位。"
    )

    lines = [
        f"**趨勢概覽**：{_ma_text(ma)}",
        f"**RSI 動能**：{_rsi_text(rsi_v)}",
        f"**MACD 訊號**：{_macd_text(macd_h, macd_v, sig_v)}",
        f"**布林通道**：{_bb_text(bb_pct, price, bb_upper, bb_lower)}",
        f"**隨機指標**：{_stoch_text(stoch_k, stoch_d)}",
        f"**ADX 趨勢強度**：{_adx_text(adx_v, di_plus, di_minus)}",
        f"**成交量**：{_vol_text(vol_ratio)}",
    ]

    confluence = []
    if rsi_v > 60 and macd_h > 0 and bullish_count >= 3:
        confluence.append("多指標共振看多")
    elif rsi_v < 40 and macd_h < 0 and bullish_count <= 2:
        confluence.append("多指標共振看空")
    if adx_v > 25 and di_plus > di_minus and rsi_v > 50:
        confluence.append("趨勢動能確認多頭")
    if stoch_k < 20 and rsi_v < 35:
        confluence.append("雙重超賣，反彈機率提升")
    if stoch_k > 80 and rsi_v > 70:
        confluence.append("雙重超買，注意回調風險")
    if vol_ratio >= 1.4 and macd_h > 0:
        confluence.append("量增價升，突破有效性較高")

    conf_text = "；".join(confluence) if confluence else "指標分歧，觀望為主"

    risk_items = []
    if rsi_v > 70:     risk_items.append("RSI 超買")
    if bb_pct > 0.9:   risk_items.append("布林上軌阻力")
    if rsi_v < 30:     risk_items.append("RSI 超賣")
    if macd_h < 0 and adx_v > 25: risk_items.append("空頭趨勢加速")
    risk_text = "，".join(risk_items) if risk_items else "無明顯風險訊號"

    return {
        "summary": summary,
        "analysis": "\n\n".join(lines),
        "confluence": conf_text,
        "risk": risk_text,
        "signal": signal,
        "signal_class": signal_class,
        "score": score,
        "support_resistance": levels,
        "source": "rule-based",
    }


def claude_analysis(payload: dict, api_key: str) -> dict:
    """Calls Claude API for AI judgment. Raises on failure."""
    import anthropic

    ind = payload.get("indicators", {})
    ma = payload.get("ma_analysis", [])
    symbol = payload.get("symbol", "")
    price = payload.get("price", 0)

    ma_str = ", ".join(
        f"{m['label']}={m['value']:.2f}({'+' if m['bullish'] else '-'})"
        for m in ma
    )
    prompt = f"""你是頂尖量化分析師，根據以下 {symbol} 技術指標數據給出**客觀判斷**，不要猜測，只根據數據說話。

股票：{symbol}  當前價：{price}
漲跌幅：{payload.get('change_pct', 0):+.2f}%  動能分數：{payload.get('momentum_score', 50):.0f}/100

技術指標：
- RSI(14): {ind.get('rsi', 'N/A')}
- MACD Line: {ind.get('macd', 'N/A')},  Signal: {ind.get('macd_signal', 'N/A')},  Histogram: {ind.get('macd_hist', 'N/A')}
- Stochastic K/D: {ind.get('stoch_k', 'N/A')}/{ind.get('stoch_d', 'N/A')}
- Bollinger %B: {ind.get('bb_pct_b', 'N/A')}  (上軌:{ind.get('bb_upper', 'N/A')} 下軌:{ind.get('bb_lower', 'N/A')})
- ADX: {ind.get('adx', 'N/A')}  +DI: {ind.get('di_plus', 'N/A')}  -DI: {ind.get('di_minus', 'N/A')}
- 10日ROC: {ind.get('roc_10', 'N/A')}%
- 成交量比率: {payload.get('volume_ratio', 'N/A')}x
- 均線: {ma_str}

請以繁體中文回答，格式如下：
**整體判斷**：（一句話）
**多頭因素**：（條列）
**空頭因素**：（條列）
**關鍵位**：支撐位與壓力位
**操作建議**：（具體策略）"""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    score = payload.get("momentum_score", 50)
    signal, signal_class = _score_to_signal(score)
    return {
        "summary": text,
        "analysis": "",
        "confluence": "",
        "risk": "",
        "signal": signal,
        "signal_class": signal_class,
        "score": score,
        "support_resistance": {},
        "source": "claude-ai",
    }


def analyze(payload: dict) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            return claude_analysis(payload, api_key)
        except Exception as e:
            print(f"Claude API error, falling back: {e}")
    return rule_based_analysis(payload)

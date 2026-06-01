"""
Alert Engine — evaluates Phase 1 / Phase 2 engine outputs and returns Alert objects.
Pure functions only: no I/O, no DB, no side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib

# ── Level ranking (higher = more urgent) ─────────────────────────────────────
LEVEL_RANK: dict[str, int] = {"S": 4, "A": 3, "B": 2, "C": 1}

LEVEL_LABEL: dict[str, str] = {
    "S": "S級（立即處理）",
    "A": "A級（高度注意）",
    "B": "B級（觀察）",
    "C": "C級（每日整理）",
}

LEVEL_EMOJI: dict[str, str] = {"S": "🚨", "A": "⚠️", "B": "🔵", "C": "ℹ️"}


@dataclass
class Alert:
    symbol: str
    alert_type: str   # CHASE_RISK | SELL_SIGNAL | KILL_SIGNAL | CAPITAL_EFF | SECTOR
    level: str        # S | A | B | C
    title: str
    message: str
    suggested_action: str
    detail: dict
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    resolved: bool = False

    @property
    def id(self) -> str:
        raw = f"{self.symbol}:{self.alert_type}:{self.created_at}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def level_rank(self) -> int:
        return LEVEL_RANK.get(self.level, 0)

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "symbol":           self.symbol,
            "alert_type":       self.alert_type,
            "level":            self.level,
            "level_label":      LEVEL_LABEL.get(self.level, self.level),
            "level_emoji":      LEVEL_EMOJI.get(self.level, ""),
            "title":            self.title,
            "message":          self.message,
            "suggested_action": self.suggested_action,
            "created_at":       self.created_at,
            "resolved":         self.resolved,
        }


# ── 1. Chase Risk ─────────────────────────────────────────────────────────────
def evaluate_chase_risk(symbol: str, cr: dict) -> Alert | None:
    """
    Fires when chase_risk score >= 75 (EXTREME).
    Level: A (per spec — Chase Risk EXTREME = A级).
    """
    score     = cr.get("score") or 0
    level_str = cr.get("level", "LOW")
    if level_str != "EXTREME" and score < 75:
        return None

    reasons = cr.get("reasons", [])
    flags   = cr.get("warning_flags", [])
    action  = cr.get("suggested_action") or "等待回測再介入，勿追高"
    label   = cr.get("level_label", level_str)

    lines = [f"追高風險評分：{score}/100【{label}】"]
    if reasons: lines.append(f"主要原因：{reasons[0]}")
    if flags:   lines.append(f"警示標記：{'、'.join(flags)}")
    lines.append(f"建議：{action}")

    return Alert(
        symbol=symbol,
        alert_type="CHASE_RISK",
        level="A",
        title=f"⚡ {symbol} 追高風險 EXTREME（{score}/100）",
        message="\n".join(lines),
        suggested_action=action,
        detail=cr,
    )


# ── 2. Sell Signal ────────────────────────────────────────────────────────────
_SELL_LEVEL: dict[str, str] = {
    "STOP_LOSS": "S",
    "SELL":      "S",
    "TRIM":      "A",
    "ROTATE":    "A",
    "WATCH":     "B",
}


def evaluate_sell_signal(symbol: str, sd: dict) -> Alert | None:
    """
    Fires when sell_decision is TRIM / SELL / STOP_LOSS / ROTATE.
    S级: STOP_LOSS, SELL.  A级: TRIM, ROTATE.  B级: WATCH.
    """
    decision    = sd.get("decision", "NONE")
    alert_level = _SELL_LEVEL.get(decision)
    if not alert_level or decision == "NONE":
        return None

    det     = sd.get("detail", {})
    label   = sd.get("decision_label", decision)
    reasons = sd.get("reasons", [])
    emoji   = LEVEL_EMOJI.get(alert_level, "⚠️")

    lines = [f"賣出決策：【{label}】"]
    if det.get("stop_price"):           lines.append(f"建議停損：{det['stop_price']:.2f}")
    if det.get("trail_price"):          lines.append(f"移動停利：{det['trail_price']:.2f}")
    if det.get("profit_price"):         lines.append(f"獲利目標：{det['profit_price']:.2f}")
    if det.get("pnl_pct") is not None:  lines.append(f"當前損益：{det['pnl_pct']:+.1f}%")
    if det.get("holding_days"):         lines.append(f"持有天數：{det['holding_days']}天")
    if reasons:                         lines.append(f"原因：{reasons[0]}")
    lines.append(f"建議：{sd.get('suggested_action', '')}")

    return Alert(
        symbol=symbol,
        alert_type="SELL_SIGNAL",
        level=alert_level,
        title=f"{emoji} {symbol} 賣出決策：{label}",
        message="\n".join(lines),
        suggested_action=sd.get("suggested_action", ""),
        detail=sd,
    )


# ── 3. Kill Signal ────────────────────────────────────────────────────────────
def _ema_list(arr: list[float], p: int) -> list[float]:
    if not arr:
        return []
    k, res = 2 / (p + 1), [arr[0]]
    for v in arr[1:]:
        res.append(v * k + res[-1] * (1 - k))
    return res


def evaluate_kill_signal(
    symbol: str,
    ohlcv: dict,
    sd: dict | None = None,
) -> Alert | None:
    """
    Detects kill signal conditions directly from OHLCV data.
    Conditions: price < MA20, MACD cross negative, RSI < 50,
    sell_decision STOP_LOSS/SELL, volume-spike red candle.
    Level: S (always — kill signal = immediate action needed).
    """
    closes  = ohlcv.get("closes",  [])
    volumes = ohlcv.get("volumes", [])
    opens   = ohlcv.get("opens",   [])
    n = len(closes)
    if n < 26:
        return None

    triggers: list[str] = []

    # ── MA20 breach ───────────────────────────────────────────────────────────
    ma20 = sum(closes[-20:]) / 20
    if closes[-1] < ma20:
        triggers.append(f"收盤跌破 MA20（{ma20:.2f}）")

    # ── MACD histogram cross negative ────────────────────────────────────────
    ema12 = _ema_list(closes, 12)
    ema26 = _ema_list(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    if len(macd_line) >= 18:
        sig = _ema_list(macd_line[-18:], 9)
        hist = [m - s for m, s in zip(macd_line[-len(sig):], sig)]
        if len(hist) >= 2 and hist[-1] < 0 < hist[-2]:
            triggers.append("MACD 柱狀圖由正轉負（動能反轉確認）")

    # ── RSI < 50 ──────────────────────────────────────────────────────────────
    if n >= 15:
        gains  = [max(0, closes[i] - closes[i-1]) for i in range(1, n)]
        losses = [max(0, closes[i-1] - closes[i]) for i in range(1, n)]
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        rsi = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
        if rsi < 50:
            triggers.append(f"RSI 跌破 50 中性線（目前 {rsi:.1f}）")

    # ── sell_engine STOP_LOSS / SELL ──────────────────────────────────────────
    sd_decision = (sd or {}).get("decision", "NONE")
    if sd_decision in ("STOP_LOSS", "SELL"):
        sd_label = (sd or {}).get("decision_label", sd_decision)
        triggers.append(f"賣出決策觸發【{sd_label}】")

    # ── Volume-spike red candle ───────────────────────────────────────────────
    if n >= 21 and volumes:
        avg_vol = sum(volumes[-21:-1]) / 20
        if (avg_vol > 0
                and volumes[-1] > avg_vol * 1.5
                and opens
                and len(opens) >= n
                and closes[-1] < opens[-1]):
            triggers.append("放量收黑 K 棒（異常量能出貨訊號）")

    if not triggers:
        return None

    kill_sentence = f'若出現「{triggers[0]}」，原本看多判斷失效。'

    return Alert(
        symbol=symbol,
        alert_type="KILL_SIGNAL",
        level="S",
        title=f"🚨 {symbol} Kill Signal 觸發（{len(triggers)} 條件）",
        message=(
            "判斷失效條件觸發：\n"
            + "\n".join(f"• {t}" for t in triggers)
            + f"\n\n{kill_sentence}"
            + "\n建議：立即評估減碼或停損"
        ),
        suggested_action="立即評估減碼或停損",
        detail={"triggers": triggers, "kill_sentence": kill_sentence},
    )


# ── 4. Capital Efficiency ─────────────────────────────────────────────────────
_CE_LEVEL: dict[str, str] = {
    "STOP_LOSS": "S",
    "ROTATE":    "A",
    "TRIM":      "A",
    "WATCH":     "B",
}


def evaluate_capital_efficiency(symbol: str, ce: dict) -> Alert | None:
    """
    Fires when:
    - level in STOP_LOSS / ROTATE / TRIM  (see _CE_LEVEL), OR
    - score < 45 regardless of level label.
    """
    score = ce.get("score")
    if score is None:
        return None

    level       = ce.get("level", "")
    alert_level = _CE_LEVEL.get(level) or ("B" if score < 45 else None)
    if not alert_level:
        return None

    det     = ce.get("detail", {})
    label   = ce.get("level_label", level)
    reasons = ce.get("reasons", [])
    emoji   = LEVEL_EMOJI.get(alert_level, "⚠️")

    lines = [f"資金效率分數：{score}/100【{label}】"]
    if det.get("pnl_pct") is not None:
        lines.append(f"損益：{det['pnl_pct']:+.1f}%")
    if det.get("holding_days"):
        lines.append(f"持有天數：{det['holding_days']}天")
    if det.get("annual_return") is not None:
        lines.append(f"年化報酬：{det['annual_return']:+.1f}%")
    if det.get("benchmark_return") is not None:
        alpha = (det.get("pnl_pct") or 0) - det["benchmark_return"]
        lines.append(f"超額報酬（α）：{alpha:+.1f}%")
    if reasons:
        lines.append(f"主要原因：{reasons[0]}")
    lines.append(f"建議：{ce.get('suggested_action', '')}")

    return Alert(
        symbol=symbol,
        alert_type="CAPITAL_EFF",
        level=alert_level,
        title=f"{emoji} {symbol} 資金效率：{label}（{score}/100）",
        message="\n".join(lines),
        suggested_action=ce.get("suggested_action", ""),
        detail=ce,
    )


# ── 5. Sector Leadership ──────────────────────────────────────────────────────
def evaluate_sector_leadership(
    sector_name: str,
    sl: dict,
    affected_symbols: list[str],
    prev_level: str | None = None,
) -> Alert | None:
    """
    Fires when sector level is LAGGING or WEAKENING (or transitions there).
    Level: A (per spec — "Sector 從 LEADING 轉弱" = A级).
    """
    if not sl or not sl.get("ok"):
        return None

    level = sl.get("level", "NEUTRAL")
    score = sl.get("score", 50)

    if level not in ("LAGGING", "WEAKENING"):
        return None

    label       = sl.get("level_label", level)
    action      = sl.get("suggested_action", "")
    flags       = sl.get("warning_flags", [])
    affected    = "、".join(affected_symbols[:5]) if affected_symbols else "—"
    transition  = (
        f"（由 {prev_level} 轉為 {level}）"
        if prev_level and prev_level != level
        else ""
    )

    lines = [
        f"板塊強度：{sector_name} 【{label}】{transition}（評分 {score}/100）",
        f"受影響持股：{affected}",
    ]
    if flags:
        lines.append(f"警示：{'、'.join(flags)}")
    lines.append(f"建議：{action}")

    return Alert(
        symbol=sector_name,
        alert_type="SECTOR",
        level="A",
        title=f"⚠️ 板塊轉弱：{sector_name}【{label}】{transition}",
        message="\n".join(lines),
        suggested_action=action,
        detail={**sl, "affected_symbols": affected_symbols, "prev_level": prev_level},
    )

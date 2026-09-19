"""High-quality AI / semiconductor breakout watch.

This module is intentionally read-only.  It never submits orders.  It combines
completed daily OHLCV, relative strength, market regime, and grounded news /
filing catalysts from ``market_intelligence``.  A notification is produced only
when at least one candidate clears the strict quality gate.
"""
from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timezone

import data_provider
import data_quality_engine
import institutional_guard
import relative_strength_score
from market_intelligence.config import IntelligenceConfig
from market_intelligence.delivery import email_ready, line_ready, send_payload
from market_intelligence.service import run_intelligence
from market_intelligence.user_context import extract_user_context, load_kv
from scott_evolution import notifications


AI_SEMI_UNIVERSE = [
    "AAOI", "LITE", "COHR", "CRDO", "FN", "VIAV", "ANET", "MRVL", "DELL",
    "NVDA", "AMD", "AVGO", "MU", "ARM", "SMCI", "AMAT", "KLAC", "LRCX",
    "QCOM", "ON",
]

_EARNINGS_WORDS = (
    "earnings", "guidance", "revenue", "eps", "results", "10-q", "8-k",
    "財報", "財測", "營收", "獲利", "每股盈餘",
)


def _safe_mean(values: list[float]) -> float:
    values = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return 0.0
    return _safe_mean(values[-period:])


def _obv(closes: list[float], volumes: list[float]) -> list[float]:
    if not closes:
        return []
    out = [0.0]
    for i in range(1, min(len(closes), len(volumes))):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])
    return out


def _aligned_bars(ohlcv: dict) -> tuple[list[float], ...] | None:
    """Validate OHLCV without silently shifting malformed rows.

    Dropping an invalid close while keeping the corresponding high, low or
    volume would manufacture a price pattern that never existed.  A research
    scanner must fail closed instead.
    """
    keys = ("closes", "opens", "highs", "lows", "volumes")
    raw = [ohlcv.get(key) or [] for key in keys]
    lengths = {len(values) for values in raw}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) < 65:
        return None
    parsed: list[list[float]] = [[] for _ in keys]
    try:
        for row in zip(*raw):
            close, open_, high, low, volume = (float(value) for value in row)
            if not all(math.isfinite(value) for value in (close, open_, high, low, volume)):
                return None
            if min(close, open_, high, low) <= 0 or volume < 0:
                return None
            if high < max(open_, close) or low > min(open_, close) or high < low:
                return None
            for values, value in zip(parsed, (close, open_, high, low, volume)):
                values.append(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return tuple(parsed)


def _event_map(report: dict) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    catalysts = {
        str(item.get("symbol") or "").upper(): item
        for item in (report.get("watchlist_impacts") or [])
        if item.get("symbol")
    }
    events: dict[str, list[dict]] = {}
    for item in report.get("top_events") or []:
        for symbol in item.get("affected_symbols") or []:
            symbol = str(symbol or "").upper().strip()
            if symbol:
                events.setdefault(symbol, []).append(item)
    return catalysts, events


def _earnings_context(events: list[dict]) -> tuple[float, str]:
    """Return a bounded 0-5 earnings/filing bonus and a grounded reason."""
    for event in events:
        text = " ".join(
            str(event.get(key) or "")
            for key in ("title", "original_title", "summary", "fact")
        ).lower()
        if not any(word in text for word in _EARNINGS_WORDS):
            continue
        importance = float(event.get("importance") or 0)
        confidence = float(event.get("confidence") or 0)
        direction = str(event.get("direction") or "NEUTRAL").upper()
        if confidence < 55 or importance < 55:
            continue
        if direction == "BULLISH":
            return 5.0, str(event.get("title") or event.get("original_title") or "正面財報/財測事件")[:120]
        if direction == "BEARISH":
            return -8.0, str(event.get("title") or event.get("original_title") or "負面財報/財測事件")[:120]
        return 2.0, str(event.get("title") or event.get("original_title") or "近期財報/公告事件")[:120]
    return 0.0, ""


def evaluate_candidate(
    symbol: str,
    ohlcv: dict,
    *,
    bench_ohlcv: dict | None,
    market: dict,
    catalyst: dict | None = None,
    events: list[dict] | None = None,
    min_score: float = 82.0,
    now: datetime | None = None,
) -> dict | None:
    """Evaluate one symbol. Returns a candidate only after strict hard gates."""
    bars = _aligned_bars(ohlcv)
    if bars is None or ohlcv.get("is_demo"):
        return None
    closes, opens, highs, lows, volumes = bars
    n = len(closes)

    quality = data_quality_engine.run_data_quality(ohlcv)
    readiness = institutional_guard.assess_signal_readiness(
        symbol,
        ohlcv=ohlcv,
        data_quality=quality,
        quote=None,
        now=now,
    )
    if readiness.get("status") == "BLOCKED":
        return None

    regime = str((market or {}).get("regime") or "sideways")
    if market.get("is_demo") or regime in {"bear", "risk_on"}:
        return None

    price = closes[-1]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    if not (price > ma20 > ma50 > 0):
        return None

    pivot = max(highs[-21:-1])
    distance_pct = (pivot - price) / pivot * 100 if pivot else 999.0
    # "Ready" means sitting just under resistance or only marginally through it.
    if distance_pct < -1.5 or distance_pct > 4.0:
        return None

    gain5 = (price / closes[-6] - 1) * 100 if closes[-6] else 0.0
    if gain5 > 10.0:
        return None
    extension20 = (price / ma20 - 1) * 100 if ma20 else 0.0
    if extension20 > 8.0:
        return None

    vol20 = _safe_mean(volumes[-20:])
    vol5 = _safe_mean(volumes[-5:])
    vol_ratio = volumes[-1] / vol20 if vol20 > 0 else 0.0
    obv = _obv(closes, volumes)
    obv_up = len(obv) >= 11 and obv[-1] > obv[-11]

    recent_ranges = [
        (highs[i] - lows[i]) / closes[i] * 100
        for i in range(n - 10, n)
        if closes[i] > 0
    ]
    prior_ranges = [
        (highs[i] - lows[i]) / closes[i] * 100
        for i in range(max(0, n - 30), n - 10)
        if closes[i] > 0
    ]
    squeeze = bool(
        recent_ranges and prior_ranges
        and _safe_mean(recent_ranges) <= _safe_mean(prior_ranges) * 0.88
    )

    rs = relative_strength_score.compute(ohlcv, bench_ohlcv)
    rs_score = float(rs.get("score") or 0)
    if rs_score < 65:
        return None

    catalyst = catalyst or {}
    direction = str(catalyst.get("direction") or "NEUTRAL").upper()
    importance = float(catalyst.get("importance") or 0)
    confidence = float(catalyst.get("confidence") or 0)
    adjustment = float(catalyst.get("score_adjustment") or 0)
    if direction == "BEARISH" and importance >= 70 and confidence >= 60:
        return None

    # Technical block: 0-55.
    technical = 0.0
    technical += 15.0 if abs(distance_pct) <= 1.0 else 12.0 if distance_pct <= 2.0 else 8.0
    technical += 10.0  # MA20 > MA50 and price > both already hard-gated
    technical += 10.0 if squeeze else 5.0
    technical += 10.0 if obv_up else 3.0
    if 0.8 <= vol_ratio <= 1.8:
        technical += 7.0
    elif vol5 >= vol20 * 0.85:
        technical += 4.0
    technical += 3.0 if gain5 >= 0 else 0.0

    rs_component = _clamp((rs_score - 50.0) / 50.0 * 20.0, 0.0, 20.0)

    # Catalyst block: 0-15. Neutral/no-news does not receive a free pass.
    catalyst_component = 3.0
    if direction == "BULLISH" and importance >= 60 and confidence >= 55:
        catalyst_component = _clamp(7.0 + max(0.0, adjustment) + importance / 25.0, 0.0, 15.0)
    elif direction == "BEARISH":
        catalyst_component = 0.0
    elif importance >= 60 and confidence >= 55:
        catalyst_component = 5.0

    market_component = 10.0 if regime == "bull" else 6.0
    earnings_bonus, earnings_reason = _earnings_context(events or [])
    if earnings_bonus < 0:
        return None

    score = round(_clamp(technical + rs_component + catalyst_component + market_component + earnings_bonus), 1)
    if score < min_score or technical < 42.0:
        return None

    swing_low10 = min(lows[-10:])
    invalid_price = max(swing_low10, ma20 * 0.985)
    entry_price = pivot * 1.003
    risk_pct = (entry_price - invalid_price) / entry_price * 100 if entry_price > 0 else 99.0
    if risk_pct <= 0 or risk_pct > 9.0:
        return None

    reasons = [
        f"距 20 日關鍵壓力 {distance_pct:+.1f}%",
        f"趨勢結構：股價 > MA20 > MA50",
        f"相對強度 {rs_score:.0f}/100",
    ]
    if squeeze:
        reasons.append("近 10 日波動收斂，具蓄勢特徵")
    if obv_up:
        reasons.append("OBV 10 日走高，量價偏累積")
    if direction == "BULLISH" and catalyst.get("summary"):
        reasons.append(f"正面催化：{str(catalyst['summary'])[:100]}")
    if earnings_reason:
        reasons.append(f"財報/公告：{earnings_reason}")

    risks = []
    if regime == "sideways":
        risks.append("大盤僅中性，突破成功率需打折")
    if vol_ratio < 1.0:
        risks.append("當日量尚未明顯放大，必須等突破量確認")
    if not catalyst:
        risks.append("缺少可驗證的個股催化，訊號主要由量價與相對強度驅動")
    if distance_pct < 0:
        risks.append("已略越過壓力，避免追價，等量能與收盤確認")

    return {
        "symbol": symbol,
        "score": score,
        "price": round(price, 2),
        "pivot": round(pivot, 2),
        "distance_to_pivot_pct": round(distance_pct, 2),
        "entry_condition": (
            f"收盤站上 ${entry_price:.2f}，且成交量至少達 20 日均量 1.3×"
        ),
        "entry_price": round(entry_price, 2),
        "volume_confirmation": round(vol20 * 1.3),
        "invalid_condition": (
            f"收盤跌破 ${invalid_price:.2f}（MA20/近10日結構失守）則訊號失效"
        ),
        "invalid_price": round(invalid_price, 2),
        "risk_pct": round(risk_pct, 1),
        "market_regime": regime,
        "relative_strength": round(rs_score, 1),
        "technical_score": round(technical, 1),
        "catalyst_direction": direction,
        "catalyst_importance": round(importance, 1),
        "catalyst_confidence": round(confidence, 1),
        "reasons": reasons[:6],
        "risks": risks[:4],
        "data_source": ohlcv.get("source"),
        "data_freshness": {
            "last_completed_bar": readiness.get("last_completed_bar"),
            "expected_completed_bar": readiness.get("expected_completed_bar"),
            "missing_trading_sessions": readiness.get("missing_trading_sessions"),
        },
    }


def _build_text(result: dict) -> str:
    candidates = result.get("candidates") or []
    lines = [
        "🚀 AI／半導體高品質突破候選",
        f"市場環境：{(result.get('market') or {}).get('overall', '未知')}",
        "",
    ]
    for item in candidates[:5]:
        lines.extend([
            f"{item['symbol']}｜{item['score']}/100｜現價 ${item['price']:.2f}",
            "原因：" + "；".join(item.get("reasons") or []),
            "進場：" + item["entry_condition"],
            "失效：" + item["invalid_condition"],
            f"初始技術風險：約 {item['risk_pct']:.1f}%",
            "風險：" + ("；".join(item.get("risks") or []) or "無額外警示"),
            "",
        ])
    lines.append("僅為突破監控訊號，不會自動下單。")
    return "\n".join(lines)


def run_breakout_watch(
    *,
    config: IntelligenceConfig | None = None,
    symbols: list[str] | None = None,
    min_score: float | None = None,
    intelligence_report: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Run one daily breakout scan. No candidate means no notification."""
    config = config or IntelligenceConfig.from_env()
    universe = list(dict.fromkeys((symbols or AI_SEMI_UNIVERSE)))[: config.max_symbols]
    try:
        threshold = float(
            min_score if min_score is not None
            else os.environ.get("BREAKOUT_MIN_SCORE", "82")
        )
    except (TypeError, ValueError):
        threshold = 82.0
    threshold = _clamp(threshold, 70.0, 95.0)

    intelligence = intelligence_report
    if not isinstance(intelligence, dict):
        intelligence = run_intelligence(
            "manual",
            user_data={"radarWatchlist": universe},
            dispatch=False,
            force=True,
            config=config,
        )
    catalysts, events = _event_map(intelligence if isinstance(intelligence, dict) else {})

    fetch_symbols = list(dict.fromkeys([*universe, "QQQ", "SPY"]))
    market_data = data_provider.get_ohlcv_multi(fetch_symbols, period="1y")
    bench = market_data.get("QQQ")
    market = data_provider.market_state(lambda sym: market_data.get(sym) or data_provider.get_ohlcv(sym))

    candidates = []
    for symbol in universe:
        ohlcv = market_data.get(symbol)
        if not ohlcv:
            continue
        candidate = evaluate_candidate(
            symbol,
            ohlcv,
            bench_ohlcv=bench,
            market=market,
            catalyst=catalysts.get(symbol),
            events=events.get(symbol) or [],
            min_score=threshold,
            now=now,
        )
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item["score"], item["relative_strength"]), reverse=True)

    return {
        "ok": True,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "universe": universe,
        "min_score": threshold,
        "market": market,
        "candidates": candidates,
        "notification_text": _build_text({"candidates": candidates, "market": market}) if candidates else "",
        "intelligence_status": (intelligence.get("_run") or {}).get("status") if isinstance(intelligence, dict) else None,
    }


def dispatch_breakout_watch(result: dict, *, config: IntelligenceConfig | None = None) -> dict:
    """Queue idempotent notifications and deliver them through the durable outbox."""
    candidates = result.get("candidates") or []
    if not candidates:
        return {"sent": [], "skipped": "NO_HIGH_QUALITY_SIGNAL"}
    config = config or IntelligenceConfig.from_env()
    text = str(result.get("notification_text") or _build_text(result))[:5000]
    signature = "|".join(
        f"{item.get('symbol')}:{item.get('entry_price')}"
        for item in candidates[:5]
    )
    digest = hashlib.sha256(signature.encode()).hexdigest()[:16]
    event_day = str(result.get("generated_at") or datetime.now(timezone.utc).isoformat())[:10]
    event_key = f"breakout:{event_day}:{digest}"
    queued = []
    skipped = []

    row = notifications.enqueue(
        event_key,
        "app",
        {"text": text},
        db_path=config.db_path,
    )
    queued.append({"channel": "app", "deduplicated": row["deduplicated"]})

    if config.dispatch_line:
        if line_ready():
            row = notifications.enqueue(
                event_key,
                "line",
                {"text": text},
                db_path=config.db_path,
            )
            queued.append({"channel": "line", "deduplicated": row["deduplicated"]})
        else:
            skipped.append({"channel": "line", "reason": "LINE 尚未設定"})

    context = extract_user_context(load_kv(config.db_path), max_symbols=config.max_symbols)
    recipient = str(context.get("email") or "").strip()
    if config.dispatch_email:
        if email_ready(recipient):
            row = notifications.enqueue(
                event_key,
                "email",
                {
                "to": recipient,
                "subject": "RocketStock｜AI/半導體高品質突破候選",
                "body": text,
                },
                db_path=config.db_path,
            )
            queued.append({"channel": "email", "deduplicated": row["deduplicated"]})
        else:
            skipped.append({"channel": "email", "reason": "SMTP 或收件人尚未設定"})

    delivery = notifications.deliver_due(
        send_payload,
        limit=20,
        db_path=config.db_path,
    )
    return {
        "event_key": event_key,
        "queued": queued,
        "skipped": skipped,
        "delivery": delivery,
        "sent": [item["channel"] for item in queued],
    }

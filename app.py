from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import traceback
import demo_data as _demo

app = Flask(__name__)


# ── Technical Indicator Calculations ──────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k=14, d=3):
    lowest_low = low.rolling(k).min()
    highest_high = high.rolling(k).max()
    k_pct = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d_pct = k_pct.rolling(d).mean()
    return k_pct, d_pct


def bollinger(series: pd.Series, period=20, std_dev=2):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    pct_b = (series - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return upper, mid, lower, pct_b, bandwidth


def rate_of_change(series: pd.Series, period: int = 10) -> pd.Series:
    return (series / series.shift(period) - 1) * 100


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    dm_plus = (high.diff()).clip(lower=0)
    dm_minus = (-low.diff()).clip(lower=0)
    dm_plus = dm_plus.where(dm_plus > dm_minus, 0)
    dm_minus = dm_minus.where(dm_minus > dm_plus, 0)
    atr = tr.ewm(com=period - 1, min_periods=period).mean()
    di_plus = 100 * dm_plus.ewm(com=period - 1, min_periods=period).mean() / atr.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(com=period - 1, min_periods=period).mean() / atr.replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx_val = dx.ewm(com=period - 1, min_periods=period).mean()
    return adx_val, di_plus, di_minus


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    avg_vol = volume.rolling(period).mean()
    return volume / avg_vol.replace(0, np.nan)


def momentum_score(rsi_v, macd_h, stoch_k, roc_v, bb_pct, adx_v, di_plus, di_minus):
    """Composite 0-100 momentum score."""
    scores = []

    # RSI contribution (oversold <30 bearish, overbought >70 bullish)
    if not np.isnan(rsi_v):
        scores.append(min(max(rsi_v, 0), 100))

    # MACD histogram: positive → bullish
    if not np.isnan(macd_h):
        scores.append(60 if macd_h > 0 else 40)

    # Stochastic
    if not np.isnan(stoch_k):
        scores.append(min(max(stoch_k, 0), 100))

    # ROC: positive → bullish, normalise to 0-100
    if not np.isnan(roc_v):
        scores.append(min(max(50 + roc_v * 2, 0), 100))

    # Bollinger %B
    if not np.isnan(bb_pct):
        scores.append(min(max(bb_pct * 100, 0), 100))

    # ADX directional bias
    if not (np.isnan(adx_v) or np.isnan(di_plus) or np.isnan(di_minus)):
        strength = min(adx_v / 50, 1)
        direction = 60 if di_plus > di_minus else 40
        scores.append(50 + (direction - 50) * strength)

    return round(float(np.mean(scores)), 1) if scores else 50.0


def signal_label(score: float) -> tuple[str, str]:
    if score >= 70:
        return "強力買入", "bullish"
    elif score >= 58:
        return "溫和買入", "mild-bullish"
    elif score >= 42:
        return "中性觀望", "neutral"
    elif score >= 30:
        return "溫和賣出", "mild-bearish"
    else:
        return "強力賣出", "bearish"


# ── Main Data Fetch ────────────────────────────────────────────────────────────

def _live_history(symbol: str):
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="1y", interval="1d", auto_adjust=True)
    if hist.empty or len(hist) < 60:
        return None, None, True
    return ticker, hist, False


def fetch_momentum(symbol: str) -> dict:
    is_demo = False
    name_override = None
    try:
        ticker, hist, failed = _live_history(symbol)
        if failed:
            raise ValueError("live fetch empty")
    except Exception:
        hist = _demo.generate(symbol)
        name_override = _demo.name(symbol)
        ticker = None
        is_demo = True

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]
    current_price = float(close.iloc[-1])

    # Moving averages
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    # Indicators
    rsi_series = rsi(close)
    macd_line, signal_line, histogram = macd(close)
    stoch_k, stoch_d = stochastic(high, low, close)
    bb_upper, bb_mid, bb_lower, bb_pct_b, bb_bw = bollinger(close)
    roc_series = rate_of_change(close, 10)
    adx_series, di_plus_s, di_minus_s = adx(high, low, close)
    vol_ratio_s = volume_ratio(volume)

    def last(s):
        v = s.iloc[-1]
        return float(v) if not pd.isna(v) else None

    rsi_val = last(rsi_series)
    macd_val = last(macd_line)
    signal_val = last(signal_line)
    hist_val = last(histogram)
    stoch_k_val = last(stoch_k)
    stoch_d_val = last(stoch_d)
    bb_upper_val = last(bb_upper)
    bb_lower_val = last(bb_lower)
    bb_mid_val = last(bb_mid)
    bb_pct_val = last(bb_pct_b)
    roc_val = last(roc_series)
    adx_val = last(adx_series)
    di_plus_val = last(di_plus_s)
    di_minus_val = last(di_minus_s)
    vol_ratio_val = last(vol_ratio_s)

    score = momentum_score(
        rsi_val or 50, hist_val or 0, stoch_k_val or 50,
        roc_val or 0, bb_pct_val or 0.5, adx_val or 0,
        di_plus_val or 0, di_minus_val or 0
    )
    signal, signal_class = signal_label(score)

    # 52-week stats
    high_52w = float(high.rolling(252).max().iloc[-1])
    low_52w = float(low.rolling(252).min().iloc[-1])
    pct_from_high = (current_price / high_52w - 1) * 100
    pct_from_low = (current_price / low_52w - 1) * 100

    # Price change
    prev_close = float(close.iloc[-2])
    change_pct = (current_price / prev_close - 1) * 100
    change_abs = current_price - prev_close

    # Chart series (last 60 days)
    n = 60
    dates = hist.index[-n:].strftime("%m/%d").tolist()
    prices = close.iloc[-n:].round(2).tolist()
    volumes = volume.iloc[-n:].tolist()
    sma20_data = sma20.iloc[-n:].round(2).tolist()
    sma50_data = sma50.iloc[-n:].round(2).tolist()
    rsi_data = rsi_series.iloc[-n:].round(2).tolist()
    macd_data = macd_line.iloc[-n:].round(4).tolist()
    signal_data = signal_line.iloc[-n:].round(4).tolist()
    hist_data = histogram.iloc[-n:].round(4).tolist()

    # MA signals
    ma_signals = []
    sma20_v = last(sma20)
    sma50_v = last(sma50)
    sma200_v = last(sma200)
    ema12_v = last(ema12)
    ema26_v = last(ema26)

    def ma_sig(label, ma_val):
        if ma_val is None:
            return None
        bull = current_price > ma_val
        pct = (current_price / ma_val - 1) * 100
        return {"label": label, "value": round(ma_val, 2), "pct": round(pct, 2), "bullish": bull}

    for s in filter(None, [
        ma_sig("SMA 20", sma20_v),
        ma_sig("SMA 50", sma50_v),
        ma_sig("SMA 200", sma200_v),
        ma_sig("EMA 12", ema12_v),
        ma_sig("EMA 26", ema26_v),
    ]):
        ma_signals.append(s)

    bullish_ma = sum(1 for m in ma_signals if m["bullish"])
    total_ma = len(ma_signals)

    # Info
    info = {}
    if name_override:
        info["name"] = name_override
    elif ticker is not None:
        try:
            raw = ticker.fast_info
            info["name"] = getattr(raw, "last_price", None) and symbol
        except Exception:
            pass
        try:
            info["name"] = ticker.info.get("longName") or ticker.info.get("shortName") or symbol
        except Exception:
            info["name"] = symbol
    else:
        info["name"] = symbol

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return {
        "symbol": symbol.upper(),
        "name": info.get("name", symbol),
        "price": round(current_price, 2),
        "change_abs": round(change_abs, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(volume.iloc[-1]),
        "volume_ratio": round(vol_ratio_val, 2) if vol_ratio_val else None,
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_from_high": round(pct_from_high, 2),
        "pct_from_low": round(pct_from_low, 2),
        "momentum_score": score,
        "signal": signal,
        "signal_class": signal_class,
        "indicators": {
            "rsi": round(rsi_val, 2) if rsi_val else None,
            "macd": round(macd_val, 4) if macd_val else None,
            "macd_signal": round(signal_val, 4) if signal_val else None,
            "macd_hist": round(hist_val, 4) if hist_val else None,
            "stoch_k": round(stoch_k_val, 2) if stoch_k_val else None,
            "stoch_d": round(stoch_d_val, 2) if stoch_d_val else None,
            "bb_upper": round(bb_upper_val, 2) if bb_upper_val else None,
            "bb_mid": round(bb_mid_val, 2) if bb_mid_val else None,
            "bb_lower": round(bb_lower_val, 2) if bb_lower_val else None,
            "bb_pct_b": round(bb_pct_val, 3) if bb_pct_val else None,
            "roc_10": round(roc_val, 2) if roc_val else None,
            "adx": round(adx_val, 2) if adx_val else None,
            "di_plus": round(di_plus_val, 2) if di_plus_val else None,
            "di_minus": round(di_minus_val, 2) if di_minus_val else None,
        },
        "ma_signals": ma_signals,
        "bullish_ma": bullish_ma,
        "total_ma": total_ma,
        "chart": {
            "dates": dates,
            "prices": prices,
            "volumes": volumes,
            "sma20": sma20_data,
            "sma50": sma50_data,
            "rsi": rsi_data,
            "macd": macd_data,
            "macd_signal": signal_data,
            "macd_hist": hist_data,
        },
        "updated_at": now_utc,
        "is_demo": is_demo,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/momentum/<symbol>")
def api_momentum(symbol):
    symbol = symbol.strip().upper()
    try:
        data = fetch_momentum(symbol)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/compare")
def api_compare():
    raw = request.args.get("symbols", "")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    results = []
    errors = []
    for sym in symbols[:8]:
        try:
            d = fetch_momentum(sym)
            results.append({
                "symbol": d["symbol"],
                "name": d["name"],
                "price": d["price"],
                "change_pct": d["change_pct"],
                "momentum_score": d["momentum_score"],
                "signal": d["signal"],
                "signal_class": d["signal_class"],
                "rsi": d["indicators"]["rsi"],
                "macd_hist": d["indicators"]["macd_hist"],
                "volume_ratio": d["volume_ratio"],
            })
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})
    results.sort(key=lambda x: x["momentum_score"], reverse=True)
    return jsonify({"ok": True, "results": results, "errors": errors})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

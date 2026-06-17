"""股票工具：把上層 Scott 投資分析系統的功能接給 CLI Agent。

對外提供的工具：
  - get_stock_price：查某檔股票最新價與區間高低
  - get_fundamentals：查基本面（市值、本益比、EPS、營收、利潤率、估值）
  - get_market_state：研判大盤多空
  - analyze_signals：技術指標訊號與關鍵價位
  - compare_stocks / scan_stocks：多檔比較與掃描
  - montecarlo_forecast：蒙地卡羅模擬未來價格的機率區間
  - backtest_strategy：對某檔股票跑策略回測

這些功能需要上層專案的相依套件（pandas、numpy 等）。若未安裝，
工具會回傳清楚的提示，而不會讓整個 Agent 崩潰。
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

# 讓本模組能 import 上層 Scott repo 的模組（data_provider、backtest、demo_data）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_IMPORT_HINT = (
    "錯誤：股票功能需要上層專案的套件（pandas、numpy 等）尚未安裝。\n"
    "請在 cli_agent 目錄執行：pip3 install -r ../requirements.txt"
)

# backtest.run 支援的策略名稱。
_STRATEGIES = [
    "rsi",
    "macd",
    "ma_cross",
    "bollinger",
    "combined",
    "decision_core",
    "decision_core_v2",
    "decision_core_v3",
]


def _load():
    """延遲載入股票模組；缺套件時回傳 None。"""
    try:
        import backtest
        import data_provider
        import demo_data

        return data_provider, backtest, demo_data
    except ImportError:
        return None


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt_date(ts) -> str:
    """把各種時間戳格式轉成 YYYY-MM-DD 字串（僅作標籤用）。"""
    try:
        if isinstance(ts, (int, float)):
            return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        pass
    return str(ts)[:10]


def _build_ohlcv(symbol: str, period: str, dp, demo) -> list[dict]:
    """組出 backtest.run 需要的 OHLCV 串列：先試真實/降級資料，再退回示範資料。"""
    try:
        d = dp.get_ohlcv(symbol.upper(), period)
    except Exception:  # noqa: BLE001 — 任何取數失敗都退回示範資料
        d = None

    if d and d.get("closes"):
        closes = d["closes"]
        opens = d.get("opens", closes)
        highs = d.get("highs", closes)
        lows = d.get("lows", closes)
        volumes = d.get("volumes", [])
        timestamps = d.get("timestamps", [])
        rows = []
        for i in range(len(closes)):
            rows.append(
                {
                    "date": _fmt_date(timestamps[i]) if i < len(timestamps) else str(i),
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                    "volume": _safe_int(volumes[i]) if i < len(volumes) else 0,
                }
            )
        return rows

    # 退而求其次：本機示範資料（無需網路）。
    try:
        hist = demo.generate(symbol.upper())
        return [
            {
                "date": row.Index.strftime("%Y-%m-%d"),
                "open": float(row.Open),
                "high": float(row.High),
                "low": float(row.Low),
                "close": float(row.Close),
                "volume": int(row.Volume),
            }
            for row in hist.itertuples()
        ]
    except Exception:  # noqa: BLE001
        return []


def _fetch_series(symbol: str, period: str, dp, demo) -> dict | None:
    """取得收盤/高/低/量序列：先試真實/降級資料,再退回示範資料。"""
    try:
        d = dp.get_ohlcv(symbol.upper(), period)
    except Exception:  # noqa: BLE001
        d = None
    if d and d.get("closes"):
        return d
    try:
        hist = demo.generate(symbol.upper())
        return {
            "closes": [float(x) for x in hist["Close"].tolist()],
            "highs": [float(x) for x in hist["High"].tolist()],
            "lows": [float(x) for x in hist["Low"].tolist()],
            "volumes": [int(x) for x in hist["Volume"].tolist()],
            "is_demo": True,
            "source": "demo",
        }
    except Exception:  # noqa: BLE001
        return None


def _last(arr) -> float | None:
    """回傳序列中最後一個非 None 的值（指標前段常為 None）。"""
    for v in reversed(arr):
        if v is not None:
            return v
    return None


def get_stock_price(symbol: str) -> str:
    """查某檔股票最新收盤價、漲跌幅與區間高低。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, _ = mods

    try:
        d = dp.get_ohlcv(symbol.upper())
    except Exception as e:  # noqa: BLE001
        return f"錯誤：取得 {symbol} 資料失敗：{e}"

    if not d or not d.get("closes"):
        return f"錯誤：找不到 {symbol} 的資料。"

    closes = d["closes"]
    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else last
    change = (last / prev - 1) * 100 if prev else 0.0
    hi = max(d.get("highs", closes))
    lo = min(d.get("lows", closes))
    demo = "（示範資料，非即時）" if d.get("is_demo") else ""
    return (
        f"{symbol.upper()} 最新收盤：{last:.2f}（較前一日 {change:+.2f}%）\n"
        f"區間高/低：{hi:.2f} / {lo:.2f}　資料來源：{d.get('source', '?')} {demo}".rstrip()
    )


def get_market_state() -> str:
    """研判目前大盤多空（依 SPY / QQQ）。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, _ = mods

    try:
        ms = dp.market_state()
    except Exception as e:  # noqa: BLE001
        return f"錯誤：取得大盤狀態失敗：{e}"

    demo = "（示範資料）" if ms.get("is_demo") else ""
    return f"大盤研判：{ms.get('overall', '?')}（regime: {ms.get('regime', '?')}）{demo}".rstrip()


def backtest_strategy(
    symbol: str, strategy: str = "decision_core", period: str = "1y"
) -> str:
    """對某檔股票跑策略回測，回傳關鍵績效指標。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods

    if strategy not in _STRATEGIES:
        return f"錯誤：未知策略 '{strategy}'。可用：{', '.join(_STRATEGIES)}"

    ohlcv = _build_ohlcv(symbol, period, dp, demo)
    if not ohlcv:
        return f"錯誤：無法取得 {symbol} 的歷史資料。"

    try:
        r = bt.run(ohlcv, strategy, {})
    except Exception as e:  # noqa: BLE001
        return f"錯誤：回測失敗：{e}"

    return (
        f"{symbol.upper()} ｜ 策略：{strategy} ｜ {len(ohlcv)} 根K棒\n"
        f"總報酬：{r.get('total_return', 0):.2f}%　年化：{r.get('annual_return', 0):.2f}%\n"
        f"勝率：{r.get('win_rate', 0):.1f}%　交易次數：{r.get('num_trades', 0)}\n"
        f"獲利因子：{r.get('profit_factor', 0):.2f}　夏普：{r.get('sharpe', 0)}"
        f"　最大回檔：{r.get('max_drawdown', 0):.2f}%\n"
        f"買進持有對照：{r.get('bh_return', 0):.2f}%"
    )


def _signal_for(symbol: str, period: str, dp, bt, demo, signals) -> dict:
    """計算單一股票的指標與訊號,回傳結構化 dict(失敗時帶 error)。"""
    s = _fetch_series(symbol, period, dp, demo)
    if not s or not s.get("closes"):
        return {"symbol": symbol.upper(), "error": "無法取得資料"}

    closes = s["closes"]
    if len(closes) < 60:
        return {"symbol": symbol.upper(), "error": f"資料太少（{len(closes)} 筆）"}

    rsi = bt._rsi(closes)
    macd_line, macd_sig, macd_hist = bt._macd(closes)
    bb_upper, bb_mid, bb_lower, bb_pct = bt._bb(closes)
    price = closes[-1]
    prev = closes[-2] if len(closes) > 1 else price
    change = (price / prev - 1) * 100 if prev else 0.0

    ma_analysis = []
    for label, period_n in (("MA20", 20), ("MA60", 60), ("MA200", 200)):
        v = _last(bt._sma(closes, period_n))
        if v is not None:
            ma_analysis.append({"label": label, "value": v})

    vols = s.get("volumes") or []
    vol_ratio = 1.0
    if len(vols) >= 21:
        avg = sum(vols[-21:-1]) / 20
        vol_ratio = vols[-1] / avg if avg else 1.0

    payload = {
        "symbol": symbol.upper(),
        "price": price,
        "volume_ratio": vol_ratio,
        "indicators": {
            "rsi": _last(rsi),
            "macd": _last(macd_line),
            "macd_signal": _last(macd_sig),
            "macd_hist": _last(macd_hist),
            "bb_upper": _last(bb_upper),
            "bb_lower": _last(bb_lower),
            "bb_mid": _last(bb_mid),
            "bb_pct_b": _last(bb_pct),
        },
        "ma_analysis": ma_analysis,
    }

    try:
        r = signals.detect(payload)
    except Exception as e:  # noqa: BLE001
        return {"symbol": symbol.upper(), "error": f"訊號計算失敗：{e}"}

    return {
        "symbol": symbol.upper(),
        "price": price,
        "change": change,
        "rsi": _last(rsi),
        "macd_hist": _last(macd_hist),
        "detect": r,
        "is_demo": bool(s.get("is_demo")),
    }


def _normalize_symbols(symbols, limit: int = 10) -> list[str]:
    """把代號參數正規化成字串陣列(容忍逗號字串),並去重、限量。"""
    if isinstance(symbols, str):
        symbols = symbols.replace(",", " ").split()
    out, seen = [], set()
    for s in symbols or []:
        sym = str(s).strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out[:limit]


def analyze_signals(symbol: str, period: str = "6mo") -> str:
    """計算技術指標(RSI/MACD/布林/均線)並研判進出場訊號。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods
    try:
        import signals
    except ImportError:
        return _IMPORT_HINT

    info = _signal_for(symbol, period, dp, bt, demo, signals)
    if info.get("error"):
        return f"錯誤：{symbol.upper()} {info['error']}。"

    r = info["detect"]
    lv = r.get("levels", {})
    demo_note = "（示範資料，非即時）" if info["is_demo"] else ""
    lines = [
        f"{info['symbol']} ｜ 技術訊號：{r.get('signal', '?')}"
        f"（匯流分數 {r.get('confluence', 0)}/100）{demo_note}".rstrip(),
        f"收盤：{info['price']:.2f}　RSI：{_fmt(info['rsi'])}"
        f"　MACD柱：{_fmt(info['macd_hist'], 4)}",
    ]
    if r.get("reasons_bull"):
        lines.append("偏多理由：")
        lines += [f"  ・{x}" for x in r["reasons_bull"]]
    if r.get("reasons_bear"):
        lines.append("偏空理由：")
        lines += [f"  ・{x}" for x in r["reasons_bear"]]
    if lv:
        supports = "、".join(str(x) for x in lv.get("supports", [])) or "—"
        resistances = "、".join(str(x) for x in lv.get("resistances", [])) or "—"
        tp = "、".join(str(x) for x in lv.get("take_profit", [])) or "—"
        lines.append(f"支撐：{supports}　壓力：{resistances}")
        lines.append(
            f"建議停損：{lv.get('stop_loss', '—')}　目標：{tp}"
            f"（風險約 {lv.get('risk_pct', 0)}%）"
        )
    return "\n".join(lines)


def compare_stocks(symbols, period: str = "6mo") -> str:
    """同時比較多檔股票的價格、漲跌、RSI 與技術訊號(一行一檔)。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods
    try:
        import signals
    except ImportError:
        return _IMPORT_HINT

    syms = _normalize_symbols(symbols)
    if not syms:
        return "錯誤：請提供至少一檔股票代號。"

    lines, demo_seen = [], False
    for sym in syms:
        info = _signal_for(sym, period, dp, bt, demo, signals)
        if info.get("error"):
            lines.append(f"{sym}：{info['error']}")
            continue
        demo_seen = demo_seen or info["is_demo"]
        r = info["detect"]
        lines.append(
            f"{info['symbol']}｜{info['price']:.2f}（{info['change']:+.2f}%）"
            f"｜RSI {_fmt(info['rsi'])}｜{r.get('signal', '?')}"
            f"(匯流{r.get('confluence', 0)})"
        )
    header = "多檔比較" + ("（示範資料，非即時）" if demo_seen else "") + "："
    return header + "\n" + "\n".join(lines)


def scan_stocks(symbols, period: str = "6mo") -> str:
    """掃描一份清單,挑出偏多訊號、偏空/觀望與超賣(RSI<30)的標的。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods
    try:
        import signals
    except ImportError:
        return _IMPORT_HINT

    syms = _normalize_symbols(symbols)
    if not syms:
        return "錯誤：請提供至少一檔股票代號。"

    bullish, bearish, oversold, failed = [], [], [], []
    demo_seen = False
    for sym in syms:
        info = _signal_for(sym, period, dp, bt, demo, signals)
        if info.get("error"):
            failed.append(f"{sym}（{info['error']}）")
            continue
        demo_seen = demo_seen or info["is_demo"]
        r = info["detect"]
        conf = r.get("confluence", 0)
        entry = (
            conf,
            f"{info['symbol']} {r.get('signal', '?')}(匯流{conf})"
            f"｜RSI {_fmt(info['rsi'])}",
        )
        if r.get("is_bullish"):
            bullish.append(entry)
        else:
            bearish.append(entry)
        rsi = info["rsi"]
        if rsi is not None and rsi < 30:
            oversold.append(f"{info['symbol']}（RSI {rsi:.1f}）")

    bullish.sort(reverse=True)
    bearish.sort(reverse=True)

    out = [f"掃描結果（共 {len(syms)} 檔）" + ("（示範資料）" if demo_seen else "") + "："]
    if bullish:
        out.append("🔥 偏多訊號（依匯流分數排序）：")
        out += [f"  ・{t}" for _, t in bullish]
    if bearish:
        out.append("⚠️ 偏空／觀望：")
        out += [f"  ・{t}" for _, t in bearish]
    if oversold:
        out.append("📉 超賣可留意（RSI<30）：" + "、".join(oversold))
    if failed:
        out.append("（無法分析：" + "、".join(failed) + "）")
    return "\n".join(out)



def _parse_horizons(horizons, default=(5, 21, 63)) -> list[int]:
    """把預測天期參數正規化成正整數陣列（容忍逗號字串），去重並限量。"""
    if horizons is None or horizons == "":
        return list(default)
    if isinstance(horizons, str):
        horizons = horizons.replace(",", " ").split()
    out, seen = [], set()
    for h in horizons:
        try:
            n = int(h)
        except (TypeError, ValueError):
            continue
        if 0 < n <= 504 and n not in seen:  # 上限約兩年交易日
            seen.add(n)
            out.append(n)
    return out[:6] or list(default)


_HORIZON_LABELS = {5: "1 週後", 21: "1 個月後", 63: "3 個月後", 126: "半年後", 252: "1 年後"}


def montecarlo_forecast(
    symbol: str,
    period: str = "6mo",
    horizons="5,21,63",
    simulations: int = 50000,
    event_day: int = 0,
    event_drop_pct: float = 0.0,
    event_prob: float = 0.0,
) -> str:
    """用蒙地卡羅模擬某檔股票未來價格的機率區間，而非單一預測值。

    基礎模型為漂移 0 的 GBM（隨機漫步假設），不預設漲跌方向，輸出各天期的
    P5/P25/P50/P75/P95 百分位與「高於現價的機率」，重點在量化不確定性。

    可選的事件風險（跳躍擴散）：當設定 event_day（事件落在第幾個交易日）、
    event_drop_pct（事件預期衝擊，如 -0.15 代表平均 -15%）與 event_prob
    （事件發生機率 0~1）時，凡天期涵蓋到該日的路徑，會以該機率疊加一次性
    跳躍——用來模擬 IPO 解禁賣壓、財報等已知催化事件造成的肥尾下檔風險。
    """
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, demo = mods
    try:
        import numpy as np
    except ImportError:
        return _IMPORT_HINT

    s = _fetch_series(symbol, period, dp, demo)
    if not s or not s.get("closes"):
        return f"錯誤：無法取得 {symbol.upper()} 的資料。"

    closes = np.asarray([float(x) for x in s["closes"]], dtype=float)
    closes = closes[closes > 0]
    if len(closes) < 5:
        return f"錯誤：{symbol.upper()} 價格資料太少（{len(closes)} 筆），無法估計波動率。"

    logret = np.diff(np.log(closes))
    sigma = float(logret.std(ddof=1))
    if not np.isfinite(sigma) or sigma <= 0:
        return f"錯誤：{symbol.upper()} 波動率估計無效。"

    s0 = float(closes[-1])
    n_sims = max(1000, min(int(simulations), 500000))
    rng = np.random.default_rng(42)
    horizon_list = sorted(_parse_horizons(horizons))

    # 事件風險參數正規化：三者齊備且合理時才啟用。
    ev_day = int(event_day) if event_day else 0
    ev_prob = min(max(float(event_prob), 0.0), 1.0)
    ev_drop = float(event_drop_pct)
    event_on = ev_day > 0 and ev_prob > 0 and ev_drop != 0.0
    # 跳躍幅度的對數常態參數：期望衝擊 ev_drop，散佈取其半幅（下限 3%）。
    ev_mu = np.log1p(ev_drop) if event_on else 0.0
    ev_sigma = max(abs(ev_drop) * 0.5, 0.03) if event_on else 0.0

    demo_note = "（示範資料，非即時）" if s.get("is_demo") else ""
    model_name = "GBM+跳躍" if event_on else "GBM"
    lines = [
        f"{symbol.upper()} ｜ 蒙地卡羅預測（{model_name}，{n_sims:,} 條路徑）{demo_note}".rstrip(),
        f"現價：{s0:.2f}　估計每日波動率 σ≈{sigma:.2%}"
        f"（年化≈{sigma * (252 ** 0.5):.0%}）　漂移μ=0（隨機漫步）",
    ]
    if event_on:
        lines.append(
            f"事件風險：第 T+{ev_day} 日 以 {ev_prob:.0%} 機率發生，"
            f"平均衝擊 {ev_drop:+.0%}（模擬解禁／財報等催化）"
        )
    lines.append("─" * 36)

    for h in horizon_list:
        # 漂移 0 的 GBM：ln(S_T/S_0) ~ N(-0.5σ²·T, σ²·T)
        drift = -0.5 * sigma**2 * h
        shock = sigma * (h**0.5) * rng.standard_normal(n_sims)
        st = s0 * np.exp(drift + shock)
        # 若此天期已涵蓋事件日，對中籤路徑疊加一次性跳躍。
        if event_on and h >= ev_day:
            hit = rng.random(n_sims) < ev_prob
            jump = np.exp(ev_mu + ev_sigma * rng.standard_normal(n_sims))
            st = np.where(hit, st * jump, st)
        p5, p25, p50, p75, p95 = np.percentile(st, [5, 25, 50, 75, 95])
        p_up = float((st > s0).mean())
        label = _HORIZON_LABELS.get(h, f"T+{h} 日")
        flag = " ⚠含事件" if event_on and h >= ev_day else ""
        lines.append(
            f"{label}（T+{h}）{flag}：中位 {p50:.2f}　"
            f"區間 P5–P95 {p5:.2f}~{p95:.2f}　"
            f"P25–P75 {p25:.2f}~{p75:.2f}　上漲機率 {p_up:.0%}"
        )
    lines.append("─" * 36)
    tail = (
        "註：此為機率分佈而非價格保證；"
        + ("已納入上述事件風險，" if event_on else "未納入解禁、財報等事件風險，")
        + "波動率越高代表不確定性越大，請搭配風險控管使用。"
    )
    lines.append(tail)
    return "\n".join(lines)


def _fmt(value, digits: int = 1) -> str:
    """把可能為 None 的數值格式化成字串。"""
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_big(value) -> str:
    """把大數字格式化成兆/億/百萬（B/M）字串。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:.2f}兆"
    if a >= 1e8:
        return f"{v / 1e8:.2f}億"
    if a >= 1e6:
        return f"{v / 1e6:.1f}百萬"
    return f"{v:,.0f}"


def _fmt_pct(value, already_pct: bool = False) -> str:
    """把比率格式化成百分比字串；already_pct=True 表示傳入值本身就是百分數。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{v if already_pct else v * 100:.2f}%"


def _to_float(value):
    """容忍字串/None 的浮點轉換，失敗或非數字回傳 None。"""
    if value in (None, "", "None", "-", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fundamentals_yfinance(symbol: str) -> dict | None:
    """用 yfinance 抓基本面（Railway 等可連 Yahoo 的環境適用）。"""
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).get_info()
    except Exception:  # noqa: BLE001 — 任何取數失敗都回 None，交給後備
        return None
    if not info or not isinstance(info, dict):
        return None
    if not (info.get("marketCap") or info.get("trailingPE") or info.get("totalRevenue")):
        return None
    return {
        "name": info.get("longName") or info.get("shortName") or symbol.upper(),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _to_float(info.get("marketCap")),
        "pe_trailing": _to_float(info.get("trailingPE")),
        "pe_forward": _to_float(info.get("forwardPE")),
        "eps": _to_float(info.get("trailingEps")),
        "peg": _to_float(info.get("pegRatio") or info.get("trailingPegRatio")),
        "ps": _to_float(info.get("priceToSalesTrailing12Months")),
        "revenue": _to_float(info.get("totalRevenue")),
        "profit_margin": _to_float(info.get("profitMargins")),  # 0~1 比率
        "gross_margin": _to_float(info.get("grossMargins")),
        "wk52_high": _to_float(info.get("fiftyTwoWeekHigh")),
        "wk52_low": _to_float(info.get("fiftyTwoWeekLow")),
        "dividend_yield": _to_float(info.get("dividendYield")),  # 0~1 比率
        "source": "yfinance",
        "margin_is_ratio": True,
    }


def _fundamentals_alpha_vantage(symbol: str) -> dict | None:
    """用 Alpha Vantage OVERVIEW 抓基本面（需 ALPHA_VANTAGE_KEY）。"""
    import os

    key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if not key:
        return None
    try:
        import requests

        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "OVERVIEW", "symbol": symbol.upper(), "apikey": key},
            timeout=15,
        )
        d = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not d or not d.get("Symbol"):
        return None
    return {
        "name": d.get("Name") or symbol.upper(),
        "sector": d.get("Sector"),
        "industry": d.get("Industry"),
        "market_cap": _to_float(d.get("MarketCapitalization")),
        "pe_trailing": _to_float(d.get("PERatio")),
        "pe_forward": _to_float(d.get("ForwardPE")),
        "eps": _to_float(d.get("EPS")),
        "peg": _to_float(d.get("PEGRatio")),
        "ps": _to_float(d.get("PriceToSalesRatioTTM")),
        "revenue": _to_float(d.get("RevenueTTM")),
        "profit_margin": _to_float(d.get("ProfitMargin")),  # 0~1 比率
        "gross_margin": None,
        "wk52_high": _to_float(d.get("52WeekHigh")),
        "wk52_low": _to_float(d.get("52WeekLow")),
        "dividend_yield": _to_float(d.get("DividendYield")),  # 0~1 比率
        "source": "alpha_vantage",
        "margin_is_ratio": True,
    }


def get_fundamentals(symbol: str) -> str:
    """查某檔股票的基本面：市值、本益比、EPS、營收、利潤率、估值等。"""
    f = _fundamentals_yfinance(symbol) or _fundamentals_alpha_vantage(symbol)
    if not f:
        return (
            f"錯誤：無法取得 {symbol.upper()} 的基本面資料。"
            "可能是代號有誤、該標的無財報資料，或資料源暫時無法連線"
            "（可設定 ALPHA_VANTAGE_KEY 環境變數啟用備援來源）。"
        )

    ratio = f.get("margin_is_ratio", True)
    sec = "／".join(x for x in (f.get("sector"), f.get("industry")) if x)
    lines = [
        f"{f['name']}（{symbol.upper()}）基本面｜來源：{f['source']}",
        f"產業：{sec or '—'}",
        f"市值：{_fmt_big(f.get('market_cap'))}　"
        f"營收(TTM)：{_fmt_big(f.get('revenue'))}",
        f"本益比 P/E：{_fmt(f.get('pe_trailing'), 1)}（預估 {_fmt(f.get('pe_forward'), 1)}）　"
        f"EPS：{_fmt(f.get('eps'), 2)}",
        f"PEG：{_fmt(f.get('peg'), 2)}　股價營收比 P/S：{_fmt(f.get('ps'), 1)}",
        f"淨利率：{_fmt_pct(f.get('profit_margin'), already_pct=not ratio)}　"
        f"毛利率：{_fmt_pct(f.get('gross_margin'), already_pct=not ratio)}",
        f"52週高/低：{_fmt(f.get('wk52_high'), 2)} / {_fmt(f.get('wk52_low'), 2)}　"
        f"股息殖利率：{_fmt_pct(f.get('dividend_yield'), already_pct=not ratio)}",
    ]
    lines.append("註：基本面數據僅供參考，不構成投資建議。")
    return "\n".join(lines)


STOCK_TOOL_SCHEMAS = [
    {
        "name": "get_stock_price",
        "description": (
            "查詢某檔股票的最新收盤價、單日漲跌幅與區間高低。"
            "當使用者問到某檔股票的價格、走勢或近況時使用。"
            "支援美股（如 AAPL）與台股（如 2330.TW）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代號，例如 'AAPL'、'NVDA'、'2330.TW'。",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_fundamentals",
        "description": (
            "查詢某檔股票的基本面數據：市值、本益比（P/E）、預估本益比、EPS、"
            "PEG、股價營收比（P/S）、營收、淨利率、毛利率、52週高低與股息殖利率。"
            "當使用者問到某檔股票『貴不貴』、估值、合理價、財報、基本面、"
            "本益比、EPS、營收、利潤率等時使用。支援美股；台股與部分標的視資料源而定。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代號，例如 'SPCX'、'NVDA'、'AAPL'。",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_state",
        "description": (
            "研判目前整體大盤是偏多、偏弱還是風險升高（依 SPY / QQQ）。"
            "當使用者問到大盤、市場氣氛、現在適不適合進場時使用。"
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "analyze_signals",
        "description": (
            "計算某檔股票的技術指標（RSI、MACD、布林通道、均線），研判目前"
            "是偏多還偏空，並給出偏多/偏空理由與關鍵價位（支撐、壓力、停損、目標）。"
            "當使用者問到某檔股票的技術面、進出場時機、該不該買/賣時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代號，例如 'NVDA'、'2330.TW'。",
                },
                "period": {
                    "type": "string",
                    "description": "資料期間，例如 '3mo'、'6mo'、'1y'，預設 '6mo'。",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "compare_stocks",
        "description": (
            "同時比較多檔股票，列出各自的收盤價、漲跌幅、RSI 與技術訊號。"
            "當使用者想一次看好幾檔、做比較、或問「哪一檔比較強」時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "股票代號清單，例如 ['NVDA','AAPL','2330.TW']（最多 10 檔）。",
                },
                "period": {
                    "type": "string",
                    "description": "資料期間，預設 '6mo'。",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "scan_stocks",
        "description": (
            "掃描一份股票清單，挑出偏多訊號、偏空/觀望、以及超賣（RSI<30）的標的，"
            "並依匯流分數排序。當使用者想從一籃子股票中找出值得注意的標的時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要掃描的股票代號清單（最多 10 檔）。",
                },
                "period": {
                    "type": "string",
                    "description": "資料期間，預設 '6mo'。",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "montecarlo_forecast",
        "description": (
            "用蒙地卡羅模擬某檔股票未來價格的機率區間，輸出各天期的"
            "P5/P25/P50/P75/P95 百分位與上漲機率。基礎為漂移 0 的 GBM（隨機漫步），"
            "不預設方向，重點在量化不確定性。可選擇加入事件風險（解禁、財報等）"
            "模擬肥尾下檔。當使用者要求『預測走勢』、未來價格、目標價或上漲機率時"
            "使用——以機率分佈回應，而非單一保證值。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代號，例如 'SPCX'、'NVDA'、'2330.TW'。",
                },
                "period": {
                    "type": "string",
                    "description": "估計波動率所用的歷史期間，例如 '3mo'、'6mo'、'1y'，預設 '6mo'。",
                },
                "horizons": {
                    "type": "string",
                    "description": "預測天期（交易日），逗號分隔，例如 '5,21,63'，預設 '5,21,63'。",
                },
                "simulations": {
                    "type": "integer",
                    "description": "模擬路徑數，預設 50000（範圍 1000–500000）。",
                },
                "event_day": {
                    "type": "integer",
                    "description": (
                        "（選用）已知催化事件落在第幾個交易日，例如解禁日 90、"
                        "財報日 21。預設 0 表示不模擬事件。需與 event_drop_pct、"
                        "event_prob 一起設定才生效。"
                    ),
                },
                "event_drop_pct": {
                    "type": "number",
                    "description": (
                        "（選用）事件的平均價格衝擊，小數表示，例如 -0.15 代表平均 -15%"
                        "（解禁賣壓常為負）。預設 0。"
                    ),
                },
                "event_prob": {
                    "type": "number",
                    "description": "（選用）事件發生機率，0~1，例如 0.6。預設 0。",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "backtest_strategy",
        "description": (
            "對某檔股票用指定策略跑歷史回測，回傳總報酬、年化、勝率、"
            "獲利因子、夏普值、最大回檔等績效。當使用者想驗證某個交易"
            "策略在某檔股票上的歷史表現時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "股票代號，例如 'NVDA'、'2330.TW'。",
                },
                "strategy": {
                    "type": "string",
                    "enum": _STRATEGIES,
                    "description": "回測策略名稱，預設 decision_core。",
                },
                "period": {
                    "type": "string",
                    "description": "資料期間，例如 '6mo'、'1y'、'5y'，預設 '1y'。",
                },
            },
            "required": ["symbol"],
        },
    },
]

STOCK_TOOL_FUNCTIONS = {
    "get_stock_price": get_stock_price,
    "get_fundamentals": get_fundamentals,
    "get_market_state": get_market_state,
    "analyze_signals": analyze_signals,
    "compare_stocks": compare_stocks,
    "scan_stocks": scan_stocks,
    "montecarlo_forecast": montecarlo_forecast,
    "backtest_strategy": backtest_strategy,
}

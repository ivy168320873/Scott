"""股票工具：把上層 Scott 投資分析系統的功能接給 CLI Agent。

對外提供三個工具：
  - get_stock_price：查某檔股票最新價與區間高低
  - get_market_state：研判大盤多空
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


def _build_ohlcv(symbol: str, period: str, dp, demo, demo_n: int = 252) -> list[dict]:
    """組出 backtest.run 需要的 OHLCV 串列：先試真實/降級資料，再退回示範資料。

    demo_n：示範資料退回時要產生的天數（walk-forward 等需要較長歷史）。
    """
    try:
        d = dp.get_ohlcv(symbol.upper(), period)
    except Exception:  # noqa: BLE001 — 任何取數失敗都退回示範資料
        d = None

    # 真實資料就直接用；但若是「示範資料且長度不足」，改產生足夠長的示範資料。
    short_demo = d and d.get("is_demo") and len(d.get("closes", [])) < demo_n
    if d and d.get("closes") and not short_demo:
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
        hist = demo.generate(symbol.upper(), n=demo_n)
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



def _fmt(value, digits: int = 1) -> str:
    """把可能為 None 的數值格式化成字串。"""
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _rank_strategies(ohlcv: list, bt) -> list[tuple[str, dict]]:
    """對所有策略跑回測，依風險調整後（夏普→報酬）排序，回傳 [(策略, 結果)]。"""
    results = []
    for strat in _STRATEGIES:
        try:
            r = bt.run(ohlcv, strat, {})
        except Exception:  # noqa: BLE001
            continue
        results.append((strat, r))
    results.sort(
        key=lambda x: (x[1].get("sharpe", 0) or 0, x[1].get("total_return", 0) or 0),
        reverse=True,
    )
    return results


def compare_strategies(symbol: str, period: str = "2y") -> str:
    """對一檔股票跑遍所有策略並排名，找出歷史上表現最好的（風險調整後）。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods

    ohlcv = _build_ohlcv(symbol, period, dp, demo, demo_n=504)
    if not ohlcv:
        return f"錯誤：無法取得 {symbol} 的歷史資料。"

    ranked = _rank_strategies(ohlcv, bt)
    if not ranked:
        return f"錯誤：{symbol} 沒有任何策略可回測。"

    bh = ranked[0][1].get("bh_return", 0)
    lines = [
        f"{symbol.upper()} 策略比較（{len(ohlcv)} 根K棒，依風險調整後排序）"
        f"｜買進持有對照 {bh:+.1f}%：",
    ]
    for i, (strat, r) in enumerate(ranked, 1):
        flag = "" if r.get("num_trades", 0) >= 3 else "（交易過少，參考性低）"
        lines.append(
            f"{i}. {strat}｜報酬 {r.get('total_return', 0):+.1f}%"
            f" 年化 {r.get('annual_return', 0):+.1f}%"
            f" 勝率 {r.get('win_rate', 0):.0f}%"
            f" 交易 {r.get('num_trades', 0)}"
            f" 夏普 {r.get('sharpe', 0)}"
            f" 回檔 {r.get('max_drawdown', 0):.1f}%{flag}"
        )
    best = ranked[0]
    beats = "贏過" if best[1].get("total_return", 0) > bh else "輸給"
    lines.append(
        f"➡️ 風險調整後最佳：{best[0]}（{beats}買進持有）。"
        "夏普越高代表報酬相對波動越穩；數據為歷史回測，不保證未來。"
    )
    return "\n".join(lines)


def validate_strategy(
    symbol: str, strategy: str = "decision_core_v3", period: str = "5y"
) -> str:
    """用 walk-forward + 蒙地卡羅驗證策略是否穩健（而非只是過去剛好有效）。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods
    try:
        import optimizer
    except ImportError:
        return _IMPORT_HINT

    if strategy not in _STRATEGIES:
        return f"錯誤：未知策略 '{strategy}'。可用：{', '.join(_STRATEGIES)}"

    # walk-forward 需要較長歷史（至少約 315 根K棒）。
    ohlcv = _build_ohlcv(symbol, period, dp, demo, demo_n=1260)
    if not ohlcv:
        return f"錯誤：無法取得 {symbol} 的歷史資料。"

    lines = [f"{symbol.upper()} ｜ 策略穩健度驗證：{strategy}（{len(ohlcv)} 根K棒）"]

    try:
        wf = optimizer.rolling_walk_forward(ohlcv, strategy)
    except Exception as e:  # noqa: BLE001
        wf = {"error": str(e)}
    if wf.get("error"):
        lines.append(f"・前進測試：{wf['error']}")
    else:
        lines.append(
            "【前進測試 Walk-Forward】（拿過去訓練、未見過的資料測試）\n"
            f"  結論：{wf.get('verdict', '?')}\n"
            f"  樣本外平均報酬：{wf.get('avg_oos_return', 0):+.1f}%"
            f"　樣本外勝率：{wf.get('avg_oos_win_rate', 0):.0f}%\n"
            f"  過擬合落差：{wf.get('overfit_gap', 0)}（越小越好）"
            f"　穩定度：{wf.get('stability_pct', 0)}%"
        )

    try:
        r = bt.run(ohlcv, strategy, {})
        mc = optimizer.monte_carlo(r.get("trades", []))
    except Exception as e:  # noqa: BLE001
        mc = {"error": str(e)}
    if mc.get("error"):
        lines.append(f"・蒙地卡羅：{mc['error']}")
    else:
        ret = mc.get("return", {})
        dd = mc.get("drawdown", {})
        lines.append(
            "【蒙地卡羅模擬】（打亂歷史交易、模擬數千種未來）\n"
            f"  獲利機率：{mc.get('prob_profit', 0)}%\n"
            f"  未來報酬區間：悲觀(p5) {ret.get('p5', 0):+.1f}%"
            f"／中位(p50) {ret.get('p50', 0):+.1f}%"
            f"／樂觀(p95) {ret.get('p95', 0):+.1f}%\n"
            f"  可能最大回檔(p95)：{dd.get('p95', 0):.1f}%"
        )

    lines.append("⚠️ 驗證能降低（但無法消除）過擬合風險；歷史不保證未來，請控管部位。")
    return "\n".join(lines)


def trade_plan(
    symbol: str, account_size: float = 100000, risk_pct: float = 1.0, period: str = "6mo"
) -> str:
    """整合大盤、個股技術訊號、最佳策略與部位大小，給一份完整進出場建議。"""
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
    price = info["price"]
    stop = lv.get("stop_loss")
    targets = lv.get("take_profit", [])
    demo_note = "（示範資料，非即時）" if info["is_demo"] else ""

    # 大盤背景
    try:
        ms = dp.market_state()
        market = f"{ms.get('overall', '?')}（{ms.get('regime', '?')}）"
    except Exception:  # noqa: BLE001
        market = "未知"

    # 最佳策略（用 1 年資料快速比一輪）
    best_line = ""
    try:
        ohlcv = _build_ohlcv(symbol, "1y", dp, demo, demo_n=252)
        ranked = _rank_strategies(ohlcv, bt) if ohlcv else []
        if ranked:
            b = ranked[0]
            best_line = (
                f"歷史最佳策略：{b[0]}（報酬 {b[1].get('total_return', 0):+.1f}%、"
                f"勝率 {b[1].get('win_rate', 0):.0f}%、夏普 {b[1].get('sharpe', 0)}）"
            )
    except Exception:  # noqa: BLE001
        pass

    # 部位大小：每筆風險 = 本金 × risk_pct%，股數 = 風險金額 / 每股停損距離
    sizing = ""
    if stop and price > stop:
        risk_amount = account_size * risk_pct / 100
        per_share = price - stop
        shares = int(risk_amount / per_share) if per_share > 0 else 0
        cost = shares * price
        sizing = (
            f"部位試算（本金 {account_size:,.0f}、單筆風險 {risk_pct}%）：\n"
            f"  建議股數約 {shares} 股（約 {cost:,.0f} 成本），"
            f"觸及停損約虧 {risk_amount:,.0f}"
        )

    lines = [
        f"📋 {symbol.upper()} 進出場建議{demo_note}",
        f"大盤背景：{market}",
        f"技術訊號：{r.get('signal', '?')}（匯流 {r.get('confluence', 0)}/100）"
        f"｜RSI {_fmt(info['rsi'])}",
    ]
    if best_line:
        lines.append(best_line)
    lines.append(
        f"參考進場：{price:.2f}　停損：{stop if stop else '—'}　"
        f"目標：{('、'.join(str(t) for t in targets)) if targets else '—'}"
        f"（風險約 {lv.get('risk_pct', 0)}%）"
    )
    if sizing:
        lines.append(sizing)
    if r.get("reasons_bull"):
        lines.append("偏多：" + "；".join(r["reasons_bull"][:3]))
    if r.get("reasons_bear"):
        lines.append("偏空：" + "；".join(r["reasons_bear"][:3]))
    lines.append(
        "⚠️ 以上為機械式訊號彙整，僅供參考、不構成投資建議；"
        "請自行核對並嚴守停損。"
    )
    return "\n".join(lines)


# 大盤盤勢 → (適合的候選策略, 操作方向說明)
_REGIME_PLAN = {
    "bull": (
        ["ma_cross", "decision_core", "decision_core_v2", "decision_core_v3", "combined"],
        "趨勢偏多，順勢操作；趨勢／動能策略較有利。",
    ),
    "risk_on": (
        ["macd", "decision_core", "decision_core_v2", "decision_core_v3"],
        "波動／風險升高，動能策略較合適，但要控管部位。",
    ),
    "sideways": (
        ["rsi", "bollinger"],
        "區間盤整，均值回歸（低買高賣）較合適。",
    ),
    "bear": (
        ["rsi", "bollinger"],
        "空頭／偏弱，以防禦為主——降低部位、嚴設停損，避免追多。",
    ),
}


def regime_strategy(symbol: str, period: str = "1y") -> str:
    """先判斷大盤多空，自動選用適合該盤勢的策略，並在該股挑出最佳、附上目前訊號。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, bt, demo = mods
    try:
        import signals
    except ImportError:
        return _IMPORT_HINT

    try:
        ms = dp.market_state()
    except Exception as e:  # noqa: BLE001
        return f"錯誤：取得大盤狀態失敗：{e}"

    regime = ms.get("regime", "sideways")
    overall = ms.get("overall", "?")
    candidates, direction = _REGIME_PLAN.get(regime, (list(_STRATEGIES), "盤勢中性。"))

    ohlcv = _build_ohlcv(symbol, period, dp, demo, demo_n=252)
    if not ohlcv:
        return f"錯誤：無法取得 {symbol} 的歷史資料。"

    ranked = []
    for strat in candidates:
        try:
            r = bt.run(ohlcv, strat, {})
        except Exception:  # noqa: BLE001
            continue
        ranked.append((strat, r))
    ranked.sort(
        key=lambda x: (x[1].get("sharpe", 0) or 0, x[1].get("total_return", 0) or 0),
        reverse=True,
    )

    demo_seen = bool(ms.get("is_demo"))
    info = _signal_for(symbol, "6mo", dp, bt, demo, signals)
    if not info.get("error"):
        demo_seen = demo_seen or info["is_demo"]

    lines = [
        f"🧭 依大盤切換策略 — {symbol.upper()}" + ("（示範資料）" if demo_seen else ""),
        f"大盤狀態：{overall}（{regime}）",
        f"策略方向：{direction}",
    ]
    if ranked:
        b, r = ranked[0]
        lines.append(
            f"推薦策略：{b}（此股 {period} 回測：報酬 {r.get('total_return', 0):+.1f}%"
            f"、勝率 {r.get('win_rate', 0):.0f}%、夏普 {r.get('sharpe', 0)}"
            f"、回檔 {r.get('max_drawdown', 0):.1f}%）"
        )
    else:
        lines.append("推薦策略：（此盤勢下候選策略皆無有效回測結果）")

    if not info.get("error"):
        rr = info["detect"]
        lines.append(
            f"目前個股訊號：{rr.get('signal', '?')}（匯流 {rr.get('confluence', 0)}/100）"
            f"｜RSI {_fmt(info['rsi'])}"
        )

    if regime == "bear":
        lines.append("※ 空頭格局：寧可錯過、不要做錯，部位放小並嚴守停損。")
    lines.append("⚠️ 機械式彙整，僅供參考、不構成投資建議。")
    return "\n".join(lines)


def _momentum_label(score: float) -> str:
    if score >= 75:
        return "強勁動能"
    if score >= 60:
        return "偏多動能"
    if score >= 40:
        return "中性"
    if score >= 25:
        return "偏弱"
    return "弱勢"


def momentum_analysis(symbol: str, period: str = "6mo") -> str:
    """整合趨勢、相對強度（對大盤）、量能三大引擎，給一份動能分析。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, _ = mods
    try:
        import relative_strength_score as rss
        import trend_score
        import volume_score
    except ImportError:
        return _IMPORT_HINT

    try:
        ohlcv = dp.get_ohlcv(symbol.upper(), period)
    except Exception as e:  # noqa: BLE001
        return f"錯誤：取得 {symbol} 資料失敗：{e}"
    if not ohlcv or not ohlcv.get("closes"):
        return f"錯誤：找不到 {symbol} 的資料。"

    # 相對強度需要大盤基準（SPY）。
    try:
        bench = dp.get_ohlcv("SPY", period)
    except Exception:  # noqa: BLE001
        bench = None

    try:
        trend = trend_score.compute(ohlcv)
        rs = rss.compute(ohlcv, bench)
        vol = volume_score.compute(ohlcv)
    except Exception as e:  # noqa: BLE001
        return f"錯誤：動能計算失敗：{e}"

    composite = round((trend.get("score", 40) + rs.get("score", 40) + vol.get("score", 40)) / 3)
    demo_note = "（示範資料，非即時）" if ohlcv.get("is_demo") else ""

    rs_detail = rs.get("detail", {})
    rs_sub = rs.get("sub_scores", {})
    vol_detail = vol.get("detail", {})

    lines = [
        f"🚀 動能分析 — {symbol.upper()}{demo_note}",
        f"綜合動能：{_momentum_label(composite)}（{composite}/100）",
        f"・趨勢：{trend.get('label', '?')}（{trend.get('score', '?')}）",
        f"・相對大盤：{rs.get('label', '?')}（{rs.get('score', '?')}）"
        f"｜近20日 {_fmt(rs_detail.get('ret_20d_pct'))}%"
        f"、超越SPY {_fmt(rs_detail.get('excess_20d'))}%"
        f"、動能百分位 {_fmt(rs_sub.get('momentum_percentile'))}%",
        f"・量能：{vol.get('label', '?')}（{vol.get('score', '?')}）"
        f"｜近5日量比 {_fmt(vol_detail.get('vol_ratio_5d'), 2)}",
    ]

    flags = []
    rsig = rs.get("signals", {})
    vsig = vol.get("signals", {})
    if rsig.get("strong_momentum"):
        flags.append("✅強勁動能")
    elif rsig.get("positive_momentum"):
        flags.append("✅正動能")
    if rsig.get("outperforming_20d"):
        flags.append("✅強於大盤")
    if rsig.get("underperforming_20d"):
        flags.append("⚠️弱於大盤")
    if vsig.get("high_volume_5d"):
        flags.append("✅近期爆量")
    if vsig.get("vol_expanding"):
        flags.append("✅量能擴張")
    if vsig.get("distribution_warning"):
        flags.append("⚠️出貨警示")
    if flags:
        lines.append("動能訊號：" + " ".join(flags))

    reasons = (trend.get("reasons") or [])[:2] + (rs.get("reasons") or [])[:2]
    if reasons:
        lines.append("重點：" + "；".join(reasons))

    lines.append("⚠️ 機械式彙整，僅供參考、不構成投資建議。")
    return "\n".join(lines)


def _as_range(tr) -> str:
    """把 target_range（可能是 list / dict / str / None）轉成可讀字串。"""
    if not tr:
        return "—"
    if isinstance(tr, dict):
        lo = tr.get("low", tr.get("min"))
        hi = tr.get("high", tr.get("max"))
        if lo is not None and hi is not None:
            return f"{lo}~{hi}"
        return "、".join(f"{k}:{v}" for k, v in tr.items())
    if isinstance(tr, (list, tuple)):
        return "~".join(str(x) for x in tr)
    return str(tr)


def institutional_score(
    symbol: str, risk_profile: str = "balanced", period: str = "1y"
) -> str:
    """7 模組機構動能總評（趨勢/量能/相對強度/催化/估值/風險），給評級與決策。

    這是 Scott 系統 Phase 16 的統一決策引擎（與網頁版 /api/momentum-score 同源）。
    """
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, _ = mods
    try:
        import final_decision_engine as fde
    except ImportError:
        return _IMPORT_HINT

    if risk_profile not in ("conservative", "balanced", "aggressive"):
        risk_profile = "balanced"

    try:
        ohlcv = dp.get_ohlcv(symbol.upper(), period)
    except Exception as e:  # noqa: BLE001
        return f"錯誤：取得 {symbol} 資料失敗：{e}"
    if not ohlcv or not ohlcv.get("closes"):
        return f"錯誤：找不到 {symbol} 的資料。"

    try:
        bench = dp.get_ohlcv("QQQ", period)
    except Exception:  # noqa: BLE001
        bench = None

    try:
        r = fde.compute(ohlcv, bench_ohlcv=bench, risk_profile=risk_profile)
    except Exception as e:  # noqa: BLE001
        return f"錯誤：機構評分計算失敗：{e}"

    cs = r.get("component_scores", {})
    demo_note = "（示範資料，非即時）" if r.get("is_demo") else ""
    lines = [
        f"🏛️ 機構動能總評 — {symbol.upper()}（風險偏好：{risk_profile}）{demo_note}".rstrip(),
        f"總分：{_fmt(r.get('total_score'))}（{r.get('grade', '?')}）"
        f"｜訊號：{r.get('signal_status', '?')}"
        f"｜行動：{r.get('action_label', '?')}（{r.get('action_code', '?')}）",
        f"各模組分數：趨勢 {_fmt(cs.get('trend'))}　量能 {_fmt(cs.get('volume'))}"
        f"　相對強度 {_fmt(cs.get('rs'))}　催化 {_fmt(cs.get('catalyst'))}"
        f"　估值 {_fmt(cs.get('valuation'))}　風險 {_fmt(cs.get('risk'))}(越高越危險)",
        f"停損：{r.get('stop_loss') if r.get('stop_loss') is not None else '—'}"
        f"　目標：{_as_range(r.get('target_range'))}"
        f"　失效點：{r.get('invalidation') if r.get('invalidation') is not None else '—'}",
    ]
    pos = r.get("position_sizing_suggestion")
    if isinstance(pos, dict):
        label = pos.get("label") or ""
        est = pos.get("estimated_label")
        pct = pos.get("pct")
        txt = label
        if pct is not None and pct != 0:
            txt += f"（{pct}%）"
        if est and est != label:
            txt += f"｜預估 {est}"
        if txt:
            lines.append(f"建議部位：{txt}")
    elif pos:
        lines.append(f"建議部位：{pos}")
    reasons = r.get("reasons") or []
    if reasons:
        lines.append("重點：" + "；".join(str(x) for x in reasons[:4]))
    warnings = r.get("risk_warnings") or []
    if warnings:
        lines.append("⚠️ 風險警示：" + "；".join(str(x) for x in warnings[:3]))
    gate = (r.get("detail") or {}).get("gate_notes") or []
    if gate:
        lines.append("（閘門：" + "；".join(str(x) for x in gate) + "）")
    lines.append("⚠️ 機械式彙整，僅供參考、不構成投資建議。")
    return "\n".join(lines)


def full_analysis(
    symbol: str, risk_profile: str = "balanced", period: str = "1y"
) -> str:
    """共識決策：彙整大盤、機構總評、投資委員會、相對強度四個獨立視角，

    只有當多數一致時才給高信念訊號（更全面、提高勝率的核心：寧缺勿濫）。
    """
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, _ = mods
    try:
        import final_decision_engine as fde
        import investment_committee_engine as ice
        import relative_strength_score as rss
    except ImportError:
        return _IMPORT_HINT

    if risk_profile not in ("conservative", "balanced", "aggressive"):
        risk_profile = "balanced"
    sym = symbol.upper()

    try:
        ohlcv = dp.get_ohlcv(sym, period)
    except Exception as e:  # noqa: BLE001
        return f"錯誤：取得 {sym} 資料失敗：{e}"
    if not ohlcv or not ohlcv.get("closes"):
        return f"錯誤：找不到 {sym} 的資料。"
    try:
        bench = dp.get_ohlcv("QQQ", period)
    except Exception:  # noqa: BLE001
        bench = None

    bull = bear = 0
    rows = []
    demo_seen = bool(ohlcv.get("is_demo"))

    # 1) 大盤背景
    try:
        ms = dp.market_state()
        regime = ms.get("regime", "sideways")
        d = "多" if regime == "bull" else "空" if regime == "bear" else "中"
        bull += d == "多"
        bear += d == "空"
        rows.append(f"大盤：{ms.get('overall', '?')}（{regime}）→ {d}")
    except Exception:  # noqa: BLE001
        rows.append("大盤：取得失敗 → 中")

    # 2) 機構動能總評
    try:
        fr = fde.compute(ohlcv, bench_ohlcv=bench, risk_profile=risk_profile)
        code = fr.get("action_code", "WATCH")
        d = "多" if code == "BUY" else "空" if code in ("TRIM", "EXIT", "SELL") else "中"
        bull += d == "多"
        bear += d == "空"
        rows.append(
            f"機構總評：{_fmt(fr.get('total_score'))}（{fr.get('grade', '?')}）"
            f"／{fr.get('action_label', code)} → {d}"
        )
    except Exception:  # noqa: BLE001
        rows.append("機構總評：計算失敗 → 中")

    # 3) AI 投資委員會
    try:
        cr = ice.run_investment_committee(sym, dp.get_ohlcv, risk_profile=risk_profile)
        fd = cr.get("final_decision", "WATCH")
        d = "多" if fd == "BUY" else "空" if fd in ("SELL", "EXIT") else "中"
        bull += d == "多"
        bear += d == "空"
        rows.append(
            f"投資委員會：{_VOTE_LABEL.get(fd, fd)}"
            f"（買{cr.get('buy_votes', 0)}/賣{cr.get('sell_votes', 0)}）→ {d}"
        )
    except Exception:  # noqa: BLE001
        rows.append("投資委員會：計算失敗 → 中")

    # 4) 相對強度（對大盤）
    try:
        rr = rss.compute(ohlcv, bench)
        sc = rr.get("score", 50)
        d = "多" if sc >= 65 else "空" if sc <= 40 else "中"
        bull += d == "多"
        bear += d == "空"
        rows.append(f"相對強度：{rr.get('label', '?')}（{_fmt(sc)}）→ {d}")
    except Exception:  # noqa: BLE001
        rows.append("相對強度：計算失敗 → 中")

    # 共識研判
    if bull >= 3 and bear == 0:
        verdict = "🟢 高信念偏多（多數一致看多）"
    elif bear >= 3 and bull == 0:
        verdict = "🔴 高信念偏空（多數一致看空）"
    elif bull > bear:
        verdict = "🟡 偏多但有分歧（把握度中等）"
    elif bear > bull:
        verdict = "🟡 偏空但有分歧（把握度中等）"
    else:
        verdict = "⚪ 分歧／訊號不明（建議觀望）"

    lines = [
        f"🎯 共識決策 — {sym}" + ("（示範資料，非即時）" if demo_seen else ""),
        f"結論：{verdict}　（看多 {bull}／看空 {bear}／共 4 個視角）",
        *[f"  ・{r}" for r in rows],
    ]
    # 逆風提醒
    if "偏多" in verdict and any("空" in r and r.startswith("大盤") for r in rows):
        lines.append("⚠️ 大盤逆風：個股偏多但大盤偏空，宜減碼或等回檔再進。")
    lines.append(
        "💡 提高勝率的關鍵：只在「高信念」時出手，分歧時寧可空手；"
        "並嚴守停損。機械式彙整，不構成投資建議。"
    )
    return "\n".join(lines)


_COMMITTEE_NAMES = {
    "technical": "技術",
    "risk": "風險",
    "market": "市場",
    "institutional": "機構資金流",
    "portfolio": "投組",
}
_VOTE_LABEL = {"BUY": "買進", "HOLD": "續抱", "WATCH": "觀望", "SELL": "賣出"}


def committee_vote(symbol: str, risk_profile: str = "balanced") -> str:
    """AI 投資委員會：5 個委員會（技術/風險/市場/機構資金流/投組）各自投票後彙整。"""
    mods = _load()
    if mods is None:
        return _IMPORT_HINT
    dp, _, _ = mods
    try:
        import investment_committee_engine as ice
    except ImportError:
        return _IMPORT_HINT

    if risk_profile not in ("conservative", "balanced", "aggressive"):
        risk_profile = "balanced"

    try:
        r = ice.run_investment_committee(
            symbol.upper(), dp.get_ohlcv, risk_profile=risk_profile
        )
    except Exception as e:  # noqa: BLE001
        return f"錯誤：投資委員會分析失敗：{e}"
    if not r.get("ok", True) and r.get("error"):
        return f"錯誤：{r['error']}"

    votes = r.get("committee_votes", {})
    final = r.get("final_decision", "?")
    final_label = _VOTE_LABEL.get(final, final)
    demo_note = "（示範資料，非即時）" if r.get("is_demo") else ""

    lines = [
        f"🗳️ AI 投資委員會 — {symbol.upper()}（風險偏好：{risk_profile}）{demo_note}".rstrip(),
        f"最終決議：{final_label}"
        f"（共識分數 {r.get('committee_score', '?')}、信心 {r.get('confidence', '?')}）",
        f"票數：買進 {r.get('buy_votes', 0)}　續抱 {r.get('hold_votes', 0)}"
        f"　觀望 {r.get('watch_votes', 0)}　賣出 {r.get('sell_votes', 0)}",
        "各委員會投票：",
    ]
    for key, name in _COMMITTEE_NAMES.items():
        v = votes.get(key)
        if not v:
            continue
        vote_label = _VOTE_LABEL.get(v.get("vote"), v.get("vote", "?"))
        reason = (v.get("reasons") or [""])[0]
        lines.append(
            f"  ・{name}委員會：{vote_label}（信心 {v.get('confidence', '?')}）"
            + (f" — {reason}" if reason else "")
        )

    maj = r.get("majority_reasons") or []
    mino = r.get("minority_reasons") or []
    if maj:
        lines.append("多數理由：" + "；".join(str(x) for x in maj[:3]))
    if mino:
        lines.append("少數異見：" + "；".join(str(x) for x in mino[:2]))
    if r.get("override_notes"):
        lines.append("否決/調整：" + "；".join(str(x) for x in r["override_notes"][:2]))
    lines.append("⚠️ 機械式彙整，僅供參考、不構成投資建議。")
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
        "name": "full_analysis",
        "description": (
            "共識決策：一次彙整大盤、機構動能總評、AI 投資委員會、相對強度四個獨立"
            "視角，只有多數一致時才給高信念訊號。這是最全面的一鍵總結——當使用者問"
            "「綜合來看該不該買」「給我最全面的判斷」「現在進場勝算高嗎」時，優先用這個。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號，例如 'NVDA'、'2330.TW'。"},
                "risk_profile": {
                    "type": "string",
                    "enum": ["conservative", "balanced", "aggressive"],
                    "description": "風險偏好，預設 balanced。",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "committee_vote",
        "description": (
            "AI 投資委員會：5 個獨立委員會（技術、風險、市場、機構資金流、投組）"
            "各自對該股投票 買進/續抱/觀望/賣出，再彙整成最終決議，並列出各委員會"
            "的票與理由。當使用者想看「不同角度怎麼看這檔」「委員會怎麼投」時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號，例如 'NVDA'、'2330.TW'。"},
                "risk_profile": {
                    "type": "string",
                    "enum": ["conservative", "balanced", "aggressive"],
                    "description": "風險偏好，預設 balanced。",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "institutional_score",
        "description": (
            "7 模組機構級動能總評（趨勢、量能、相對強度、催化、估值、風險），"
            "加權算出總分與評級(A+~D)、訊號狀態、明確行動(買進/續抱/觀望/減碼/出場)、"
            "停損、目標區、部位建議。這是 Scott 系統最完整的決策引擎"
            "（與網頁版 /api/momentum-score 同源）。當使用者想要某檔的「綜合總評/"
            "最終判斷/該買該賣」時，優先用這個。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號，例如 'NVDA'、'2330.TW'。"},
                "risk_profile": {
                    "type": "string",
                    "enum": ["conservative", "balanced", "aggressive"],
                    "description": "風險偏好，預設 balanced。",
                },
                "period": {"type": "string", "description": "資料期間，預設 '1y'。"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "momentum_analysis",
        "description": (
            "整合趨勢、相對強度（對大盤 SPY 比較）、量能三大引擎，給一份動能分析："
            "綜合動能分數、是否強於大盤、動能百分位、是否爆量等。"
            "當使用者問某檔的動能、強弱、有沒有領先大盤、是不是強勢股時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號，例如 'NVDA'、'2330.TW'。"},
                "period": {"type": "string", "description": "資料期間，預設 '6mo'。"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "regime_strategy",
        "description": (
            "先判斷目前大盤是多是空，自動選用適合該盤勢的策略類型（多頭用趨勢/動能、"
            "盤整與空頭用均值回歸/防禦），在該股挑出最佳策略並附上目前訊號。"
            "當使用者問「現在這盤勢該用什麼策略操作這檔」時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號，例如 'NVDA'、'2330.TW'。"},
                "period": {"type": "string", "description": "資料期間，預設 '1y'。"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "compare_strategies",
        "description": (
            "對一檔股票跑遍全部 8 種策略並排名（依風險調整後報酬），"
            "找出歷史上表現最好的策略。當使用者問「哪個策略最會賺/最適合這檔」時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號，例如 'NVDA'、'2330.TW'。"},
                "period": {"type": "string", "description": "資料期間，預設 '2y'。"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "validate_strategy",
        "description": (
            "用 walk-forward 前進測試 + 蒙地卡羅模擬驗證某策略是否穩健（避免過擬合、"
            "判斷是不是只是過去剛好有效）。當使用者想確認一個策略可不可靠、會不會賺時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號。"},
                "strategy": {
                    "type": "string",
                    "enum": _STRATEGIES,
                    "description": "要驗證的策略，預設 decision_core_v3。",
                },
                "period": {"type": "string", "description": "資料期間，預設 '5y'（驗證需長歷史）。"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "trade_plan",
        "description": (
            "整合大盤狀態、個股技術訊號、歷史最佳策略與部位大小，給一份完整的"
            "進出場建議（進場價、停損、目標、建議股數）。當使用者問「這檔該怎麼操作/"
            "該買多少/完整建議」時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "股票代號。"},
                "account_size": {
                    "type": "number",
                    "description": "帳戶本金，用來算部位大小，預設 100000。",
                },
                "risk_pct": {
                    "type": "number",
                    "description": "單筆交易願意承受的風險百分比，預設 1（即 1%）。",
                },
                "period": {"type": "string", "description": "資料期間，預設 '6mo'。"},
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
    "get_market_state": get_market_state,
    "analyze_signals": analyze_signals,
    "compare_stocks": compare_stocks,
    "scan_stocks": scan_stocks,
    "full_analysis": full_analysis,
    "committee_vote": committee_vote,
    "institutional_score": institutional_score,
    "momentum_analysis": momentum_analysis,
    "regime_strategy": regime_strategy,
    "compare_strategies": compare_strategies,
    "validate_strategy": validate_strategy,
    "trade_plan": trade_plan,
    "backtest_strategy": backtest_strategy,
}

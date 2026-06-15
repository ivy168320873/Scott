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

    s = _fetch_series(symbol, period, dp, demo)
    if not s or not s.get("closes"):
        return f"錯誤：無法取得 {symbol} 的資料。"

    closes = s["closes"]
    if len(closes) < 60:
        return f"錯誤：{symbol} 的資料太少（{len(closes)} 筆），無法計算指標。"

    # 用 backtest 既有的指標計算函式。
    rsi = bt._rsi(closes)
    macd_line, macd_sig, macd_hist = bt._macd(closes)
    bb_upper, bb_mid, bb_lower, bb_pct = bt._bb(closes)
    price = closes[-1]

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
        return f"錯誤：訊號計算失敗：{e}"

    lv = r.get("levels", {})
    demo_note = "（示範資料，非即時）" if s.get("is_demo") else ""
    lines = [
        f"{symbol.upper()} ｜ 技術訊號：{r.get('signal', '?')}"
        f"（匯流分數 {r.get('confluence', 0)}/100）{demo_note}".rstrip(),
        f"收盤：{price:.2f}　RSI：{_fmt(_last(rsi))}　MACD柱：{_fmt(_last(macd_hist), 4)}",
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


def _fmt(value, digits: int = 1) -> str:
    """把可能為 None 的數值格式化成字串。"""
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


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
    "backtest_strategy": backtest_strategy,
}

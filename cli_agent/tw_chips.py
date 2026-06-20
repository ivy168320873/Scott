"""台股真實籌碼資料：三大法人買賣超、融資融券（TWSE 免費開放資料）。

⚠️ 重要：
  - 僅支援台股「上市」(.TW)。上櫃 (.TWO) 走的是 TPEX 另一組 API，暫不支援。
  - 資料為「收盤後」更新（非即時），且為最近一個有資料的交易日。
  - TWSE 的 JSON 欄位格式偶爾調整；本模組以「欄位名稱關鍵字」比對來容錯，
    但仍需在有網路的環境（如 Railway）實測確認。
"""

from __future__ import annotations

import datetime as _dt

_TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"          # 三大法人買賣超
_TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"  # 融資融券
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ScottAgent/1.0)"}
_TIMEOUT = 8


def _is_twse(symbol: str) -> tuple[bool, str]:
    """判斷是否為台股上市，回傳 (是否支援, 數字代號)。"""
    s = symbol.upper().strip()
    if s.endswith(".TWO"):
        return False, s[:-4]
    if s.endswith(".TW"):
        return True, s[:-3]
    return False, s


def _recent_trading_dates(n: int = 6):
    """產生最近 n 個工作日的 YYYYMMDD（往回走，跳過週末）。"""
    d = _dt.date.today()
    out = []
    while len(out) < n:
        if d.weekday() < 5:  # 一~五
            out.append(d.strftime("%Y%m%d"))
        d -= _dt.timedelta(days=1)
    return out


def _get_json(url: str, params: dict):
    try:
        import requests
    except ImportError:
        return None
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:  # noqa: BLE001 — 網路/解析失敗都當作無資料
        return None


def _extract_table(j: dict):
    """從 TWSE 回應取出 (fields, data)，相容 {fields,data} 與 {tables:[...]} 兩種格式。"""
    if not isinstance(j, dict):
        return None, None
    if j.get("data") and j.get("fields"):
        return j["fields"], j["data"]
    for t in j.get("tables", []) or []:
        if t.get("data") and t.get("fields"):
            return t["fields"], t["data"]
    return None, None


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").replace(" ", "").strip()
    if s in ("", "--", "---", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _col(fields: list, row: list, *keywords: str):
    """回傳 row 中、欄位名稱同時包含所有 keyword 的第一個數值。"""
    for i, f in enumerate(fields):
        name = str(f)
        if all(k in name for k in keywords) and i < len(row):
            return _num(row[i])
    return None


def _lots(shares) -> str:
    """股 → 張（/1000），帶正負號。"""
    if shares is None:
        return "—"
    lots = shares / 1000
    return f"{lots:+,.0f} 張"


def tw_institutional(symbol: str) -> str:
    """查台股某檔的三大法人買賣超（外資/投信/自營商），收盤後資料。"""
    ok, code = _is_twse(symbol)
    if not ok:
        return f"錯誤：此工具僅支援台股上市(.TW)。'{symbol}' 不支援（上櫃.TWO 暫不支援）。"

    for date in _recent_trading_dates():
        j = _get_json(
            _TWSE_T86,
            {"date": date, "selectType": "ALLBUT0999", "response": "json"},
        )
        if not j or j.get("stat") != "OK":
            continue
        fields, data = _extract_table(j)
        if not fields or not data:
            continue
        row = next((r for r in data if str(r[0]).strip() == code), None)
        if not row:
            continue  # 當天有資料但找不到此股 → 試前一天（可能停牌）

        foreign = _col(fields, row, "外", "買賣超")
        trust = _col(fields, row, "投信", "買賣超")
        dealer = _col(fields, row, "自營商買賣超股數")
        total = _col(fields, row, "三大法人買賣超")
        if total is None and None not in (foreign, trust, dealer):
            total = foreign + trust + dealer

        dstr = f"{date[:4]}/{date[4:6]}/{date[6:]}"
        net_word = "淨買超" if (total or 0) > 0 else "淨賣超" if (total or 0) < 0 else "持平"
        return (
            f"🏦 台股三大法人買賣超 — {symbol.upper()}（{dstr}）\n"
            f"外資：{_lots(foreign)}　投信：{_lots(trust)}　自營商：{_lots(dealer)}\n"
            f"三大法人合計：{_lots(total)}（{net_word}）\n"
            f"（單位:張，正=買超、負=賣超；資料來源:TWSE 開放資料，收盤後更新）"
        )

    return f"查不到 {symbol.upper()} 近期的三大法人資料（可能停牌、或 TWSE 暫無資料）。"


def tw_margin(symbol: str) -> str:
    """查台股某檔的融資融券餘額與較前日變化，收盤後資料。"""
    ok, code = _is_twse(symbol)
    if not ok:
        return f"錯誤：此工具僅支援台股上市(.TW)。'{symbol}' 不支援（上櫃.TWO 暫不支援）。"

    for date in _recent_trading_dates():
        j = _get_json(
            _TWSE_MARGIN,
            {"date": date, "selectType": "ALL", "response": "json"},
        )
        if not j or j.get("stat") != "OK":
            continue
        fields, data = _extract_table(j)
        if not fields or not data:
            continue
        row = next((r for r in data if str(r[0]).strip() == code), None)
        if not row:
            continue

        margin_today = _col(fields, row, "融資", "今日餘額")
        margin_prev = _col(fields, row, "融資", "前日餘額")
        short_today = _col(fields, row, "融券", "今日餘額")
        short_prev = _col(fields, row, "融券", "前日餘額")

        def _bal(today, prev):
            if today is None:
                return "—"
            if prev is not None:
                return f"{today:,.0f} 張（較前日 {today - prev:+,.0f}）"
            return f"{today:,.0f} 張"

        dstr = f"{date[:4]}/{date[4:6]}/{date[6:]}"
        return (
            f"💰 台股融資融券 — {symbol.upper()}（{dstr}）\n"
            f"融資餘額：{_bal(margin_today, margin_prev)}\n"
            f"融券餘額：{_bal(short_today, short_prev)}\n"
            f"（融資增=散戶看多加碼；融券增=看空力道。資料來源:TWSE，收盤後更新）"
        )

    return f"查不到 {symbol.upper()} 近期的融資融券資料。"


TW_TOOL_SCHEMAS = [
    {
        "name": "tw_institutional",
        "description": (
            "查台股某檔的『真實』三大法人買賣超（外資、投信、自營商淨買賣，單位張），"
            "資料來自 TWSE 開放資料、收盤後更新。僅支援台股上市(.TW)。"
            "當使用者問台股的法人買賣超、外資投信動向、籌碼面時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "台股上市代號，例如 '2330.TW'。"}
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "tw_margin",
        "description": (
            "查台股某檔的『真實』融資融券餘額與較前日變化（散戶籌碼），"
            "資料來自 TWSE 開放資料、收盤後更新。僅支援台股上市(.TW)。"
            "當使用者問台股的融資融券、散戶籌碼、券資比時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "台股上市代號，例如 '2330.TW'。"}
            },
            "required": ["symbol"],
        },
    },
]

TW_TOOL_FUNCTIONS = {
    "tw_institutional": tw_institutional,
    "tw_margin": tw_margin,
}

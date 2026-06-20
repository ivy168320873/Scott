"""
Symbol-to-sector mapping for auto-injecting Sector Leadership into api_analyze.
Used by Phase 2.5: api_analyze automatically detects a symbol's sector and
fetches Sector Leadership data without requiring the frontend to pre-supply it.
"""
from __future__ import annotations

# sector name → canonical list of peer symbols（每個清單以最具代表性／流動性高者排前面，
# sector_rotation 會取前幾檔做產業廣度抽樣）。每檔只歸一個產業，避免反查表衝突。
SECTOR_SYMBOLS: dict[str, list[str]] = {
    # ── 美股 ──────────────────────────────────────────────────────────────
    "半導體AI晶片": [
        "NVDA", "AVGO", "AMD", "TSM", "MU", "MRVL", "ARM", "QCOM", "TXN",
        "ADI", "AMAT", "LRCX", "KLAC", "ASML", "MCHP", "ON", "SMCI", "AMKR", "FORM",
    ],
    "AI雲端軟體": [
        "MSFT", "ORCL", "CRM", "NOW", "ADBE", "PLTR", "SNOW", "DDOG", "NET",
        "MDB", "PATH", "WDAY", "TEAM", "HUBS",
    ],
    "網路安全": ["PANW", "CRWD", "FTNT", "ZS", "OKTA", "S", "CYBR"],
    "大型科技平台": ["AAPL", "AMZN", "GOOGL", "META", "NFLX"],
    "電動車汽車": ["TSLA", "GM", "F", "RIVN", "LCID", "LI", "XPEV", "NIO"],
    "太空航太": ["RKLB", "LUNR", "ASTS", "RDW", "SPCE"],
    "國防軍工": ["RTX", "LMT", "NOC", "GD", "LHX", "BA", "HII", "KTOS", "AVAV"],
    "潔淨能源核能": [
        "VST", "CEG", "NEE", "GEV", "FSLR", "ENPH", "FLNC", "RUN", "SMR", "OKLO", "BE", "NXT",
    ],
    "生技醫療": [
        "LLY", "NVO", "UNH", "ABBV", "MRK", "PFE", "AMGN", "ISRG",
        "VRTX", "REGN", "GILD", "BMY", "MRNA",
    ],
    "金融銀行": ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP"],
    "消費零售": ["COST", "WMT", "HD", "MCD", "NKE", "SBUX", "TGT", "LULU", "LOW", "DIS"],
    "工業機械": ["CAT", "DE", "HON", "GE", "UNP", "EMR", "ETN", "PH"],
    "加密區塊鏈": ["COIN", "MSTR", "MARA", "RIOT", "HOOD", "CLSK"],
    "中概股": ["BABA", "PDD", "JD", "BIDU", "TCOM", "BILI"],
    # ── 台股 ──────────────────────────────────────────────────────────────
    "台股半導體": [
        "2330.TW", "2454.TW", "2303.TW", "3034.TW", "2379.TW",
        "3443.TW", "3035.TW", "5269.TW", "6415.TW",
    ],
    "台股AI伺服器EMS": [
        "2317.TW", "2382.TW", "2376.TW", "2377.TW", "3231.TW",
        "4938.TW", "3017.TW", "2356.TW", "2354.TW",
    ],
    "台股PCB載板": ["3037.TW", "2368.TW", "8046.TW", "3044.TW", "6269.TW"],
    "台股金融": ["2881.TW", "2882.TW", "2891.TW", "2886.TW", "2884.TW", "2885.TW"],
    "台股航運": ["2603.TW", "2609.TW", "2615.TW", "2610.TW"],
    "台股電力綠能": ["1519.TW", "1503.TW", "1605.TW", "1513.TW"],
}

# Derived reverse mapping: symbol (uppercase) → sector name
SYMBOL_TO_SECTOR: dict[str, str] = {
    sym: sector
    for sector, syms in SECTOR_SYMBOLS.items()
    for sym in syms
}


def get_sector(symbol: str) -> str | None:
    """Return the sector name for a symbol, or None if unknown."""
    return SYMBOL_TO_SECTOR.get(symbol.upper().strip())


def get_sector_symbols(sector: str) -> list[str]:
    """Return the canonical peer list for a sector (empty list if unknown)."""
    return SECTOR_SYMBOLS.get(sector, [])

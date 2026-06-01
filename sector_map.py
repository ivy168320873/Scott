"""
Symbol-to-sector mapping for auto-injecting Sector Leadership into api_analyze.
Used by Phase 2.5: api_analyze automatically detects a symbol's sector and
fetches Sector Leadership data without requiring the frontend to pre-supply it.
"""
from __future__ import annotations

# sector name → canonical list of peer symbols
SECTOR_SYMBOLS: dict[str, list[str]] = {
    "半導體AI晶片":        ["NVDA", "AMD", "AVGO", "MRVL", "ARM", "MU", "AMKR", "FORM", "AXTI"],
    "AI雲端軟體/網路安全": ["MSFT", "SNOW", "PLTR", "CRM", "NOW", "PATH", "OKTA"],
    "太空航太":            ["LUNR", "RKLB", "RDW", "SPCE", "SIDU"],
    "國防航太":            ["KTOS", "AVAV", "RTX", "LMT", "NOC"],
    "電力能源":            ["BE", "ON", "ENPH", "NEE", "SMR"],
    "台股半導體IC":        ["2330.TW", "2303.TW", "2379.TW", "3661.TW", "3443.TW"],
    "台股電子EMS":         ["2317.TW", "4938.TW", "2382.TW", "2356.TW"],
    "台股PCB零組件":       ["2368.TW", "3037.TW", "8046.TW"],
    "台股電力重電":        ["1519.TW", "1503.TW", "1605.TW"],
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

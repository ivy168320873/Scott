"""sector_map：產業對照表的一致性與查詢行為測試。

反查表若有同一檔股票被歸到兩個產業，get_sector 的結果會取決於 dict
建立順序，屬於難以察覺的資料錯誤，因此用測試把「每檔只歸一個產業」
這條註解中的約定固定下來。
"""
from __future__ import annotations

import pytest
import sector_map


def test_sector_symbols_not_empty():
    assert sector_map.SECTOR_SYMBOLS


def test_every_sector_has_symbols():
    for sector, symbols in sector_map.SECTOR_SYMBOLS.items():
        assert symbols, f"產業 {sector} 沒有任何成分股"


def test_no_symbol_belongs_to_two_sectors():
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for sector, symbols in sector_map.SECTOR_SYMBOLS.items():
        for symbol in symbols:
            if symbol in seen:
                duplicates.append(f"{symbol}（{seen[symbol]} / {sector}）")
            seen[symbol] = sector
    assert not duplicates, f"以下股票被歸到多個產業：{duplicates}"


def test_no_duplicate_symbol_within_a_sector():
    for sector, symbols in sector_map.SECTOR_SYMBOLS.items():
        assert len(symbols) == len(set(symbols)), f"產業 {sector} 有重複成分股"


def test_reverse_map_covers_every_symbol():
    total = sum(len(s) for s in sector_map.SECTOR_SYMBOLS.values())
    assert len(sector_map.SYMBOL_TO_SECTOR) == total


def test_symbols_are_uppercase():
    """反查表以 upper() 查詢，原始表若含小寫會永遠查不到。"""
    for symbol in sector_map.SYMBOL_TO_SECTOR:
        assert symbol == symbol.upper(), f"{symbol} 不是大寫"


# ── get_sector ────────────────────────────────────────────────────────────────

def test_get_sector_known_us_symbol():
    assert sector_map.get_sector("NVDA") == "半導體AI晶片"


def test_get_sector_known_tw_symbol():
    assert sector_map.get_sector("2330.TW") == "台股半導體"


@pytest.mark.parametrize("raw", ["nvda", "  NVDA  ", "Nvda", "\tnvda\n"])
def test_get_sector_normalises_case_and_whitespace(raw):
    assert sector_map.get_sector(raw) == "半導體AI晶片"


@pytest.mark.parametrize("unknown", ["ZZZZ", "", "   ", "9999.TW"])
def test_get_sector_unknown_returns_none(unknown):
    assert sector_map.get_sector(unknown) is None


@pytest.mark.parametrize("value", [None, 123, [], {}, object()])
def test_get_sector_non_string_returns_none(value):
    """回歸測試：非字串輸入曾拋 AttributeError（symbol 解析失敗時可能傳 None）。"""
    assert sector_map.get_sector(value) is None


# ── get_sector_symbols ────────────────────────────────────────────────────────

def test_get_sector_symbols_known():
    symbols = sector_map.get_sector_symbols("台股半導體")
    assert "2330.TW" in symbols


def test_get_sector_symbols_unknown_returns_empty_list():
    assert sector_map.get_sector_symbols("不存在的產業") == []


def test_get_sector_symbols_returns_copy_not_internal_list():
    """回歸測試：直接回傳模組級 list，呼叫端 append 會汙染全域對照表。"""
    original = list(sector_map.SECTOR_SYMBOLS["台股半導體"])
    returned = sector_map.get_sector_symbols("台股半導體")
    returned.append("POLLUTION")
    assert sector_map.SECTOR_SYMBOLS["台股半導體"] == original


def test_get_sector_symbols_round_trip():
    """任一產業的成分股反查回來必須是同一個產業。"""
    for sector in sector_map.SECTOR_SYMBOLS:
        for symbol in sector_map.get_sector_symbols(sector):
            assert sector_map.get_sector(symbol) == sector

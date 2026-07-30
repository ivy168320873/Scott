"""「缺成交量」資訊必須沿著正式資料路徑一路保留到引擎。

回歸背景：`has_volume` 原本只由「volumes 序列是否存在且對齊」推導，
但**所有**正式資料都先經過會把缺量補成 0 的正規化層
（`data_provider._normalize`、`decision_engine.normalize_list` /
`normalize_yahoo`），於是 `has_volume` 恆為 True，保護完全不觸發。
症狀：憑空產出「板塊整體量能萎縮（0%）」、「爆量長上影」訊號靜默消失。

因此這裡的輸入一律由**真正的正規化層**產生，不用手寫 dict——
手寫 dict 會繞過缺陷，讓測試給出假的「已修復」訊號。
"""
from __future__ import annotations

import data_provider
import decision_engine as de
import pytest
import risk_engine
import sector_engine
from ohlcv_utils import sanitize_ohlcv


def _rows(n: int = 25, *, volume=None) -> list[dict]:
    rows = []
    for i in range(n):
        row = {"close": 100.0 + i, "open": 99.0 + i, "high": 101.0 + i, "low": 98.0 + i}
        if volume is not None:
            row["volume"] = volume
        rows.append(row)
    return rows


# ── 正規化層必須輸出 has_volume ──────────────────────────────────────────────

def test_data_provider_marks_missing_volume():
    out = data_provider._normalize(_rows(), source="test")
    assert out["has_volume"] is False
    assert out["volumes"] == [0] * 25          # 仍補 0，但已標示為無資料


def test_data_provider_marks_present_volume():
    out = data_provider._normalize(_rows(volume=1_000_000), source="test")
    assert out["has_volume"] is True


def test_normalize_list_marks_missing_volume():
    assert de.normalize_list(_rows())["has_volume"] is False


def test_normalize_list_marks_present_volume():
    assert de.normalize_list(_rows(volume=500_000))["has_volume"] is True


def test_normalize_yahoo_marks_missing_volume():
    n = 5
    payload = {"chart": {"result": [{
        "timestamp": list(range(n)),
        "indicators": {"quote": [{
            "open": [1.0] * n, "high": [2.0] * n, "low": [0.5] * n,
            "close": [1.5] * n, "volume": [None] * n,
        }]},
    }]}}
    assert de.normalize_yahoo(payload)["has_volume"] is False


# ── sanitize_ohlcv 必須尊重上游判定，且自行識別全 0 量 ──────────────────────

def test_sanitize_preserves_upstream_missing_volume_flag():
    """回歸測試：正規化層補 0 後，sanitize 曾誤判 has_volume=True。"""
    normalized = data_provider._normalize(_rows(), source="test")
    assert sanitize_ohlcv(normalized)["has_volume"] is False


def test_sanitize_treats_all_zero_volume_as_missing():
    """整段視窗量全為 0 不是真實市場資料，而是缺值。"""
    out = sanitize_ohlcv({"closes": [10.0] * 25, "volumes": [0] * 25})
    assert out["has_volume"] is False


def test_sanitize_keeps_single_zero_volume_day_as_real_data():
    """單日 0 量（停牌）是合法資料，不得整條判為缺值。"""
    volumes = [1_000_000.0] * 24 + [0.0]
    out = sanitize_ohlcv({"closes": [10.0] * 25, "volumes": volumes})
    assert out["has_volume"] is True
    assert out["volumes"][-1] == 0.0


# ── 端對端：正式路徑不得憑空產出量能結論 ────────────────────────────────────

def test_sector_via_data_provider_does_not_claim_contraction():
    normalized = data_provider._normalize(_rows(), source="test")
    result = sector_engine.calc_sector_leadership("測試", {"A": normalized})
    assert "量能萎縮" not in result["warning_flags"]
    assert not any("量能萎縮" in reason for reason in result["reasons"])
    assert result["detail"]["vol_expand_ratio_pct"] is None


def test_sector_via_normalize_list_does_not_claim_contraction():
    normalized = de.normalize_list(_rows())
    result = sector_engine.calc_sector_leadership("測試", {"A": normalized})
    assert "量能萎縮" not in result["warning_flags"]


def test_risk_via_data_provider_reports_missing_volume_honestly():
    normalized = data_provider._normalize(_rows(30), source="test")
    result = risk_engine.calc_chase_risk(normalized)
    assert result["ok"] is True
    assert "爆量長上影" not in result["warning_flags"]
    assert any("缺成交量資料" in reason for reason in result["reasons"])


def test_real_volume_via_data_provider_still_scores_volume():
    """對照組：有真實量資料時，量能項必須恢復計分。"""
    rows = _rows(25, volume=1_000_000)
    rows[-1]["volume"] = 5_000_000          # 最後一根放量
    normalized = data_provider._normalize(rows, source="test")
    result = sector_engine.calc_sector_leadership("測試", {"A": normalized})
    assert result["detail"]["vol_expand_ratio_pct"] is not None


# ── 極小樣本不得產生高信心結論 ──────────────────────────────────────────────

def test_small_volume_sample_is_not_reported_as_full_ratio():
    """回歸測試：4 檔中僅 1 檔有量且放量，曾宣稱「100% 個股量能放大」。"""
    members = {}
    for i in range(3):
        members[f"NOVOL{i}"] = data_provider._normalize(_rows(), source="test")
    rows = _rows(25, volume=1_000_000)
    rows[-1]["volume"] = 9_000_000
    members["HASVOL"] = data_provider._normalize(rows, source="test")

    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["detail"]["vol_expand_ratio_pct"] is None
    assert not any("100% 個股量能放大" in reason for reason in result["reasons"])
    assert any("有成交量資料" in reason for reason in result["reasons"])


def test_sufficient_volume_sample_reports_ratio_with_sample_size():
    members = {}
    for i in range(4):
        rows = _rows(25, volume=1_000_000)
        rows[-1]["volume"] = 9_000_000
        members[f"S{i}"] = data_provider._normalize(rows, source="test")
    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["detail"]["vol_expand_ratio_pct"] == pytest.approx(100.0)
    assert any("4/4 檔" in reason for reason in result["reasons"])

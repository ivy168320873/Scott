"""daily_report_engine：持倉摘要對 holding_days=None 的容忍度。

回歸背景：`portfolio_engine` 改為不假造 `holding_days`（缺漏時回 None）後，
本模組第 314 行仍寫 `.get("holding_days", 30)`。因為鍵存在、值為 None，
default 不會套用，於是 `calc_drag_score` 拋 TypeError，再被裸 `except`
吞掉，導致該筆持倉被**整筆靜默移出每日報告**——而修改前它是正常顯示的。

這裡以純記憶體的 fake ohlcv_fn 驅動，不觸碰資料庫也不連網。
"""
from __future__ import annotations

import daily_report_engine as dre
import pytest
from conftest import flat_series, make_ohlcv


def _ohlcv_fn(_symbol):
    return make_ohlcv(flat_series(25, 110.0))


def _position(symbol: str, **kwargs) -> dict:
    base = {"symbol": symbol, "cost": 100.0, "qty": 10.0, "buy_date": "2020-01-01"}
    base.update(kwargs)
    return base


def test_empty_positions_returns_zeroed_summary():
    summary = dre._build_portfolio_summary([], _ohlcv_fn)
    assert summary["total"] == 0
    assert summary["efficiency_score_avg"] is None
    assert summary["position_details"] == []


def test_normal_position_is_analysed():
    summary = dre._build_portfolio_summary([_position("GOOD")], _ohlcv_fn)
    detail = summary["position_details"][0]
    assert detail["ok"] is True
    assert detail["score"] is not None
    assert summary["efficiency_score_avg"] is not None


@pytest.mark.parametrize("buy_date", ["", "2024/01/01", "not-a-date", None])
def test_position_without_usable_buy_date_stays_in_report(buy_date):
    """回歸測試：buy_date 缺漏／格式錯誤的持倉不得從報告中消失。

    這些持倉在修復前會因 TypeError 被吞掉而變成 ok=False。
    """
    summary = dre._build_portfolio_summary(
        [_position("NODATE", buy_date=buy_date)], _ohlcv_fn)
    detail = summary["position_details"][0]
    assert detail["ok"] is True, "buy_date 缺漏不應讓持倉被丟出報告"
    assert detail["score"] is not None
    assert detail["symbol"] == "NODATE"


def test_missing_buy_date_still_counted_in_averages():
    """缺 buy_date 的持倉必須計入平均分，否則平均值會被扭曲。"""
    positions = [_position("A"), _position("B", buy_date="")]
    summary = dre._build_portfolio_summary(positions, _ohlcv_fn)
    assert summary["total"] == 2
    assert len(summary["position_details"]) == 2
    assert all(d["ok"] for d in summary["position_details"])
    assert summary["efficiency_score_avg"] is not None
    assert summary["drag_score_avg"] is not None


def test_position_with_no_ohlcv_is_marked_not_ok():
    """真正取不到 K 線時才標記 ok=False（與型別錯誤造成的失敗要能區分）。"""
    summary = dre._build_portfolio_summary([_position("EMPTY")], lambda _s: None)
    detail = summary["position_details"][0]
    assert detail["ok"] is False
    assert detail["score"] is None


def test_analysis_failure_is_logged_not_swallowed(monkeypatch, caplog):
    """裸 except 不得靜默吞錯——必須留下可追蹤的日誌。"""
    def _boom(*args, **kwargs):
        raise RuntimeError("模擬引擎失敗")

    monkeypatch.setattr(dre._pe, "calc_capital_efficiency", _boom)
    with caplog.at_level("ERROR"):
        summary = dre._build_portfolio_summary([_position("BOOM")], _ohlcv_fn)

    assert summary["position_details"][0]["ok"] is False
    assert any("BOOM" in record.message or "BOOM" in record.getMessage()
               for record in caplog.records), "失敗未被記錄到日誌"

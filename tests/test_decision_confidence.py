from datetime import datetime, timedelta, timezone
from pathlib import Path

import decision_confidence_engine as dce
import signal_confidence_engine as sce
import top_tier_decision_engine as ttde


NOW = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


def _inputs(**overrides):
    values = {
        "symbol": "NVDA",
        "decision": "BUY",
        "composite_score": 84,
        "data_quality": {
            "data_quality_score": 95,
            "data_status": "OK",
            "source": "yahoo_api",
            "is_demo": False,
            "bar_count": 220,
            "last_price": 150,
            "last_date": "2026-08-01",
        },
        "market_regime": {"market_regime": "RISK_ON", "market_score": 90},
        "chase_risk": {"score": 20},
        "position_sizing": {
            "position_size_level": "NORMAL",
            "stop_loss_price": 138.5,
        },
        "signal_calibration": {
            "confidence_score": 86,
            "sample_size": 30,
            "evaluated_size": 25,
            "win_rate_1d": 64.0,
            "win_rate_3d": 68.0,
            "win_rate_5d": 72.0,
            "avg_return_5d": 3.2,
            "avg_relative_return_5d": 1.4,
            "false_signal_rate": 20.0,
            "stop_loss_rate": 8.0,
            "recommendation": "TRUST",
        },
        "kill_signal": {"triggered": False},
        "blockers": [],
        "risk_controls": ["單筆風險控制在 1%"],
        "now": NOW,
    }
    values.update(overrides)
    return values


def test_high_quality_fresh_buy_has_strong_explainable_score():
    card = dce.build_confidence_card(**_inputs())

    assert card["confidence_score"] >= 85
    assert card["grade"] == "A"
    assert len(card["evidence"]) == 6
    assert "非勝率" in card["score_meaning"]
    assert card["data_freshness"]["status"] == "FRESH"
    assert any("停損價 138.50" in item for item in card["invalidation_conditions"])


def test_stale_data_hard_caps_confidence():
    inputs = _inputs()
    inputs["data_quality"] = dict(inputs["data_quality"], last_date="2026-07-20")
    card = dce.build_confidence_card(**inputs)

    assert card["confidence_score"] <= 45
    assert card["score_cap"] == 45
    assert any("未更新" in item for item in card["caps_applied"])


def test_demo_data_is_blocked_even_when_other_signals_are_strong():
    inputs = _inputs()
    inputs["data_quality"] = dict(
        inputs["data_quality"],
        is_demo=True,
        data_status="DEMO",
        data_quality_score=20,
        source="demo",
    )
    card = dce.build_confidence_card(**inputs)

    assert card["confidence_score"] <= 25
    assert card["level"] == "BLOCKED"
    assert any("Demo" in item for item in card["caps_applied"])


def test_no_completed_history_is_honestly_capped():
    inputs = _inputs(signal_calibration={
        "confidence_score": 50,
        "sample_size": 1,
        "evaluated_size": 0,
        "recommendation": "WATCH",
    })
    card = dce.build_confidence_card(**inputs)

    assert card["confidence_score"] <= 75
    assert card["historical_calibration"]["evaluated_size"] == 0
    assert any("5 日驗證" in item for item in card["caps_applied"])


def test_defensive_sell_can_be_well_supported_in_risk_off_market():
    card = dce.build_confidence_card(**_inputs(
        decision="SELL",
        composite_score=22,
        market_regime={"market_regime": "RISK_OFF", "market_score": 25},
        chase_risk={"score": 88},
        position_sizing={"position_size_level": "NO_TRADE"},
        kill_signal={"triggered": True},
    ))

    market = next(item for item in card["evidence"] if item["id"] == "market_agreement")
    assert market["score"] == 90
    assert card["confidence_score"] >= 80
    assert any("RISK_ON" in item for item in card["invalidation_conditions"])


def test_signal_recording_deduplicates_and_backfills_outcomes(tmp_path, monkeypatch):
    db_path = tmp_path / "signals.db"
    monkeypatch.setattr(sce, "_DB_PATH", str(db_path))
    sce.init_db(str(db_path))
    payload = {
        "symbol": "TEST",
        "signal_date": "2026-07-01",
        "decision": "BUY",
        "entry_price": 100,
        "is_demo": False,
    }

    first = sce.record_signal_once(payload)
    second = sce.record_signal_once(payload)
    assert first["recorded"] is True
    assert second["duplicate"] is True
    assert first["id"] == second["id"]

    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    subject = {
        "timestamps": [int((start + timedelta(days=i)).timestamp()) for i in range(7)],
        "closes": [100, 101, 102, 103, 104, 105, 106],
        "is_demo": False,
    }
    benchmark = {
        "timestamps": subject["timestamps"],
        "closes": [200, 201, 202, 203, 204, 205, 206],
        "is_demo": False,
    }
    result = sce.update_symbol_outcomes("TEST", subject, benchmark)
    row = sce.get_signal_history(limit=1)[0]

    assert result["updated"] == 1
    assert row["price_1d"] == 101
    assert row["price_3d"] == 103
    assert row["price_5d"] == 105
    assert row["return_5d"] == 5.0
    assert row["benchmark_return_5d"] == 2.5
    assert row["was_correct"] == 1

    sce.record_signal({
        "symbol": "DEMO",
        "signal_date": "2026-07-01",
        "decision": "BUY",
        "entry_price": 100,
        "is_demo": True,
    })
    assert sce.get_confidence_stats("BUY")["sample_size"] == 1


def test_top_tier_engine_always_returns_confidence_card(monkeypatch):
    monkeypatch.setattr(ttde, "_HAS_SCE", False)
    end = datetime.now(timezone.utc)
    closes = [100 + i * 0.25 for i in range(220)]
    ohlcv = {
        "closes": closes,
        "opens": [price - 0.2 for price in closes],
        "highs": [price + 0.8 for price in closes],
        "lows": [price - 0.8 for price in closes],
        "volumes": [1_000_000 + i * 100 for i in range(220)],
        "timestamps": [int((end - timedelta(days=219 - i)).timestamp()) for i in range(220)],
        "is_demo": False,
        "source": "test",
    }

    result = ttde.run_top_tier_decision("TEST", lambda _symbol: ohlcv)

    assert result["ok"] is True
    assert result["confidence_card"]["symbol"] == "TEST"
    assert result["confidence_card"]["decision"] == result["decision"]
    assert len(result["confidence_card"]["evidence"]) == 6


def test_mobile_template_contains_automatic_confidence_card_contract():
    template = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text()

    assert "function _renderConfidenceCard(c)" in template
    assert "runTopTierDecision({auto: true" in template
    assert "aria-label=\"信號可信度評分卡\"" in template
    assert "@media(max-width:519px)" in template

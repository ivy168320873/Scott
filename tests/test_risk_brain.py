import pytest

from scott_evolution.risk_brain import assess_trade, get_profile, save_profile


def _proposal(**patch):
    value = {
        "symbol": "NVDA",
        "decision": "BUY",
        "confidence_score": 80,
        "data_quality_status": "OK",
        "entry_price": 100,
        "stop_price": 95,
        "target_price": 110,
        "requested_position_pct": 12,
    }
    value.update(patch)
    return value


def test_profile_persists_and_reduces_oversized_trade(tmp_path):
    db = str(tmp_path / "risk.db")
    profile = save_profile({"preset": "balanced", "account_size": 100_000}, db)
    assert get_profile(db)["account_size"] == 100_000
    gate = assess_trade(_proposal(), profile, {"account_value": 100_000})
    assert gate["allowed"] is True
    assert gate["status"] == "REDUCE"
    assert gate["max_shares"] == 100
    assert gate["recommended_position_pct"] == 10


def test_risk_brain_blocks_demo_and_daily_kill_switch(tmp_path):
    profile = save_profile({"preset": "aggressive"}, str(tmp_path / "risk.db"))
    demo = assess_trade(_proposal(is_demo=True), profile, {"account_value": 100_000})
    assert demo["allowed"] is False
    assert any("Demo" in reason for reason in demo["blockers"])
    killed = assess_trade(
        _proposal(), profile, {"account_value": 100_000, "daily_pnl_pct": -5}
    )
    assert killed["allowed"] is False
    assert any("停機線" in reason for reason in killed["blockers"])


def test_profile_rejects_inconsistent_limits(tmp_path):
    with pytest.raises(ValueError):
        save_profile(
            {"preset": "custom", "max_position_pct": 30, "max_sector_pct": 20},
            str(tmp_path / "risk.db"),
        )

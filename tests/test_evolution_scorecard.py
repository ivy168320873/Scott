from scott_evolution.scorecard import build_scorecard


def _evidence(demo=False):
    return [
        {"category": name, "source": "unit", "statement": f"{name} checked", "is_demo": demo}
        for name in ("price", "trend", "volume", "risk", "market")
    ]


def test_full_evidence_and_calibration_can_be_actionable():
    card = build_scorecard(
        {
            "symbol": "NVDA",
            "decision": "BUY",
            "top_tier_score": 86,
            "data_quality_status": "OK",
            "data_quality_score": 95,
            "market_regime": "RISK_ON",
            "chase_risk_score": 25,
            "evidence": _evidence(),
        },
        {
            "outcome_sample_size": 30,
            "paper_win_rate": 70,
            "confidence_score": 78,
            "recommendation": "TRUST",
        },
    )
    assert card["actionable"] is True
    assert card["evidence_coverage"] == 1
    assert card["confidence_score"] >= 68
    assert card["historical"]["wilson_lower_bound_95"] is not None


def test_missing_or_demo_evidence_hard_caps_score():
    missing = build_scorecard(
        {"symbol": "NVDA", "decision": "BUY", "top_tier_score": 99, "evidence": []},
        {"outcome_sample_size": 50, "paper_win_rate": 90, "confidence_score": 90},
    )
    assert missing["actionable"] is False
    assert "必要證據覆蓋不足 60%" in missing["blockers"]
    demo = build_scorecard(
        {
            "symbol": "NVDA",
            "decision": "BUY",
            "top_tier_score": 99,
            "evidence": _evidence(demo=True),
        },
        {"outcome_sample_size": 50, "paper_win_rate": 90, "confidence_score": 90},
    )
    assert demo["confidence_score"] <= 25
    assert demo["actionable"] is False

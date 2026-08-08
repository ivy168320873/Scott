import signal_confidence_engine as confidence


def test_paper_outcome_updates_calibration_without_pretending_it_is_five_day(tmp_path):
    db = str(tmp_path / "confidence.db")
    confidence.init_db(db)
    record = confidence.record_signal(
        {"symbol": "NVDA", "decision": "BUY", "entry_price": 100}
    )
    result = confidence.update_paper_outcome(
        record["id"],
        {
            "paper_trade_id": "paper-1",
            "paper_return_pct": 8,
            "paper_r_multiple": 1.6,
            "paper_exit_reason": "TARGET",
        },
    )
    assert result["ok"] is True
    stats = confidence.get_confidence_stats("BUY")
    assert stats["outcome_sample_size"] == 1
    assert stats["paper_win_rate"] == 100
    assert stats["win_rate_5d"] is None
    assert stats["confidence_score"] <= 40

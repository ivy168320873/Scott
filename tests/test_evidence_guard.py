from scott_evolution.evidence import (
    enforce_market_evidence,
    requires_market_evidence,
    tool_evidence,
)


def test_market_question_without_tool_result_is_blocked():
    assert requires_market_evidence("NVDA 現在可以買嗎？") is True
    guarded = enforce_market_evidence("NVDA 現在可以買嗎？", "可以買", [])
    assert guarded["blocked"] is True
    assert "沒有取得可驗證" in guarded["reply"]


def test_successful_tool_result_is_attached_as_evidence():
    evidence = [tool_evidence("get_stock_price", {"symbol": "NVDA"}, "price=100")]
    guarded = enforce_market_evidence("NVDA 股價？", "目前 100", evidence)
    assert guarded["blocked"] is False
    assert "資料依據：" in guarded["reply"]
    assert "get_stock_price" in guarded["reply"]


def test_general_chat_does_not_require_market_tool():
    guarded = enforce_market_evidence("你好", "你好，有什麼需要？", [])
    assert guarded["required"] is False
    assert guarded["blocked"] is False

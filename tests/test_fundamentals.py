"""get_fundamentals 與格式化輔助的單元測試（不需網路）。"""

import stock_tools as st


# ── 格式化輔助 ────────────────────────────────────────────────────────────

def test_fmt_big_units():
    assert st._fmt_big(2.65e12) == "2.65兆"
    assert st._fmt_big(5.5e10) == "550.00億"
    assert st._fmt_big(3.2e6) == "3.2百萬"
    assert st._fmt_big(0) == "—"
    assert st._fmt_big(None) == "—"
    assert st._fmt_big("not a number") == "—"


def test_fmt_pct_ratio_vs_already_pct():
    assert st._fmt_pct(0.23) == "23.00%"
    assert st._fmt_pct(23, already_pct=True) == "23.00%"
    assert st._fmt_pct(None) == "—"


def test_to_float_tolerates_junk():
    assert st._to_float("12.5") == 12.5
    assert st._to_float(None) is None
    assert st._to_float("N/A") is None
    assert st._to_float("-") is None
    assert st._to_float("") is None


# ── 主流程（注入合成基本面，確定性）────────────────────────────────────────

_SAMPLE = {
    "name": "Test Corp", "sector": "Tech", "industry": "Software",
    "market_cap": 2.65e12, "pe_trailing": 80.0, "pe_forward": 55.0,
    "eps": 2.5, "peg": 1.8, "ps": 30.0, "revenue": 5.5e10,
    "profit_margin": 0.21, "gross_margin": 0.62,
    "wk52_high": 225.6, "wk52_low": 135.0, "dividend_yield": 0.0,
    "source": "yfinance", "margin_is_ratio": True,
}


def test_get_fundamentals_formats_sample(monkeypatch):
    monkeypatch.setattr(st, "_fundamentals_yfinance", lambda s: dict(_SAMPLE))
    out = st.get_fundamentals("TEST")
    assert "Test Corp" in out
    assert "本益比" in out and "EPS" in out
    assert "2.65兆" in out          # 市值格式化
    assert "21.00%" in out          # 淨利率比率 → 百分比
    assert "來源：yfinance" in out


def test_get_fundamentals_fallback_to_alpha_vantage(monkeypatch):
    monkeypatch.setattr(st, "_fundamentals_yfinance", lambda s: None)
    monkeypatch.setattr(
        st, "_fundamentals_alpha_vantage",
        lambda s: {**_SAMPLE, "source": "alpha_vantage"},
    )
    out = st.get_fundamentals("TEST")
    assert "來源：alpha_vantage" in out


def test_get_fundamentals_graceful_failure(monkeypatch):
    monkeypatch.setattr(st, "_fundamentals_yfinance", lambda s: None)
    monkeypatch.setattr(st, "_fundamentals_alpha_vantage", lambda s: None)
    out = st.get_fundamentals("NOPE")
    assert out.startswith("錯誤")

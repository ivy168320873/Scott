"""montecarlo_forecast 與相關輔助函式的單元測試（不需網路）。"""

import re

import stock_tools as st


# ── 輔助函式 ──────────────────────────────────────────────────────────────

def test_parse_horizons_comma_string():
    assert st._parse_horizons("5,21,63") == [5, 21, 63]


def test_parse_horizons_dedup_and_limit():
    # 去重、保序、且最多 6 個
    assert st._parse_horizons("5,5,21") == [5, 21]
    assert len(st._parse_horizons("1,2,3,4,5,6,7,8")) == 6


def test_parse_horizons_invalid_falls_back_to_default():
    assert st._parse_horizons("") == [5, 21, 63]
    assert st._parse_horizons("abc") == [5, 21, 63]
    # 超出範圍（>504）會被濾掉，全部無效則回預設
    assert st._parse_horizons("9999") == [5, 21, 63]


# ── 主流程（注入合成價格序列，確定性）──────────────────────────────────────

def _patch_series(monkeypatch, closes):
    monkeypatch.setattr(
        st, "_fetch_series",
        lambda symbol, period, dp, demo: {"closes": closes, "is_demo": False},
    )


def _median_from_output(text, horizon_label):
    m = re.search(rf"{re.escape(horizon_label)}.*?中位 ([\d.]+)", text)
    return float(m.group(1)) if m else None


def test_forecast_plain_gbm_structure(monkeypatch):
    closes = [100 + i * 0.1 for i in range(60)]
    _patch_series(monkeypatch, closes)
    out = st.montecarlo_forecast("TEST", horizons="21", simulations=5000)
    assert "蒙地卡羅預測（GBM" in out
    assert "上漲機率" in out
    assert "中位" in out
    # 純 GBM 不應出現跳躍標記
    assert "跳躍" not in out
    assert "⚠含事件" not in out


def test_forecast_too_few_points_errors(monkeypatch):
    _patch_series(monkeypatch, [100.0, 101.0])
    out = st.montecarlo_forecast("TEST", simulations=1000)
    assert out.startswith("錯誤")


def test_forecast_event_risk_lowers_post_event_median(monkeypatch):
    closes = [100.0 + i * 0.05 for i in range(80)]
    _patch_series(monkeypatch, closes)
    out = st.montecarlo_forecast(
        "TEST", horizons="63,126", simulations=20000,
        event_day=90, event_drop_pct=-0.20, event_prob=0.8,
    )
    assert "GBM+跳躍" in out
    assert "⚠含事件" in out
    # 事件日(90)之前的 T+63 不帶事件；之後的 T+126 帶事件，中位數應較低。
    pre = _median_from_output(out, "T+63")
    post = _median_from_output(out, "T+126")
    assert pre is not None and post is not None
    assert post < pre


def test_forecast_event_ignored_before_event_day(monkeypatch):
    closes = [100.0 + i * 0.05 for i in range(80)]
    _patch_series(monkeypatch, closes)
    out = st.montecarlo_forecast(
        "TEST", horizons="5", simulations=3000,
        event_day=90, event_drop_pct=-0.2, event_prob=0.8,
    )
    # 唯一天期(5)早於事件日(90)，該行不應被標記為含事件。
    assert "⚠含事件" not in out

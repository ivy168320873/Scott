"""
Stress Test Engine — Phase 9
Simulates market shocks on a portfolio and reports risk impact.

Public API
----------
generate_stress_test(portfolio, scenario, shock_pct, benchmark,
                     ohlcv_fn, watchlist=None) -> dict
save_result(result) -> int
get_latest() -> dict | None
get_history(limit=20) -> list[dict]
SCENARIOS: list[str]
init_db()
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
from datetime import datetime, date, timezone

import decision_engine  as _de
import alert_engine     as _ae
import portfolio_engine as _pe
import rotation_engine  as _re
import sector_map       as _smap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCENARIOS: list[str] = [
    'market_crash',
    'sector_rotation',
    'stop_loss_break',
    'kill_signal_trigger',
    'chase_failure',
    'capital_efficiency_decay',
    'liquidity_shock',
    'custom',
]

SCENARIO_LABELS: dict[str, str] = {
    'market_crash':              '大盤急跌',
    'sector_rotation':           '板塊主線轉弱',
    'stop_loss_break':           '個股跌破停損',
    'kill_signal_trigger':       'Kill Signal 觸發',
    'chase_failure':             '追高失敗',
    'capital_efficiency_decay':  '資金效率惡化',
    'liquidity_shock':           '流動性不足',
    'custom':                    '自訂壓力',
}

_DEFAULT_SHOCK: dict[str, float] = {
    'market_crash':             -5.0,
    'sector_rotation':          -4.0,
    'stop_loss_break':         -10.0,
    'kill_signal_trigger':      -8.0,
    'chase_failure':            -3.0,
    'capital_efficiency_decay': -15.0,
    'liquidity_shock':           0.0,
    'custom':                    0.0,
}

_DB_PATH = os.environ.get("USER_DATA_DB", "./user_data.db")
_LOCK = threading.Lock()

DISCLAIMER = "此為壓力測試與決策輔助，不代表自動下單。"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

class _LockConn:
    def __enter__(self):
        _LOCK.acquire()
        self._con = sqlite3.connect(_DB_PATH, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        return self._con

    def __exit__(self, *_):
        self._con.commit()
        self._con.close()
        _LOCK.release()


def _lock_conn() -> _LockConn:
    return _LockConn()


def init_db() -> None:
    """Create stress_test_results table if it doesn't exist."""
    with _lock_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS stress_test_results (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario      TEXT,
                generated_at  TEXT,
                shock_pct     REAL,
                stress_score  INTEGER,
                stress_level  TEXT,
                result_json   TEXT
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS str_scenario_ts "
            "ON stress_test_results(scenario, generated_at)"
        )


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------

def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


# ---------------------------------------------------------------------------
# Scenario shock logic
# ---------------------------------------------------------------------------

def _apply_scenario_shock(
    ohlcv: dict,
    scenario: str,
    shock_pct: float,
    position: dict,
) -> dict:
    """
    Return a deep copy of ohlcv with modifications based on the scenario.
    """
    o = copy.deepcopy(ohlcv)
    closes  = o.get("closes",  [])
    highs   = o.get("highs",   [])
    lows    = o.get("lows",    [])
    opens   = o.get("opens",   [])
    volumes = o.get("volumes", [])

    n = len(closes)
    if n == 0:
        return o

    factor = 1 + shock_pct / 100.0

    if scenario == 'market_crash':
        # Shock last bar
        closes[-1]  = closes[-1]  * factor
        if highs:  highs[-1]  = highs[-1]  * factor
        if lows:   lows[-1]   = lows[-1]   * factor
        # If shock < -3%, partially shock preceding 3 days (30–60% of shock)
        if shock_pct < -3 and n >= 4:
            partial_factors = [
                1 + shock_pct * 0.30 / 100.0,  # 3 bars back (lightest)
                1 + shock_pct * 0.45 / 100.0,  # 2 bars back
                1 + shock_pct * 0.60 / 100.0,  # 1 bar back
            ]
            for offset, pf in enumerate(partial_factors, start=1):
                idx = -(offset + 1)   # -2, -3, -4
                if abs(idx) <= n:
                    closes[idx] = closes[idx] * pf
                    if highs:  highs[idx]  = highs[idx]  * pf
                    if lows:   lows[idx]   = lows[idx]   * pf

    elif scenario == 'stop_loss_break':
        cost = float(position.get('cost', closes[-1]) or closes[-1])
        stop_loss = float(
            position.get('stop_loss')
            or position.get('stopLoss')
            or cost * 0.92
        )
        new_close = min(stop_loss * 0.93, cost * 0.88)
        if closes[-1] > 0:
            ratio = new_close / closes[-1]
        else:
            ratio = 1.0
        closes[-1] = new_close
        if highs:  highs[-1]  = highs[-1]  * ratio
        if lows:   lows[-1]   = lows[-1]   * ratio

    elif scenario == 'kill_signal_trigger':
        # Force last 3 closes to decline 2%/day below MA20
        # Creates RSI < 50 and MA20 breach
        if n >= 20:
            ma20 = _sma(closes, 20)
        else:
            ma20 = closes[-1] if closes[-1] > 0 else 100.0

        # Start from MA20 * 0.99 and decline 2% each day
        for i in range(3):
            idx = -(3 - i)          # -3, -2, -1
            if abs(idx) <= n:
                decline = (1 - 0.02) ** (i + 1)
                closes[idx] = ma20 * 0.99 * decline

        # Set last volume = avg_volume * 2
        if volumes and n >= 2:
            avg_vol = sum(volumes[:-1][-20:]) / max(len(volumes[:-1][-20:]), 1)
            volumes[-1] = avg_vol * 2

        # Last bar: red candle (open > close)
        if opens and closes:
            opens[-1] = closes[-1] * 1.01

        # Adjust highs/lows proportionally
        if highs and closes[-1] > 0:
            highs[-1] = max(opens[-1] if opens else closes[-1], closes[-1]) * 1.005
        if lows:
            lows[-1] = closes[-1] * 0.995

    elif scenario == 'chase_failure':
        if n >= 2:
            if n >= 20:
                ma20 = _sma(closes, 20)
            else:
                ma20 = closes[-1] if closes[-1] > 0 else 100.0

            # close[-2] = MA20 * 1.15  (chase risk high)
            closes[-2] = ma20 * 1.15
            if highs and len(highs) >= 2:
                highs[-2] = closes[-2] * 1.01
            if lows and len(lows) >= 2:
                lows[-2] = closes[-2] * 0.99

            # open[-1] = close[-2] * 1.02
            prev_close = closes[-2]
            if opens:
                opens[-1] = prev_close * 1.02
            # close[-1] = close[-2] * 0.95  (lower than prev day low)
            closes[-1] = prev_close * 0.95
            if highs:
                highs[-1] = opens[-1] if opens else closes[-1] * 1.01
            if lows:
                lows[-1]  = closes[-1] * 0.99

    elif scenario == 'capital_efficiency_decay':
        # Apply shock_pct to last close
        closes[-1] = closes[-1] * factor
        if highs:  highs[-1]  = highs[-1]  * factor
        if lows:   lows[-1]   = lows[-1]   * factor
        # holding_days is modified externally (in position dict copy)

    elif scenario == 'liquidity_shock':
        # Set all recent volumes (last 20 days) to 1% of current value
        for i in range(1, min(21, n + 1)):
            idx = -i
            if volumes and abs(idx) <= len(volumes):
                volumes[idx] = volumes[idx] * 0.01

    elif scenario == 'sector_rotation':
        symbol = position.get('symbol', '')
        sector = _smap.get_sector(symbol)
        pos_sector = sector

        # Apply shock_pct * 1.2 to same-sector, +1% to other sectors
        # For this position, determine which applies
        if pos_sector is not None:
            # Treat this position as same-sector → apply shock * 1.2
            effective = shock_pct * 1.2
        else:
            # No sector known → apply +1% (other sector treatment)
            effective = 1.0
        eff_factor = 1 + effective / 100.0
        closes[-1] = closes[-1] * eff_factor
        if highs:  highs[-1]  = highs[-1]  * eff_factor
        if lows:   lows[-1]   = lows[-1]   * eff_factor

    elif scenario == 'custom':
        closes[-1] = closes[-1] * factor
        if highs:  highs[-1]  = highs[-1]  * factor
        if lows:   lows[-1]   = lows[-1]   * factor

    # Write back modified lists
    o['closes']  = closes
    o['highs']   = highs
    o['lows']    = lows
    o['opens']   = opens
    o['volumes'] = volumes
    return o


# ---------------------------------------------------------------------------
# Per-position metrics
# ---------------------------------------------------------------------------

def _compute_position_metrics(
    symbol: str,
    pos: dict,
    ohlcv: dict,
    bench_ret: float | None,
    scenario: str,
) -> dict:
    """
    Run all engines on the given OHLCV and return a metrics dict.
    """
    closes  = ohlcv.get('closes', [])
    volumes = ohlcv.get('volumes', [])

    if not closes:
        return {'ok': False, 'error': '無收盤資料'}

    current_price = closes[-1]
    cost = float(pos.get('cost', 0) or 0)
    qty  = float(pos.get('qty',  0) or 0)

    if cost <= 0:
        return {'ok': False, 'error': '成本資料不足'}

    pnl_pct = (current_price - cost) / cost * 100

    # Holding days
    buy_date_str = (
        pos.get('buy_date')
        or pos.get('buyDate')
        or ''
    )
    try:
        buy_dt = datetime.strptime(buy_date_str, '%Y-%m-%d').date()
        holding_days = (date.today() - buy_dt).days
    except Exception:
        holding_days = 30
    holding_days = max(holding_days, 1)

    # capital_efficiency_decay adds 30 days
    if scenario == 'capital_efficiency_decay':
        holding_days = holding_days + 30

    # Sell decision
    sd = _de.run_sell_decision(ohlcv, cost=cost, holding_days=holding_days)
    sell_signal = sd.get('decision', 'HOLD')

    # Kill signal
    kill_alert = _ae.evaluate_kill_signal(symbol, ohlcv, sd)
    kill_signal  = kill_alert is not None
    kill_triggers = (
        kill_alert.detail.get('triggers', [])
        if kill_alert
        else []
    )

    # Capital efficiency
    holding = {
        'symbol':   symbol,
        'cost':     cost,
        'qty':      qty,
        'buy_date': buy_date_str,
    }
    ce = _pe.calc_capital_efficiency(holding, ohlcv, benchmark_return=bench_ret)
    ce_score = ce.get('score') if ce.get('score') is not None else 50
    ce_level = ce.get('level', 'HOLD')

    # Drag score
    mom_score = _re._momentum_score(closes)
    drag = _re.calc_drag_score(
        ce_score,
        holding_days,
        pnl_pct,
        bench_ret,
        None,
        sell_signal,
        mom_score,
    )
    drag_score = drag['drag_score']

    # Chase risk
    chase_risk = _re._chase_risk_score(closes)

    # Liquidity issue
    liquidity_issue = False
    if scenario == 'liquidity_shock' and volumes:
        avg_volume = sum(volumes[-20:]) / max(len(volumes[-20:]), 1)
        liquidity_issue = avg_volume < 500_000

    return {
        'ok':             True,
        'current_price':  current_price,
        'pnl_pct':        round(pnl_pct, 2),
        'holding_days':   holding_days,
        'sell_decision':  sd,
        'sell_signal':    sell_signal,
        'kill_signal':    kill_signal,
        'kill_triggers':  kill_triggers,
        'ce':             ce,
        'ce_score':       ce_score,
        'ce_level':       ce_level,
        'drag_score':     drag_score,
        'drag':           drag,
        'chase_risk':     chase_risk,
        'liquidity_issue': liquidity_issue,
    }


# ---------------------------------------------------------------------------
# Stress score
# ---------------------------------------------------------------------------

def _calc_stress_score(positions_impacted: list[dict]) -> tuple[int, str]:
    score = 0

    stop_n = sum(
        1 for p in positions_impacted
        if p.get('sell_signal_after') == 'STOP_LOSS'
    )
    kill_n = sum(
        1 for p in positions_impacted
        if p.get('kill_signal_after')
    )
    ce_n = sum(
        1 for p in positions_impacted
        if (p.get('ce_score_after') or 100) < 35
    )
    drag_n = sum(
        1 for p in positions_impacted
        if (p.get('drag_score_after') or 0) > 75
    )
    liq_n = sum(
        1 for p in positions_impacted
        if p.get('liquidity_issue')
    )

    score += min(40, stop_n * 20)
    score += min(25, kill_n * 12)
    score += min(15, ce_n  * 8)
    score += min(15, drag_n * 8)
    score += min(10, liq_n  * 5)

    # PnL expansion
    total_pnl_delta = sum(
        (p.get('pnl_pct_before', 0) - p.get('pnl_pct_after', 0))
        for p in positions_impacted
        if p.get('ok')
    )
    score += min(10, int(abs(total_pnl_delta) / 5))
    score = min(100, max(0, score))

    level_map = [
        (30,  '低壓力'),
        (60,  '中度壓力'),
        (80,  '高壓力'),
        (101, '極高壓力'),
    ]
    level = next(lbl for thr, lbl in level_map if score <= thr)
    return score, level


# ---------------------------------------------------------------------------
# Action plan builder
# ---------------------------------------------------------------------------

def _build_action_plan(positions_impacted: list[dict]) -> list[dict]:
    actions: list[dict] = []

    for p in positions_impacted:
        if not p.get('ok'):
            continue
        sym = p.get('symbol', '?')

        if p.get('sell_signal_after') == 'STOP_LOSS':
            actions.append({
                'priority':  1,
                'symbol':    sym,
                'action':    f'停損 {sym}',
                'reason':    '壓力情境下觸發固定停損線',
                'urgency':   'HIGH',
            })
        elif p.get('kill_signal_after'):
            actions.append({
                'priority':  2,
                'symbol':    sym,
                'action':    f'評估減碼 {sym}',
                'reason':    f'Kill Signal 觸發（{len(p.get("kill_triggers_after", []))} 條件）',
                'urgency':   'HIGH',
            })
        elif (p.get('ce_score_after') or 100) < 35:
            actions.append({
                'priority':  3,
                'symbol':    sym,
                'action':    f'考慮換股 {sym}',
                'reason':    f'資金效率分數 {p.get("ce_score_after", "?")} < 35',
                'urgency':   'MEDIUM',
            })
        elif (p.get('drag_score_after') or 0) > 75:
            actions.append({
                'priority':  4,
                'symbol':    sym,
                'action':    f'觀察 {sym} 拖累狀況',
                'reason':    f'拖累分數 {p.get("drag_score_after", "?")} > 75',
                'urgency':   'MEDIUM',
            })
        elif p.get('liquidity_issue'):
            actions.append({
                'priority':  5,
                'symbol':    sym,
                'action':    f'注意 {sym} 流動性',
                'reason':    '流動性衝擊下成交量極低',
                'urgency':   'LOW',
            })

    # Sort by priority then limit to 7
    actions.sort(key=lambda x: x['priority'])
    # Re-index priority
    for i, a in enumerate(actions[:7], start=1):
        a['priority'] = i
    return actions[:7]


# ---------------------------------------------------------------------------
# Main: generate_stress_test
# ---------------------------------------------------------------------------

def generate_stress_test(
    portfolio: list[dict],
    scenario: str,
    shock_pct: float | None,
    benchmark: str,
    ohlcv_fn,
    watchlist: list[str] | None = None,
) -> dict:
    """
    Simulate a market shock on a portfolio and return a full stress-test report.

    Parameters
    ----------
    portfolio   : list of position dicts (symbol, cost, qty, buy_date, ...)
    scenario    : one of SCENARIOS
    shock_pct   : override shock magnitude (None = use default per scenario)
    benchmark   : benchmark symbol string (e.g. 'QQQ')
    ohlcv_fn    : callable(symbol) -> normalized ohlcv dict | None
    watchlist   : optional list of symbols to pre-score
    """
    # 1. Validate scenario
    if scenario not in SCENARIOS:
        scenario = 'market_crash'

    # 2. Effective shock_pct
    if shock_pct is None or (scenario != 'custom' and shock_pct == 0):
        effective_shock = _DEFAULT_SHOCK.get(scenario, -5.0)
    else:
        effective_shock = float(shock_pct)
    # For custom, always use supplied shock_pct
    if scenario == 'custom':
        effective_shock = float(shock_pct) if shock_pct is not None else 0.0

    generated_at = datetime.now(timezone.utc).isoformat()

    # 3. Benchmark: before + after
    bench_ret       = None
    bench_ret_after = None
    try:
        bench_ohlcv = ohlcv_fn(benchmark)
        if bench_ohlcv and bench_ohlcv.get('closes') and len(bench_ohlcv['closes']) >= 22:
            bc = bench_ohlcv['closes']
            bench_ret = (bc[-1] - bc[-22]) / bc[-22] * 100
        if bench_ohlcv and bench_ohlcv.get('closes'):
            shocked_bench = _apply_scenario_shock(
                bench_ohlcv, scenario, effective_shock, {}
            )
            bc2 = shocked_bench['closes']
            if len(bc2) >= 22:
                bench_ret_after = (bc2[-1] - bc2[-22]) / bc2[-22] * 100
    except Exception:
        pass

    # 4. Process each position
    positions_impacted: list[dict] = []

    for pos in portfolio:
        symbol = (
            pos.get('symbol')
            or pos.get('sym', '')
        ).upper().strip()
        if not symbol:
            continue

        cost     = float(pos.get('cost', 0) or 0)
        qty      = float(pos.get('qty',  0) or 0)
        buy_date = (
            pos.get('buy_date')
            or pos.get('buyDate')
            or ''
        )

        try:
            # Fetch raw OHLCV
            raw_ohlcv = ohlcv_fn(symbol)
            if raw_ohlcv is None or not raw_ohlcv.get('closes'):
                positions_impacted.append({
                    'symbol': symbol,
                    'ok':     False,
                    'error':  '無法取得行情資料',
                    'cost':   cost,
                    'qty':    qty,
                })
                continue

            # Before metrics
            before_m = _compute_position_metrics(
                symbol, pos, raw_ohlcv, bench_ret, 'none'
            )

            # Shocked OHLCV
            shocked_ohlcv = _apply_scenario_shock(
                raw_ohlcv, scenario, effective_shock, pos
            )

            # After metrics (pass scenario so capital_efficiency_decay adds days)
            after_m = _compute_position_metrics(
                symbol, pos, shocked_ohlcv, bench_ret_after, scenario
            )

            price_before = before_m.get('current_price', cost)
            price_after  = after_m.get('current_price',  cost)
            pnl_before   = before_m.get('pnl_pct', 0.0)
            pnl_after    = after_m.get('pnl_pct',  0.0)

            # Rotation decision after shock
            rot_action = 'HOLD'
            rot_label  = '繼續持有'
            try:
                ce_after       = after_m.get('ce', {})
                drag_after     = after_m.get('drag', {})
                alts = _re.find_alternative_candidates(
                    symbol,
                    _smap.get_sector(symbol),
                    {},
                    after_m.get('chase_risk', 30),
                    {},
                )
                rot_result = _re.make_rotation_decision(
                    symbol=symbol,
                    ce_score=after_m.get('ce_score', 50),
                    ce_level=after_m.get('ce_level', 'HOLD'),
                    drag_score=after_m.get('drag_score', 0),
                    drag_level=drag_after.get('drag_level', 'LOW'),
                    alternatives=alts,
                    momentum_score=_re._momentum_score(shocked_ohlcv.get('closes', [])),
                    chase_risk_score=after_m.get('chase_risk', 30),
                    sell_signal=after_m.get('sell_signal', 'HOLD'),
                )
                rot_action = rot_result.get('rotation_action', 'WATCH')
                rot_label  = rot_result.get('rotation_label', '觀察')
            except Exception:
                pass

            # Determine action_required
            ss_after = after_m.get('sell_signal', 'HOLD')
            if ss_after == 'STOP_LOSS':
                action_required = 'STOP_LOSS'
            elif after_m.get('kill_signal') or ss_after in ('SELL', 'ROTATE'):
                action_required = 'REDUCE'
            elif ss_after == 'WATCH' or (after_m.get('ce_score') or 100) < 45:
                action_required = 'WATCH'
            else:
                action_required = 'HOLD'

            positions_impacted.append({
                'symbol':               symbol,
                'ok':                   True,
                'cost':                 cost,
                'qty':                  qty,
                'price_before':         round(price_before, 4),
                'price_after':          round(price_after,  4),
                'pnl_pct_before':       round(pnl_before, 2),
                'pnl_pct_after':        round(pnl_after,  2),
                'pnl_delta':            round(pnl_after - pnl_before, 2),
                'sell_signal_before':   before_m.get('sell_signal', 'HOLD'),
                'sell_signal_after':    after_m.get('sell_signal',  'HOLD'),
                'kill_signal_before':   before_m.get('kill_signal', False),
                'kill_signal_after':    after_m.get('kill_signal',  False),
                'kill_triggers_after':  after_m.get('kill_triggers', []),
                'ce_score_before':      before_m.get('ce_score'),
                'ce_score_after':       after_m.get('ce_score'),
                'ce_level_after':       after_m.get('ce_level', 'HOLD'),
                'drag_score_before':    before_m.get('drag_score', 0),
                'drag_score_after':     after_m.get('drag_score',  0),
                'chase_risk_before':    before_m.get('chase_risk', 0),
                'chase_risk_after':     after_m.get('chase_risk',  0),
                'rotation_action_after': rot_action,
                'rotation_label_after':  rot_label,
                'liquidity_issue':      after_m.get('liquidity_issue', False),
                'action_required':      action_required,
            })

        except Exception as exc:
            positions_impacted.append({
                'symbol': symbol,
                'ok':     False,
                'error':  str(exc),
                'cost':   cost,
                'qty':    qty,
            })

    # If all positions failed, return error
    ok_positions = [p for p in positions_impacted if p.get('ok')]
    if not ok_positions and positions_impacted:
        return {
            'ok':     False,
            'error':  '所有持倉資料獲取失敗',
            'positions_impacted': positions_impacted,
            'disclaimer': DISCLAIMER,
        }

    # 5. Portfolio-level aggregation
    total_value_before = sum(
        p['price_before'] * p['qty']
        for p in ok_positions
        if 'price_before' in p
    )
    total_value_after = sum(
        p['price_after'] * p['qty']
        for p in ok_positions
        if 'price_after' in p
    )
    total_cost = sum(
        p['cost'] * p['qty']
        for p in ok_positions
    )
    n_pos = len(ok_positions)

    def _overall_pnl(total_val: float) -> float:
        if total_cost > 0:
            return (total_val - total_cost) / total_cost * 100
        return 0.0

    overall_pnl_before = round(_overall_pnl(total_value_before), 2)
    overall_pnl_after  = round(_overall_pnl(total_value_after),  2)

    portfolio_before = {
        'total_value':    round(total_value_before, 2),
        'total_cost':     round(total_cost, 2),
        'total_pnl_pct':  overall_pnl_before,
        'positions_count': n_pos,
    }
    portfolio_after = {
        'total_value':    round(total_value_after, 2),
        'total_cost':     round(total_cost, 2),
        'total_pnl_pct':  overall_pnl_after,
        'pnl_change_pct': round(overall_pnl_after - overall_pnl_before, 2),
        'positions_count': n_pos,
    }

    # 6. Stress score
    stress_score, stress_level = _calc_stress_score(positions_impacted)

    # 7. Risk summary
    stop_loss_triggered = [
        p['symbol'] for p in ok_positions
        if p.get('sell_signal_after') == 'STOP_LOSS'
    ]
    kill_signals_triggered = [
        p['symbol'] for p in ok_positions
        if p.get('kill_signal_after')
    ]
    ce_degraded = [
        p['symbol'] for p in ok_positions
        if (p.get('ce_score_after') or 100) < 35
    ]
    drag_critical = [
        p['symbol'] for p in ok_positions
        if (p.get('drag_score_after') or 0) > 75
    ]
    liquidity_issues = [
        p['symbol'] for p in ok_positions
        if p.get('liquidity_issue')
    ]
    total_alerts = (
        len(stop_loss_triggered)
        + len(kill_signals_triggered)
        + len(ce_degraded)
        + len(drag_critical)
        + len(liquidity_issues)
    )

    risk_summary = {
        'stop_loss_triggered':  stop_loss_triggered,
        'kill_signals_triggered': kill_signals_triggered,
        'ce_degraded':          ce_degraded,
        'drag_critical':        drag_critical,
        'liquidity_issues':     liquidity_issues,
        'total_alerts':         total_alerts,
    }

    # 8. Alerts triggered (simulated, NOT saved to DB)
    alerts_triggered: list[dict] = []
    for p in ok_positions:
        sym = p['symbol']

        if p.get('kill_signal_after'):
            # Build a synthetic OHLCV for the alert
            try:
                raw_ohlcv = ohlcv_fn(sym)
                if raw_ohlcv:
                    shocked = _apply_scenario_shock(raw_ohlcv, scenario, effective_shock, p)
                    pos_dict = next(
                        (x for x in portfolio if (x.get('symbol', '') or '').upper() == sym),
                        {}
                    )
                    cost_val = float(pos_dict.get('cost', 0) or 0)
                    sd_dict  = _de.run_sell_decision(shocked, cost=cost_val, holding_days=30)
                    kill_a   = _ae.evaluate_kill_signal(sym, shocked, sd_dict)
                    if kill_a:
                        d = kill_a.to_dict()
                        d['type'] = kill_a.alert_type
                        alerts_triggered.append(d)
            except Exception:
                pass

        if p.get('sell_signal_after') in ('STOP_LOSS', 'SELL', 'TRIM', 'ROTATE'):
            # Sell signal alert
            try:
                raw_ohlcv = ohlcv_fn(sym)
                if raw_ohlcv:
                    shocked = _apply_scenario_shock(raw_ohlcv, scenario, effective_shock, p)
                    pos_dict = next(
                        (x for x in portfolio if (x.get('symbol', '') or '').upper() == sym),
                        {}
                    )
                    cost_val = float(pos_dict.get('cost', 0) or 0)
                    sd_dict  = _de.run_sell_decision(shocked, cost=cost_val, holding_days=30)
                    sell_a   = _ae.evaluate_sell_signal(sym, sd_dict)
                    if sell_a:
                        d = sell_a.to_dict()
                        d['type'] = sell_a.alert_type
                        alerts_triggered.append(d)
            except Exception:
                pass

    # De-duplicate alerts by symbol+type
    seen_alert_keys: set[str] = set()
    unique_alerts: list[dict] = []
    for a in alerts_triggered:
        k = f"{a.get('symbol')}:{a.get('type', a.get('alert_type', ''))}"
        if k not in seen_alert_keys:
            seen_alert_keys.add(k)
            unique_alerts.append(a)
    alerts_triggered = unique_alerts

    # 9. Sell decisions list
    sell_decisions = [
        p for p in ok_positions
        if p.get('sell_signal_after') in ('STOP_LOSS', 'SELL', 'ROTATE')
    ]

    # 10. Rotation suggestions
    _ACTIONABLE_ROTATIONS = {'ROTATE_FULL', 'ROTATE_PARTIAL', 'TRIM', 'STOP_LOSS'}
    rotation_suggestions = [
        p for p in ok_positions
        if p.get('rotation_action_after') in _ACTIONABLE_ROTATIONS
    ]

    # 11. Capital efficiency changes
    capital_efficiency_changes = [
        {
            'symbol':    p['symbol'],
            'ce_before': p.get('ce_score_before'),
            'ce_after':  p.get('ce_score_after'),
            'delta':     (
                (p.get('ce_score_after') or 0) - (p.get('ce_score_before') or 0)
            ),
            'level_after': p.get('ce_level_after', 'HOLD'),
        }
        for p in ok_positions
    ]

    # 12. Sector impact
    sector_impact: dict[str, dict] = {}
    for p in ok_positions:
        sym    = p['symbol']
        sector = _smap.get_sector(sym)
        if sector is None:
            sector = '未知板塊'
        if sector not in sector_impact:
            shock_applied = (
                effective_shock * 1.2
                if scenario == 'sector_rotation' and _smap.get_sector(sym) is not None
                else effective_shock
            )
            sector_impact[sector] = {
                'affected_symbols': [],
                'shock_applied':    round(shock_applied, 2),
            }
        sector_impact[sector]['affected_symbols'].append(sym)

    # Sector rotation: mark other-sector shock as +1%
    if scenario == 'sector_rotation':
        for sect, info in sector_impact.items():
            if sect == '未知板塊':
                info['shock_applied'] = 1.0

    # 13. Action plan
    action_plan = _build_action_plan(positions_impacted)

    result = {
        'ok':                        True,
        'scenario':                  scenario,
        'scenario_label':            SCENARIO_LABELS.get(scenario, scenario),
        'shock_pct':                 effective_shock,
        'generated_at':              generated_at,
        'benchmark':                 benchmark,
        'benchmark_return_before':   round(bench_ret, 2) if bench_ret is not None else None,
        'benchmark_return_after':    round(bench_ret_after, 2) if bench_ret_after is not None else None,
        'portfolio_before':          portfolio_before,
        'portfolio_after':           portfolio_after,
        'portfolio_stress_score':    stress_score,
        'stress_level':              stress_level,
        'risk_summary':              risk_summary,
        'positions_impacted':        positions_impacted,
        'alerts_triggered':          alerts_triggered,
        'sell_decisions':            sell_decisions,
        'rotation_suggestions':      rotation_suggestions,
        'capital_efficiency_changes': capital_efficiency_changes,
        'sector_impact':             sector_impact,
        'action_plan':               action_plan,
        'disclaimer':                DISCLAIMER,
    }

    # Save to DB
    try:
        save_result(result)
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def save_result(result: dict) -> int:
    """Persist a stress test result and return the new row id."""
    with _lock_conn() as con:
        cur = con.execute(
            """
            INSERT INTO stress_test_results
                (scenario, generated_at, shock_pct, stress_score, stress_level, result_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.get('scenario'),
                result.get('generated_at'),
                result.get('shock_pct'),
                result.get('portfolio_stress_score'),
                result.get('stress_level'),
                json.dumps(result, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def get_latest() -> dict | None:
    """Return the most recent stress test result, or None."""
    with _lock_conn() as con:
        row = con.execute(
            "SELECT * FROM stress_test_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    payload = json.loads(d.pop('result_json', '{}'))
    return {
        '_db_id':          d.get('id'),
        '_db_scenario':    d.get('scenario'),
        '_db_generated_at': d.get('generated_at'),
        '_db_shock_pct':   d.get('shock_pct'),
        '_db_stress_score': d.get('stress_score'),
        '_db_stress_level': d.get('stress_level'),
        **payload,
    }


def get_history(limit: int = 20) -> list[dict]:
    """Return up to `limit` stress test results, newest first."""
    with _lock_conn() as con:
        rows = con.execute(
            "SELECT * FROM stress_test_results ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        payload = json.loads(d.pop('result_json', '{}'))
        results.append({
            '_db_id':           d.get('id'),
            '_db_scenario':     d.get('scenario'),
            '_db_generated_at': d.get('generated_at'),
            '_db_shock_pct':    d.get('shock_pct'),
            '_db_stress_score': d.get('stress_score'),
            '_db_stress_level': d.get('stress_level'),
            **payload,
        })
    return results

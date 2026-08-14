"""
24/7 Market Monitor Engine

Provides:
  - NYSE market calendar (US Eastern, holiday-aware)
  - State persistence across Flask restarts
  - Alert queue (info / warning / critical)
  - Intraday position P&L + trailing stop engine
  - Market regime filter (SPY EMA50 / EMA200)
  - Volume surge detection
"""
from __future__ import annotations
import json
import logging
import math
import os
import tempfile
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from exchange_calendar import exchange_day

logger = logging.getLogger(__name__)

_VOLUME_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
STATE_FILE = os.path.join(
    _VOLUME_PATH or os.path.dirname(__file__), "monitor_state.json"
)


def _now_et() -> datetime:
    """Current datetime in US/Eastern (DST-aware)."""
    try:
        import pytz
        return datetime.now(pytz.timezone('America/New_York'))
    except ImportError:
        # Rough fallback (no DST): UTC-5
        from datetime import timezone
        return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)


# ── Market Calendar ───────────────────────────────────────────────────────────

def is_trading_day(dt=None) -> bool:
    dt = dt or _now_et()
    return bool(exchange_day("US", dt.date())["is_trading_day"])


def _regular_close_minutes(dt) -> int:
    day = exchange_day("US", dt.date())
    close_time = str(day.get("close_time") or "16:00")
    hour, minute = (int(part) for part in close_time.split(":", 1))
    return hour * 60 + minute


def is_market_open(dt=None) -> bool:
    """True during NYSE regular session 09:30–16:00 ET."""
    dt = dt or _now_et()
    if not is_trading_day(dt):
        return False
    t = dt.hour * 60 + dt.minute
    return 570 <= t < _regular_close_minutes(dt)


def is_pre_market(dt=None) -> bool:
    dt = dt or _now_et()
    if not is_trading_day(dt):
        return False
    t = dt.hour * 60 + dt.minute
    return 240 <= t < 570       # 4:00–9:30


def is_after_hours(dt=None) -> bool:
    dt = dt or _now_et()
    if not is_trading_day(dt):
        return False
    t = dt.hour * 60 + dt.minute
    return _regular_close_minutes(dt) <= t < 1200


def market_status(dt=None) -> dict:
    dt     = dt or _now_et()
    calendar = exchange_day("US", dt.date())
    open_  = is_market_open(dt)
    pre_   = is_pre_market(dt)
    after_ = is_after_hours(dt)
    wknd   = dt.weekday() >= 5
    hday   = dt.weekday() < 5 and not calendar["is_trading_day"]

    if open_:    label = "開市中 🟢"
    elif pre_:   label = "盤前 🟡"
    elif after_: label = "盤後 🟡"
    elif wknd:   label = "週末 ⚫"
    elif hday:   label = "假日 ⚫"
    else:        label = "休市 ⚫"

    # Time until next open (minutes)
    minutes_to_open = None
    if not open_:
        next_o = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        if next_o <= dt:
            next_o += timedelta(days=1)
        while not is_trading_day(next_o):
            next_o += timedelta(days=1)
        minutes_to_open = int((next_o - dt).total_seconds() / 60)

    minutes_to_close = None
    if open_:
        close_minutes = _regular_close_minutes(dt)
        close = dt.replace(
            hour=close_minutes // 60,
            minute=close_minutes % 60,
            second=0,
            microsecond=0,
        )
        minutes_to_close = max(0, int((close - dt).total_seconds() / 60))

    return {
        'open':             open_,
        'pre_market':       pre_,
        'after_hours':      after_,
        'label':            label,
        'et_time':          dt.strftime('%H:%M'),
        'et_date':          dt.strftime('%Y-%m-%d'),
        'weekday':          dt.strftime('%a'),
        'minutes_to_open':  minutes_to_open,
        'minutes_to_close': minutes_to_close,
        'early_close':      calendar.get('early_close', False),
        'holiday_name':     calendar.get('holiday_name'),
        'calendar_source':  calendar.get('source'),
    }


# ── State Persistence ─────────────────────────────────────────────────────────

class StateManager:
    """Thread-safe JSON persistence for scan results, orders, and alerts."""

    _EMPTY: dict = {
        'candidates':     [],
        'executed_today': [],
        'alerts':         [],
        'positions':      {},   # sym -> {entry, stop, r1, leg1_closed, shares}
        'sod_equity':     None,
        'daily_pnl_pct':  0.0,
        'kill_switch':    False,
        'last_saved':     None,
        'regime':         {},
    }

    def __init__(self, path: str = STATE_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._state: dict = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path) as f:
                data = json.load(f)
            # Reset daily fields if last save was a different calendar day (ET)
            last = data.get('last_saved')
            if last:
                saved_day  = last[:10]
                today_et   = _now_et().strftime('%Y-%m-%d')
                if saved_day != today_et:
                    data['executed_today'] = []
                    data['kill_switch']    = False
                    data['daily_pnl_pct']  = 0.0
                    data['sod_equity']     = None
            return {**self._EMPTY, **data}
        except Exception:
            return dict(self._EMPTY)

    def save(self):
        with self._lock:
            try:
                self._state['last_saved'] = datetime.utcnow().isoformat()
                parent = os.path.dirname(os.path.abspath(self._path))
                os.makedirs(parent, exist_ok=True)
                fd, temporary = tempfile.mkstemp(
                    prefix="monitor-state-", suffix=".json", dir=parent
                )
                try:
                    with os.fdopen(fd, 'w') as f:
                        json.dump(self._state, f, default=str, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temporary, self._path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            except Exception as e:
                logger.warning("StateManager save failed: %s", e)

    def get(self, key, default=None):
        return self._state.get(key, default)

    def set(self, key, value):
        self._state[key] = value
        self.save()

    # ── Alerts ───────────────────────────────────────────────────────────────

    def add_alert(self, level: str, msg: str):
        with self._lock:
            alert = {
                'ts':    datetime.utcnow().isoformat(),
                'level': level,
                'msg':   msg,
            }
            alerts = self._state.setdefault('alerts', [])
            alerts.append(alert)
            self._state['alerts'] = alerts[-300:]
        self.save()
        logger.info("[ALERT %s] %s", level.upper(), msg)

    def get_alerts(self, n: int = 60) -> list:
        return list(self._state.get('alerts', []))[-n:]

    # ── Positions ─────────────────────────────────────────────────────────────

    def update_position(self, sym: str, data: dict):
        with self._lock:
            pos = self._state.setdefault('positions', {})
            pos[sym] = {**pos.get(sym, {}), **data}
        self.save()

    def remove_position(self, sym: str):
        with self._lock:
            self._state.get('positions', {}).pop(sym, None)
        self.save()

    def get_positions(self) -> dict:
        return dict(self._state.get('positions', {}))

    # ── Candidates ───────────────────────────────────────────────────────────

    def set_candidates(self, candidates: list):
        self._state['candidates'] = candidates
        self.save()

    def get_candidates(self) -> list:
        return list(self._state.get('candidates', []))

    # ── Kill Switch ───────────────────────────────────────────────────────────

    def is_kill_switch(self) -> bool:
        return bool(self._state.get('kill_switch', False))

    def engage_kill_switch(self, reason: str):
        self._state['kill_switch'] = True
        self.add_alert('critical', f'🛑 每日虧損限制觸發 — 停止所有交易：{reason}')

    def reset_kill_switch(self):
        self._state['kill_switch'] = False
        self.save()


# Singleton
_state: Optional[StateManager] = None
_state_lock = threading.Lock()


def get_state() -> StateManager:
    global _state
    if _state is None:
        with _state_lock:
            if _state is None:
                _state = StateManager()
    return _state


# ── Shorthand alert helpers ───────────────────────────────────────────────────

def alert_info(msg: str):     get_state().add_alert('info',     msg)
def alert_warn(msg: str):     get_state().add_alert('warning',  msg)
def alert_crit(msg: str):     get_state().add_alert('critical', msg)


# ── Market Regime Filter ──────────────────────────────────────────────────────

def _ema(arr: list, p: int) -> list:
    k, val, out = 2 / (p + 1), None, []
    for v in arr:
        val = v if val is None else v * k + val * (1 - k)
        out.append(val)
    return out


def check_market_regime(spy_closes: list) -> dict:
    """
    Broad market filter using SPY.
    Returns bullish=True if SPY > EMA50.
    Strong=True if also > EMA200 and 20-day return > -5%.
    """
    n = len(spy_closes)
    if n < 52:
        return {'bullish': True, 'strong': True, 'label': '數據不足', 'spy_price': 0}

    e50  = _ema(spy_closes, 50)[-1]
    e200 = _ema(spy_closes, min(200, n))[-1]
    last = spy_closes[-1]
    prev20 = spy_closes[-21] if n >= 21 else spy_closes[0]
    chg20 = (last - prev20) / prev20 * 100 if prev20 else 0

    bullish = last > e50
    strong  = bullish and last > e200 and chg20 > -8

    if not bullish:     label = "空頭 🔴"
    elif not strong:    label = "弱多 🟡"
    else:               label = "強多 🟢"

    return {
        'bullish':   bullish,
        'strong':    strong,
        'spy_price': round(last, 2),
        'ema50':     round(e50, 2),
        'ema200':    round(e200, 2),
        'chg20d':    round(chg20, 2),
        'label':     label,
    }


# ── Volume Surge Detection ────────────────────────────────────────────────────

def detect_volume_surge(volumes: list, threshold: float = 2.5) -> dict:
    """True if today's volume ≥ threshold × 20-day average."""
    if len(volumes) < 21:
        return {'surge': False, 'ratio': 0.0}
    avg20 = sum(volumes[-21:-1]) / 20
    ratio = volumes[-1] / avg20 if avg20 else 0
    return {
        'surge': ratio >= threshold,
        'ratio': round(ratio, 2),
        'label': f'量比 {ratio:.1f}×' if ratio >= threshold else '',
    }


# ── Position P&L Helper ───────────────────────────────────────────────────────

def calc_pnl(entry: float, current: float, stop: float, shares: float) -> dict:
    """Calculate position P&L and R-multiple."""
    pnl_pct  = (current / entry - 1) * 100 if entry else 0
    pnl_usd  = (current - entry) * shares
    risk     = entry - stop if stop < entry else entry * 0.05
    r_mult   = (current - entry) / risk if risk > 0 else 0
    return {
        'pnl_pct':    round(pnl_pct, 2),
        'pnl_usd':    round(pnl_usd, 2),
        'r_multiple': round(r_mult, 2),
        'above_be':   current >= entry,
    }


# ── Daily Kill Switch Check ───────────────────────────────────────────────────

def check_kill_switch(equity: float, sod_equity: float, limit_pct: float = 5.0) -> bool:
    """
    Returns True and engages kill switch if daily loss exceeds limit_pct.
    """
    if sod_equity and sod_equity > 0:
        daily_pnl = (equity - sod_equity) / sod_equity * 100
        st = get_state()
        st._state['daily_pnl_pct'] = round(daily_pnl, 2)
        if daily_pnl < -limit_pct and not st.is_kill_switch():
            st.engage_kill_switch(f"日虧損 {daily_pnl:.1f}% 超過 {limit_pct}% 限制")
            return True
    return get_state().is_kill_switch()


# ── ATR Trailing Stop Updater ─────────────────────────────────────────────────

def update_trailing_stop(current_stop: float, current_price: float,
                          atr: float, mult: float = 2.0,
                          leg1_closed: bool = False) -> float:
    """
    Returns the new (raised) trailing stop.
    Uses tighter mult after leg-1 is closed (breakeven management).
    """
    trail_mult = mult * 0.8 if leg1_closed else mult
    new_trail  = current_price - atr * trail_mult
    return max(current_stop, new_trail)


# ── System Health Summary ─────────────────────────────────────────────────────

def system_health() -> dict:
    st = get_state()
    ms = market_status()
    return {
        'market':       ms,
        'kill_switch':  st.is_kill_switch(),
        'daily_pnl':    st.get('daily_pnl_pct', 0.0),
        'candidates':   len(st.get_candidates()),
        'alerts_total': len(st.get('alerts', [])),
        'last_saved':   st.get('last_saved'),
        'regime':       st.get('regime', {}),
        'positions':    len(st.get_positions()),
    }

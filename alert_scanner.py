"""
Alert Scanner — orchestrates Phase 1 engine calls and evaluates alerts.

Design: accepts an `ohlcv_fn` callable so it never imports from app.py
(avoids circular imports).  app.py passes `_get_ohlcv_norm` at call time.

Public API
----------
scan_symbol(symbol, ohlcv_fn)          → list[Alert]
scan_position(pos, ohlcv_fn)           → list[Alert]
scan_portfolio(positions, ohlcv_fn)    → list[Alert]   (positions + sectors)
scan_watchlist(symbols, ohlcv_fn)      → list[Alert]
run_full_scan(positions, watchlist, ohlcv_fn, send_fn=None) → list[Alert]
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import decision_engine as _de
import sector_map      as _smap
import alert_engine    as _ae
import alert_history   as _ah
import alert_formatter as _af

logger = logging.getLogger(__name__)

OhlcvFn = Callable[[str], dict | None]


# ── Single-symbol helpers ─────────────────────────────────────────────────────

def _chase_alerts(symbol: str, ohlcv: dict) -> list[_ae.Alert]:
    alerts = []
    cr = _de.run_chase_risk(ohlcv)
    a  = _ae.evaluate_chase_risk(symbol, cr)
    if a and _ah.should_send(symbol, "CHASE_RISK", a.level):
        alerts.append(a)
    return alerts


def _sell_alerts(symbol: str, ohlcv: dict, cost: float, holding_days: int = 0) -> tuple[list[_ae.Alert], dict]:
    alerts = []
    sd = _de.run_sell_decision(ohlcv, cost=cost, holding_days=holding_days)
    a  = _ae.evaluate_sell_signal(symbol, sd)
    if a and _ah.should_send(symbol, "SELL_SIGNAL", a.level):
        alerts.append(a)
    return alerts, sd   # return sd for kill signal evaluation


def _kill_alerts(symbol: str, ohlcv: dict, sd: dict | None) -> list[_ae.Alert]:
    alerts = []
    a = _ae.evaluate_kill_signal(symbol, ohlcv, sd)
    if a and _ah.should_send(symbol, "KILL_SIGNAL", a.level):
        alerts.append(a)
    return alerts


def _ce_alerts(symbol: str, ohlcv: dict, holding: dict) -> list[_ae.Alert]:
    alerts = []
    ce = _de.run_capital_efficiency(holding, ohlcv)
    a  = _ae.evaluate_capital_efficiency(symbol, ce)
    if a and _ah.should_send(symbol, "CAPITAL_EFF", a.level):
        alerts.append(a)
    return alerts


# ── Public scan functions ─────────────────────────────────────────────────────

def scan_symbol(symbol: str, ohlcv_fn: OhlcvFn) -> list[_ae.Alert]:
    """
    Scan a watchlist symbol (no position cost).
    Checks: Chase Risk only (sell/CE need cost info).
    """
    alerts = []
    try:
        ohlcv = ohlcv_fn(symbol)
        if not ohlcv:
            return alerts
        alerts += _chase_alerts(symbol, ohlcv)
        # Kill signal without cost context
        a = _ae.evaluate_kill_signal(symbol, ohlcv, sd=None)
        if a and _ah.should_send(symbol, "KILL_SIGNAL", a.level):
            alerts.append(a)
    except Exception as exc:
        logger.warning("scan_symbol %s: %s", symbol, exc)
    return alerts


def scan_position(pos: dict, ohlcv_fn: OhlcvFn) -> list[_ae.Alert]:
    """
    Scan a portfolio position.
    pos keys: symbol/sym, cost/entry, qty/shares, buy_date (optional)
    Checks: Chase Risk + Sell Signal + Kill Signal + Capital Efficiency.
    """
    symbol = (pos.get("symbol") or pos.get("sym", "")).upper().strip()
    cost   = float(pos.get("cost") or pos.get("entry") or 0)
    qty    = float(pos.get("qty")  or pos.get("shares") or 0)
    buy_date = pos.get("buy_date", "")

    if not symbol or cost <= 0:
        return []

    alerts = []
    try:
        ohlcv = ohlcv_fn(symbol)
        if not ohlcv:
            return alerts

        # Chase Risk
        alerts += _chase_alerts(symbol, ohlcv)

        # Sell Signal + sd result
        sell_alerts, sd = _sell_alerts(
            symbol, ohlcv, cost=cost,
            holding_days=_holding_days(buy_date),
        )
        alerts += sell_alerts

        # Kill Signal (uses sell decision context for richer triggers)
        alerts += _kill_alerts(symbol, ohlcv, sd)

        # Capital Efficiency
        holding = {"symbol": symbol, "cost": cost, "qty": qty, "buy_date": buy_date}
        alerts += _ce_alerts(symbol, ohlcv, holding)

    except Exception as exc:
        logger.warning("scan_position %s: %s", symbol, exc)

    return alerts


def scan_sectors(positions: list[dict], ohlcv_fn: OhlcvFn) -> list[_ae.Alert]:
    """
    Derive sectors from position symbols, fetch sector OHLCV, evaluate leadership.
    """
    # Group symbols by sector
    sector_to_syms: dict[str, list[str]] = defaultdict(list)
    for pos in positions:
        sym    = (pos.get("symbol") or pos.get("sym", "")).upper().strip()
        sector = _smap.get_sector(sym)
        if sector:
            sector_to_syms[sector].append(sym)

    alerts = []
    for sector_name, held_syms in sector_to_syms.items():
        try:
            peer_syms   = _smap.get_sector_symbols(sector_name)[:8]
            sector_ohlcv: dict = {}
            for s, ov in _parallel_fetch(peer_syms, ohlcv_fn):
                if ov:
                    sector_ohlcv[s] = ov

            if not sector_ohlcv:
                continue

            sl = _de.run_sector_leadership(sector_name, sector_ohlcv)
            a  = _ae.evaluate_sector_leadership(sector_name, sl, held_syms)
            if a and _ah.should_send(sector_name, "SECTOR", a.level):
                alerts.append(a)
        except Exception as exc:
            logger.warning("scan_sectors %s: %s", sector_name, exc)

    return alerts


def scan_portfolio(positions: list[dict], ohlcv_fn: OhlcvFn) -> list[_ae.Alert]:
    """Scan all positions (individual + sector) in parallel."""
    alerts: list[_ae.Alert] = []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(scan_position, pos, ohlcv_fn): pos for pos in positions}
        for fut in as_completed(futures):
            try:
                alerts += fut.result()
            except Exception as exc:
                logger.warning("scan_portfolio future: %s", exc)

    alerts += scan_sectors(positions, ohlcv_fn)
    return alerts


def scan_watchlist(symbols: list[str], ohlcv_fn: OhlcvFn) -> list[_ae.Alert]:
    """Scan watchlist symbols (chase risk + kill signal only)."""
    alerts: list[_ae.Alert] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(scan_symbol, sym, ohlcv_fn): sym for sym in symbols}
        for fut in as_completed(futures):
            try:
                alerts += fut.result()
            except Exception as exc:
                logger.warning("scan_watchlist future: %s", exc)
    return alerts


def run_full_scan(
    positions: list[dict],
    watchlist: list[str],
    ohlcv_fn: OhlcvFn,
    send_fn: Callable[[_ae.Alert], None] | None = None,
) -> list[_ae.Alert]:
    """
    Full scan: portfolio + watchlist.
    Records every fired alert in history.
    Optionally calls `send_fn(alert)` for each new alert (email/LINE).
    """
    all_alerts: list[_ae.Alert] = []

    all_alerts += scan_portfolio(positions, ohlcv_fn)
    all_alerts += scan_watchlist(watchlist, ohlcv_fn)

    # Deduplicate by id (same symbol+type fired twice in parallel)
    seen: set[str] = set()
    unique: list[_ae.Alert] = []
    for a in all_alerts:
        if a.id not in seen:
            seen.add(a.id)
            unique.append(a)

    # Record + dispatch
    for a in unique:
        try:
            _ah.record(a)
        except Exception as exc:
            logger.warning("record alert %s: %s", a.id, exc)
        if send_fn:
            try:
                send_fn(a)
            except Exception as exc:
                logger.warning("send_fn alert %s: %s", a.id, exc)

    logger.info("run_full_scan: %d alerts fired", len(unique))
    return unique


# ── Internal helpers ──────────────────────────────────────────────────────────

def _holding_days(buy_date_str: str) -> int:
    if not buy_date_str:
        return 0
    try:
        from datetime import date, datetime
        bd = datetime.strptime(buy_date_str, "%Y-%m-%d").date()
        return max((date.today() - bd).days, 0)
    except Exception:
        return 0


def _parallel_fetch(symbols: list[str], ohlcv_fn: OhlcvFn) -> list[tuple[str, dict | None]]:
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(lambda s: (s, ohlcv_fn(s)), symbols))

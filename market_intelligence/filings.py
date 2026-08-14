"""Structured SEC filing collector for dilution, earnings and insider events."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import requests


_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FORMS = {
    "8-K", "10-Q", "10-K", "S-1", "S-3", "424B3", "424B5",
    "DEF 14A", "4", "SC 13D", "SC 13G", "NT 10-Q", "6-K", "20-F",
}
_DILUTION_FORMS = {"S-1", "S-3", "424B3", "424B5"}
_CACHE = {"ts": 0.0, "mapping": {}}
_LOCK = threading.Lock()


def _get_json(url: str, user_agent: str, *, session=None) -> dict:
    getter = session.get if session is not None else requests.get
    response = getter(
        url,
        params={},
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _ticker_mapping(user_agent: str, *, session=None) -> dict[str, dict]:
    with _LOCK:
        if _CACHE["mapping"] and time.monotonic() - _CACHE["ts"] < 24 * 3600:
            return dict(_CACHE["mapping"])
    payload = _get_json(_TICKERS_URL, user_agent, session=session)
    mapping = {}
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("ticker") or "").upper().replace("-", ".").strip()
        try:
            cik = int(item.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if symbol:
            mapping[symbol] = {
                "cik": cik,
                "name": str(item.get("title") or "")[:240],
            }
    with _LOCK:
        _CACHE.update({"ts": time.monotonic(), "mapping": mapping})
    return dict(mapping)


def fetch_sec_filings(
    symbols: list[str],
    *,
    user_agent: str,
    lookback_days: int = 45,
    limit_per_symbol: int = 8,
    session=None,
    now: datetime | None = None,
) -> list[dict]:
    """Return normalized filing articles for US symbols.

    SEC requests are disabled unless the operator supplies a descriptive
    ``SEC_USER_AGENT`` containing contact information, per SEC fair-access
    guidance.
    """
    user_agent = str(user_agent or "").strip()
    if len(user_agent) < 12 or "@" not in user_agent:
        return []
    current = now or datetime.now(timezone.utc)
    cutoff = current.date() - timedelta(days=max(1, min(365, lookback_days)))
    mapping = _ticker_mapping(user_agent, session=session)
    results = []
    from .sources import _article
    last_submission_request = 0.0

    for requested in symbols[:20]:
        symbol = str(requested or "").upper().strip()
        if symbol.endswith((".TW", ".TWO")):
            continue
        company = mapping.get(symbol) or mapping.get(symbol.replace("-", "."))
        if not company:
            continue
        cik = company["cik"]
        elapsed = time.monotonic() - last_submission_request
        if last_submission_request and elapsed < 0.12:
            time.sleep(0.12 - elapsed)
        payload = _get_json(_SUBMISSIONS.format(cik=cik), user_agent, session=session)
        last_submission_request = time.monotonic()
        recent = ((payload.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        filed = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []
        accepted = recent.get("acceptanceDateTime") or []
        added = 0
        for index, form in enumerate(forms):
            form = str(form or "").upper().strip()
            if form not in _FORMS or index >= len(filed):
                continue
            try:
                filing_day = datetime.fromisoformat(str(filed[index])[:10]).date()
            except ValueError:
                continue
            if filing_day < cutoff:
                continue
            accession = str(accessions[index] if index < len(accessions) else "")
            document = str(documents[index] if index < len(documents) else "")
            accession_path = accession.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_path}/{document}"
                if accession_path and document
                else f"https://www.sec.gov/Archives/edgar/data/{cik}/"
            )
            description = str(
                descriptions[index] if index < len(descriptions) else ""
            ).strip()
            risk = "DILUTION_RISK" if form in _DILUTION_FORMS else (
                "INSIDER_TRANSACTION" if form == "4" else "STRUCTURED_FILING"
            )
            item = _article(
                source_provider="SEC EDGAR",
                publisher="U.S. Securities and Exchange Commission",
                title=f"{symbol} SEC {form} filing — {description or company['name']}",
                summary=(
                    f"Fact: {company['name']} filed Form {form} on {filing_day.isoformat()}. "
                    f"Primary document: {description or document or 'not specified'}."
                ),
                url=url,
                published_at=(
                    str(accepted[index]).replace("Z", "+00:00")
                    if index < len(accepted) and accepted[index]
                    else f"{filing_day.isoformat()}T00:00:00+00:00"
                ),
                symbols=[symbol],
                raw={
                    "form": form,
                    "cik": cik,
                    "accession_number": accession,
                    "risk_category": risk,
                    "is_primary_source": True,
                },
            )
            if item:
                results.append(item)
                added += 1
            if added >= max(1, min(20, limit_per_symbol)):
                break
    return results

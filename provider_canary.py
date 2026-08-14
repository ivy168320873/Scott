"""No-secret live contract checks for public primary market-data sources."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

import exchange_calendar
import tpex_flow
import twse_flow


def run_canary(now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    checks = []

    def record(name: str, operation) -> None:
        try:
            detail = operation()
            checks.append({"name": name, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - CLI reports every contract failure
            checks.append({"name": name, "ok": False, "error": str(exc)[:300]})

    def twse_calendar_check():
        calendar = exchange_calendar._fetch_twse_year(current.year)
        if not calendar:
            raise RuntimeError("official schedule parsed to zero dates")
        return {"parsed_dates": len(calendar), "source": "TWSE_OFFICIAL"}

    def twse_flow_check():
        result = twse_flow.get_twse_flow("2330.TW", now=current)
        if not result.get("ok") or not result.get("institutional"):
            raise RuntimeError(result.get("warnings") or result.get("status"))
        return {"status": result["status"], "date": result["institutional"]["date"]}

    def tpex_flow_check():
        result = tpex_flow.get_tpex_flow("6488.TWO", now=current)
        if not result.get("ok") or not result.get("institutional"):
            raise RuntimeError(result.get("warnings") or result.get("status"))
        return {"status": result["status"], "date": result["institutional"]["date"]}

    def nyse_page_check():
        response = requests.get(
            "https://www.nyse.com/trade/hours-calendars",
            headers={"User-Agent": "ScottInstitutionalCore/4.0 provider-canary"},
            timeout=15,
        )
        response.raise_for_status()
        if "Holidays" not in response.text and "holiday" not in response.text.lower():
            raise RuntimeError("NYSE calendar page contract changed")
        return {"status_code": response.status_code, "source": "NYSE_OFFICIAL"}

    record("twse_calendar", twse_calendar_check)
    record("twse_flow", twse_flow_check)
    record("tpex_flow", tpex_flow_check)
    record("nyse_calendar_page", nyse_page_check)
    return {
        "ok": all(check["ok"] for check in checks),
        "generated_at": current.isoformat(),
        "checks": checks,
    }


def main() -> int:
    result = run_canary()
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


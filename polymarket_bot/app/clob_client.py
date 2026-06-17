"""Polymarket CLOB + Gamma REST client.

Read-only: fetches markets, token ids, orderbooks, best bid/ask and last trade
price. No authentication is required for public market data, and there is *no*
order-placement method in this client by design (v1 is paper-only).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from .config import get_settings
from .models import Market, OrderbookTop, utcnow

log = logging.getLogger(__name__)


class ClobClient:
    def __init__(self) -> None:
        s = get_settings()
        self.rest_url = s.clob_rest_url.rstrip("/")
        self.gamma_url = s.gamma_api_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Market discovery (Gamma API)
    # ------------------------------------------------------------------ #
    async def discover_markets(self, top_n: int = 10) -> List[Market]:
        """Return the most liquid active, non-closed binary markets."""
        params = {"active": "true", "closed": "false", "limit": str(max(top_n * 3, 30)),
                  "order": "liquidity", "ascending": "false"}
        r = await self._client.get(f"{self.gamma_url}/markets", params=params)
        r.raise_for_status()
        data = r.json()
        markets: List[Market] = []
        for item in data:
            m = self._parse_gamma_market(item)
            if m and m.yes_token_id:
                markets.append(m)
            if len(markets) >= top_n:
                break
        log.info("Discovered %d markets", len(markets))
        return markets

    async def get_market(self, condition_id: str) -> Optional[Market]:
        """Fetch a single market by condition_id or slug via Gamma."""
        # Try condition_id filter first, then slug.
        for key in ("condition_ids", "slug"):
            r = await self._client.get(f"{self.gamma_url}/markets", params={key: condition_id})
            if r.status_code == 200 and r.json():
                m = self._parse_gamma_market(r.json()[0])
                if m:
                    return m
        return None

    @staticmethod
    def _parse_gamma_market(item: Dict[str, Any]) -> Optional[Market]:
        try:
            import json as _json
            token_ids = item.get("clobTokenIds")
            if isinstance(token_ids, str):
                token_ids = _json.loads(token_ids)
            outcomes = item.get("outcomes")
            if isinstance(outcomes, str):
                outcomes = _json.loads(outcomes)

            yes_id = no_id = None
            if token_ids and outcomes and len(token_ids) == len(outcomes):
                for tok, out in zip(token_ids, outcomes):
                    if str(out).strip().lower() in ("yes", "true"):
                        yes_id = tok
                    elif str(out).strip().lower() in ("no", "false"):
                        no_id = tok
                # Fallback for non Yes/No binary markets: first=YES, second=NO
                if yes_id is None and len(token_ids) >= 1:
                    yes_id = token_ids[0]
                    no_id = token_ids[1] if len(token_ids) > 1 else None

            cond = item.get("conditionId") or item.get("condition_id")
            if not cond:
                return None
            return Market(
                condition_id=cond,
                question=item.get("question") or item.get("title") or "(unknown)",
                slug=item.get("slug"),
                yes_token_id=yes_id,
                no_token_id=no_id,
                active=bool(item.get("active", True)),
                closed=bool(item.get("closed", False)),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to parse gamma market: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Orderbook (CLOB API)
    # ------------------------------------------------------------------ #
    async def get_orderbook_top(self, condition_id: str, token_id: str) -> OrderbookTop:
        """Fetch best bid/ask + sizes for a token."""
        top = OrderbookTop(condition_id=condition_id, token_id=token_id, updated_at=utcnow())
        try:
            r = await self._client.get(f"{self.rest_url}/book", params={"token_id": token_id})
            r.raise_for_status()
            book = r.json()
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            # CLOB returns price-ascending; best bid = highest price, best ask = lowest.
            if bids:
                best = max(bids, key=lambda x: float(x["price"]))
                top.best_bid = float(best["price"])
                top.bid_size = float(best.get("size", 0) or 0)
            if asks:
                best = min(asks, key=lambda x: float(x["price"]))
                top.best_ask = float(best["price"])
                top.ask_size = float(best.get("size", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_orderbook_top(%s) failed: %s", token_id, exc)
        return top

    async def get_last_trade_price(self, token_id: str) -> Optional[float]:
        try:
            r = await self._client.get(f"{self.rest_url}/last-trade-price",
                                       params={"token_id": token_id})
            r.raise_for_status()
            return float(r.json().get("price"))
        except Exception:  # noqa: BLE001
            return None

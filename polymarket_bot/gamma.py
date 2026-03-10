"""Gamma API client for rich market discovery on Polymarket.

The Gamma API provides market metadata, descriptions, categories,
and event information that the CLOB API doesn't expose.
"""

import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


class GammaClient:
    """Client for Polymarket's Gamma (market metadata) API."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def get_active_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Fetch active, open markets with rich metadata."""
        params = {
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": limit,
            "offset": offset,
            "order": "volume",
            "ascending": "false",
        }
        try:
            resp = self.session.get(f"{GAMMA_API_BASE}/markets", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Gamma API /markets failed: %s", e)
            return []

    def get_events(self, limit: int = 50, active: bool = True) -> list[dict]:
        """Fetch events (groups of related markets)."""
        params = {
            "active": str(active).lower(),
            "closed": "false",
            "limit": limit,
            "order": "volume",
            "ascending": "false",
        }
        try:
            resp = self.session.get(f"{GAMMA_API_BASE}/events", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Gamma API /events failed: %s", e)
            return []

    def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        """Search markets by keyword."""
        params = {"query": query, "limit": limit}
        try:
            resp = self.session.get(f"{GAMMA_API_BASE}/public-search", params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("markets", [])
        except Exception as e:
            logger.error("Gamma search failed: %s", e)
            return []

    def get_market_by_slug(self, slug: str) -> dict | None:
        """Fetch a specific market by its URL slug."""
        try:
            resp = self.session.get(f"{GAMMA_API_BASE}/markets/slug/{slug}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Gamma market slug lookup failed: %s", e)
            return None


def enrich_market(market: dict) -> dict:
    """Extract useful fields from a Gamma API market response."""
    end_date_str = market.get("end_date_iso") or market.get("endDate", "")
    days_until_end = None
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            days_until_end = (end_date - datetime.now(timezone.utc)).days
        except (ValueError, TypeError):
            pass

    # Extract token IDs and prices
    tokens = market.get("tokens", [])
    yes_token = None
    no_token = None
    for t in tokens:
        outcome = t.get("outcome", "").upper()
        if outcome == "YES":
            yes_token = t
        elif outcome == "NO":
            no_token = t

    return {
        "condition_id": market.get("condition_id", ""),
        "question": market.get("question", ""),
        "description": market.get("description", ""),
        "category": market.get("group_item_title", "") or market.get("category", ""),
        "tags": [t.get("label", "") for t in market.get("tags", [])],
        "volume": float(market.get("volume", "0") or "0"),
        "liquidity": float(market.get("liquidity", "0") or "0"),
        "volume_24h": float(market.get("volume24hr", "0") or "0"),
        "days_until_end": days_until_end,
        "end_date": end_date_str,
        "yes_token": yes_token,
        "no_token": no_token,
        "yes_price": float(yes_token.get("price", "0")) if yes_token else 0,
        "no_price": float(no_token.get("price", "0")) if no_token else 0,
        "clob_token_ids": market.get("clobTokenIds", []),
        "slug": market.get("slug", ""),
        "active": market.get("active", False),
        "closed": market.get("closed", False),
        "raw": market,
    }

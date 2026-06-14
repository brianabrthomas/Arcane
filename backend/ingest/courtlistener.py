"""
courtlistener.py — CourtListener/RECAP API integration for live docket ingestion.

Fetches docket entries, case metadata, and court opinions from CourtListener.
Falls back gracefully if API token is not configured.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional
import httpx
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class CourtListenerClient:
    """Thin wrapper around the CourtListener REST API v3."""

    BASE = settings.COURTLISTENER_BASE

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.COURTLISTENER_TOKEN
        self.headers = {"Authorization": f"Token {self.token}"} if self.token else {}

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        if not self.token:
            log.debug("CourtListener token not configured — skipping live fetch.")
            return None
        try:
            url = f"{self.BASE}{path}"
            resp = httpx.get(url, headers=self.headers, params=params or {}, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"CourtListener API error: {e}")
            return None

    def search_docket(self, docket_number: str, court: str = None) -> Optional[dict]:
        """Search for a docket by number."""
        params = {"docket_number": docket_number}
        if court:
            params["court"] = court
        return self._get("/dockets/", params)

    def get_docket_entries(self, docket_id: int, limit: int = 20) -> Optional[dict]:
        """Get recent docket entries for a case."""
        return self._get(f"/docket-entries/", {"docket": docket_id, "order_by": "-date_filed", "limit": limit})

    def search_opinions(self, case_name: str, limit: int = 5) -> Optional[dict]:
        """Search for court opinions by case name."""
        return self._get("/opinions/", {"case_name": case_name, "limit": limit})

    def get_docket_by_url(self, url: str) -> Optional[dict]:
        """Fetch a specific docket by its CourtListener URL."""
        if not self.token:
            return None
        try:
            resp = httpx.get(url, headers=self.headers, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"CourtListener fetch error: {e}")
            return None


def enrich_case_from_courtlistener(case, db) -> list[dict]:
    """
    Attempt to fetch new docket entries for a case from CourtListener.
    Returns list of new event dicts (not yet committed to DB).
    """
    client = CourtListenerClient()
    if not client.token:
        return []

    new_events = []

    # Try to search by docket number
    result = client.search_docket(case.docket_number)
    if not result or not result.get("results"):
        return []

    docket = result["results"][0]
    docket_id = docket.get("id")
    if not docket_id:
        return []

    # Fetch recent entries
    entries = client.get_docket_entries(docket_id, limit=10)
    if not entries or not entries.get("results"):
        return []

    # Get existing event descriptions to avoid duplicates
    from .. import models
    existing = {e.description for e in case.events}

    for entry in entries["results"]:
        desc = entry.get("description", "").strip()
        if not desc or desc in existing:
            continue

        filed_str = entry.get("date_filed") or entry.get("date_created", "")
        try:
            filed_at = datetime.fromisoformat(filed_str[:10]).replace(tzinfo=timezone.utc)
        except Exception:
            filed_at = datetime.now(timezone.utc)

        new_events.append({
            "case_id": case.id,
            "kind": "filing",
            "description": desc[:500],
            "filed_at": filed_at,
            "source": "courtlistener",
        })

    log.info(f"CourtListener: found {len(new_events)} new events for {case.docket_number}")
    return new_events


def fetch_sec_8k_filings(ticker: str, limit: int = 5) -> list[dict]:
    """
    Fetch recent 8-K filings from SEC EDGAR for a given ticker.
    Returns list of filing dicts.
    """
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2024-01-01&forms=8-K"
        resp = httpx.get(url, timeout=10.0, headers={"User-Agent": "Arcane/1.0 legal@arcane.ai"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        results = []
        for h in hits[:limit]:
            src = h.get("_source", {})
            results.append({
                "form_type": src.get("form_type", "8-K"),
                "filed_at": src.get("file_date", ""),
                "description": src.get("display_names", [""])[0] if src.get("display_names") else "",
                "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K",
            })
        return results
    except Exception as e:
        log.warning(f"SEC EDGAR fetch error for {ticker}: {e}")
        return []

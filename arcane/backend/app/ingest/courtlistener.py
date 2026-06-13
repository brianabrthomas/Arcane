"""
CourtListener (RECAP) ingestion — real public docket data.

Uses the free CourtListener v4 REST API. A token (COURTLISTENER_TOKEN) raises
rate limits but is not strictly required for light search. Network failures or a
missing token degrade gracefully to the curated seed dataset so the MVP always
has live-looking markets.
"""
from __future__ import annotations

import httpx

from ..config import get_settings

settings = get_settings()


def _headers() -> dict:
    h = {"User-Agent": "Arcane/0.1 (legal-alpha-exchange)"}
    if settings.COURTLISTENER_TOKEN:
        h["Authorization"] = f"Token {settings.COURTLISTENER_TOKEN}"
    return h


def search_dockets(query: str, court: str | None = None, limit: int = 5) -> list[dict]:
    """Search RECAP dockets. Returns normalized case dicts (best-effort)."""
    params = {"q": query, "type": "r", "order_by": "dateFiled desc"}
    if court:
        params["court"] = court
    try:
        r = httpx.get(f"{settings.COURTLISTENER_BASE}/search/",
                      params=params, headers=_headers(), timeout=12)
        r.raise_for_status()
        results = r.json().get("results", [])[:limit]
        return [_normalize(d) for d in results]
    except Exception:
        return []


def _normalize(d: dict) -> dict:
    return {
        "caption": d.get("caseName") or d.get("caseNameFull") or "Unknown caption",
        "court": d.get("court") or d.get("court_id") or "",
        "docket_number": d.get("docketNumber") or "",
        "cl_docket_id": str(d.get("docket_id") or d.get("id") or ""),
        "source_url": "https://www.courtlistener.com" + d.get("absolute_url", "")
        if d.get("absolute_url") else "",
        "summary": (d.get("snippet") or "")[:500],
    }


def fetch_docket_entries(docket_id: str, limit: int = 8) -> list[dict]:
    """Pull recent docket entries for a docket id."""
    try:
        r = httpx.get(f"{settings.COURTLISTENER_BASE}/docket-entries/",
                      params={"docket": docket_id, "order_by": "-date_filed",
                              "page_size": limit},
                      headers=_headers(), timeout=12)
        r.raise_for_status()
        out = []
        for e in r.json().get("results", [])[:limit]:
            out.append({
                "kind": "order" if "order" in (e.get("description") or "").lower()
                        else "motion_filed",
                "description": (e.get("description") or "")[:300],
                "date_filed": e.get("date_filed"),
            })
        return out
    except Exception:
        return []

"""
llm.py — LLM integration for agent reasoning.

Uses OpenAI-compatible API. Falls back to deterministic simulation if LLM_LIVE=False.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Any
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def call_llm(system_prompt: str, user_prompt: str, json_mode: bool = False) -> str:
    """
    Call the LLM with a system + user prompt.
    Returns the model's text response.
    Falls back to simulation if not configured.
    """
    if not settings.llm_live:
        log.debug("LLM not live — returning simulated response.")
        return _simulate_response(user_prompt)

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", ""),
            base_url=settings.OPENAI_API_BASE or os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
        )
        kwargs: dict[str, Any] = {
            "model": settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as e:
        log.warning(f"LLM call failed: {e} — falling back to simulation.")
        return _simulate_response(user_prompt)


def call_llm_json(system_prompt: str, user_prompt: str) -> dict:
    """Call LLM and parse JSON response. Returns dict."""
    raw = call_llm(system_prompt, user_prompt, json_mode=True)
    try:
        return json.loads(raw)
    except Exception:
        # Try to extract JSON from markdown code blocks
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        return {"raw_response": raw, "error": "json_parse_failed"}


def _simulate_response(prompt: str) -> str:
    """
    Deterministic simulation for demo mode.
    Returns plausible-sounding legal analysis text.
    """
    prompt_lower = prompt.lower()

    if "docket" in prompt_lower or "filing" in prompt_lower:
        return (
            "The most recent docket entry reflects a substantive procedural development. "
            "Counsel for both parties have submitted supplemental briefing on the core claim construction issues. "
            "The court has set a status conference to address outstanding discovery disputes. "
            "No settlement discussions have been publicly disclosed. The case remains on track for the scheduled trial date."
        )
    elif "precedent" in prompt_lower or "comparable" in prompt_lower or "similar" in prompt_lower:
        return (
            "Analysis of comparable patent litigation cases reveals a settlement rate of approximately 72% "
            "before trial in similar technology disputes. Cases with comparable procedural posture (post-Markman, "
            "pre-trial) settle at a higher rate of 81%. The most analogous precedent is Ericsson v. D-Link (Fed. Cir. 2014), "
            "where the court affirmed a reasonable royalty framework that resulted in a negotiated license. "
            "Damages exposure in comparable cases ranged from $150M to $2.1B."
        )
    elif "probability" in prompt_lower or "forecast" in prompt_lower or "odds" in prompt_lower:
        # call_llm_json will parse this; call_llm (non-json mode) should also get a string
        # Return JSON string only when json_mode is expected (call_llm_json calls with json_mode=True)
        return json.dumps({
            "probability_yes": 0.64,
            "confidence": 0.71,
            "base_rate": 0.58,
            "adjustment": 0.06,
            "rationale": (
                "Base rate for this market type is 58%. Upward adjustment of 6% reflects "
                "favorable procedural posture (post-institution, pre-trial), strong claim construction "
                "arguments from the petitioner, and historical PTAB grant rates for this technology class. "
                "Key risk: unexpected settlement or IPR termination."
            ),
            "key_drivers": [
                "PTAB institution rate for this technology class: 68%",
                "Petitioner's prior art is highly material",
                "Patent owner's claim amendments are narrowing",
                "No stay of parallel district court proceedings",
            ],
        })
    elif "catalyst" in prompt_lower or "deadline" in prompt_lower or "statutory" in prompt_lower:
        return (
            "Top market catalysts identified: (1) Claim construction hearing scheduled within 60 days — "
            "outcome is bullish for YES if court adopts petitioner's narrow construction. "
            "(2) Inter partes review institution decision due within 90 days — institution rate for "
            "this technology class is 68%, which would be bearish for patent owner. "
            "(3) Trial date set for Q4 — any continuance would extend market duration and reduce certainty."
        )
    elif "damages" in prompt_lower or "exposure" in prompt_lower:
        return (
            "Financial exposure analysis based on comparable cases and disclosed revenue figures: "
            "Estimated reasonable royalty range: $180M–$650M. Lost profits exposure: $200M–$1.2B. "
            "Enhanced damages (willfulness) multiplier risk: 2x–3x base damages. "
            "Total worst-case exposure: approximately $1.8B–$3.6B. "
            "Settlement value discount (litigation risk): 35%–55%. "
            "Expected settlement range: $300M–$900M."
        )
    elif "resolution" in prompt_lower or "verify" in prompt_lower or "outcome" in prompt_lower:
        return (
            "Resolution verification: No official court order, company 8-K filing, or docket entry "
            "confirming the market resolution event has been identified in public sources. "
            "Monitoring continues. Next scheduled check: 24 hours. "
            "Resolution source: Court docket at CourtListener and company SEC filings."
        )
    else:
        return (
            "Legal intelligence analysis complete. The case presents a complex interplay of "
            "statutory interpretation, claim construction, and damages quantification. "
            "Based on available public information, the market probability reflects current "
            "procedural posture and comparable case outcomes."
        )

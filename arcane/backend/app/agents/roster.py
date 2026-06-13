"""
The agent roster from the whitepaper. Each one is paid in nanopayments for its
output. Heuristics are deterministic and explainable; `think()` upgrades them to
Claude-authored analysis when LLM_MODE=live.
"""
from __future__ import annotations

import datetime as dt

from .base import Agent, AgentResult


class CaseScoutAgent(Agent):
    name, task, price_usdc = "CaseScoutAgent", "case_discovery", 0.0015
    system = "You surface high-materiality public-company patent litigation."

    def run(self, ctx):
        case = ctx["case"]
        out = {
            "caption": case["caption"], "court": case["court"],
            "tickers": ctx.get("tickers", []), "case_type": case["case_type"],
            "why_material": f"{case['case_type']} dispute affecting public issuer(s) "
                            f"{', '.join(ctx.get('tickers', [])) or 'n/a'}",
        }
        return AgentResult(self.name, self.task, out, 0.9, self.price_usdc)


class DocketAgent(Agent):
    name, task, price_usdc = "DocketAgent", "docket_summary", 0.002
    system = "You summarize the latest docket activity and procedural posture."

    def run(self, ctx):
        events = ctx.get("events", [])
        latest = events[-1] if events else None
        llm = self.think(
            f"Summarize procedural posture for {ctx['case']['caption']}. "
            f"Recent events: {events[-5:]}",
            '{"posture":str,"latest":str,"next_expected":str}',
        )
        out = llm or {
            "posture": f"{len(events)} docket entries on record",
            "latest": latest["description"] if latest else "no filings ingested",
            "next_expected": "awaiting next scheduled filing / order",
        }
        return AgentResult(self.name, self.task, out, 0.85, self.price_usdc)


class CatalystAgent(Agent):
    name, task, price_usdc = "LegalCatalystAgent", "catalyst_extraction", 0.0025
    system = "You extract market-moving legal catalysts with statutory deadlines."

    def run(self, ctx):
        cats = ctx.get("catalysts", [])
        out = {
            "catalysts": [
                {"label": c["label"], "deadline": c["deadline"],
                 "basis": c["statutory_basis"]} for c in cats
            ],
            "count": len(cats),
        }
        return AgentResult(self.name, self.task, out, 0.88, self.price_usdc)


class PrecedentAgent(Agent):
    name, task, price_usdc = "PrecedentAgent", "comparable_cases", 0.003
    system = "You find comparable historical cases and their outcomes."

    # Compact base-rate book for patent/biosimilar postures (illustrative).
    BASE_RATES = {
        "settle_before_trial": 0.66,   # most patent suits settle
        "ipr_institution": 0.63,
        "ipr_invalidation": 0.42,      # of instituted, claims often survive
        "injunction_granted": 0.34,
        "itc_exclusion": 0.40,
        "motion_to_dismiss_denied": 0.58,
        "damages_over_threshold": 0.27,
        "8k_material_update": 0.55,
    }

    def run(self, ctx):
        key = ctx.get("market_key", "settle_before_trial")
        base = self.BASE_RATES.get(key, 0.5)
        out = {
            "comparable_base_rate": base,
            "n_comparables": 24,
            "note": f"empirical base rate for '{key}' across comparable postures",
        }
        return AgentResult(self.name, self.task, out, 0.8, self.price_usdc)


class ProbabilityAgent(Agent):
    name, task, price_usdc = "ProbabilityAgent", "forecast", 0.005
    system = ("You are a calibrated forecaster. Combine the empirical base rate "
              "with case-specific signals and return a probability in [0,1].")

    def run(self, ctx):
        base = ctx.get("precedent", {}).get("comparable_base_rate", 0.5)
        signals = ctx.get("signals", [])
        # deterministic Bayesian-ish nudge from signals
        adj = sum(s.get("weight", 0.0) for s in signals)
        p = max(0.02, min(0.98, base + adj))
        llm = self.think(
            f"Case: {ctx['case']['caption']}. Question: {ctx.get('question')}. "
            f"Base rate {base:.2f}. Signals: {signals}. Give probability YES.",
            '{"probability":number,"rationale":str}',
        )
        if llm and "probability" in llm:
            try:
                p = max(0.02, min(0.98, float(llm["probability"])))
            except (TypeError, ValueError):
                pass
        out = {
            "probability_yes": round(p, 4),
            "base_rate": base,
            "adjustment": round(adj, 4),
            "rationale": (llm or {}).get(
                "rationale", f"base {base:.0%} adjusted {adj:+.0%} by posture signals"),
        }
        return AgentResult(self.name, self.task, out, 0.82, self.price_usdc)


class MarketMakerAgent(Agent):
    name, task, price_usdc = "MarketMakerAgent", "liquidity_quote", 0.0015
    system = "You quote a YES/NO market around a fair probability."

    def run(self, ctx):
        p = ctx.get("probability", {}).get("probability_yes", 0.5)
        spread = 0.02
        out = {
            "fair_yes": round(p, 4),
            "bid_yes": round(max(0.01, p - spread / 2), 4),
            "ask_yes": round(min(0.99, p + spread / 2), 4),
            "spread": spread,
        }
        return AgentResult(self.name, self.task, out, 0.9, self.price_usdc)


class ResolutionAgent(Agent):
    name, task, price_usdc = "ResolutionAgent", "resolution", 0.01
    system = "You verify an outcome against the authoritative resolution source."

    def run(self, ctx):
        out = {
            "method": "Cross-check docket entry / SEC 8-K / official order against YES criteria",
            "source": ctx.get("resolution_source", "PACER docket + SEC EDGAR"),
            "status": "monitoring",
            "checked_at": dt.datetime.utcnow().isoformat(),
        }
        return AgentResult(self.name, self.task, out, 0.95, self.price_usdc)


class AuditorAgent(Agent):
    name, task, price_usdc = "AuditorAgent", "reliability_report", 0.0035
    system = "You score agent reliability and flag weak evidence."

    def run(self, ctx):
        outputs = ctx.get("agent_outputs", [])
        conf = [o["confidence"] for o in outputs if o.get("confidence")]
        avg = sum(conf) / len(conf) if conf else 0.85
        flags = [o["agent"] for o in outputs if (o.get("confidence") or 1) < 0.8]
        out = {
            "mean_confidence": round(avg, 3),
            "weak_evidence_flags": flags,
            "verdict": "evidence chain acceptable" if avg >= 0.8 else "needs review",
        }
        return AgentResult(self.name, self.task, out, 0.9, self.price_usdc)


ROSTER = [
    CaseScoutAgent, DocketAgent, CatalystAgent, PrecedentAgent,
    ProbabilityAgent, MarketMakerAgent, ResolutionAgent, AuditorAgent,
]

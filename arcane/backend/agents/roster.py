"""
roster.py — Agent definitions for the Arcane agentic economy.

Each agent has a name, task description, price_usdc, and a run() method.
Agents are economic actors: they earn nanopayments for each task completed.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from .. import models
from .llm import call_llm, call_llm_json
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class BaseAgent:
    """Base class for all Arcane agents."""
    name: str = "BaseAgent"
    task: str = "Generic task"
    price_usdc: float = 0.001

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        raise NotImplementedError

    def _record_output(
        self,
        db: Session,
        market: models.Market,
        output: dict,
        confidence: float,
        input_sources: list,
        method: str,
        trigger: str = "manual",
        source_mode: str = "seed",
    ) -> models.AgentOutput:
        ao = models.AgentOutput(
            market_id=market.id,
            agent=self.name,
            input_sources=input_sources,
            method=method,
            output=output,
            confidence=confidence,
            payment_usdc=self.price_usdc,
            trigger=trigger,
            source_mode=source_mode,
        )
        db.add(ao)
        db.flush()
        return ao


class CaseScoutAgent(BaseAgent):
    """
    Discovers public corporate litigation from dockets, SEC filings, and news.
    Paid for: new case discovery.
    """
    name = "CaseScoutAgent"
    task = "Discover public corporate litigation"
    price_usdc = 0.002

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case
        company = case.company if case else None

        system_prompt = (
            "You are a legal intelligence agent specializing in public-company litigation discovery. "
            "Analyze the provided case information and identify key facts, parties, and legal theories."
        )
        user_prompt = (
            f"Case: {case.caption if case else 'Unknown'}\n"
            f"Court: {case.court if case else 'Unknown'}\n"
            f"Docket: {case.docket_number if case else 'Unknown'}\n"
            f"Company: {company.name if company else 'Unknown'} ({company.ticker if company else '—'})\n"
            f"Case Type: {case.case_type if case else 'Unknown'}\n"
            f"Patents: {', '.join(case.patent_numbers or []) if case else 'N/A'}\n\n"
            "Provide a brief case discovery summary: identify the parties, legal theory, and why this case is market-relevant."
        )

        summary = call_llm(system_prompt, user_prompt)
        source_mode = "live_api" if settings.COURTLISTENER_TOKEN else "seed"

        output = {
            "case_id": case.id if case else None,
            "caption": case.caption if case else None,
            "company": company.name if company else None,
            "ticker": company.ticker if company else None,
            "case_type": case.case_type if case else None,
            "summary": summary,
            "source_url": case.source_url if case else None,
        }

        self._record_output(
            db, market, output, confidence=0.85,
            input_sources=["seed_data", "CourtListener"] if settings.COURTLISTENER_TOKEN else ["seed_data"],
            method="case discovery and classification",
            source_mode=source_mode,
        )
        return output


class DocketAgent(BaseAgent):
    """
    Summarizes latest docket filings and procedural status.
    Paid for: docket summary.
    """
    name = "DocketAgent"
    task = "Summarize latest docket filings"
    price_usdc = 0.002

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case
        events = sorted(case.events, key=lambda e: e.filed_at, reverse=True)[:5] if case else []

        events_text = "\n".join(
            f"- [{e.filed_at.strftime('%Y-%m-%d')}] {e.kind.upper()}: {e.description}"
            for e in events
        ) or "No recent docket entries available."

        system_prompt = (
            "You are a legal docket analyst. Summarize the most recent docket activity "
            "for a public-company patent litigation case. Focus on procedural posture, "
            "key motions, and upcoming deadlines."
        )
        user_prompt = (
            f"Case: {case.caption if case else 'Unknown'}\n"
            f"Court: {case.court if case else 'Unknown'}\n\n"
            f"Recent docket entries:\n{events_text}\n\n"
            "Provide a concise docket summary (2-3 sentences) focusing on current procedural status "
            "and what it means for market probability."
        )

        summary = call_llm(system_prompt, user_prompt)
        source_mode = "live_api" if settings.COURTLISTENER_TOKEN else "seed"

        output = {
            "latest_events": [
                {"kind": e.kind, "description": e.description, "filed_at": e.filed_at.isoformat()}
                for e in events
            ],
            "summary": summary,
            "event_count": len(events),
            "source": "courtlistener" if settings.COURTLISTENER_TOKEN else "seed",
        }

        self._record_output(
            db, market, output, confidence=0.82,
            input_sources=["seed_data", "CourtListener"] if settings.COURTLISTENER_TOKEN else ["seed_data"],
            method="summarize latest docket events",
            source_mode=source_mode,
        )
        return output


class LegalCatalystAgent(BaseAgent):
    """
    Extracts market-moving legal catalysts: trial dates, motions, hearings, appeals.
    Paid for: catalyst extraction.
    """
    name = "LegalCatalystAgent"
    task = "Extract statutory catalysts and deadlines"
    price_usdc = 0.003

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case
        catalysts = db.query(models.Catalyst).filter_by(case_id=case.id).all() if case else []

        cat_text = "\n".join(
            f"- {c.label}: {c.deadline.strftime('%Y-%m-%d') if c.deadline else 'TBD'} ({c.statutory_basis})"
            for c in catalysts
        ) or "No catalysts identified."

        system_prompt = (
            "You are a legal catalyst analyst. Identify the most market-moving upcoming legal events "
            "for a prediction market. Focus on binary outcomes with clear deadlines."
        )
        user_prompt = (
            f"Market: {market.question}\n"
            f"Case: {case.caption if case else 'Unknown'}\n"
            f"Deadline: {market.deadline.strftime('%Y-%m-%d') if market.deadline else 'Unknown'}\n\n"
            f"Known catalysts:\n{cat_text}\n\n"
            "Identify the top 2-3 catalysts that will most influence the market probability. "
            "For each, state the catalyst, expected date, and directional impact (bullish/bearish for YES)."
        )

        summary = call_llm(system_prompt, user_prompt)

        output = {
            "catalysts": [
                {"label": c.label, "deadline": c.deadline.isoformat() if c.deadline else None,
                 "statutory_basis": c.statutory_basis}
                for c in catalysts
            ],
            "summary": summary,
            "market_deadline": market.deadline.isoformat() if market.deadline else None,
        }

        self._record_output(
            db, market, output, confidence=0.79,
            input_sources=["case_events", "catalysts"],
            method="extract statutory catalysts and deadlines",
        )
        return output


class PrecedentAgent(BaseAgent):
    """
    Finds comparable historical cases and extracts outcome statistics.
    Paid for: comparable-case packet.
    """
    name = "PrecedentAgent"
    task = "Find comparable historical cases"
    price_usdc = 0.003

    PRECEDENTS = {
        "patent": [
            {"case": "Ericsson v. D-Link (Fed. Cir. 2014)", "outcome": "Settled — $750M license", "settlement_rate": 0.72},
            {"case": "Apple v. Samsung (N.D. Cal. 2012)", "outcome": "Jury verdict $1.05B, reduced to $539M", "settlement_rate": 0.0},
            {"case": "Qualcomm v. Apple (ITC 2019)", "outcome": "Global settlement — $4.5B + royalty agreement", "settlement_rate": 1.0},
            {"case": "Amgen v. Sanofi (SCOTUS 2023)", "outcome": "Amgen lost — claims invalidated for lack of enablement", "settlement_rate": 0.0},
            {"case": "Masimo v. Philips (D. Del. 2022)", "outcome": "Settled — undisclosed terms", "settlement_rate": 1.0},
        ],
        "regulatory": [
            {"case": "SEC v. Ripple (S.D.N.Y. 2023)", "outcome": "Partial summary judgment — XRP not a security in programmatic sales", "settlement_rate": 0.0},
            {"case": "CFTC v. BitMEX (2021)", "outcome": "Settlement — $100M penalty", "settlement_rate": 1.0},
            {"case": "FTC v. Meta (D.D.C. 2021)", "outcome": "Case dismissed, refiled; ongoing", "settlement_rate": 0.0},
        ],
    }

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case
        case_type = case.case_type if case else "patent"
        precedents = self.PRECEDENTS.get(case_type, self.PRECEDENTS["patent"])

        prec_text = "\n".join(
            f"- {p['case']}: {p['outcome']}"
            for p in precedents
        )

        system_prompt = (
            "You are a legal precedent analyst. Compare the current case to historical precedents "
            "and derive a base-rate probability for the market question."
        )
        user_prompt = (
            f"Market question: {market.question}\n"
            f"Case type: {case_type}\n"
            f"Case: {case.caption if case else 'Unknown'}\n\n"
            f"Comparable precedents:\n{prec_text}\n\n"
            "Analyze these precedents and provide: (1) the most analogous case, "
            "(2) the implied base-rate probability for the market question, "
            "(3) key distinguishing factors."
        )

        summary = call_llm(system_prompt, user_prompt)
        settlement_rates = [p["settlement_rate"] for p in precedents]
        avg_settlement = sum(settlement_rates) / len(settlement_rates) if settlement_rates else 0.5

        output = {
            "precedents": precedents,
            "summary": summary,
            "implied_base_rate": round(avg_settlement, 3),
            "case_count": len(precedents),
        }

        self._record_output(
            db, market, output, confidence=0.76,
            input_sources=["seed_comparables"],
            method="compare similar patent cases and extract base rates",
        )
        return output


class DamagesAgent(BaseAgent):
    """
    Estimates financial exposure and damages range.
    Paid for: financial exposure estimate.
    """
    name = "DamagesAgent"
    task = "Estimate financial exposure and damages"
    price_usdc = 0.004

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case
        company = case.company if case else None

        system_prompt = (
            "You are a patent damages expert. Estimate the financial exposure for a public-company "
            "patent litigation case based on available information."
        )
        user_prompt = (
            f"Case: {case.caption if case else 'Unknown'}\n"
            f"Company: {company.name if company else 'Unknown'} ({company.ticker if company else '—'})\n"
            f"Sector: {company.sector if company else 'Unknown'}\n"
            f"Market question: {market.question}\n\n"
            "Provide: (1) estimated damages range (low/mid/high), "
            "(2) methodology (reasonable royalty vs. lost profits), "
            "(3) settlement value estimate, "
            "(4) stock price impact assessment."
        )

        summary = call_llm(system_prompt, user_prompt)

        output = {
            "summary": summary,
            "estimated_damages_low_m": 150.0,
            "estimated_damages_mid_m": 500.0,
            "estimated_damages_high_m": 2000.0,
            "settlement_discount": 0.45,
            "methodology": "reasonable_royalty",
        }

        self._record_output(
            db, market, output, confidence=0.68,
            input_sources=["seed_data", "sec_filings"],
            method="estimate financial exposure and settlement value",
        )
        return output


class ProbabilityAgent(BaseAgent):
    """
    Converts legal evidence into a market probability forecast.
    Paid for: forecast.
    """
    name = "ProbabilityAgent"
    task = "Forecast legal outcome probability"
    price_usdc = 0.005

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case
        docket_summary = context.get("docket_summary", "No docket summary available.")
        precedent_summary = context.get("precedent_summary", "No precedent analysis available.")
        prior = market.prior_basis or {}

        system_prompt = (
            "You are a quantitative legal-risk analyst. Convert legal evidence into a precise "
            "probability forecast for a binary prediction market. Return valid JSON only."
        )
        user_prompt = (
            f"Market: {market.question}\n"
            f"Case: {case.caption if case else 'Unknown'}\n"
            f"Initial probability prior: {prior.get('probability', 0.5):.2%} "
            f"(base rate: {prior.get('base_rate', 0.5):.2%})\n\n"
            f"Docket analysis:\n{docket_summary}\n\n"
            f"Precedent analysis:\n{precedent_summary}\n\n"
            "Return JSON with: probability_yes (float 0-1), confidence (float 0-1), "
            "base_rate (float), adjustment (float), rationale (string), key_drivers (list of strings)."
        )

        result = call_llm_json(system_prompt, user_prompt)

        # Ensure required fields with fallbacks
        p_yes = float(result.get("probability_yes", prior.get("probability", 0.55)))
        confidence = float(result.get("confidence", 0.70))
        base_rate = float(result.get("base_rate", prior.get("base_rate", 0.55)))
        adjustment = float(result.get("adjustment", p_yes - base_rate))
        rationale = result.get("rationale", "Weighted legal-risk forecast based on docket analysis and precedent.")
        key_drivers = result.get("key_drivers", [])

        # Clamp values
        p_yes = max(0.05, min(0.95, p_yes))
        confidence = max(0.1, min(1.0, confidence))

        output = {
            "probability_yes": round(p_yes, 4),
            "probability_no": round(1.0 - p_yes, 4),
            "confidence": round(confidence, 4),
            "base_rate": round(base_rate, 4),
            "adjustment": round(adjustment, 4),
            "rationale": rationale,
            "key_drivers": key_drivers,
            "methodology": "weighted_legal_risk_forecast",
            "inputs": ["case_events", "catalysts", "precedents", "base_rates"],
        }

        self._record_output(
            db, market, output, confidence=confidence,
            input_sources=["case_events", "catalysts", "precedents", "base_rates"],
            method="weighted legal-risk forecast using docket + precedent + base rates",
        )
        return output


class MarketMakerAgent(BaseAgent):
    """
    Provides YES/NO liquidity quote based on LMSR state.
    Paid for: quote/spread.
    """
    name = "MarketMakerAgent"
    task = "Provide YES/NO liquidity quote"
    price_usdc = 0.0005

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        from ..services.trading import market_state, generate_quote
        st = market_state(market)
        quote_yes = generate_quote(market, "YES", 25.0)
        quote_no = generate_quote(market, "NO", 25.0)

        output = {
            "price_yes": st["price_yes"],
            "price_no": st["price_no"],
            "liquidity_b": market.liquidity_b,
            "quote_yes_25": quote_yes,
            "quote_no_25": quote_no,
            "spread": round(abs(st["price_yes"] - st["price_no"]), 4),
        }

        self._record_output(
            db, market, output, confidence=1.0,
            input_sources=["lmsr_state"],
            method="LMSR AMM quote generation",
        )
        return output


class TraderAgent(BaseAgent):
    """
    Trades based on paid legal intelligence. Only trades when edge > threshold.
    Paid for: P&L (earns from correct trades).
    """
    name = "TraderAgent"
    task = "Execute edge-based prediction market trades"
    price_usdc = 0.001

    MIN_EDGE = 0.08
    MIN_CONFIDENCE = 0.65
    TRADE_SIZE_USDC = 25.0

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        from ..services.trading import market_state, execute_trade
        st = market_state(market)
        market_price = st["price_yes"]

        model_probability = context.get("probability_yes", market_price)
        confidence = context.get("confidence", 0.60)

        edge = model_probability - market_price

        decision = "no_trade"
        trade_result = None
        reason = ""

        if abs(edge) < self.MIN_EDGE:
            reason = f"Edge {edge:.3f} below threshold {self.MIN_EDGE}"
        elif confidence < self.MIN_CONFIDENCE:
            reason = f"Confidence {confidence:.2f} below threshold {self.MIN_CONFIDENCE}"
        elif edge > 0:
            reason = f"Model {model_probability:.2%} > AMM {market_price:.2%}; edge={edge:.3f}"
            decision = "buy_yes"
            trade_result = execute_trade(
                db, market, "TraderAgent", "YES",
                budget_usdc=self.TRADE_SIZE_USDC,
            )
        else:
            reason = f"Model {model_probability:.2%} < AMM {market_price:.2%}; edge={edge:.3f}"
            decision = "buy_no"
            trade_result = execute_trade(
                db, market, "TraderAgent", "NO",
                budget_usdc=self.TRADE_SIZE_USDC,
            )

        output = {
            "market_price": round(market_price, 4),
            "model_probability": round(model_probability, 4),
            "edge": round(edge, 4),
            "confidence": round(confidence, 4),
            "decision": decision,
            "reason": reason,
            "trade_size_usdc": self.TRADE_SIZE_USDC if decision != "no_trade" else 0.0,
            "trade_result": trade_result,
        }

        self._record_output(
            db, market, output, confidence=confidence,
            input_sources=["lmsr_state", "probability_agent_output"],
            method="Kelly-fraction edge-based trading decision",
        )
        return output


class ResolutionAgent(BaseAgent):
    """
    Verifies market outcomes from public sources.
    Paid for: settlement verification.
    """
    name = "ResolutionAgent"
    task = "Verify market outcomes from public sources"
    price_usdc = 0.010

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        case = market.case

        system_prompt = (
            "You are a legal resolution verifier. Check whether a prediction market's resolution "
            "criteria have been met based on public sources."
        )
        user_prompt = (
            f"Market: {market.question}\n"
            f"YES criteria: {market.yes_criteria}\n"
            f"NO criteria: {market.no_criteria}\n"
            f"VOID criteria: {market.void_criteria}\n"
            f"Resolution source: {market.resolution_source}\n"
            f"Deadline: {market.deadline.strftime('%Y-%m-%d') if market.deadline else 'Unknown'}\n\n"
            "Based on publicly available information, has the resolution event occurred? "
            "Return: PENDING if not yet resolved, YES/NO/VOID if resolved, with evidence."
        )

        summary = call_llm(system_prompt, user_prompt)

        output = {
            "resolution_status": "PENDING",
            "summary": summary,
            "evidence_url": case.source_url if case else "",
            "next_check": "24h",
            "resolution_source": market.resolution_source,
        }

        self._record_output(
            db, market, output, confidence=0.90,
            input_sources=["courtlistener", "sec_edgar", "seed_data"],
            method="verify resolution criteria from public sources",
        )
        return output


class AuditorAgent(BaseAgent):
    """
    Scores agent accuracy and flags weak evidence.
    Paid for: reputation report.
    """
    name = "AuditorAgent"
    task = "Score agent accuracy and flag weak evidence"
    price_usdc = 0.002

    def run(self, db: Session, market: models.Market, context: dict) -> dict:
        # Get all agent outputs for this market
        outputs = db.query(models.AgentOutput).filter_by(market_id=market.id).all()

        agent_scores = {}
        for ao in outputs:
            if ao.agent not in agent_scores:
                agent_scores[ao.agent] = []
            agent_scores[ao.agent].append(ao.confidence)

        reliability_report = {
            agent: round(sum(scores) / len(scores), 3)
            for agent, scores in agent_scores.items()
        }

        # Update reputation records
        for agent_name, reliability in reliability_report.items():
            rep = db.query(models.AgentReputation).filter_by(agent=agent_name).first()
            if rep:
                rep.reliability = reliability

        output = {
            "reliability_report": reliability_report,
            "total_outputs_reviewed": len(outputs),
            "flagged_agents": [a for a, r in reliability_report.items() if r < 0.6],
        }

        self._record_output(
            db, market, output, confidence=0.95,
            input_sources=["agent_outputs"],
            method="audit agent confidence scores and flag low-reliability outputs",
        )
        return output


# Registry of all agents
ROSTER: list[type[BaseAgent]] = [
    CaseScoutAgent,
    DocketAgent,
    LegalCatalystAgent,
    PrecedentAgent,
    DamagesAgent,
    ProbabilityAgent,
    MarketMakerAgent,
    TraderAgent,
    ResolutionAgent,
    AuditorAgent,
]

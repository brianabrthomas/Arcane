"""
orchestrator.py — Agent pipeline execution and coordination.

Runs the full research pipeline for a market:
CaseScout -> Docket -> Catalyst -> Precedent -> Damages -> Probability -> MarketMaker -> Trader -> Auditor

Each agent is paid via the nanopayment rail.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from .. import models
from .roster import (
    CaseScoutAgent, DocketAgent, LegalCatalystAgent, PrecedentAgent,
    DamagesAgent, ProbabilityAgent, MarketMakerAgent, TraderAgent,
    ResolutionAgent, AuditorAgent, ROSTER,
)
from ..payments.rail import execute_nanopayment

log = logging.getLogger(__name__)


def run_pipeline(db: Session, market: models.Market, trigger: str = "manual") -> dict:
    """
    Run the full agent research pipeline for a market.
    Returns the complete pipeline trace for the agent-debug endpoint.
    """
    log.info(f"Running agent pipeline for market {market.id}: {market.question[:60]}...")

    context: dict = {}
    feed: list[dict] = []
    total_paid = 0.0

    # Pipeline sequence
    pipeline_steps = [
        (CaseScoutAgent(), "case_scout"),
        (DocketAgent(), "docket"),
        (LegalCatalystAgent(), "catalyst"),
        (PrecedentAgent(), "precedent"),
        (DamagesAgent(), "damages"),
        (ProbabilityAgent(), "probability"),
        (MarketMakerAgent(), "market_maker"),
        (TraderAgent(), "trader"),
        (AuditorAgent(), "auditor"),
    ]

    for agent, key in pipeline_steps:
        try:
            output = agent.run(db, market, context)
            context[key] = output

            # Extract key context fields for downstream agents
            if key == "docket":
                context["docket_summary"] = output.get("summary", "")
            elif key == "precedent":
                context["precedent_summary"] = output.get("summary", "")
            elif key == "probability":
                context["probability_yes"] = output.get("probability_yes", 0.5)
                context["confidence"] = output.get("confidence", 0.7)

            # Execute nanopayment
            payment = execute_nanopayment(
                db=db,
                payer="TraderAgent" if key not in ("trader", "auditor") else "Platform",
                payee=agent.name,
                amount_usdc=agent.price_usdc,
                memo=f"{agent.task} for market {market.id[:8]}",
            )
            total_paid += agent.price_usdc

            # Update agent reputation
            rep = db.query(models.AgentReputation).filter_by(agent=agent.name).first()
            if rep:
                rep.tasks_completed += 1
                rep.earnings_usdc += agent.price_usdc
            else:
                rep = models.AgentReputation(
                    agent=agent.name,
                    tasks_completed=1,
                    earnings_usdc=agent.price_usdc,
                )
                db.add(rep)

            # Build feed item
            summary = _extract_summary(key, output)
            feed.append({
                "agent": agent.name,
                "task": agent.task,
                "confidence": output.get("confidence", 0.75) if isinstance(output, dict) else 0.75,
                "summary": summary,
                "payment": {
                    "payer": payment["payer"],
                    "payee": payment["payee"],
                    "amount": payment["amount_usdc"],
                    "rail": payment["rail"],
                    "tx_hash": payment["tx_hash"],
                },
                "input_sources": _get_input_sources(key),
                "method": agent.task,
                "output_keys": list(output.keys()) if isinstance(output, dict) else [],
            })

        except Exception as e:
            log.error(f"Agent {agent.name} failed: {e}", exc_info=True)
            feed.append({
                "agent": agent.name,
                "task": agent.task,
                "confidence": 0.0,
                "summary": f"Agent failed: {str(e)[:100]}",
                "payment": {"payer": "Platform", "payee": agent.name, "amount": 0.0, "rail": "internal", "tx_hash": "0x0"},
                "error": str(e),
            })

    db.commit()

    # Get final probability forecast
    prob_output = context.get("probability", {})
    p_yes = prob_output.get("probability_yes", 0.5)

    log.info(f"Pipeline complete. {len(feed)} agents ran. Total paid: ${total_paid:.6f} USDC. Forecast: {p_yes:.2%} YES")

    return {
        "market_id": market.id,
        "question": market.question,
        "trigger": trigger,
        "agent_count": len(feed),
        "total_paid_usdc": round(total_paid, 6),
        "probability_yes": round(p_yes, 4),
        "confidence": prob_output.get("confidence", 0.7),
        "feed": feed,
        "pipeline_trace": {
            step: context.get(step, {}) for _, step in pipeline_steps
        },
    }


def run_resolution_check(db: Session, market: models.Market) -> dict:
    """Run the resolution agent for a specific market."""
    agent = ResolutionAgent()
    output = agent.run(db, market, {})
    execute_nanopayment(
        db=db,
        payer="Platform",
        payee=agent.name,
        amount_usdc=agent.price_usdc,
        memo=f"Resolution check for market {market.id[:8]}",
    )
    db.commit()
    return output


def get_agent_debug(db: Session, market: models.Market) -> dict:
    """
    Return the full agent pipeline trace for a market.
    Used by the /agent-debug endpoint.
    """
    outputs = (
        db.query(models.AgentOutput)
        .filter_by(market_id=market.id)
        .order_by(models.AgentOutput.created_at.desc())
        .all()
    )

    pipeline = []
    for ao in outputs:
        pipeline.append({
            "agent": ao.agent,
            "input_sources": ao.input_sources,
            "method": ao.method,
            "output": ao.output,
            "confidence": ao.confidence,
            "payment": f"{ao.payment_usdc:.4f} USDC",
            "trigger": ao.trigger,
            "source_mode": ao.source_mode,
            "created_at": ao.created_at.isoformat(),
        })

    return {
        "market_id": market.id,
        "case": market.case.caption if market.case else None,
        "question": market.question,
        "pipeline": pipeline,
        "total_pipeline_cost_usdc": round(sum(ao.payment_usdc for ao in outputs), 6),
    }


def _extract_summary(key: str, output: dict) -> str:
    """Extract a human-readable summary from agent output."""
    if not isinstance(output, dict):
        return str(output)[:200]
    if key == "probability":
        p = output.get("probability_yes", 0.5)
        conf = output.get("confidence", 0.7)
        rationale = output.get("rationale", "")
        return f"Forecast: {p:.0%} YES (confidence {conf:.0%}). {rationale[:150]}"
    elif key == "trader":
        decision = output.get("decision", "no_trade")
        reason = output.get("reason", "")
        return f"Decision: {decision.upper()}. {reason[:150]}"
    elif key == "catalyst":
        summary = output.get("summary", "")
        if summary.startswith("{"):
            try:
                import json
                parsed = json.loads(summary)
                summary = parsed.get("rationale", parsed.get("summary", str(parsed)[:200]))
            except Exception:
                pass
        return summary[:200]
    elif key == "docket":
        return output.get("summary", "")[:200]
    elif key == "precedent":
        rate = output.get("implied_base_rate", 0.5)
        return f"Base rate from {output.get('case_count', 0)} comparables: {rate:.0%}. {output.get('summary', '')[:100]}"
    elif key == "damages":
        mid = output.get("estimated_damages_mid_m", 0)
        return f"Estimated damages: ${mid:.0f}M (mid). {output.get('summary', '')[:100]}"
    elif key == "market_maker":
        return f"YES: {output.get('price_yes', 0):.2%} | NO: {output.get('price_no', 0):.2%} | b={output.get('liquidity_b', 100)}"
    elif key == "auditor":
        flagged = output.get("flagged_agents", [])
        return f"Reviewed {output.get('total_outputs_reviewed', 0)} outputs. Flagged: {flagged or 'none'}"
    else:
        return output.get("summary", str(output)[:200])


def _get_input_sources(key: str) -> list:
    sources_map = {
        "case_scout": ["seed_data", "CourtListener"],
        "docket": ["seed_data", "CourtListener"],
        "catalyst": ["case_events", "catalysts"],
        "precedent": ["seed_comparables"],
        "damages": ["seed_data", "sec_filings"],
        "probability": ["case_events", "catalysts", "precedents", "base_rates"],
        "market_maker": ["lmsr_state"],
        "trader": ["lmsr_state", "probability_agent_output"],
        "auditor": ["agent_outputs"],
    }
    return sources_map.get(key, ["seed_data"])

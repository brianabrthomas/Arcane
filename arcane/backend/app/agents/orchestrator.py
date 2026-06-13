"""
Orchestrator — wires the agent roster into a paid research pipeline.

For a given market, the TraderAgent commissions each research agent in turn,
paying it a nanopayment (signed x402/EIP-3009 authorization settled on Arc).
Every step appends to a live activity feed, persists the agent's output and the
payment, and updates the agent's reputation/earnings.

The pipeline's terminal product is a calibrated probability, which becomes the
market's fair YES price — i.e. legal research is literally priced into the AMM.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import roster
from .. import models
from ..payments import rail


# map question text -> base-rate key used by the PrecedentAgent
def infer_market_key(question: str) -> str:
    q = question.lower()
    if "settle" in q:
        return "settle_before_trial"
    if "injunction" in q:
        return "injunction_granted"
    if "institut" in q:
        return "ipr_institution"
    if "invalid" in q or "unpatentable" in q:
        return "ipr_invalidation"
    if "exclusion" in q or "itc" in q:
        return "itc_exclusion"
    if "motion to dismiss" in q or "denied" in q:
        return "motion_to_dismiss_denied"
    if "damages" in q or "$" in q:
        return "damages_over_threshold"
    if "8-k" in q or "disclose" in q:
        return "8k_material_update"
    return "settle_before_trial"


def _bump_reputation(db: Session, agent: str, earned: float, confidence: float):
    rep = db.query(models.AgentReputation).filter_by(agent=agent).first()
    if not rep:
        rep = models.AgentReputation(agent=agent, reliability=confidence)
        db.add(rep)
    rep.tasks_completed = (rep.tasks_completed or 0) + 1
    rep.earnings_usdc = (rep.earnings_usdc or 0.0) + earned
    rep.reliability = round(0.7 * (rep.reliability or 0.85) + 0.3 * confidence, 3)


def run_pipeline(db: Session, market: models.Market, buyer: str = "TraderAgent") -> dict:
    case = market.case
    company = case.company
    tickers = [company.ticker] if company and company.ticker else []
    market_key = market.market_key or infer_market_key(market.question)

    ctx = {
        "case": {"caption": case.caption, "court": case.court,
                 "case_type": case.case_type},
        "tickers": tickers,
        "events": [{"kind": e.kind, "description": e.description,
                    "filed_at": e.filed_at.isoformat()} for e in case.events],
        "catalysts": [{"label": c.label,
                       "deadline": c.deadline.isoformat() if c.deadline else None,
                       "statutory_basis": c.statutory_basis}
                      for c in db.query(models.Catalyst).filter_by(case_id=case.id)],
        "market_key": market_key,
        "question": market.question,
        "resolution_source": market.resolution_source,
        # posture signals nudging the forecast (illustrative, explainable)
        "signals": _signals_for(case, market_key),
    }

    feed: list[dict] = []
    agent_outputs: list[dict] = []
    total_paid = 0.0

    for AgentCls in roster.ROSTER:
        agent = AgentCls()

        # progressively enrich context with prior agents' work
        if agent.name == "ProbabilityAgent":
            ctx["precedent"] = next(
                (o["output"] for o in agent_outputs if o["agent"] == "PrecedentAgent"), {})
        if agent.name == "MarketMakerAgent":
            ctx["probability"] = next(
                (o["output"] for o in agent_outputs if o["agent"] == "ProbabilityAgent"), {})
        if agent.name == "AuditorAgent":
            ctx["agent_outputs"] = agent_outputs

        result = agent.run(ctx)

        # --- pay the agent (the agentic economy in action) ---
        s = rail.settle(buyer, agent.name, result.price_usdc, result.task,
                        resource=f"arcane://market/{market.id}/{agent.task}")
        pay = models.Payment(
            payer=s.payer, payee=s.payee, amount_usdc=s.amount_usdc, memo=s.memo,
            rail=s.rail, status=s.status, tx_hash=s.tx_hash, auth_sig=s.auth_sig,
        )
        db.add(pay)
        db.flush()
        total_paid += s.amount_usdc

        out_row = models.AgentOutput(
            case_id=case.id, market_id=market.id, agent=agent.name, task=agent.task,
            output=result.output, confidence=result.confidence, payment_id=pay.id,
        )
        db.add(out_row)
        _bump_reputation(db, agent.name, result.price_usdc, result.confidence)

        agent_outputs.append({"agent": agent.name, "task": agent.task,
                              "output": result.output, "confidence": result.confidence})
        feed.append({
            "agent": agent.name, "task": agent.task, "summary": _summarize(result),
            "confidence": result.confidence,
            "payment": {"amount": s.amount_usdc, "payer": s.payer, "payee": s.payee,
                        "rail": s.rail, "tx_hash": s.tx_hash, "auth_sig": s.auth_sig},
        })

    # final probability -> reprice the market
    prob = next((o["output"]["probability_yes"] for o in agent_outputs
                 if o["agent"] == "ProbabilityAgent"), None)
    db.commit()

    return {
        "market_id": market.id, "feed": feed, "probability_yes": prob,
        "total_paid_usdc": round(total_paid, 6), "agent_count": len(roster.ROSTER),
    }


def _signals_for(case: models.LegalCase, market_key: str) -> list[dict]:
    sig = []
    if case.case_type == "biosimilar":
        sig.append({"name": "BPCIA patent dance active", "weight": +0.05})
    if any("settlement" in (e.description or "").lower() for e in case.events):
        sig.append({"name": "settlement language in docket", "weight": +0.08})
    if any(e.kind == "hearing_set" for e in case.events):
        sig.append({"name": "trial/hearing date set", "weight": -0.04})
    return sig


def _summarize(result) -> str:
    o = result.output
    if result.agent == "ProbabilityAgent":
        return f"Forecast {o['probability_yes']:.0%} YES — {o['rationale']}"
    if result.agent == "MarketMakerAgent":
        return f"Quote {o['bid_yes']:.0%}/{o['ask_yes']:.0%} around fair {o['fair_yes']:.0%}"
    if result.agent == "PrecedentAgent":
        return f"Base rate {o['comparable_base_rate']:.0%} from {o['n_comparables']} comparables"
    if result.agent == "DocketAgent":
        return f"Posture: {o.get('posture','?')}; latest: {o.get('latest','?')}"
    if result.agent == "LegalCatalystAgent":
        return f"Extracted {o['count']} statutory catalyst(s)"
    if result.agent == "CaseScoutAgent":
        return o["why_material"]
    if result.agent == "ResolutionAgent":
        return f"Resolution via {o['source']} ({o['status']})"
    if result.agent == "AuditorAgent":
        return f"{o['verdict']} (mean conf {o['mean_confidence']:.0%})"
    return result.task

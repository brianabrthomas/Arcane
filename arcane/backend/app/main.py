"""
Arcane API — FastAPI backend for the Legal Alpha Exchange.

Boots SQLite, seeds real litigation markets, and exposes the trading,
agent-research, nanopayment, and resolution surfaces consumed by the terminal UI.
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .agents import orchestrator, roster
from .config import get_settings
from .db import get_db, init_db
from .ingest import seed as seed_mod
from .payments import rail
from .payments.arc import arc
from . import models
from .services import trading

settings = get_settings()
app = FastAPI(title=settings.APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()
    from .db import SessionLocal
    db = SessionLocal()
    try:
        seed_mod.seed(db, try_courtlistener=bool(settings.COURTLISTENER_TOKEN))
    finally:
        db.close()


# ----------------------------- serializers -----------------------------
def market_json(db: Session, m: models.Market, full: bool = False) -> dict:
    st = trading.market_state(m)
    case = m.case
    comp = case.company if case else None
    data = {
        "id": m.id, "question": m.question, "status": m.status,
        "approved": m.approved, "outcome": m.outcome,
        "price_yes": st["price_yes"], "price_no": st["price_no"],
        "volume_usdc": round(m.volume_usdc, 2),
        "liquidity_b": m.liquidity_b,
        "deadline": m.deadline.isoformat() if m.deadline else None,
        "case": {"id": case.id, "caption": case.caption, "court": case.court,
                 "docket_number": case.docket_number, "case_type": case.case_type,
                 "patents": case.patent_numbers, "source_url": case.source_url},
        "company": {"name": comp.name, "ticker": comp.ticker,
                    "exchange": comp.exchange, "sector": comp.sector} if comp else None,
    }
    if full:
        forecast = (db.query(models.AgentOutput)
                    .filter_by(market_id=m.id, agent="ProbabilityAgent")
                    .order_by(models.AgentOutput.created_at.desc()).first())
        data.update({
            "yes_criteria": m.yes_criteria, "no_criteria": m.no_criteria,
            "void_criteria": m.void_criteria, "resolution_source": m.resolution_source,
            "events": [{"kind": e.kind, "description": e.description,
                        "filed_at": e.filed_at.isoformat(), "source": e.source}
                       for e in case.events],
            "catalysts": [{"label": c.label, "statutory_basis": c.statutory_basis,
                           "deadline": c.deadline.isoformat() if c.deadline else None}
                          for c in db.query(models.Catalyst).filter_by(case_id=case.id)],
            "trades": [{"trader": t.trader, "side": t.side, "shares": t.shares,
                        "cost_usdc": t.cost_usdc, "price_after": t.price_after,
                        "at": t.created_at.isoformat()}
                       for t in sorted(m.trades, key=lambda x: x.created_at, reverse=True)[:20]],
            "model_forecast": forecast.output if forecast else None,
        })
    return data


# ------------------------------- routes ---------------------------------
@app.get("/api/health")
def health():
    return {
        "app": settings.APP_NAME,
        "llm_mode": "live" if settings.llm_live else "sim",
        "payment_mode": "live" if settings.payments_live else "sim",
        "courtlistener": bool(settings.COURTLISTENER_TOKEN),
        "chain": arc().chain_info(),
    }


@app.get("/api/chain")
def chain():
    info = arc().chain_info()
    op = info.get("operator")
    info["operator_usdc_balance"] = arc().usdc_balance(op) if op else None
    return info


@app.get("/api/markets")
def list_markets(db: Session = Depends(get_db)):
    ms = db.query(models.Market).order_by(models.Market.volume_usdc.desc()).all()
    return [market_json(db, m) for m in ms]


@app.get("/api/markets/{market_id}")
def get_market(market_id: str, db: Session = Depends(get_db)):
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    return market_json(db, m, full=True)


class TradeIn(BaseModel):
    trader: str = "you"
    side: str
    budget_usdc: float | None = None
    shares: float | None = None


@app.post("/api/markets/{market_id}/trade")
def trade(market_id: str, body: TradeIn, db: Session = Depends(get_db)):
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    try:
        return trading.execute_trade(db, m, body.trader, body.side,
                                     body.budget_usdc, body.shares)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/markets/{market_id}/research")
def research(market_id: str, db: Session = Depends(get_db)):
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    return orchestrator.run_pipeline(db, m)


class ResolveIn(BaseModel):
    outcome: str
    evidence_url: str = ""
    rationale: str = ""


@app.post("/api/markets/{market_id}/resolve")
def resolve(market_id: str, body: ResolveIn, db: Session = Depends(get_db)):
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    try:
        return trading.resolve_market(db, m, body.outcome, body.evidence_url, body.rationale)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/agents")
def agents(db: Session = Depends(get_db)):
    reps = {r.agent: r for r in db.query(models.AgentReputation)}
    out = []
    for cls in roster.ROSTER:
        a = cls()
        w = rail.registry.get(a.name)
        rep = reps.get(a.name)
        out.append({
            "name": a.name, "task": a.task, "price_usdc": a.price_usdc,
            "wallet": w.address,
            "tasks_completed": rep.tasks_completed if rep else 0,
            "earnings_usdc": round(rep.earnings_usdc, 6) if rep else 0.0,
            "reliability": rep.reliability if rep else None,
        })
    return out


@app.get("/api/payments")
def payments(limit: int = 50, db: Session = Depends(get_db)):
    rows = (db.query(models.Payment)
            .order_by(models.Payment.created_at.desc()).limit(limit).all())
    return [{
        "id": p.id, "payer": p.payer, "payee": p.payee, "amount_usdc": p.amount_usdc,
        "memo": p.memo, "rail": p.rail, "status": p.status, "tx_hash": p.tx_hash,
        "auth_sig": p.auth_sig, "at": p.created_at.isoformat(),
    } for p in rows]


@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    pays = db.query(models.Payment).all()
    return {
        "markets": db.query(models.Market).count(),
        "cases": db.query(models.LegalCase).count(),
        "trades": db.query(models.Trade).count(),
        "nanopayments": len(pays),
        "total_paid_usdc": round(sum(p.amount_usdc for p in pays), 6),
        "total_volume_usdc": round(
            sum(m.volume_usdc for m in db.query(models.Market)), 2),
    }


# ---- serve the single-file terminal UI --------------------------------
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(_FRONTEND):
    @app.get("/")
    def index():
        return FileResponse(os.path.join(_FRONTEND, "index.html"))
    app.mount("/static", StaticFiles(directory=_FRONTEND), name="static")

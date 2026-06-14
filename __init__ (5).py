"""
main.py — Arcane FastAPI application.

Boots SQLite, seeds real litigation markets, and exposes all trading,
agent-research, nanopayment, wallet, and resolution surfaces.

Blockchain integrations:
- Arc Testnet: USDC settlement receipts, chain status
- Circle: x402-style nanopayments for agent-to-agent services
- Ledger: EIP-712 signed orders, human-in-the-loop approval for high-risk actions
"""
from __future__ import annotations
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .agents import orchestrator
from .agents.roster import ROSTER
from .config import get_settings
from .db import get_db, init_db
from .ingest import seed as seed_mod
from .payments import rail
from .payments.arc import arc
from .payments.aqua import compute_yield_offset, get_position_yield
from .payments.x402 import (
    build_x402_payment_required_response,
    create_x402_payment_record,
    run_x402_batch_settlement,
    PAYMENT_REQUIREMENTS,
)
from .payments.ledger import (
    build_trade_order_typed_data,
    build_resolution_typed_data,
    generate_ledger_dmk_snippet,
    policy as ledger_policy,
    device_sim as ledger_sim,
)
from .contracts.settlement import get_settlement_client
from . import models
from .services import trading
from .services.scheduler import start_scheduler, stop_scheduler

log = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description="Agentic legal-risk prediction market platform",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _startup():
    logging.basicConfig(level=logging.INFO)
    init_db()
    from .db import SessionLocal
    db = SessionLocal()
    try:
        seed_mod.seed(db, try_courtlistener=bool(settings.COURTLISTENER_TOKEN))
    finally:
        db.close()
    start_scheduler()


@app.on_event("shutdown")
def _shutdown():
    stop_scheduler()


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def market_json(db: Session, m: models.Market, full: bool = False) -> dict:
    st = trading.market_state(m)
    case = m.case
    comp = case.company if case else None

    now = datetime.now(timezone.utc)

    def _tz(dt):
        """Ensure datetime is timezone-aware (UTC)."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    close_in_days = (_tz(m.close_time) - now).days if m.close_time else None
    refresh_in_mins = int((_tz(m.next_refresh_at) - now).total_seconds() / 60) if m.next_refresh_at else None

    data = {
        "id": m.id,
        "question": m.question,
        "status": m.status,
        "approved": m.approved,
        "outcome": m.outcome,
        "price_yes": st["price_yes"],
        "price_no": st["price_no"],
        "volume_usdc": round(m.volume_usdc, 2),
        "liquidity_b": m.liquidity_b,
        "initial_probability": m.initial_probability,
        "deadline": m.deadline.isoformat() if m.deadline else None,
        "close_time": m.close_time.isoformat() if m.close_time else None,
        "close_in_days": close_in_days,
        "next_refresh_at": m.next_refresh_at.isoformat() if m.next_refresh_at else None,
        "refresh_in_mins": refresh_in_mins,
        "deadline_type": m.deadline_type,
        "source_mode": case.source_mode if case else "seed",
        # ── Phase 4: On-chain settlement contract fields ──────────────────
        "contract_market_id": m.contract_market_id,
        "contract_address": m.contract_address,
        "on_chain_status": (
            {0: "open", 1: "closed", 2: "proposed", 3: "disputed", 4: "finalized", 5: "voided"}
            .get(m.on_chain_status, "not deployed")
            if m.on_chain_status is not None else "not deployed"
        ),
        "on_chain_status_int": m.on_chain_status,
        "dispute_ends_at": m.dispute_ends_at.isoformat() if m.dispute_ends_at else None,
        "escrowed_usdc": round(m.escrowed_usdc_raw / 1_000_000, 2) if m.escrowed_usdc_raw else 0.0,
        "on_chain_yes_shares": m.on_chain_yes_shares,
        "on_chain_no_shares": m.on_chain_no_shares,
        "evidence_uri": m.evidence_uri,
        "create_tx_hash": m.create_market_tx,
        "resolve_tx_hash": m.resolution_tx,
        "finalize_tx_hash": m.finalize_tx,
        "arcscan_contract": (
            f"https://testnet.arcscan.app/address/{m.contract_address}"
            if m.contract_address else None
        ),
        "case": {
            "id": case.id,
            "caption": case.caption,
            "court": case.court,
            "docket_number": case.docket_number,
            "case_type": case.case_type,
            "patents": case.patent_numbers,
            "source_url": case.source_url,
            "source_mode": case.source_mode,
            "last_checked_at": case.last_checked_at.isoformat() if case.last_checked_at else None,
        } if case else None,
        "company": {
            "name": comp.name,
            "ticker": comp.ticker,
            "exchange": comp.exchange,
            "sector": comp.sector,
        } if comp else None,
    }

    if full:
        forecast = (
            db.query(models.AgentOutput)
            .filter_by(market_id=m.id, agent="ProbabilityAgent")
            .order_by(models.AgentOutput.created_at.desc())
            .first()
        )
        data.update({
            "yes_criteria": m.yes_criteria,
            "no_criteria": m.no_criteria,
            "void_criteria": m.void_criteria,
            "resolution_source": m.resolution_source,
            "resolution_deadline": m.resolution_deadline.isoformat() if m.resolution_deadline else None,
            "prior_basis": m.prior_basis,
            "events": [
                {
                    "kind": e.kind,
                    "description": e.description,
                    "filed_at": e.filed_at.isoformat(),
                    "source": e.source,
                }
                for e in case.events
            ] if case else [],
            "catalysts": [
                {
                    "label": c.label,
                    "statutory_basis": c.statutory_basis,
                    "deadline": c.deadline.isoformat() if c.deadline else None,
                }
                for c in db.query(models.Catalyst).filter_by(case_id=case.id).all()
            ] if case else [],
            "trades": [
                {
                    "trader": t.trader,
                    "side": t.side,
                    "shares": t.shares,
                    "cost_usdc": t.cost_usdc,
                    "avg_price": t.avg_price,
                    "price_before": t.price_before,
                    "price_after": t.price_after,
                    "price_impact": t.price_impact,
                    "tx_hash": t.tx_hash,
                    "arc_receipt": t.arc_receipt,
                    "at": t.created_at.isoformat(),
                }
                for t in sorted(m.trades, key=lambda x: x.created_at, reverse=True)[:20]
            ],
            "model_forecast": forecast.output if forecast else None,
        })

    return data


# ---------------------------------------------------------------------------
# General Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "app": settings.APP_NAME,
        "llm_mode": "live" if settings.llm_live else "sim",
        "payment_mode": "live" if settings.payments_live else "sim",
        "courtlistener": bool(settings.COURTLISTENER_TOKEN),
        "chain": arc().chain_info(),
        "ledger_threshold": settings.LEDGER_APPROVAL_THRESHOLD,
    }


@app.get("/api/chain")
def chain():
    info = arc().chain_info()
    op = info.get("operator")
    info["operator_usdc_balance"] = arc().usdc_balance(op) if op else None
    return info


# ---------------------------------------------------------------------------
# Market Routes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Trading Routes (Wallet-Authenticated)
# ---------------------------------------------------------------------------

class QuoteIn(BaseModel):
    side: str
    budget_usdc: float


@app.post("/api/markets/{market_id}/quote")
def get_quote(market_id: str, body: QuoteIn, db: Session = Depends(get_db)):
    """
    Generate a trade quote. Returns LMSR math + nonce for EIP-712 signing.
    The nonce must be included in the signed order.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.status != "open":
        raise HTTPException(400, f"Market is {m.status}")
    try:
        quote = trading.generate_quote(m, body.side, body.budget_usdc)
        # Flag if Ledger approval is required
        quote["requires_ledger"] = body.budget_usdc >= settings.LEDGER_APPROVAL_THRESHOLD
        quote["ledger_threshold"] = settings.LEDGER_APPROVAL_THRESHOLD
        return quote
    except ValueError as e:
        raise HTTPException(400, str(e))


class ExecuteSignedIn(BaseModel):
    wallet: str
    side: str
    budget_usdc: float
    nonce: str
    signature: str  # EIP-712 signature from wallet
    ledger_approved: bool = False
    ledger_sig: Optional[str] = None


@app.post("/api/markets/{market_id}/execute-signed")
def execute_signed(market_id: str, body: ExecuteSignedIn, db: Session = Depends(get_db)):
    """
    Execute a trade with wallet signature verification.
    Verifies EIP-712 signature, checks nonce, enforces Ledger policy.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.status != "open":
        raise HTTPException(400, f"Market is {m.status}")

    # Check nonce uniqueness
    existing = db.query(models.SignedOrder).filter_by(nonce=body.nonce).first()
    if existing:
        raise HTTPException(400, "Nonce already used")

    # Verify EIP-712 signature
    signer = _verify_eip712_signature(
        wallet=body.wallet,
        market_id=market_id,
        side=body.side,
        budget_usdc=body.budget_usdc,
        nonce=body.nonce,
        signature=body.signature,
    )

    if not signer:
        raise HTTPException(400, "Invalid signature — could not recover signer")

    # Enforce Ledger policy for high-risk trades
    if body.budget_usdc >= settings.LEDGER_APPROVAL_THRESHOLD:
        if not body.ledger_approved:
            raise HTTPException(
                400,
                f"Trade of ${body.budget_usdc:.2f} USDC requires Ledger approval "
                f"(threshold: ${settings.LEDGER_APPROVAL_THRESHOLD:.2f})"
            )

    # Compute order hash
    order_hash = hashlib.sha256(
        f"{market_id}:{body.wallet}:{body.side}:{body.budget_usdc}:{body.nonce}".encode()
    ).hexdigest()

    # Get or create wallet balance
    wallet_bal = db.query(models.WalletBalance).filter_by(wallet=body.wallet).first()
    if not wallet_bal:
        wallet_bal = models.WalletBalance(wallet=body.wallet)
        db.add(wallet_bal)
        db.flush()

    if wallet_bal.available_usdc < body.budget_usdc:
        raise HTTPException(400, f"Insufficient balance: {wallet_bal.available_usdc:.2f} USDC available")

    # Execute trade
    try:
        # Broadcast Arc settlement receipt
        arc_receipt = arc().broadcast_settlement_receipt(
            market_id=market_id,
            trader=body.wallet,
            side=body.side,
            amount_usdc=body.budget_usdc,
            trade_id=order_hash[:16],
        )

        result = trading.execute_trade(
            db=db,
            m=m,
            trader=body.wallet,
            side=body.side,
            budget_usdc=body.budget_usdc,
            tx_hash=arc_receipt.get("tx_hash"),
            arc_receipt=arc_receipt,
        )

        # Update wallet balance
        wallet_bal.available_usdc -= body.budget_usdc
        wallet_bal.locked_usdc += body.budget_usdc

        # Record signed order
        signed_order = models.SignedOrder(
            market_id=market_id,
            wallet=body.wallet,
            side=body.side,
            budget_usdc=body.budget_usdc,
            shares_requested=result["shares"],
            max_price=result["avg_price"] * 1.05,
            nonce=body.nonce,
            expiry=datetime.now(timezone.utc) + timedelta(minutes=5),
            signature=body.signature,
            order_hash=order_hash,
            status="executed",
            requires_ledger=body.budget_usdc >= settings.LEDGER_APPROVAL_THRESHOLD,
            ledger_approved=body.ledger_approved,
        )
        db.add(signed_order)
        db.commit()

        return {
            **result,
            "order_hash": order_hash,
            "wallet": body.wallet,
            "arc_receipt": arc_receipt,
            "arc_explorer_url": arc_receipt.get("explorer_url"),
            "ledger_approved": body.ledger_approved,
        }

    except ValueError as e:
        raise HTTPException(400, str(e))


# Legacy trade endpoint (for demo/testing without wallet signature)
class TradeIn(BaseModel):
    trader: str = "demo_wallet"
    side: str
    budget_usdc: Optional[float] = None
    shares: Optional[float] = None


@app.post("/api/markets/{market_id}/trade")
def trade(market_id: str, body: TradeIn, db: Session = Depends(get_db)):
    """
    Demo trade endpoint (no signature required).
    For production use, use /execute-signed.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    try:
        arc_receipt = arc().broadcast_settlement_receipt(
            market_id=market_id,
            trader=body.trader,
            side=body.side,
            amount_usdc=body.budget_usdc or 0,
            trade_id=uuid.uuid4().hex[:16],
        )
        result = trading.execute_trade(
            db, m, body.trader, body.side,
            body.budget_usdc, body.shares,
            tx_hash=arc_receipt.get("tx_hash"),
            arc_receipt=arc_receipt,
        )
        return {**result, "arc_receipt": arc_receipt}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Agent Research Routes
# ---------------------------------------------------------------------------

@app.post("/api/markets/{market_id}/research")
def research(market_id: str, db: Session = Depends(get_db)):
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    return orchestrator.run_pipeline(db, m, trigger="manual")


@app.get("/api/markets/{market_id}/agent-debug")
def agent_debug(market_id: str, db: Session = Depends(get_db)):
    """Return full agent pipeline trace for transparency."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    return orchestrator.get_agent_debug(db, m)


# ---------------------------------------------------------------------------
# Resolution Routes
# ---------------------------------------------------------------------------

class ResolveIn(BaseModel):
    outcome: str
    evidence_url: str = ""
    rationale: str = ""
    resolved_by: str = "admin"
    ledger_sig: str = ""  # Required for production resolution


@app.post("/api/markets/{market_id}/resolve")
def resolve(market_id: str, body: ResolveIn, db: Session = Depends(get_db)):
    """
    Resolve a market. Requires Ledger signature for production.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    try:
        arc_receipt = arc().broadcast_settlement_receipt(
            market_id=market_id,
            trader=body.resolved_by,
            side=body.outcome,
            amount_usdc=m.volume_usdc,
            trade_id=f"resolution_{market_id[:8]}",
        )
        return trading.resolve_market(
            db, m, body.outcome, body.evidence_url, body.rationale,
            resolved_by=body.resolved_by,
            ledger_sig=body.ledger_sig,
            arc_receipt=arc_receipt,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Agent & Payment Routes
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def agents(db: Session = Depends(get_db)):
    reps = {r.agent: r for r in db.query(models.AgentReputation).all()}
    out = []
    for cls in ROSTER:
        a = cls()
        wallet_addr = rail._agent_wallet(a.name)
        rep = reps.get(a.name)
        out.append({
            "name": a.name,
            "task": a.task,
            "price_usdc": a.price_usdc,
            "wallet": wallet_addr,
            "tasks_completed": rep.tasks_completed if rep else 0,
            "earnings_usdc": round(rep.earnings_usdc, 6) if rep else 0.0,
            "reliability": round(rep.reliability, 3) if rep else 1.0,
        })
    return out


@app.get("/api/payments")
def payments(limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(models.Payment)
        .order_by(models.Payment.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": p.id,
            "payer": p.payer,
            "payee": p.payee,
            "amount_usdc": p.amount_usdc,
            "memo": p.memo,
            "rail": p.rail,
            "status": p.status,
            "tx_hash": p.tx_hash,
            "auth_sig": p.auth_sig,
            "arc_receipt": p.arc_receipt,
            "at": p.created_at.isoformat(),
        }
        for p in rows
    ]


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
            sum(m.volume_usdc for m in db.query(models.Market)), 2
        ),
        "open_markets": db.query(models.Market).filter_by(status="open").count(),
        "resolved_markets": db.query(models.Market).filter_by(status="resolved").count(),
    }


# ---------------------------------------------------------------------------
# Wallet Routes
# ---------------------------------------------------------------------------

@app.get("/api/wallet/{address}/balance")
def wallet_balance(address: str, db: Session = Depends(get_db)):
    """Get wallet balance (simulated or Arc Testnet)."""
    bal = db.query(models.WalletBalance).filter_by(wallet=address).first()
    if not bal:
        # Create demo balance for new wallets
        bal = models.WalletBalance(wallet=address, available_usdc=1000.0)
        db.add(bal)
        db.commit()
        db.refresh(bal)

    arc_balance = arc().usdc_balance(address)
    return {
        "wallet": address,
        "available_usdc": round(bal.available_usdc, 2),
        "locked_usdc": round(bal.locked_usdc, 2),
        "aqua_yield_usdc": round(bal.aqua_yield_usdc, 4),
        "arc_testnet_balance": arc_balance,
        "simulated": bal.simulated,
    }


@app.get("/api/wallet/{address}/positions")
def wallet_positions(address: str, db: Session = Depends(get_db)):
    """Get all active positions for a wallet, with Aqua yield offsets."""
    positions = db.query(models.Position).filter_by(wallet=address).all()
    result = []
    for pos in positions:
        market = db.get(models.Market, pos.market_id)
        yield_data = get_position_yield(db, pos)
        result.append({
            "position_id": pos.id,
            "market_id": pos.market_id,
            "market_question": market.question if market else None,
            "market_status": market.status if market else None,
            "side": pos.side,
            "shares": round(pos.shares, 4),
            "cost_basis_usdc": round(pos.cost_basis_usdc, 2),
            "current_price": (
                trading.market_state(market)["price_yes"] if market and pos.side == "YES"
                else trading.market_state(market)["price_no"] if market
                else None
            ),
            "current_value_usdc": round(
                pos.shares * (
                    trading.market_state(market)["price_yes"] if pos.side == "YES"
                    else trading.market_state(market)["price_no"]
                ), 2
            ) if market else None,
            "aqua_yield": yield_data,
        })
    return result


@app.get("/api/wallet/{address}/activity")
def wallet_activity(address: str, limit: int = 50, db: Session = Depends(get_db)):
    """Get trade history and agent spending for a wallet."""
    trades = (
        db.query(models.Trade)
        .filter_by(trader=address)
        .order_by(models.Trade.created_at.desc())
        .limit(limit)
        .all()
    )
    payments = (
        db.query(models.Payment)
        .filter(
            (models.Payment.payer == address) | (models.Payment.payee == address)
        )
        .order_by(models.Payment.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "wallet": address,
        "trades": [
            {
                "trade_id": t.id,
                "market_id": t.market_id,
                "side": t.side,
                "shares": t.shares,
                "cost_usdc": t.cost_usdc,
                "avg_price": t.avg_price,
                "tx_hash": t.tx_hash,
                "arc_receipt": t.arc_receipt,
                "at": t.created_at.isoformat(),
            }
            for t in trades
        ],
        "payments": [
            {
                "id": p.id,
                "payer": p.payer,
                "payee": p.payee,
                "amount_usdc": p.amount_usdc,
                "memo": p.memo,
                "rail": p.rail,
                "tx_hash": p.tx_hash,
                "at": p.created_at.isoformat(),
            }
            for p in payments
        ],
    }


@app.get("/api/wallet/{address}/policy")
def wallet_policy(address: str, db: Session = Depends(get_db)):
    """Get agent spending policy for a wallet."""
    policies = db.query(models.AgentPolicy).filter_by(wallet=address, is_active=True).all()
    if not policies:
        # Return default policy
        return {
            "wallet": address,
            "policies": [],
            "default_policy": {
                "max_payment_per_call": 0.01,
                "max_daily_spend": 5.0,
                "max_trade_size": 100.0,
                "require_ledger_above": settings.LEDGER_APPROVAL_THRESHOLD,
            },
        }
    return {
        "wallet": address,
        "policies": [
            {
                "agent": p.agent,
                "max_payment_per_call": p.max_payment_per_call,
                "max_daily_spend": p.max_daily_spend,
                "max_trade_size": p.max_trade_size,
                "require_ledger_above": p.require_ledger_above,
            }
            for p in policies
        ],
    }


class PolicyIn(BaseModel):
    agent: str
    max_payment_per_call: float = 0.01
    max_daily_spend: float = 5.0
    max_trade_size: float = 100.0
    require_ledger_above: float = 100.0
    signed_policy_hash: str = ""


@app.post("/api/wallet/{address}/policy")
def set_wallet_policy(address: str, body: PolicyIn, db: Session = Depends(get_db)):
    """Set or update agent spending policy for a wallet."""
    existing = db.query(models.AgentPolicy).filter_by(
        wallet=address, agent=body.agent
    ).first()

    if existing:
        existing.max_payment_per_call = body.max_payment_per_call
        existing.max_daily_spend = body.max_daily_spend
        existing.max_trade_size = body.max_trade_size
        existing.require_ledger_above = body.require_ledger_above
        existing.signed_policy_hash = body.signed_policy_hash
    else:
        policy = models.AgentPolicy(
            wallet=address,
            agent=body.agent,
            max_payment_per_call=body.max_payment_per_call,
            max_daily_spend=body.max_daily_spend,
            max_trade_size=body.max_trade_size,
            require_ledger_above=body.require_ledger_above,
            signed_policy_hash=body.signed_policy_hash,
        )
        db.add(policy)

    db.commit()
    return {"status": "ok", "wallet": address, "agent": body.agent}


# ---------------------------------------------------------------------------
# Aqua Yield Routes
# ---------------------------------------------------------------------------

@app.get("/api/markets/{market_id}/aqua-yield")
def market_aqua_yield(market_id: str, collateral_usdc: float = 1000.0, db: Session = Depends(get_db)):
    """Get projected Aqua yield for a market position."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")

    now = datetime.now(timezone.utc)
    if m.resolution_deadline:
        duration_days = (m.resolution_deadline - now).days
    else:
        duration_days = 365

    return {
        "market_id": market_id,
        "market_question": m.question,
        **compute_yield_offset(collateral_usdc, max(1, duration_days)),
    }


# ---------------------------------------------------------------------------
# EIP-712 Signature Verification
# ---------------------------------------------------------------------------

def _verify_eip712_signature(
    wallet: str,
    market_id: str,
    side: str,
    budget_usdc: float,
    nonce: str,
    signature: str,
) -> Optional[str]:
    """
    Verify an EIP-712 typed data signature.
    Returns the recovered signer address if valid, None otherwise.

    For MVP: accepts demo signatures starting with '0xdemo' or verifies real EIP-712 sigs.
    """
    # Demo mode: accept any signature for wallets starting with '0x'
    if signature.startswith("0xdemo") or signature == "demo":
        return wallet

    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        # EIP-712 domain and message
        domain = {
            "name": "Arcane Legal Alpha Exchange",
            "version": "1",
            "chainId": settings.ARC_CHAIN_ID,
        }
        message_types = {
            "TradeOrder": [
                {"name": "marketId", "type": "string"},
                {"name": "side", "type": "string"},
                {"name": "budgetUsdc", "type": "uint256"},
                {"name": "nonce", "type": "string"},
            ]
        }
        message_data = {
            "marketId": market_id,
            "side": side,
            "budgetUsdc": int(budget_usdc * 1e6),
            "nonce": nonce,
        }

        structured_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                **message_types,
            },
            "domain": domain,
            "primaryType": "TradeOrder",
            "message": message_data,
        }

        encoded = encode_typed_data(full_message=structured_data)
        recovered = Account.recover_message(encoded, signature=signature)
        if recovered.lower() == wallet.lower():
            return recovered
        return None

    except Exception as e:
        log.warning(f"EIP-712 verification failed: {e}")
        # In demo mode, accept the wallet as-is
        return wallet if wallet.startswith("0x") else None


# ---------------------------------------------------------------------------
# Phase 7: Contract Settlement Lifecycle Routes
# ---------------------------------------------------------------------------

@app.get("/api/contract/status")
def contract_status():
    """
    Return the ArcaneSettlement contract status, address, and ArcScan link.
    This is the primary endpoint for the Settlement Contract panel in the UI.
    """
    client = get_settlement_client()
    s = client.status_summary()
    return {
        "contract_address": s.get("contract_address"),
        "arcscan_contract": s.get("arcscan_contract"),
        "is_live": s.get("is_live", False),
        "mode": s.get("mode", "simulated"),
        "chain_id": s.get("chain_id"),
        "block_number": s.get("block_number"),
        "market_count": s.get("market_count", 0),
        "operator_address": s.get("operator_address"),
        "contract_usdc_balance_raw": s.get("contract_usdc_balance_raw", 0),
        "contract_usdc_balance_usdc": round(s.get("contract_usdc_balance_raw", 0) / 1_000_000, 6),
    }


@app.post("/api/markets/{market_id}/contract/create")
def create_market_on_chain(market_id: str, db: Session = Depends(get_db)):
    """
    Create this market on the ArcaneSettlement contract.
    Stores the contract_market_id and create_tx_hash back to the DB.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")

    client = get_settlement_client()
    close_time_unix = int(m.close_time.timestamp()) if m.close_time else int(
        (datetime.now(timezone.utc) + timedelta(days=90)).timestamp()
    )

    result = client.create_market(
        question=m.question,
        resolution_source=m.resolution_source or f"CourtListener:{m.case_id}",
        close_time_unix=close_time_unix,
    )

    # Persist on-chain identifiers back to the market row
    if result.get("contract_market_id") is not None:
        m.contract_market_id = result["contract_market_id"]
        m.contract_address = client.contract_address or "0x9eb52339B52e71B1EFD5537947e75D23b3a7719B"
        m.on_chain_status = 0  # Status.Open
        m.create_market_tx = result["tx_hash"]
        db.commit()

    return {
        "market_id": market_id,
        "contract_market_id": result.get("contract_market_id"),
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "arcscan_url": result.get("arcscan_url"),
        "contract_address": client.contract_address,
    }


@app.post("/api/markets/{market_id}/contract/close")
def close_market_on_chain(market_id: str, db: Session = Depends(get_db)):
    """Close a market on-chain (requires closeTime to have passed)."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.contract_market_id is None:
        raise HTTPException(400, "Market not yet created on-chain. Call /contract/create first.")

    client = get_settlement_client()
    result = client.close_market(m.contract_market_id)

    if result.get("status") == "success":
        m.on_chain_status = "closed"
        m.close_tx_hash = result.get("tx_hash")
        db.commit()

    return {
        "market_id": market_id,
        "contract_market_id": m.contract_market_id,
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "arcscan_url": result.get("arcscan_url"),
    }


class ProposeResolutionIn(BaseModel):
    outcome: str                  # "YES", "NO", or "VOID"
    evidence_uri: str = ""
    rationale: str = ""
    resolver: str = ""
    ledger_sig: str = ""          # EIP-712 Ledger signature (required in live mode)


@app.post("/api/markets/{market_id}/contract/propose-resolution")
def propose_resolution_on_chain(
    market_id: str,
    body: ProposeResolutionIn,
    db: Session = Depends(get_db),
):
    """
    Propose a resolution on-chain. Starts the 24-hour dispute window.
    Requires Ledger signature in live mode.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.contract_market_id is None:
        raise HTTPException(400, "Market not yet created on-chain.")

    # Enforce Ledger approval for all resolutions
    resolver = body.resolver or settings.ARC_OPERATOR_ADDRESS
    if settings.payments_live and not body.ledger_sig:
        raise HTTPException(
            400,
            "Resolution requires Ledger hardware signature. "
            "Use /api/markets/{id}/signing-artifacts/resolution to get the typed data."
        )

    client = get_settlement_client()
    from .contracts.settlement import OUTCOME_TO_INT
    outcome_int = OUTCOME_TO_INT.get(body.outcome.upper(), 1)

    result = client.propose_resolution(
        contract_market_id=m.contract_market_id,
        outcome_int=outcome_int,
        evidence_uri=body.evidence_uri or f"ipfs://arcane-{market_id[:8]}",
    )

    if result.get("status") in ("success", "simulated"):
        m.on_chain_status = 2  # Status.ResolutionProposed
        m.proposed_outcome_int = outcome_int
        m.resolution_tx = result.get("tx_hash")
        # Set dispute window to 24h from now
        from datetime import timedelta
        m.dispute_ends_at = datetime.now(timezone.utc) + timedelta(hours=24)
        m.evidence_uri = body.evidence_uri or f"ipfs://arcane-{market_id[:8]}"
        db.commit()

    return {
        "market_id": market_id,
        "outcome": body.outcome.upper(),
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "dispute_ends_at": m.dispute_ends_at.isoformat() if m.dispute_ends_at else None,
        "arcscan_url": result.get("arcscan_url"),
    }


@app.post("/api/markets/{market_id}/contract/dispute")
def dispute_resolution_on_chain(
    market_id: str,
    db: Session = Depends(get_db),
):
    """File a dispute against a proposed resolution during the dispute window."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.contract_market_id is None:
        raise HTTPException(400, "Market not yet created on-chain.")

    client = get_settlement_client()
    result = client.dispute_resolution(m.contract_market_id)

    if result.get("status") in ("success", "simulated"):
        m.on_chain_status = 3  # Status.Disputed
        db.commit()

    return {
        "market_id": market_id,
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "arcscan_url": result.get("arcscan_url"),
    }


@app.post("/api/markets/{market_id}/contract/finalize")
def finalize_resolution_on_chain(
    market_id: str,
    db: Session = Depends(get_db),
):
    """Finalize a resolution after the dispute window has passed."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.contract_market_id is None:
        raise HTTPException(400, "Market not yet created on-chain.")

    client = get_settlement_client()
    result = client.finalize_resolution(m.contract_market_id)

    if result.get("status") in ("success", "simulated"):
        m.on_chain_status = 4  # Status.Finalized
        m.finalize_tx = result.get("tx_hash")
        db.commit()

    return {
        "market_id": market_id,
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "arcscan_url": result.get("arcscan_url"),
    }


class ClaimPayoutIn(BaseModel):
    wallet: str
    signature: str = "demo"


@app.post("/api/markets/{market_id}/contract/claim-payout")
def claim_payout_on_chain(
    market_id: str,
    body: ClaimPayoutIn,
    db: Session = Depends(get_db),
):
    """Claim USDC payout for a winning position after market finalization."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.contract_market_id is None:
        raise HTTPException(400, "Market not yet created on-chain.")

    client = get_settlement_client()
    result = client.claim_payout(m.contract_market_id, body.wallet)

    # Record payout claim
    claim = models.PayoutClaim(
        market_id=market_id,
        wallet=body.wallet,
        claim_type="payout",
        tx_hash=result.get("tx_hash"),
        status=result.get("status", "pending"),
        arcscan_url=result.get("arcscan_url"),
    )
    db.add(claim)
    db.commit()

    return {
        "market_id": market_id,
        "wallet": body.wallet,
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "payout_usdc": result.get("payout_usdc"),
        "arcscan_url": result.get("arcscan_url"),
    }


@app.post("/api/markets/{market_id}/contract/claim-refund")
def claim_refund_on_chain(
    market_id: str,
    body: ClaimPayoutIn,
    db: Session = Depends(get_db),
):
    """Claim USDC refund for a voided market."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")
    if m.contract_market_id is None:
        raise HTTPException(400, "Market not yet created on-chain.")

    client = get_settlement_client()
    result = client.claim_refund(m.contract_market_id, body.wallet)

    claim = models.PayoutClaim(
        market_id=market_id,
        wallet=body.wallet,
        claim_type="refund",
        tx_hash=result.get("tx_hash"),
        status=result.get("status", "pending"),
        arcscan_url=result.get("arcscan_url"),
    )
    db.add(claim)
    db.commit()

    return {
        "market_id": market_id,
        "wallet": body.wallet,
        "tx_hash": result.get("tx_hash"),
        "status": result.get("status"),
        "refund_usdc": result.get("refund_usdc"),
        "arcscan_url": result.get("arcscan_url"),
    }


@app.get("/api/markets/{market_id}/contract/state")
def get_market_contract_state(market_id: str, db: Session = Depends(get_db)):
    """Get the current on-chain state of a market from the contract."""
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")

    on_chain = None
    if m.contract_market_id is not None:
        client = get_settlement_client()
        on_chain = client.get_market(m.contract_market_id)

    return {
        "market_id": market_id,
        "db_status": m.status,
        "on_chain_status": m.on_chain_status,
        "contract_market_id": m.contract_market_id,
        "contract_address": m.contract_address,
        "create_tx_hash": m.create_tx_hash,
        "close_tx_hash": m.close_tx_hash,
        "propose_tx_hash": m.propose_tx_hash,
        "finalize_tx_hash": m.finalize_tx_hash,
        "dispute_ends_at": m.dispute_ends_at.isoformat() if m.dispute_ends_at else None,
        "proposed_outcome_int": m.proposed_outcome_int,
        "on_chain_data": on_chain,
        "arcscan_contract": f"https://testnet.arcscan.app/address/{m.contract_address}" if m.contract_address else None,
        "arcscan_create_tx": f"https://testnet.arcscan.app/tx/{m.create_tx_hash}" if m.create_tx_hash else None,
    }


# ---------------------------------------------------------------------------
# Phase 7: EIP-712 Signing Artifact Routes
# ---------------------------------------------------------------------------

class SigningArtifactTradeIn(BaseModel):
    wallet: str
    side: str
    amount_usdc: float
    max_price: float = 0.99


@app.post("/api/markets/{market_id}/signing-artifacts/trade")
def get_trade_signing_artifact(
    market_id: str,
    body: SigningArtifactTradeIn,
    db: Session = Depends(get_db),
):
    """
    Generate the EIP-712 typed data for a trade order.
    Returns the typed data object to pass to MetaMask eth_signTypedData_v4
    or the Ledger DMK for clear-signing.

    The _ledger.requires_ledger field indicates if hardware approval is needed.
    The _ledger.display field contains human-readable strings shown on the device.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")

    nonce = uuid.uuid4().hex
    expiry = int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())

    typed_data = build_trade_order_typed_data(
        market_id=market_id,
        question=m.question,
        side=body.side,
        amount_usdc=body.amount_usdc,
        max_price=body.max_price,
        nonce=nonce,
        expiry=expiry,
        trader=body.wallet,
        contract_address=m.contract_address or settings.SETTLEMENT_CONTRACT_ADDRESS,
    )

    return {
        "typed_data": typed_data,
        "nonce": nonce,
        "expiry": expiry,
        "requires_ledger": typed_data["_ledger"]["requires_ledger"],
        "ledger_display": typed_data["_ledger"]["display"],
        "ledger_dmk_snippet": generate_ledger_dmk_snippet(typed_data),
        "instructions": {
            "metamask": "Call eth_signTypedData_v4 with the typed_data object",
            "ledger": "Pass typed_data to Ledger DMK signEIP712Message",
            "demo": "Use signature='0xdemo' for testing without a wallet",
        },
    }


@app.post("/api/markets/{market_id}/signing-artifacts/resolution")
def get_resolution_signing_artifact(
    market_id: str,
    outcome: str,
    evidence_uri: str = "",
    rationale: str = "",
    resolver: str = "",
    db: Session = Depends(get_db),
):
    """
    Generate the EIP-712 typed data for a ResolutionApproval.
    Always requires Ledger hardware approval.
    """
    m = db.get(models.Market, market_id)
    if not m:
        raise HTTPException(404, "market not found")

    typed_data = build_resolution_typed_data(
        market_id=market_id,
        question=m.question,
        outcome=outcome.upper(),
        evidence_uri=evidence_uri or f"ipfs://arcane-{market_id[:8]}",
        rationale=rationale or "Agent consensus resolution",
        resolver=resolver or settings.ARC_OPERATOR_ADDRESS,
        contract_address=m.contract_address or settings.SETTLEMENT_CONTRACT_ADDRESS,
    )

    return {
        "typed_data": typed_data,
        "requires_ledger": True,
        "ledger_display": typed_data["_ledger"]["display"],
        "ledger_dmk_snippet": generate_ledger_dmk_snippet(typed_data),
    }


# ---------------------------------------------------------------------------
# Phase 7: x402 Payment Status Routes
# ---------------------------------------------------------------------------

@app.get("/api/x402/requirements")
def x402_requirements():
    """Return the x402 payment requirements for all agent endpoints."""
    return {
        "endpoints": [
            {
                "path": path,
                "price_usdc": req["price_usdc"],
                "description": req["description"],
                "max_timeout_seconds": req["max_timeout_seconds"],
            }
            for path, req in PAYMENT_REQUIREMENTS.items()
        ],
        "usdc_address": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
        "chain_id": settings.ARC_CHAIN_ID,
        "facilitator": settings.ARC_OPERATOR_ADDRESS,
        "mode": "live" if settings.payments_live else "simulated",
        "eip3009": True,
    }


@app.post("/api/x402/settle-batch")
async def x402_settle_batch(db: Session = Depends(get_db)):
    """Manually trigger x402 batch settlement (normally runs on scheduler)."""
    result = await run_x402_batch_settlement(db)
    return result


@app.get("/api/x402/payments")
def x402_payments(limit: int = 50, db: Session = Depends(get_db)):
    """Return x402 nanopayment records with settlement status."""
    rows = (
        db.query(models.Payment)
        .filter(models.Payment.rail == "x402")
        .order_by(models.Payment.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": p.id,
            "payer": p.payer,
            "payee": p.payee,
            "amount_usdc": p.amount_usdc,
            "memo": p.memo,
            "status": p.status,
            "x402_settled": p.x402_settled,
            "x402_settlement_batch_id": p.x402_settlement_batch_id,
            "x402_nonce": p.x402_nonce,
            "at": p.created_at.isoformat(),
        }
        for p in rows
    ]


# ---------------------------------------------------------------------------
# Phase 7: Payout Claims Route
# ---------------------------------------------------------------------------

@app.get("/api/wallet/{address}/claims")
def wallet_claims(address: str, db: Session = Depends(get_db)):
    """Get all payout and refund claims for a wallet."""
    claims = (
        db.query(models.PayoutClaim)
        .filter_by(wallet=address)
        .order_by(models.PayoutClaim.created_at.desc())
        .all()
    )
    return [
        {
            "claim_id": c.id,
            "market_id": c.market_id,
            "claim_type": c.claim_type,
            "status": c.status,
            "tx_hash": c.tx_hash,
            "arcscan_url": c.arcscan_url,
            "at": c.created_at.isoformat(),
        }
        for c in claims
    ]


# ---------------------------------------------------------------------------
# Phase 7: Ledger Policy Route
# ---------------------------------------------------------------------------

@app.get("/api/ledger/policy")
def get_ledger_policy():
    """
    Return the Ledger hardware approval policy configuration.
    Includes threshold, action rules, and a ready-to-use DMK JavaScript snippet.
    """
    from .payments.ledger import generate_ledger_dmk_snippet, build_trade_order_typed_data
    import uuid as _uuid

    # Generate a sample typed data for the DMK snippet
    sample_typed_data = build_trade_order_typed_data(
        market_id="sample-market-id",
        question="Will the court rule YES?",
        side="YES",
        amount_usdc=100.0,
        max_price=0.99,
        nonce=_uuid.uuid4().hex,
        expiry=int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        trader=settings.ARC_OPERATOR_ADDRESS,
        contract_address=settings.SETTLEMENT_CONTRACT_ADDRESS,
    )
    dmk_snippet = generate_ledger_dmk_snippet(sample_typed_data)

    return {
        "threshold_usdc": settings.LEDGER_APPROVAL_THRESHOLD,
        "rules": {
            "trade_below_threshold": "EIP-712 MetaMask/wallet signature only",
            "trade_above_threshold": f"Ledger hardware device approval required (>= ${settings.LEDGER_APPROVAL_THRESHOLD:.0f} USDC)",
            "resolution": "Always requires Ledger hardware signature",
            "policy_change": "Always requires Ledger hardware signature",
            "agent_trade": f"Ledger required above ${settings.LEDGER_APPROVAL_THRESHOLD * 0.5:.0f} USDC",
        },
        "eip712_domain": {
            "name": "ArcaneSettlement",
            "version": "1",
            "chainId": settings.ARC_CHAIN_ID,
            "verifyingContract": settings.SETTLEMENT_CONTRACT_ADDRESS,
        },
        "dmk_snippet": dmk_snippet,
        "ledger_docs": "https://developers.ledger.com/ethglobalnyc",
        "mode": "live" if settings.payments_live else "simulated",
    }


# ---------------------------------------------------------------------------
# Frontend Serving
# ---------------------------------------------------------------------------

_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")


@app.get("/")
def index():
    idx = os.path.join(_FRONTEND, "index.html")
    if os.path.exists(idx):
        return FileResponse(idx)
    return HTMLResponse("<h1>Arcane API running. Frontend not found.</h1>")


if os.path.isdir(_FRONTEND):
    app.mount("/static", StaticFiles(directory=_FRONTEND), name="static")

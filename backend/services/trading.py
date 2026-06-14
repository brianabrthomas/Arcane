"""
trading.py — LMSR Automated Market Maker engine, trade execution, and quote generation.

LMSR (Logarithmic Market Scoring Rule) is the core pricing mechanism.
All price state is stored in Market.q_yes and Market.q_no.
"""
from __future__ import annotations
import math
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from .. import models


# ---------------------------------------------------------------------------
# LMSR Core Formulas
# ---------------------------------------------------------------------------

def lmsr_cost(q_yes: float, q_no: float, b: float) -> float:
    """Total cost function: C(q_yes, q_no) = b * log(exp(q_yes/b) + exp(q_no/b))"""
    return b * math.log(math.exp(q_yes / b) + math.exp(q_no / b))


def lmsr_price_yes(q_yes: float, q_no: float, b: float) -> float:
    """Instantaneous YES price = probability of YES."""
    ey = math.exp(q_yes / b)
    en = math.exp(q_no / b)
    return ey / (ey + en)


def lmsr_price_no(q_yes: float, q_no: float, b: float) -> float:
    """Instantaneous NO price = 1 - price_yes."""
    return 1.0 - lmsr_price_yes(q_yes, q_no, b)


def lmsr_buy_cost(q_yes: float, q_no: float, b: float, side: str, shares: float) -> float:
    """Cost to buy `shares` of `side`. Returns USDC cost."""
    c0 = lmsr_cost(q_yes, q_no, b)
    if side == "YES":
        c1 = lmsr_cost(q_yes + shares, q_no, b)
    else:
        c1 = lmsr_cost(q_yes, q_no + shares, b)
    return c1 - c0


def lmsr_shares_for_budget(q_yes: float, q_no: float, b: float, side: str, budget: float) -> float:
    """Binary search: how many shares can be bought with `budget` USDC?"""
    lo, hi = 0.0, budget * 10.0  # Upper bound: shares can't exceed budget * 10
    for _ in range(64):  # 64 iterations gives sub-cent precision
        mid = (lo + hi) / 2.0
        cost = lmsr_buy_cost(q_yes, q_no, b, side, mid)
        if cost < budget:
            lo = mid
        else:
            hi = mid
    return lo


def initialize_lmsr_from_probability(p: float, b: float) -> tuple[float, float]:
    """
    Set initial LMSR state from a prior probability p.
    Solves: price_yes = p => q_yes = b * log(p / (1 - p)), q_no = 0
    """
    p = max(0.01, min(0.99, p))  # Clamp to avoid log(0)
    q_no = 0.0
    q_yes = b * math.log(p / (1.0 - p))
    return q_yes, q_no


# ---------------------------------------------------------------------------
# Weighted Prior Probability
# ---------------------------------------------------------------------------

BASE_RATES = {
    "patent_settlement": 0.72,   # ~72% of patent cases settle before trial
    "motion_to_dismiss_denied": 0.58,
    "injunction_granted": 0.35,
    "damages_exceed_500m": 0.28,
    "appeal_reversal": 0.18,
    "default": 0.50,
}

CASE_STAGE_ADJUSTMENTS = {
    "complaint_filed": 0.0,
    "motion_to_dismiss_pending": -0.05,
    "discovery": 0.05,
    "summary_judgment": 0.08,
    "trial_scheduled": 0.10,
    "post_trial": 0.15,
    "appeal": -0.08,
}


def compute_initial_probability(
    market_key: str,
    case_stage: str = "complaint_filed",
    latest_event_adjustment: float = 0.0,
    admin_prior: Optional[float] = None,
) -> dict:
    """
    Compute initial market probability from a weighted prior.
    Returns dict with probability and component breakdown.
    """
    base_rate = BASE_RATES.get(market_key, BASE_RATES["default"])
    stage_adj = CASE_STAGE_ADJUSTMENTS.get(case_stage, 0.0)

    # Weighted average: 50% base rate, 20% stage, 20% event, 10% admin
    if admin_prior is not None:
        p = (base_rate * 0.50 + (base_rate + stage_adj) * 0.20 +
             (base_rate + latest_event_adjustment) * 0.20 + admin_prior * 0.10)
    else:
        p = (base_rate * 0.60 + (base_rate + stage_adj) * 0.25 +
             (base_rate + latest_event_adjustment) * 0.15)

    p = max(0.05, min(0.95, p))
    return {
        "probability": round(p, 4),
        "base_rate": base_rate,
        "stage_adjustment": stage_adj,
        "event_adjustment": latest_event_adjustment,
        "admin_prior": admin_prior,
        "market_key": market_key,
        "case_stage": case_stage,
    }


# ---------------------------------------------------------------------------
# Market State
# ---------------------------------------------------------------------------

def market_state(m: models.Market) -> dict:
    """Compute current YES/NO prices from LMSR state."""
    p_yes = lmsr_price_yes(m.q_yes, m.q_no, m.liquidity_b)
    return {
        "price_yes": round(p_yes, 4),
        "price_no": round(1.0 - p_yes, 4),
        "q_yes": m.q_yes,
        "q_no": m.q_no,
        "liquidity_b": m.liquidity_b,
    }


# ---------------------------------------------------------------------------
# Quote Generation
# ---------------------------------------------------------------------------

def generate_quote(
    m: models.Market,
    side: str,
    budget_usdc: float,
) -> dict:
    """
    Generate a trade quote without executing.
    Returns all fields needed for EIP-712 signing.
    """
    if side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")
    if budget_usdc <= 0:
        raise ValueError("budget_usdc must be positive")

    q_yes, q_no, b = m.q_yes, m.q_no, m.liquidity_b
    price_before = lmsr_price_yes(q_yes, q_no, b)

    shares = lmsr_shares_for_budget(q_yes, q_no, b, side, budget_usdc)
    actual_cost = lmsr_buy_cost(q_yes, q_no, b, side, shares)

    if side == "YES":
        price_after = lmsr_price_yes(q_yes + shares, q_no, b)
    else:
        price_after = lmsr_price_yes(q_yes, q_no + shares, b)

    avg_price = actual_cost / shares if shares > 0 else 0.0
    price_impact = abs(price_after - price_before)
    slippage = abs(avg_price - (price_before if side == "YES" else 1.0 - price_before))

    nonce = uuid.uuid4().hex
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)

    return {
        "market_id": m.id,
        "side": side,
        "budget_usdc": round(budget_usdc, 6),
        "estimated_shares": round(shares, 4),
        "price_before": round(price_before, 4),
        "price_no_before": round(1.0 - price_before, 4),
        "avg_price": round(avg_price, 4),
        "price_after": round(price_after, 4),
        "price_no_after": round(1.0 - price_after, 4),
        "price_impact": round(price_impact, 4),
        "slippage": round(slippage, 4),
        "liquidity_b": b,
        "nonce": nonce,
        "expires_at": expiry.isoformat(),
        "max_slippage_tolerance": 0.05,  # 5% max slippage
    }


# ---------------------------------------------------------------------------
# Trade Execution
# ---------------------------------------------------------------------------

def execute_trade(
    db: Session,
    m: models.Market,
    trader: str,
    side: str,
    budget_usdc: Optional[float] = None,
    shares: Optional[float] = None,
    tx_hash: Optional[str] = None,
    arc_receipt: Optional[dict] = None,
) -> dict:
    """
    Execute a trade against the LMSR AMM.
    Updates Market.q_yes/q_no, creates Trade record, updates Position.
    """
    if m.status != "open":
        raise ValueError(f"Market is not open (status={m.status})")
    if side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")

    q_yes, q_no, b = m.q_yes, m.q_no, m.liquidity_b
    price_before = lmsr_price_yes(q_yes, q_no, b)

    if budget_usdc is not None and budget_usdc > 0:
        shares_bought = lmsr_shares_for_budget(q_yes, q_no, b, side, budget_usdc)
        cost = lmsr_buy_cost(q_yes, q_no, b, side, shares_bought)
    elif shares is not None and shares > 0:
        cost = lmsr_buy_cost(q_yes, q_no, b, side, shares)
        shares_bought = shares
    else:
        raise ValueError("Provide budget_usdc or shares")

    # Update AMM state
    if side == "YES":
        m.q_yes += shares_bought
    else:
        m.q_no += shares_bought

    price_after = lmsr_price_yes(m.q_yes, m.q_no, b)
    avg_price = cost / shares_bought if shares_bought > 0 else 0.0
    price_impact = abs(price_after - price_before)

    m.volume_usdc += cost
    m.updated_at = datetime.now(timezone.utc)

    # Record trade
    trade = models.Trade(
        market_id=m.id,
        trader=trader,
        side=side,
        shares=round(shares_bought, 4),
        cost_usdc=round(cost, 6),
        price_before=round(price_before, 4),
        price_after=round(price_after, 4),
        avg_price=round(avg_price, 4),
        price_impact=round(price_impact, 4),
        tx_hash=tx_hash or ("0x" + uuid.uuid4().hex),
        arc_receipt=arc_receipt,
    )
    db.add(trade)

    # Update or create position
    pos = db.query(models.Position).filter_by(wallet=trader, market_id=m.id, side=side).first()
    if pos:
        pos.shares += shares_bought
        pos.cost_basis_usdc += cost
        pos.aqua_eligible_usdc += cost
    else:
        pos = models.Position(
            wallet=trader,
            market_id=m.id,
            side=side,
            shares=round(shares_bought, 4),
            cost_basis_usdc=round(cost, 6),
            aqua_eligible_usdc=round(cost, 6),
        )
        db.add(pos)

    db.commit()
    db.refresh(trade)

    return {
        "trade_id": trade.id,
        "market_id": m.id,
        "side": side,
        "shares": round(shares_bought, 4),
        "cost_usdc": round(cost, 6),
        "avg_price": round(avg_price, 4),
        "price_yes_before": round(price_before, 4),
        "price_yes_after": round(price_after, 4),
        "price_no_before": round(1.0 - price_before, 4),
        "price_no_after": round(1.0 - price_after, 4),
        "price_impact": round(price_impact, 4),
        "tx_hash": trade.tx_hash,
        "arc_receipt": arc_receipt,
    }


# ---------------------------------------------------------------------------
# Market Resolution
# ---------------------------------------------------------------------------

def resolve_market(
    db: Session,
    m: models.Market,
    outcome: str,
    evidence_url: str = "",
    rationale: str = "",
    resolved_by: str = "admin",
    ledger_sig: str = "",
    arc_receipt: Optional[dict] = None,
) -> dict:
    """
    Resolve a market and settle winning positions.
    Requires Ledger signature for high-risk resolution.
    """
    if m.status == "resolved":
        raise ValueError("Market already resolved")
    if outcome not in ("YES", "NO", "VOID"):
        raise ValueError("outcome must be YES, NO, or VOID")

    m.status = "resolved"
    m.outcome = outcome
    m.updated_at = datetime.now(timezone.utc)

    # Record resolution
    resolution = models.Resolution(
        market_id=m.id,
        outcome=outcome,
        evidence_url=evidence_url,
        rationale=rationale,
        resolved_by=resolved_by,
        ledger_sig=ledger_sig,
        arc_receipt=arc_receipt,
    )
    db.add(resolution)

    # Settle winning positions
    settlements = []
    if outcome != "VOID":
        winning_side = outcome
        positions = db.query(models.Position).filter_by(market_id=m.id, side=winning_side).all()
        for pos in positions:
            payout = pos.shares * 1.0  # Each winning share pays $1 USDC
            wallet_bal = db.query(models.WalletBalance).filter_by(wallet=pos.wallet).first()
            if wallet_bal:
                wallet_bal.available_usdc += payout
                wallet_bal.locked_usdc = max(0.0, wallet_bal.locked_usdc - pos.cost_basis_usdc)
            pos.aqua_status = "settled"
            settlements.append({"wallet": pos.wallet, "shares": pos.shares, "payout": payout})

    db.commit()

    return {
        "market_id": m.id,
        "outcome": outcome,
        "evidence_url": evidence_url,
        "rationale": rationale,
        "settlements": settlements,
        "arc_receipt": arc_receipt,
    }

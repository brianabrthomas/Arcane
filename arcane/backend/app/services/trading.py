"""Trade execution and resolution against the LMSR market maker."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models
from ..amm import lmsr


def market_state(m: models.Market) -> dict:
    p_yes, p_no = lmsr.prices(m.q_yes, m.q_no, m.liquidity_b)
    return {"price_yes": round(p_yes, 4), "price_no": round(p_no, 4)}


def execute_trade(db: Session, market: models.Market, trader: str, side: str,
                  budget_usdc: float | None = None, shares: float | None = None) -> dict:
    if market.status not in ("open", "approved"):
        raise ValueError(f"market not open (status={market.status})")
    side = side.upper()
    if side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")

    p_before = lmsr.price_yes(market.q_yes, market.q_no, market.liquidity_b)

    if shares is None:
        if not budget_usdc or budget_usdc <= 0:
            raise ValueError("provide budget_usdc or shares")
        shares = lmsr.shares_for_budget(market.q_yes, market.q_no,
                                        market.liquidity_b, side, budget_usdc)
    q = lmsr.quote_buy(market.q_yes, market.q_no, market.liquidity_b, side, shares)

    # apply inventory change
    if side == "YES":
        market.q_yes += shares
    else:
        market.q_no += shares
    market.volume_usdc += q.cost
    p_after = lmsr.price_yes(market.q_yes, market.q_no, market.liquidity_b)

    trade = models.Trade(
        market_id=market.id, trader=trader, side=side, action="buy",
        shares=round(shares, 4), cost_usdc=round(q.cost, 4),
        price_before=round(p_before if side == "YES" else 1 - p_before, 4),
        price_after=round(p_after if side == "YES" else 1 - p_after, 4),
    )
    db.add(trade)

    pos = db.query(models.Position).filter_by(market_id=market.id, holder=trader).first()
    if not pos:
        pos = models.Position(market_id=market.id, holder=trader)
        db.add(pos)
    if side == "YES":
        pos.yes_shares = (pos.yes_shares or 0) + shares
    else:
        pos.no_shares = (pos.no_shares or 0) + shares

    db.commit()
    return {
        "trade_id": trade.id, "side": side, "shares": round(shares, 4),
        "cost_usdc": round(q.cost, 4), "avg_price": round(q.cost / shares, 4) if shares else 0,
        "price_yes_before": round(p_before, 4), "price_yes_after": round(p_after, 4),
        "volume_usdc": round(market.volume_usdc, 2),
    }


def resolve_market(db: Session, market: models.Market, outcome: str,
                   evidence_url: str = "", rationale: str = "") -> dict:
    outcome = outcome.upper()
    if outcome not in ("YES", "NO", "VOID"):
        raise ValueError("outcome must be YES, NO, or VOID")
    market.status = "resolved" if outcome != "VOID" else "void"
    market.outcome = outcome
    res = models.Resolution(market_id=market.id, outcome=outcome,
                            evidence_url=evidence_url, rationale=rationale)
    db.add(res)

    # settle positions: winning shares pay out $1 each
    payouts = []
    for pos in db.query(models.Position).filter_by(market_id=market.id):
        if outcome == "VOID":
            payout = (pos.yes_shares or 0) + (pos.no_shares or 0)  # refund notional
        else:
            payout = pos.yes_shares if outcome == "YES" else pos.no_shares
        if payout:
            payouts.append({"holder": pos.holder, "payout_usdc": round(payout, 4)})
    db.commit()
    return {"market_id": market.id, "outcome": outcome, "payouts": payouts}

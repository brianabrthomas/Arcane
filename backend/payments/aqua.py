"""
aqua.py — Aqua (1inch) yield layer simulation.

Simulates yield accrual on locked prediction-market collateral.
In production, this would integrate with 1inch Aqua smart contracts.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from .. import models

AQUA_DEFAULT_APY = 0.052  # 5.2% APY


def compute_yield_offset(
    collateral_usdc: float,
    duration_days: int,
    apy: float = AQUA_DEFAULT_APY,
) -> dict:
    """
    Compute projected Aqua yield for locked collateral.
    """
    projected_yield = collateral_usdc * apy * (duration_days / 365.0)
    drag_offset_pct = projected_yield / collateral_usdc if collateral_usdc > 0 else 0.0

    return {
        "eligible_collateral_usdc": round(collateral_usdc, 2),
        "projected_apy": apy,
        "duration_days": duration_days,
        "projected_yield_usdc": round(projected_yield, 4),
        "time_value_drag_offset_pct": round(drag_offset_pct * 100, 2),
        "strategy": "Aqua shared USDC yield",
        "status": "active",
        "simulated": True,
    }


def get_position_yield(db: Session, position: models.Position) -> dict:
    """Get the Aqua yield offset for a specific position."""
    if not position.created_at:
        return compute_yield_offset(0.0, 0)

    now = datetime.now(timezone.utc)

    def _tz(dt):
        if dt is None:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    elapsed_days = (now - _tz(position.created_at)).days

    # Get market resolution deadline
    market = db.get(models.Market, position.market_id)
    if market and market.resolution_deadline:
        total_days = (_tz(market.resolution_deadline) - _tz(position.created_at)).days
    else:
        total_days = 365

    accrued = position.cost_basis_usdc * AQUA_DEFAULT_APY * (elapsed_days / 365.0)
    position.aqua_accrued_usdc = round(accrued, 4)

    return {
        "position_id": position.id,
        "wallet": position.wallet,
        "market_id": position.market_id,
        "side": position.side,
        "collateral_usdc": round(position.cost_basis_usdc, 2),
        "elapsed_days": elapsed_days,
        "total_duration_days": total_days,
        "accrued_yield_usdc": round(accrued, 4),
        "projected_total_yield_usdc": round(
            compute_yield_offset(position.cost_basis_usdc, total_days)["projected_yield_usdc"], 4
        ),
        "projected_apy": AQUA_DEFAULT_APY,
        "strategy": "Aqua shared USDC yield",
        "status": position.aqua_status,
        "simulated": True,
    }

"""
rail.py — Nanopayment execution rail.

Handles agent-to-agent micropayments via Circle/x402-style flow.
Each agent has a simulated wallet address. Payments are recorded in the DB.
Falls back to internal ledger if Circle API is not configured.
"""
from __future__ import annotations
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from .. import models
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def _agent_wallet(agent_name: str) -> str:
    """Deterministically derive a demo wallet address from agent name."""
    h = hashlib.sha256(f"arcane-agent-{agent_name}".encode()).hexdigest()
    return "0x" + h[:40]


def _mock_tx_hash() -> str:
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:16]


def _mock_sig() -> str:
    return "0x" + uuid.uuid4().hex[:64]


class AgentWalletRegistry:
    """Registry of agent wallet addresses."""

    def __init__(self):
        self._wallets: dict[str, str] = {}

    def get(self, agent_name: str) -> "AgentWallet":
        if agent_name not in self._wallets:
            self._wallets[agent_name] = _agent_wallet(agent_name)
        return AgentWallet(agent_name, self._wallets[agent_name])


class AgentWallet:
    def __init__(self, name: str, address: str):
        self.name = name
        self.address = address


registry = AgentWalletRegistry()


def execute_nanopayment(
    db: Session,
    payer: str,
    payee: str,
    amount_usdc: float,
    memo: str = "",
    rail: str = "circle",
) -> dict:
    """
    Execute a nanopayment from payer to payee.
    Records the payment in the DB and returns payment details.

    For production: integrates with Circle Programmable Wallets API.
    For MVP: records in internal ledger with simulated tx hashes.
    """
    tx_hash = _mock_tx_hash()
    auth_sig = _mock_sig()
    arc_receipt = None

    if settings.payments_live and settings.CIRCLE_API_KEY:
        # Production path: Circle Programmable Wallets API
        try:
            result = _circle_transfer(payer, payee, amount_usdc, memo)
            tx_hash = result.get("tx_hash", tx_hash)
            auth_sig = result.get("auth_sig", auth_sig)
            rail = "circle"
        except Exception as e:
            log.warning(f"Circle payment failed: {e} — using internal ledger.")
            rail = "internal"
    else:
        rail = "circle_sim"  # Simulated Circle payment

    payment = models.Payment(
        payer=payer,
        payee=payee,
        amount_usdc=round(amount_usdc, 6),
        memo=memo,
        rail=rail,
        status="confirmed",
        tx_hash=tx_hash,
        auth_sig=auth_sig,
        arc_receipt=arc_receipt,
    )
    db.add(payment)
    db.flush()

    return {
        "payment_id": payment.id,
        "payer": payer,
        "payee": payee,
        "amount_usdc": round(amount_usdc, 6),
        "memo": memo,
        "rail": rail,
        "status": "confirmed",
        "tx_hash": tx_hash,
        "auth_sig": auth_sig,
    }


def _circle_transfer(payer: str, payee: str, amount_usdc: float, memo: str) -> dict:
    """
    Circle Programmable Wallets transfer.
    Uses Circle API to move USDC between agent wallets.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {settings.CIRCLE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "idempotencyKey": uuid.uuid4().hex,
        "source": {"type": "wallet", "id": payer},
        "destination": {"type": "wallet", "id": payee},
        "amount": {"amount": f"{amount_usdc:.6f}", "currency": "USD"},
        "memo": memo,
    }

    resp = httpx.post(
        f"{settings.CIRCLE_BASE_URL}/transfers",
        json=payload,
        headers=headers,
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return {
        "tx_hash": data.get("transactionHash", _mock_tx_hash()),
        "auth_sig": data.get("id", _mock_sig()),
        "status": data.get("status", "confirmed"),
    }


def get_agent_wallets() -> dict:
    """Return all agent wallet addresses."""
    from .roster import ROSTER
    result = {}
    for agent_cls in ROSTER:
        a = agent_cls()
        result[a.name] = _agent_wallet(a.name)
    return result

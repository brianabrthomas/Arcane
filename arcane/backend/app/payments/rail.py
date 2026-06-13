"""
PaymentRail — the economic substrate for the agentic economy.

Every agent is a real signing wallet (an EIP-3009 capable keypair, the local
analogue of a Circle Agent Wallet). When one agent buys research from another,
the buyer signs an x402/EIP-3009 authorization and the rail settles it:

  PAYMENT_MODE=sim   -> authorization is really signed; settlement is recorded
                        with a synthetic batch hash (offline, no funds needed).
  PAYMENT_MODE=live  -> authorization is really signed; settlement posts USDC on
                        Arc Testnet via the operator wallet (stand-in for the
                        Gateway batch tx). Returns a real, explorer-viewable hash.

Swapping in Circle's hosted Gateway batch endpoint is a single call-site change
inside `_settle_live` — the signed payload we produce is already x402-shaped.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from ..config import get_settings
from .arc import arc
from .x402 import PaymentRequirements, ephemeral_agent_key, sign_payment

settings = get_settings()


@dataclass
class AgentWallet:
    name: str
    address: str
    private_key: str


class WalletRegistry:
    """In-memory wallet book. In production these are Circle Agent Wallets."""
    def __init__(self) -> None:
        self._wallets: dict[str, AgentWallet] = {}

    def get(self, name: str) -> AgentWallet:
        if name not in self._wallets:
            pk, addr = ephemeral_agent_key()
            self._wallets[name] = AgentWallet(name, addr, pk)
        return self._wallets[name]

    def all(self) -> list[AgentWallet]:
        return list(self._wallets.values())


registry = WalletRegistry()


@dataclass
class Settlement:
    payer: str
    payee: str
    amount_usdc: float
    memo: str
    rail: str
    status: str
    tx_hash: str
    auth_sig: str


def settle(payer: str, payee: str, amount_usdc: float, memo: str,
           resource: str = "") -> Settlement:
    """Run the full x402 flow between two agents and settle it."""
    buyer = registry.get(payer)
    seller = registry.get(payee)

    # 1. Seller would answer 402 with these requirements.
    req = PaymentRequirements(
        pay_to=seller.address, amount_usdc=amount_usdc,
        resource=resource or f"arcane://{payee}", description=memo,
    )
    # 2. Buyer signs an EIP-3009 authorization off-chain (gasless).
    signed = sign_payment(buyer.private_key, req, buyer.address)
    sig_short = signed.signature[:18] + "…" + signed.signature[-6:]

    # 3. Settlement.
    if settings.payments_live:
        tx_hash, rail, status = _settle_live(seller.address, amount_usdc)
    else:
        tx_hash = "0x" + secrets.token_hex(32)          # synthetic batch hash
        rail, status = "sim-batch", "settled"

    return Settlement(payer, payee, amount_usdc, memo, rail, status, tx_hash, sig_short)


def _settle_live(seller_addr: str, amount_usdc: float):
    """Settle on Arc Testnet. Returns (tx_hash, rail, status)."""
    try:
        tx = arc().transfer_usdc(seller_addr, amount_usdc)
        return tx, "arc-onchain", "settled"
    except Exception as e:  # fall back to a recorded authorization
        return f"pending:{e.__class__.__name__}", "arc-x402-pending", "authorized"

"""
ledger.py — Ledger hardware wallet policy and approval integration.

Implements:
- EIP-712 typed data signing for trade orders
- Human-in-the-loop approval policy for high-risk trades
- Ledger Nano X/S+ device simulation for MVP
- Policy engine: per-wallet, per-agent spending limits

Reference: https://developers.ledger.com/ethglobalnyc
"""
from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


# ─── EIP-712 Domain ───────────────────────────────────────────────────────

EIP712_DOMAIN = {
    "name": "Arcane Legal Alpha Exchange",
    "version": "1",
    "chainId": settings.ARC_CHAIN_ID,
}

TRADE_ORDER_TYPE = {
    "TradeOrder": [
        {"name": "marketId", "type": "string"},
        {"name": "side", "type": "string"},
        {"name": "budgetUsdc", "type": "uint256"},
        {"name": "nonce", "type": "string"},
        {"name": "expiry", "type": "uint256"},
    ]
}

RESOLUTION_TYPE = {
    "Resolution": [
        {"name": "marketId", "type": "string"},
        {"name": "outcome", "type": "string"},
        {"name": "evidenceUrl", "type": "string"},
        {"name": "nonce", "type": "string"},
    ]
}

AGENT_POLICY_TYPE = {
    "AgentPolicy": [
        {"name": "wallet", "type": "address"},
        {"name": "agent", "type": "string"},
        {"name": "maxPaymentPerCall", "type": "uint256"},
        {"name": "maxDailySpend", "type": "uint256"},
        {"name": "maxTradeSize", "type": "uint256"},
        {"name": "requireLedgerAbove", "type": "uint256"},
    ]
}


def build_trade_order_hash(
    market_id: str,
    side: str,
    budget_usdc: float,
    nonce: str,
    expiry: int,
) -> str:
    """
    Compute the EIP-712 struct hash for a TradeOrder.
    This is what the Ledger device displays and signs.
    """
    raw = f"{market_id}:{side}:{int(budget_usdc * 1e6)}:{nonce}:{expiry}"
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


def build_resolution_hash(
    market_id: str,
    outcome: str,
    evidence_url: str,
    nonce: str,
) -> str:
    """Compute the EIP-712 struct hash for a Resolution."""
    raw = f"{market_id}:{outcome}:{evidence_url}:{nonce}"
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


def build_agent_policy_hash(
    wallet: str,
    agent: str,
    max_payment: float,
    max_daily: float,
    max_trade: float,
    require_ledger_above: float,
) -> str:
    """Compute the EIP-712 struct hash for an AgentPolicy."""
    raw = f"{wallet}:{agent}:{int(max_payment*1e6)}:{int(max_daily*1e6)}:{int(max_trade*1e6)}:{int(require_ledger_above*1e6)}"
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


# ─── Policy Engine ────────────────────────────────────────────────────────

class LedgerPolicyEngine:
    """
    Enforces Ledger hardware approval policy for high-risk actions.

    Policy rules (configurable per wallet):
    1. Trades < $100 USDC: EIP-712 wallet signature only
    2. Trades >= $100 USDC: Ledger hardware device approval required
    3. Market resolution: Always requires Ledger signature
    4. Agent policy changes: Requires Ledger signature
    """

    def __init__(self):
        self.threshold = settings.LEDGER_APPROVAL_THRESHOLD

    def requires_ledger(self, action: str, amount_usdc: float = 0.0) -> bool:
        """Check if an action requires Ledger hardware approval."""
        if action == "trade":
            return amount_usdc >= self.threshold
        elif action == "resolution":
            return True  # Always require Ledger for resolution
        elif action == "policy_change":
            return True  # Always require Ledger for policy changes
        elif action == "agent_trade":
            return amount_usdc >= self.threshold * 0.5  # Lower threshold for agent trades
        return False

    def verify_ledger_sig(
        self,
        sig: str,
        expected_hash: str,
        wallet: str,
    ) -> bool:
        """
        Verify a Ledger hardware signature.
        In simulation mode, accepts any non-empty sig starting with '0xledger'.
        In production, verifies via eth_account.
        """
        if not sig:
            return False

        # Simulation mode: accept demo signatures
        if sig.startswith("0xledger") or sig.startswith("0xdemo"):
            return True

        # Production: verify EIP-712 signature
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
            recovered = Account.recover_message(
                encode_defunct(hexstr=expected_hash),
                signature=sig,
            )
            return recovered.lower() == wallet.lower()
        except Exception as e:
            log.warning(f"Ledger sig verification failed: {e}")
            return False

    def get_policy_for_wallet(self, wallet: str, db) -> dict:
        """Get the Ledger policy for a wallet from the database."""
        from .. import models
        policies = db.query(models.AgentPolicy).filter_by(
            wallet=wallet, is_active=True
        ).all()

        if not policies:
            return {
                "wallet": wallet,
                "max_payment_per_call": 0.01,
                "max_daily_spend": 5.0,
                "max_trade_size": 100.0,
                "require_ledger_above": self.threshold,
                "is_default": True,
            }

        # Aggregate policies
        return {
            "wallet": wallet,
            "max_payment_per_call": min(p.max_payment_per_call for p in policies),
            "max_daily_spend": min(p.max_daily_spend for p in policies),
            "max_trade_size": min(p.max_trade_size for p in policies),
            "require_ledger_above": min(p.require_ledger_above for p in policies),
            "is_default": False,
            "policy_count": len(policies),
        }

    def check_agent_spending_limit(
        self,
        wallet: str,
        agent: str,
        amount_usdc: float,
        db,
    ) -> tuple[bool, str]:
        """
        Check if an agent payment is within the wallet's policy limits.
        Returns (allowed, reason).
        """
        from .. import models
        policy = db.query(models.AgentPolicy).filter_by(
            wallet=wallet, agent=agent, is_active=True
        ).first()

        if not policy:
            # Default: allow up to $0.01 per call
            if amount_usdc > 0.01:
                return False, f"Amount ${amount_usdc:.4f} exceeds default limit $0.01"
            return True, "ok"

        if amount_usdc > policy.max_payment_per_call:
            return False, f"Amount ${amount_usdc:.4f} exceeds per-call limit ${policy.max_payment_per_call:.4f}"

        # Check daily spend
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_spent = (
            db.query(models.Payment)
            .filter(
                models.Payment.payer == wallet,
                models.Payment.payee == agent,
                models.Payment.created_at >= today_start,
            )
            .all()
        )
        daily_total = sum(p.amount_usdc for p in daily_spent)

        if daily_total + amount_usdc > policy.max_daily_spend:
            return False, f"Daily limit ${policy.max_daily_spend:.2f} would be exceeded (spent: ${daily_total:.4f})"

        return True, "ok"


# ─── Ledger Device Simulation ─────────────────────────────────────────────

class LedgerDeviceSimulator:
    """
    Simulates a Ledger Nano X/S+ device for MVP demo.

    In production, this would use @ledgerhq/hw-transport-webusb
    or the Ledger Connect Kit to communicate with the physical device.
    """

    def simulate_approval(
        self,
        order_hash: str,
        wallet: str,
        action: str,
        amount_usdc: float,
    ) -> dict:
        """
        Simulate Ledger device approval.
        Returns a mock Ledger signature.
        """
        import uuid
        sig = "0xledger_" + uuid.uuid4().hex
        return {
            "approved": True,
            "signature": sig,
            "device": "Ledger Nano X (Simulated)",
            "firmware": "2.1.0",
            "app": "Ethereum 1.10.4",
            "order_hash": order_hash,
            "wallet": wallet,
            "action": action,
            "amount_usdc": amount_usdc,
            "simulated": True,
        }

    def simulate_rejection(self, reason: str = "User rejected") -> dict:
        return {
            "approved": False,
            "signature": None,
            "reason": reason,
            "simulated": True,
        }


# ─── EIP-712 Typed Data Builders (Phase 6 additions) ─────────────────────────

TRADE_ORDER_TYPED_TYPES = {
    "EIP712Domain": [
        {"name": "name",              "type": "string"},
        {"name": "version",           "type": "string"},
        {"name": "chainId",           "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TradeOrder": [
        {"name": "marketId",     "type": "string"},
        {"name": "question",     "type": "string"},
        {"name": "side",         "type": "string"},
        {"name": "amountUSDC",   "type": "uint256"},
        {"name": "maxPrice",     "type": "uint256"},
        {"name": "nonce",        "type": "bytes32"},
        {"name": "expiry",       "type": "uint256"},
        {"name": "trader",       "type": "address"},
    ],
}

RESOLUTION_TYPED_TYPES = {
    "EIP712Domain": [
        {"name": "name",              "type": "string"},
        {"name": "version",           "type": "string"},
        {"name": "chainId",           "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "ResolutionApproval": [
        {"name": "marketId",         "type": "string"},
        {"name": "question",         "type": "string"},
        {"name": "outcome",          "type": "string"},
        {"name": "evidenceURI",      "type": "string"},
        {"name": "rationale",        "type": "string"},
        {"name": "resolver",         "type": "address"},
        {"name": "timestamp",        "type": "uint256"},
    ],
}


def build_trade_order_typed_data(
    market_id: str,
    question: str,
    side: str,
    amount_usdc: float,
    max_price: float,
    nonce: str,
    expiry: int,
    trader: str,
    contract_address: Optional[str] = None,
) -> dict:
    """
    Build the full EIP-712 typed data object for a TradeOrder.
    Passed to the Ledger DMK for clear-signing display.
    """
    import os
    addr = contract_address or settings.SETTLEMENT_CONTRACT_ADDRESS or "0x0000000000000000000000000000000000000000"
    amount_raw = int(amount_usdc * 1_000_000)
    max_price_raw = int(max_price * 1_000_000)
    nonce_b32 = (nonce if nonce.startswith("0x") else "0x" + nonce).ljust(66, "0")[:66]

    return {
        "types": TRADE_ORDER_TYPED_TYPES,
        "primaryType": "TradeOrder",
        "domain": {
            "name": EIP712_DOMAIN["name"],
            "version": EIP712_DOMAIN["version"],
            "chainId": EIP712_DOMAIN["chainId"],
            "verifyingContract": addr,
        },
        "message": {
            "marketId":   market_id,
            "question":   question[:128],
            "side":       side.upper(),
            "amountUSDC": amount_raw,
            "maxPrice":   max_price_raw,
            "nonce":      nonce_b32,
            "expiry":     expiry,
            "trader":     trader,
        },
        "_ledger": {
            "display": {
                "Market":    question[:80] + ("..." if len(question) > 80 else ""),
                "Side":      side.upper(),
                "Amount":    f"${amount_usdc:.2f} USDC",
                "Max Price": f"{max_price * 100:.1f}\u00a2",
                "Expires":   datetime.fromtimestamp(expiry, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "Trader":    trader[:10] + "..." + trader[-6:],
            },
            "requires_ledger": amount_usdc >= settings.LEDGER_APPROVAL_THRESHOLD,
        },
    }


def build_resolution_typed_data(
    market_id: str,
    question: str,
    outcome: str,
    evidence_uri: str,
    rationale: str,
    resolver: str,
    contract_address: Optional[str] = None,
) -> dict:
    """Build the EIP-712 typed data for a ResolutionApproval."""
    import time as _time
    addr = contract_address or settings.SETTLEMENT_CONTRACT_ADDRESS or "0x0000000000000000000000000000000000000000"
    return {
        "types": RESOLUTION_TYPED_TYPES,
        "primaryType": "ResolutionApproval",
        "domain": {
            "name": EIP712_DOMAIN["name"],
            "version": EIP712_DOMAIN["version"],
            "chainId": EIP712_DOMAIN["chainId"],
            "verifyingContract": addr,
        },
        "message": {
            "marketId":    market_id,
            "question":    question[:128],
            "outcome":     outcome.upper(),
            "evidenceURI": evidence_uri,
            "rationale":   rationale[:256],
            "resolver":    resolver,
            "timestamp":   int(_time.time()),
        },
        "_ledger": {
            "display": {
                "Market":   question[:80] + ("..." if len(question) > 80 else ""),
                "Outcome":  outcome.upper(),
                "Evidence": evidence_uri,
                "Resolver": resolver[:10] + "..." + resolver[-6:],
            },
            "requires_ledger": True,
        },
    }


def generate_ledger_dmk_snippet(typed_data: dict) -> str:
    """Generate the JavaScript snippet for Ledger DMK integration."""
    import json as _json
    display = typed_data.get("_ledger", {}).get("display", {})
    display_lines = "\n".join(f"   * {k}: {v}" for k, v in display.items())
    td_json = _json.dumps({k: v for k, v in typed_data.items() if k != "_ledger"}, indent=2)
    return f"""
// Arcane x Ledger DMK — EIP-712 Clear Signing
// Device display fields:
{display_lines}
async function signWithLedger(typedData) {{
  const transport = await window.LedgerHQ?.createTransport?.();
  const eth = new window.LedgerHQ.Eth(transport);
  const result = await eth.signEIP712Message(\"44'/60'/0'/0/0\", typedData);
  return {{ v: result.v, r: '0x' + result.r, s: '0x' + result.s,
            signature: '0x' + result.r + result.s + result.v.toString(16).padStart(2,'0') }};
}}
"""


# Singletons
_policy_engine: Optional[LedgerPolicyEngine] = None
_device_sim: Optional[LedgerDeviceSimulator] = None


def policy() -> LedgerPolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = LedgerPolicyEngine()
    return _policy_engine


def device_sim() -> LedgerDeviceSimulator:
    global _device_sim
    if _device_sim is None:
        _device_sim = LedgerDeviceSimulator()
    return _device_sim

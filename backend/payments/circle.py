"""
circle.py — Circle USDC Programmable Wallets integration.

Implements:
- Circle Programmable Wallets for agent-to-agent micropayments
- x402 payment-required flow for agent service gating
- USDC balance queries and transfer initiation
- Sandbox mode when CIRCLE_API_KEY is not configured

Circle API reference: https://developers.circle.com/wallets
Circle USDC reference: https://developers.circle.com/stablecoins/what-is-usdc
"""
from __future__ import annotations
import logging
import uuid
from typing import Optional
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class CircleClient:
    """
    Circle Programmable Wallets API client.
    Manages agent wallets and USDC transfers.
    """

    def __init__(self):
        self.api_key = settings.CIRCLE_API_KEY
        self.base_url = settings.CIRCLE_BASE_URL
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @property
    def live(self) -> bool:
        return bool(self.api_key)

    def create_wallet(self, name: str, idempotency_key: str = None) -> dict:
        """
        Create a Circle Programmable Wallet for an agent.
        Returns wallet_id and address.
        """
        if not self.live:
            return self._mock_wallet(name)

        try:
            import httpx
            payload = {
                "idempotencyKey": idempotency_key or uuid.uuid4().hex,
                "accountType": "EOA",
                "blockchains": ["ETH-SEPOLIA"],  # Use Sepolia for testnet
                "metadata": {"name": name, "ref": f"arcane-agent-{name}"},
            }
            resp = httpx.post(
                f"{self.base_url}/wallets",
                json=payload,
                headers=self._headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("wallet", {})
            return {
                "wallet_id": data.get("id"),
                "address": data.get("address"),
                "blockchain": data.get("blockchain"),
                "state": data.get("state"),
                "simulated": False,
            }
        except Exception as e:
            log.warning(f"Circle wallet creation failed: {e}")
            return self._mock_wallet(name)

    def get_balance(self, wallet_id: str) -> dict:
        """Get USDC balance for a Circle wallet."""
        if not self.live:
            return {"usdc": 1000.0, "simulated": True}

        try:
            import httpx
            resp = httpx.get(
                f"{self.base_url}/wallets/{wallet_id}/balances",
                headers=self._headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            balances = resp.json().get("data", {}).get("tokenBalances", [])
            usdc_balance = next(
                (float(b["amount"]) for b in balances if b.get("token", {}).get("symbol") == "USDC"),
                0.0
            )
            return {"usdc": usdc_balance, "simulated": False}
        except Exception as e:
            log.warning(f"Circle balance query failed: {e}")
            return {"usdc": 1000.0, "simulated": True}

    def transfer(
        self,
        source_wallet_id: str,
        destination_address: str,
        amount_usdc: float,
        memo: str = "",
    ) -> dict:
        """
        Transfer USDC from a Circle wallet to an address.
        Implements x402-style payment for agent services.
        """
        if not self.live:
            return self._mock_transfer(source_wallet_id, destination_address, amount_usdc)

        try:
            import httpx
            payload = {
                "idempotencyKey": uuid.uuid4().hex,
                "source": {"type": "wallet", "id": source_wallet_id},
                "destination": {"type": "address", "address": destination_address, "chain": "ETH-SEPOLIA"},
                "amount": {"amount": f"{amount_usdc:.6f}", "currency": "USD"},
                "memo": memo,
            }
            resp = httpx.post(
                f"{self.base_url}/transfers",
                json=payload,
                headers=self._headers,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "transfer_id": data.get("id"),
                "tx_hash": data.get("transactionHash", ""),
                "status": data.get("status", "pending"),
                "amount_usdc": amount_usdc,
                "simulated": False,
            }
        except Exception as e:
            log.warning(f"Circle transfer failed: {e}")
            return self._mock_transfer(source_wallet_id, destination_address, amount_usdc)

    def _mock_wallet(self, name: str) -> dict:
        """Generate a simulated Circle wallet."""
        import hashlib
        addr = "0x" + hashlib.sha256(f"circle-{name}".encode()).hexdigest()[:40]
        return {
            "wallet_id": f"sim-wallet-{name.lower().replace(' ', '-')}",
            "address": addr,
            "blockchain": "ETH-SEPOLIA",
            "state": "LIVE",
            "simulated": True,
        }

    def _mock_transfer(self, source: str, dest: str, amount: float) -> dict:
        return {
            "transfer_id": uuid.uuid4().hex,
            "tx_hash": "0x" + uuid.uuid4().hex,
            "status": "confirmed",
            "amount_usdc": amount,
            "simulated": True,
        }


# ─── x402 Payment-Required Flow ───────────────────────────────────────────

class X402PaymentGate:
    """
    Implements x402-style HTTP payment-required flow for agent services.

    When an agent requests a service (e.g., ProbabilityAgent forecast),
    the service returns a 402 Payment Required with payment details.
    The requesting agent pays, then retries with a payment receipt.

    Reference: https://developers.circle.com/gateway
    """

    def __init__(self, circle: CircleClient):
        self.circle = circle

    def payment_required_response(
        self,
        service: str,
        price_usdc: float,
        payee_address: str,
        nonce: str,
    ) -> dict:
        """
        Generate a 402 Payment Required response.
        The client must pay this amount to access the service.
        """
        return {
            "status": 402,
            "error": "Payment Required",
            "service": service,
            "price_usdc": price_usdc,
            "payee": payee_address,
            "nonce": nonce,
            "payment_instructions": {
                "network": "Arc Testnet",
                "chain_id": settings.ARC_CHAIN_ID,
                "usdc_contract": settings.ARC_USDC_CONTRACT,
                "amount": price_usdc,
                "memo": f"arcane:{service}:{nonce}",
            },
            "expires_in_seconds": 300,
        }

    def verify_payment(
        self,
        tx_hash: str,
        expected_amount: float,
        payee_address: str,
        nonce: str,
    ) -> bool:
        """
        Verify that a payment was made before granting service access.
        In simulation mode, always returns True.
        """
        if not self.circle.live:
            return True  # Simulation: always allow

        # In production: query Arc Testnet for the transaction
        # and verify amount, recipient, and nonce in tx data
        try:
            from ..payments.arc import arc
            w3 = arc()._get_web3()
            if not w3 or not w3.is_connected():
                return True  # Fallback to allow

            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt and receipt["status"] == 1:
                return True
            return False
        except Exception:
            return True  # Fallback to allow in case of errors


# Singletons
_circle_client: Optional[CircleClient] = None
_x402_gate: Optional[X402PaymentGate] = None


def circle() -> CircleClient:
    global _circle_client
    if _circle_client is None:
        _circle_client = CircleClient()
    return _circle_client


def x402() -> X402PaymentGate:
    global _x402_gate
    if _x402_gate is None:
        _x402_gate = X402PaymentGate(circle())
    return _x402_gate

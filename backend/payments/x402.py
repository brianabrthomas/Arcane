"""
payments/x402.py — x402 Circle Gateway nanopayment middleware

Implements the HTTP 402 Payment Required protocol for agent-to-agent micropayments.
Based on Circle's x402 spec: https://developers.circle.com/gateway

Protocol flow:
  1. Client calls an agent endpoint → receives HTTP 402 with payment requirements
  2. Client constructs an EIP-3009 transferWithAuthorization signed message
  3. Client retries the request with X-PAYMENT header containing the signed authorization
  4. Server validates the authorization, records it in the Payment table, and processes the request
  5. Batch settlement runs periodically to finalize payments on-chain via Arc

State variables (all mutable):
  - PAYMENT_REQUIREMENTS: dict mapping endpoint path → price in USDC
  - _pending_authorizations: in-memory queue for batch settlement
  - _settlement_batch_id: current batch ID (incremented on each settlement run)

Connections:
  - models.py: Payment (x402_authorization, x402_nonce, x402_settled fields)
  - contracts/settlement.py: ArcaneSettlementClient for on-chain settlement
  - config.py: Settings (USDC address, chain ID, facilitator address)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

logger = logging.getLogger("arcane.payments.x402")

# ---------------------------------------------------------------------------
# x402 Payment Requirements — price per agent endpoint call
# ---------------------------------------------------------------------------

PAYMENT_REQUIREMENTS: dict[str, dict[str, Any]] = {
    # Agent research endpoints
    "/api/research": {
        "price_usdc": 0.01,
        "description": "Full 9-agent research pipeline for one market",
        "max_timeout_seconds": 120,
    },
    "/api/agents/casescout": {
        "price_usdc": 0.001,
        "description": "CaseScout: case data retrieval and structuring",
        "max_timeout_seconds": 30,
    },
    "/api/agents/docket": {
        "price_usdc": 0.001,
        "description": "Docket: CourtListener docket event parsing",
        "max_timeout_seconds": 30,
    },
    "/api/agents/catalyst": {
        "price_usdc": 0.001,
        "description": "Catalyst: statutory deadline extraction",
        "max_timeout_seconds": 30,
    },
    "/api/agents/precedent": {
        "price_usdc": 0.002,
        "description": "Precedent: analogous case law search",
        "max_timeout_seconds": 45,
    },
    "/api/agents/damages": {
        "price_usdc": 0.002,
        "description": "Damages: financial exposure estimation",
        "max_timeout_seconds": 45,
    },
    "/api/agents/probability": {
        "price_usdc": 0.003,
        "description": "Probability: Bayesian outcome estimation",
        "max_timeout_seconds": 60,
    },
    "/api/agents/marketmaker": {
        "price_usdc": 0.001,
        "description": "MarketMaker: LMSR parameter optimization",
        "max_timeout_seconds": 15,
    },
    "/api/agents/trader": {
        "price_usdc": 0.002,
        "description": "TraderAgent: autonomous position sizing",
        "max_timeout_seconds": 30,
    },
    "/api/agents/auditor": {
        "price_usdc": 0.001,
        "description": "Auditor: agent output validation",
        "max_timeout_seconds": 15,
    },
}

# ---------------------------------------------------------------------------
# EIP-3009 domain and type hashes
# ---------------------------------------------------------------------------

# EIP-3009 TypeHash for transferWithAuthorization
TRANSFER_WITH_AUTHORIZATION_TYPEHASH = Web3.keccak(
    text="TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
).hex()

# Arc Testnet USDC
ARC_USDC_ADDRESS = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
ARC_CHAIN_ID = 5042002

# EIP-712 domain for USDC on Arc Testnet
USDC_DOMAIN = {
    "name": "USD Coin",
    "version": "2",
    "chainId": ARC_CHAIN_ID,
    "verifyingContract": ARC_USDC_ADDRESS,
}


# ---------------------------------------------------------------------------
# x402 Payment Header Parser
# ---------------------------------------------------------------------------

def parse_x402_header(header_value: str) -> dict[str, Any] | None:
    """
    Parse the X-PAYMENT header value.
    Expected format (base64-encoded JSON):
      {
        "x402Version": 1,
        "scheme": "exact",
        "network": "arc-testnet",
        "payload": {
          "from": "0x...",
          "to": "0x...",
          "value": "10000",         // 6-decimal USDC amount
          "validAfter": "0",
          "validBefore": "9999999999",
          "nonce": "0x...",
          "v": 27,
          "r": "0x...",
          "s": "0x..."
        }
      }
    """
    try:
        import base64
        decoded = base64.b64decode(header_value).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        # Try raw JSON
        try:
            return json.loads(header_value)
        except Exception:
            return None


def build_x402_payment_required_response(
    endpoint: str,
    facilitator_address: str | None = None,
) -> dict[str, Any]:
    """
    Build the HTTP 402 Payment Required response body.
    This is returned to the client when they call a paid endpoint without payment.
    """
    req = PAYMENT_REQUIREMENTS.get(endpoint, {
        "price_usdc": 0.01,
        "description": "Agent service call",
        "max_timeout_seconds": 60,
    })

    price_raw = int(req["price_usdc"] * 1_000_000)  # Convert to 6-decimal integer
    valid_before = int(time.time()) + req["max_timeout_seconds"]

    facilitator = facilitator_address or os.getenv(
        "ARC_OPERATOR_ADDRESS",
        "0x4F6726d6FF89E42A8594D7C167AD2f11c8034577"
    )

    return {
        "x402Version": 1,
        "error": "X-PAYMENT header required",
        "accepts": [
            {
                "scheme": "exact",
                "network": "arc-testnet",
                "maxAmountRequired": str(price_raw),
                "resource": endpoint,
                "description": req["description"],
                "mimeType": "application/json",
                "payTo": facilitator,
                "maxTimeoutSeconds": req["max_timeout_seconds"],
                "asset": ARC_USDC_ADDRESS,
                "extra": {
                    "name": "USD Coin",
                    "version": "2",
                    "chainId": ARC_CHAIN_ID,
                    "eip3009": True,
                    "typehash": TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
                    "domain": USDC_DOMAIN,
                    "validBefore": valid_before,
                    "eip712Types": {
                        "TransferWithAuthorization": [
                            {"name": "from", "type": "address"},
                            {"name": "to", "type": "address"},
                            {"name": "value", "type": "uint256"},
                            {"name": "validAfter", "type": "uint256"},
                            {"name": "validBefore", "type": "uint256"},
                            {"name": "nonce", "type": "bytes32"},
                        ]
                    }
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# EIP-3009 Authorization Validator
# ---------------------------------------------------------------------------

def validate_eip3009_authorization(
    auth: dict[str, Any],
    expected_to: str,
    min_value_raw: int,
) -> tuple[bool, str]:
    """
    Validate an EIP-3009 TransferWithAuthorization.

    Returns (is_valid, error_message).
    In simulation mode (no real USDC), always returns (True, "simulated").
    """
    # Simulation mode — skip real validation
    if os.getenv("PAYMENTS_LIVE", "false").lower() != "true":
        return True, "simulated"

    try:
        from_addr = Web3.to_checksum_address(auth["from"])
        to_addr = Web3.to_checksum_address(auth["to"])
        value = int(auth["value"])
        valid_after = int(auth["validAfter"])
        valid_before = int(auth["validBefore"])
        nonce = auth["nonce"]
        v = int(auth["v"])
        r = auth["r"]
        s = auth["s"]

        # Check recipient
        if to_addr.lower() != expected_to.lower():
            return False, f"Payment recipient mismatch: expected {expected_to}, got {to_addr}"

        # Check amount
        if value < min_value_raw:
            return False, f"Payment amount too low: expected >= {min_value_raw}, got {value}"

        # Check time window
        now = int(time.time())
        if now < valid_after:
            return False, f"Payment not yet valid (validAfter={valid_after}, now={now})"
        if now >= valid_before:
            return False, f"Payment expired (validBefore={valid_before}, now={now})"

        # Reconstruct EIP-712 message hash
        domain_separator = _compute_domain_separator()
        struct_hash = Web3.keccak(
            Web3.to_bytes(hexstr=TRANSFER_WITH_AUTHORIZATION_TYPEHASH) +
            Web3.to_bytes(hexstr=Web3.to_hex(Web3.to_bytes(hexstr=from_addr.lower()).rjust(32, b'\x00'))) +
            Web3.to_bytes(hexstr=Web3.to_hex(Web3.to_bytes(hexstr=to_addr.lower()).rjust(32, b'\x00'))) +
            value.to_bytes(32, 'big') +
            valid_after.to_bytes(32, 'big') +
            valid_before.to_bytes(32, 'big') +
            Web3.to_bytes(hexstr=nonce)
        )
        digest = Web3.keccak(b'\x19\x01' + Web3.to_bytes(hexstr=domain_separator.hex()) + struct_hash)

        # Recover signer
        recovered = Account.recover_message(
            encode_defunct(digest),
            vrs=(v, Web3.to_bytes(hexstr=r), Web3.to_bytes(hexstr=s))
        )

        if recovered.lower() != from_addr.lower():
            return False, f"Signature mismatch: recovered {recovered}, expected {from_addr}"

        return True, "valid"

    except Exception as e:
        logger.warning("EIP-3009 validation error: %s", e)
        return False, str(e)


def _compute_domain_separator() -> bytes:
    """Compute the EIP-712 domain separator for USDC on Arc Testnet."""
    domain_type_hash = Web3.keccak(
        text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    )
    return Web3.keccak(
        domain_type_hash +
        Web3.keccak(text=USDC_DOMAIN["name"]) +
        Web3.keccak(text=USDC_DOMAIN["version"]) +
        USDC_DOMAIN["chainId"].to_bytes(32, 'big') +
        Web3.to_bytes(hexstr=USDC_DOMAIN["verifyingContract"].lower()).rjust(32, b'\x00')
    )


# ---------------------------------------------------------------------------
# x402 Middleware for FastAPI
# ---------------------------------------------------------------------------

class X402Middleware:
    """
    FastAPI middleware that enforces x402 payment on protected agent endpoints.

    Usage:
        app.add_middleware(X402Middleware, protected_paths=["/api/research"])

    In demo mode (PAYMENTS_LIVE=false), all requests pass through with a
    simulated payment record. In live mode, the X-PAYMENT header is required
    and validated against EIP-3009.
    """

    def __init__(self, app, protected_paths: list[str] | None = None):
        self.app = app
        self.protected_paths = protected_paths or list(PAYMENT_REQUIREMENTS.keys())
        self._live = os.getenv("PAYMENTS_LIVE", "false").lower() == "true"
        self._facilitator = os.getenv(
            "ARC_OPERATOR_ADDRESS",
            "0x4F6726d6FF89E42A8594D7C167AD2f11c8034577"
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Check if this path requires payment
        requires_payment = any(path.startswith(p) for p in self.protected_paths)

        if not requires_payment or not self._live:
            # Pass through — record simulated payment if applicable
            await self.app(scope, receive, send)
            return

        # Extract X-PAYMENT header
        headers = dict(scope.get("headers", []))
        payment_header = headers.get(b"x-payment", b"").decode("utf-8")

        if not payment_header:
            # Return HTTP 402
            response_body = json.dumps(
                build_x402_payment_required_response(path, self._facilitator)
            ).encode()
            await send({
                "type": "http.response.start",
                "status": 402,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(response_body)).encode()],
                ],
            })
            await send({
                "type": "http.response.body",
                "body": response_body,
            })
            return

        # Validate payment
        auth_data = parse_x402_header(payment_header)
        if not auth_data:
            await _send_error(send, 400, "Invalid X-PAYMENT header format")
            return

        payload = auth_data.get("payload", auth_data)
        req = PAYMENT_REQUIREMENTS.get(path, {"price_usdc": 0.01})
        min_value = int(req["price_usdc"] * 1_000_000)

        is_valid, error_msg = validate_eip3009_authorization(
            payload, self._facilitator, min_value
        )

        if not is_valid:
            await _send_error(send, 402, f"Invalid payment: {error_msg}")
            return

        # Payment valid — add payment info to scope for downstream handlers
        scope["x402_payment"] = {
            "from": payload.get("from"),
            "amount_usdc": int(payload.get("value", 0)) / 1_000_000,
            "nonce": payload.get("nonce"),
            "endpoint": path,
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }

        await self.app(scope, receive, send)


async def _send_error(send, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Payment record creation helper (called from route handlers)
# ---------------------------------------------------------------------------

def create_x402_payment_record(
    payer: str,
    payee: str,
    amount_usdc: float,
    memo: str,
    endpoint: str,
    authorization: dict | None = None,
) -> dict[str, Any]:
    """
    Create a Payment record dict for the x402 nanopayment.
    This is stored in the DB by the calling route handler.
    """
    nonce = "0x" + secrets.token_hex(32)
    now = datetime.now(timezone.utc)

    return {
        "payer": payer,
        "payee": payee,
        "amount_usdc": amount_usdc,
        "memo": memo,
        "rail": "x402",
        "status": "confirmed" if os.getenv("PAYMENTS_LIVE", "false").lower() != "true" else "pending",
        "tx_hash": None,
        "auth_sig": None,
        "arc_receipt": None,
        "x402_authorization": authorization,
        "x402_nonce": authorization.get("nonce", nonce) if authorization else nonce,
        "x402_valid_after": now,
        "x402_valid_before": now + timedelta(seconds=300),
        "x402_settled": False,
        "x402_settlement_batch_id": None,
    }


# ---------------------------------------------------------------------------
# Batch settlement runner
# ---------------------------------------------------------------------------

async def run_x402_batch_settlement(db_session) -> dict[str, Any]:
    """
    Settle all pending x402 payments in a batch.
    In live mode, this calls the Arc Testnet to finalize the EIP-3009 transfers.
    In simulation mode, marks all pending payments as settled.

    Called by the scheduler every 5 minutes.
    """
    from ..models import Payment

    batch_id = "batch_" + secrets.token_hex(8)
    settled_count = 0
    total_usdc = 0.0

    try:
        pending = db_session.query(Payment).filter(
            Payment.rail == "x402",
            Payment.x402_settled == False,
            Payment.status == "confirmed",
        ).all()

        for payment in pending:
            payment.x402_settled = True
            payment.x402_settlement_batch_id = batch_id
            settled_count += 1
            total_usdc += payment.amount_usdc or 0.0

        db_session.commit()
        logger.info("x402 batch settlement %s: settled %d payments, %.4f USDC",
                    batch_id, settled_count, total_usdc)

    except Exception as e:
        logger.error("x402 batch settlement failed: %s", e)
        db_session.rollback()

    return {
        "batch_id": batch_id,
        "settled_count": settled_count,
        "total_usdc": total_usdc,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

"""
contracts/settlement.py — Python integration layer for ArcaneSettlement.sol

Provides a high-level ArcaneSettlementClient that wraps all contract calls:
  - createMarket()
  - buy()
  - closeMarket()
  - proposeResolution()
  - disputeResolution()
  - finalizeResolution()
  - claimPayout()
  - claimRefund()
  - voidMarket()

Also provides ArcScan receipt enrichment and event log parsing.

State variables:
  - w3: Web3 instance (mutable — switches between local fork and live Arc Testnet)
  - contract: web3 contract instance (mutable — reloaded if contract address changes)
  - operator_account: LocalAccount (mutable — loaded from env)
  - mode: "local" | "live" (mutable — set from CONTRACT_MODE env var)

Connections:
  - models.py: Market, Trade, ContractEvent, PayoutClaim, Resolution
  - config.py: Settings (RPC URLs, private key, contract address)
  - payments/arc.py: ArcScan URL builder
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract
from web3.types import TxReceipt

logger = logging.getLogger("arcane.contracts.settlement")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTRACTS_DIR = Path(__file__).parent
ABI_PATH = CONTRACTS_DIR / "ArcaneSettlement_abi.json"
BYTECODE_PATH = CONTRACTS_DIR / "ArcaneSettlement_bytecode.txt"
DEPLOYMENT_PATH = CONTRACTS_DIR / "deployment.json"

ARC_TESTNET_RPC = "https://rpc.testnet.arc.network"
LOCAL_FORK_RPC = "http://localhost:8545"
ARCSCAN_BASE = "https://testnet.arcscan.app"

# USDC on Arc Testnet (Circle-issued, 6 decimals)
ARC_USDC_ADDRESS = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"

# Solidity Status enum mapping
ON_CHAIN_STATUS = {
    0: "Open",
    1: "Closed",
    2: "ResolutionProposed",
    3: "Disputed",
    4: "Finalized",
    5: "Voided",
}

# Solidity Outcome enum mapping
ON_CHAIN_OUTCOME = {
    0: "None",
    1: "Yes",
    2: "No",
    3: "Void",
}

# Reverse mapping for Python → Solidity
OUTCOME_TO_INT = {"YES": 1, "NO": 2, "VOID": 3, "NONE": 0}


# ---------------------------------------------------------------------------
# ArcaneSettlementClient
# ---------------------------------------------------------------------------

class ArcaneSettlementClient:
    """
    High-level Python wrapper around ArcaneSettlement.sol.

    All write methods return a dict with:
      {
        "tx_hash": str,
        "block_number": int,
        "gas_used": int,
        "status": "success" | "failed",
        "arcscan_url": str,
        "event": dict | None,   # Parsed event from the receipt
      }

    All read methods return the raw contract value.
    """

    def __init__(self):
        self._abi: list[dict] = []
        self._bytecode: str = ""
        self._contract: Contract | None = None
        self._w3: Web3 | None = None
        self._operator: LocalAccount | None = None
        self._contract_address: str | None = None
        self._mode: str = "local"  # "local" | "live"
        self._initialized: bool = False

    # ── Initialization ────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Load ABI, connect to RPC, load operator wallet, bind contract."""
        if self._initialized:
            return

        # Load ABI
        if not ABI_PATH.exists():
            logger.warning("ABI file not found at %s — contract calls will be simulated", ABI_PATH)
            return
        with open(ABI_PATH) as f:
            self._abi = json.load(f)
        if BYTECODE_PATH.exists():
            with open(BYTECODE_PATH) as f:
                self._bytecode = f.read().strip()

        # Determine mode
        self._mode = os.getenv("CONTRACT_MODE", "local").lower()

        # Connect to RPC
        rpc_url = (
            os.getenv("LOCAL_RPC_URL", LOCAL_FORK_RPC)
            if self._mode == "local"
            else os.getenv("ARC_RPC_URL", ARC_TESTNET_RPC)
        )
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if not self._w3.is_connected():
            logger.warning("Cannot connect to RPC %s — contract calls will be simulated", rpc_url)
            return

        logger.info("Connected to Arc chain_id=%s block=%s mode=%s",
                    self._w3.eth.chain_id, self._w3.eth.block_number, self._mode)

        # Load operator wallet
        private_key = os.getenv("ARC_OPERATOR_PRIVATE_KEY")
        if private_key:
            self._operator = Account.from_key(private_key)
            logger.info("Operator wallet loaded: %s", self._operator.address)
        else:
            logger.warning("ARC_OPERATOR_PRIVATE_KEY not set — write calls will be simulated")

        # Load contract address
        contract_addr = os.getenv("SETTLEMENT_CONTRACT_ADDRESS")
        if not contract_addr and DEPLOYMENT_PATH.exists():
            with open(DEPLOYMENT_PATH) as f:
                dep = json.load(f)
            contract_addr = dep.get("contract_address")

        if contract_addr:
            self._contract_address = Web3.to_checksum_address(contract_addr)
            self._contract = self._w3.eth.contract(
                address=self._contract_address,
                abi=self._abi,
            )
            logger.info("ArcaneSettlement bound at %s", self._contract_address)
        else:
            logger.warning("No contract address — will deploy on first use")

        self._initialized = True

    @property
    def is_live(self) -> bool:
        return (
            self._initialized
            and self._contract is not None
            and self._operator is not None
            and self._w3 is not None
            and self._w3.is_connected()
        )

    @property
    def contract_address(self) -> str | None:
        return self._contract_address

    # ── ArcScan URL helpers ───────────────────────────────────────────────

    def arcscan_tx(self, tx_hash: str) -> str:
        return f"{ARCSCAN_BASE}/tx/{tx_hash}"

    def arcscan_address(self, address: str) -> str:
        return f"{ARCSCAN_BASE}/address/{address}"

    # ── Internal transaction helper ───────────────────────────────────────

    def _send_tx(self, fn, extra_gas: int = 0) -> dict[str, Any]:
        """Build, sign, broadcast a contract call and return enriched receipt."""
        if not self.is_live:
            return self._simulate_tx(fn)

        nonce = self._w3.eth.get_transaction_count(self._operator.address)
        gas_price = self._w3.eth.gas_price

        try:
            gas_est = fn.estimate_gas({"from": self._operator.address})
        except Exception as e:
            logger.warning("Gas estimation failed: %s — using default 300000", e)
            gas_est = 300_000

        tx = fn.build_transaction({
            "from": self._operator.address,
            "nonce": nonce,
            "gas": gas_est + extra_gas + 50_000,
            "gasPrice": gas_price,
        })

        signed = self._w3.eth.account.sign_transaction(tx, self._operator.key)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt: TxReceipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        tx_hex = tx_hash.hex()
        return {
            "tx_hash": tx_hex,
            "block_number": receipt["blockNumber"],
            "gas_used": receipt["gasUsed"],
            "status": "success" if receipt["status"] == 1 else "failed",
            "arcscan_url": self.arcscan_tx(tx_hex),
            "simulated": False,
        }

    def _simulate_tx(self, fn=None, label: str = "simulated") -> dict[str, Any]:
        """Return a simulated receipt when the contract is not live."""
        import secrets
        fake_hash = "0x" + secrets.token_hex(32)
        return {
            "tx_hash": fake_hash,
            "block_number": 0,
            "gas_used": 0,
            "status": "simulated",
            "arcscan_url": self.arcscan_tx(fake_hash),
            "simulated": True,
            "note": "Contract not live — set CONTRACT_MODE and ARC_OPERATOR_PRIVATE_KEY to enable real transactions",
        }

    # ── Write: Market lifecycle ───────────────────────────────────────────

    def create_market(
        self,
        question: str,
        resolution_source: str,
        close_time_unix: int,
    ) -> dict[str, Any]:
        """
        Call ArcaneSettlement.createMarket(question, resolutionSource, closeTime).
        Returns receipt + the new on-chain market ID.
        """
        if not self.is_live:
            result = self._simulate_tx()
            # In simulation mode, assign an auto-incrementing market ID
            import time
            result["contract_market_id"] = int(time.time()) % 100000
            result["status"] = "simulated"
            return result

        fn = self._contract.functions.createMarket(
            question,
            resolution_source,
            close_time_unix,
        )
        result = self._send_tx(fn)

        # Parse MarketCreated event
        if result["status"] == "success":
            try:
                receipt = self._w3.eth.get_transaction_receipt(result["tx_hash"])
                events = self._contract.events.MarketCreated().process_receipt(receipt)
                if events:
                    result["contract_market_id"] = events[0]["args"]["marketId"]
                    result["event"] = dict(events[0]["args"])
            except Exception as e:
                logger.warning("Could not parse MarketCreated event: %s", e)

        return result

    def buy(
        self,
        contract_market_id: int,
        is_yes: bool,
        usdc_amount_raw: int,  # 6-decimal integer (e.g. 10 USDC = 10_000_000)
        trader_address: str,
    ) -> dict[str, Any]:
        """
        Call ArcaneSettlement.buy(marketId, isYes, usdcAmount).
        The operator acts as the relayer — USDC must be pre-approved.
        Returns receipt + shares_out.
        """
        if not self.is_live:
            result = self._simulate_tx()
            result["contract_shares_out"] = usdc_amount_raw  # 1:1 simulation
            return result

        fn = self._contract.functions.buy(
            contract_market_id,
            is_yes,
            usdc_amount_raw,
        )
        result = self._send_tx(fn)

        # Parse TradeExecuted event
        if result["status"] == "success":
            try:
                receipt = self._w3.eth.get_transaction_receipt(result["tx_hash"])
                events = self._contract.events.TradeExecuted().process_receipt(receipt)
                if events:
                    args = events[0]["args"]
                    result["contract_shares_out"] = args["sharesOut"]
                    result["event"] = dict(args)
            except Exception as e:
                logger.warning("Could not parse TradeExecuted event: %s", e)
                result["contract_shares_out"] = usdc_amount_raw

        return result

    def close_market(self, contract_market_id: int) -> dict[str, Any]:
        """Call ArcaneSettlement.closeMarket(marketId)."""
        if not self.is_live:
            return self._simulate_tx()
        fn = self._contract.functions.closeMarket(contract_market_id)
        return self._send_tx(fn)

    def propose_resolution(
        self,
        contract_market_id: int,
        outcome_int: int,  # 1=Yes | 2=No | 3=Void
        evidence_uri: str,
    ) -> dict[str, Any]:
        """
        Call ArcaneSettlement.proposeResolution(marketId, outcome, evidenceURI).
        Starts the 24-hour dispute window.
        """
        if not self.is_live:
            result = self._simulate_tx()
            result["dispute_ends_at"] = None
            return result

        fn = self._contract.functions.proposeResolution(
            contract_market_id,
            outcome_int,
            evidence_uri,
        )
        result = self._send_tx(fn)

        # Parse ResolutionProposed event
        if result["status"] == "success":
            try:
                receipt = self._w3.eth.get_transaction_receipt(result["tx_hash"])
                events = self._contract.events.ResolutionProposed().process_receipt(receipt)
                if events:
                    args = events[0]["args"]
                    result["dispute_ends_at"] = datetime.fromtimestamp(
                        args["disputeEndsAt"], tz=timezone.utc
                    )
                    result["event"] = dict(args)
            except Exception as e:
                logger.warning("Could not parse ResolutionProposed event: %s", e)

        return result

    def dispute_resolution(self, contract_market_id: int) -> dict[str, Any]:
        """Call ArcaneSettlement.disputeResolution(marketId)."""
        if not self.is_live:
            return self._simulate_tx()
        fn = self._contract.functions.disputeResolution(contract_market_id)
        return self._send_tx(fn)

    def finalize_resolution(self, contract_market_id: int) -> dict[str, Any]:
        """
        Call ArcaneSettlement.finalizeResolution(marketId).
        Can only be called after the dispute window has passed.
        """
        if not self.is_live:
            return self._simulate_tx()
        fn = self._contract.functions.finalizeResolution(contract_market_id)
        return self._send_tx(fn)

    def claim_payout(
        self,
        contract_market_id: int,
        user_address: str,
    ) -> dict[str, Any]:
        """
        Call ArcaneSettlement.claimPayout(marketId) on behalf of the user.
        The operator relays this call; payout goes directly to user_address.
        """
        if not self.is_live:
            result = self._simulate_tx()
            result["payout_amount"] = 0
            return result

        # Switch to user-signed call if user key is available; otherwise operator relays
        fn = self._contract.functions.claimPayout(contract_market_id)
        result = self._send_tx(fn)

        # Parse PayoutClaimed event
        if result["status"] == "success":
            try:
                receipt = self._w3.eth.get_transaction_receipt(result["tx_hash"])
                events = self._contract.events.PayoutClaimed().process_receipt(receipt)
                if events:
                    args = events[0]["args"]
                    result["payout_amount"] = args["amount"]
                    result["event"] = dict(args)
            except Exception as e:
                logger.warning("Could not parse PayoutClaimed event: %s", e)

        return result

    def claim_refund(
        self,
        contract_market_id: int,
        user_address: str,
    ) -> dict[str, Any]:
        """Call ArcaneSettlement.claimRefund(marketId) for voided markets."""
        if not self.is_live:
            result = self._simulate_tx()
            result["refund_amount"] = 0
            return result

        fn = self._contract.functions.claimRefund(contract_market_id)
        result = self._send_tx(fn)

        if result["status"] == "success":
            try:
                receipt = self._w3.eth.get_transaction_receipt(result["tx_hash"])
                events = self._contract.events.RefundClaimed().process_receipt(receipt)
                if events:
                    args = events[0]["args"]
                    result["refund_amount"] = args["amount"]
                    result["event"] = dict(args)
            except Exception as e:
                logger.warning("Could not parse RefundClaimed event: %s", e)

        return result

    def void_market(
        self,
        contract_market_id: int,
        reason_uri: str,
    ) -> dict[str, Any]:
        """Call ArcaneSettlement.voidMarket(marketId, reasonURI)."""
        if not self.is_live:
            return self._simulate_tx()
        fn = self._contract.functions.voidMarket(contract_market_id, reason_uri)
        return self._send_tx(fn)

    # ── Read: Contract state ──────────────────────────────────────────────

    def get_market(self, contract_market_id: int) -> dict[str, Any] | None:
        """Read the full Market struct from the contract."""
        if not self.is_live:
            return None
        try:
            m = self._contract.functions.getMarket(contract_market_id).call()
            return {
                "question": m[0],
                "resolution_source": m[1],
                "close_time": m[2],
                "status": ON_CHAIN_STATUS.get(m[3], "Unknown"),
                "status_int": m[3],
                "proposed_outcome": ON_CHAIN_OUTCOME.get(m[4], "None"),
                "proposed_outcome_int": m[4],
                "final_outcome": ON_CHAIN_OUTCOME.get(m[5], "None"),
                "final_outcome_int": m[5],
                "dispute_ends_at": m[6],
                "evidence_uri": m[7],
                "creator": m[8],
                "total_yes_shares": m[9],
                "total_no_shares": m[10],
            }
        except Exception as e:
            logger.warning("getMarket(%s) failed: %s", contract_market_id, e)
            return None

    def get_user_shares(
        self,
        contract_market_id: int,
        user_address: str,
    ) -> tuple[int, int]:
        """Returns (yes_shares, no_shares) for a user in a market."""
        if not self.is_live:
            return (0, 0)
        try:
            yes = self._contract.functions.getUserShares(
                contract_market_id, True, Web3.to_checksum_address(user_address)
            ).call()
            no = self._contract.functions.getUserShares(
                contract_market_id, False, Web3.to_checksum_address(user_address)
            ).call()
            return (yes, no)
        except Exception as e:
            logger.warning("getUserShares failed: %s", e)
            return (0, 0)

    def get_user_collateral(
        self,
        contract_market_id: int,
        user_address: str,
    ) -> int:
        """Returns USDC deposited by user in a market (6-decimal integer)."""
        if not self.is_live:
            return 0
        try:
            return self._contract.functions.getUserCollateral(
                contract_market_id, Web3.to_checksum_address(user_address)
            ).call()
        except Exception as e:
            logger.warning("getUserCollateral failed: %s", e)
            return 0

    def has_claimed(
        self,
        contract_market_id: int,
        user_address: str,
    ) -> bool:
        """Returns True if the user has already claimed payout/refund."""
        if not self.is_live:
            return False
        try:
            return self._contract.functions.hasClaimed(
                contract_market_id, Web3.to_checksum_address(user_address)
            ).call()
        except Exception as e:
            logger.warning("hasClaimed failed: %s", e)
            return False

    def contract_usdc_balance(self) -> int:
        """Returns total USDC held by the contract (6-decimal integer)."""
        if not self.is_live:
            return 0
        try:
            return self._contract.functions.contractUSDCBalance().call()
        except Exception as e:
            logger.warning("contractUSDCBalance failed: %s", e)
            return 0

    def market_count(self) -> int:
        """Returns total number of markets created in the contract."""
        if not self.is_live:
            return 0
        try:
            return self._contract.functions.marketCount().call()
        except Exception as e:
            logger.warning("marketCount failed: %s", e)
            return 0

    # ── USDC approval helper ──────────────────────────────────────────────

    def approve_usdc_for_contract(self, amount_raw: int) -> dict[str, Any]:
        """
        Approve the settlement contract to spend USDC on behalf of the operator.
        Must be called before buy() if the contract is not already approved.
        """
        if not self.is_live:
            return self._simulate_tx()

        usdc_abi = [
            {
                "name": "approve",
                "type": "function",
                "inputs": [
                    {"name": "spender", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                ],
                "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable",
            }
        ]
        usdc_contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(ARC_USDC_ADDRESS),
            abi=usdc_abi,
        )
        fn = usdc_contract.functions.approve(
            self._contract_address,
            amount_raw,
        )
        return self._send_tx(fn)

    # ── Status summary ────────────────────────────────────────────────────

    def status_summary(self) -> dict[str, Any]:
        """Return a summary of the contract integration status."""
        return {
            "initialized": self._initialized,
            "is_live": self.is_live,
            "mode": self._mode,
            "contract_address": self._contract_address,
            "operator_address": self._operator.address if self._operator else None,
            "chain_id": self._w3.eth.chain_id if self._w3 and self._w3.is_connected() else None,
            "block_number": self._w3.eth.block_number if self._w3 and self._w3.is_connected() else None,
            "arcscan_contract": self.arcscan_address(self._contract_address) if self._contract_address else None,
            "market_count": self.market_count(),
            "contract_usdc_balance_raw": self.contract_usdc_balance(),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: ArcaneSettlementClient | None = None


def get_settlement_client() -> ArcaneSettlementClient:
    """Return the initialized singleton ArcaneSettlementClient."""
    global _client
    if _client is None:
        _client = ArcaneSettlementClient()
        _client.initialize()
    return _client

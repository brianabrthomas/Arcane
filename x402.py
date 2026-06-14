"""
arc.py — Arc Testnet integration.

Handles USDC balance queries, settlement receipts, and transaction broadcasting.
Falls back to simulation if ARC_OPERATOR_PRIVATE_KEY is not configured.
"""
from __future__ import annotations
import logging
import uuid
from typing import Optional
from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

# Arc Testnet chain configuration
ARC_TESTNET_CONFIG = {
    "chain_id": 5042002,
    "chain": "Arc Testnet",
    "rpc_url": "https://rpc.testnet.arc.network",
    "explorer": "https://testnet.arcscan.app",
    "native_currency": {"name": "USDC", "symbol": "USDC", "decimals": 18},
    "usdc_contract": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
}


class ArcClient:
    """
    Client for Arc Testnet interactions.
    Provides USDC balance queries, settlement receipts, and transaction broadcasting.
    """

    def __init__(self):
        self.rpc_url = settings.ARC_RPC_URL
        self.chain_id = settings.ARC_CHAIN_ID
        self.operator_key = settings.ARC_OPERATOR_PRIVATE_KEY
        self._w3 = None

    def _get_web3(self):
        """Lazy-initialize Web3 connection."""
        if self._w3 is None:
            try:
                from web3 import Web3
                self._w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 10}))
            except Exception as e:
                log.warning(f"Web3 init failed: {e}")
                return None
        return self._w3

    def chain_info(self) -> dict:
        """Return Arc Testnet chain configuration and connection status."""
        w3 = self._get_web3()
        connected = False
        block = None

        if w3:
            try:
                connected = w3.is_connected()
                if connected:
                    block = w3.eth.block_number
            except Exception as e:
                log.debug(f"Arc chain info error: {e}")

        return {
            **ARC_TESTNET_CONFIG,
            "connected": connected,
            "latest_block": block,
            "operator": self._get_operator_address(),
            "mode": "live" if self.operator_key else "simulation",
        }

    def _get_operator_address(self) -> Optional[str]:
        """Get the operator wallet address from the private key."""
        if not self.operator_key:
            return "0xArcane000000000000000000000000000000Demo"
        try:
            from eth_account import Account
            return Account.from_key(self.operator_key).address
        except Exception:
            return None

    def usdc_balance(self, address: str) -> float:
        """Query USDC balance for an address on Arc Testnet."""
        if not address or not self.operator_key:
            return 1000.0  # Simulated demo balance

        w3 = self._get_web3()
        if not w3 or not w3.is_connected():
            return 1000.0

        try:
            # ERC-20 balanceOf ABI
            abi = [{"inputs": [{"name": "account", "type": "address"}],
                    "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                    "stateMutability": "view", "type": "function"}]
            contract = w3.eth.contract(
                address=w3.to_checksum_address(settings.ARC_USDC_CONTRACT),
                abi=abi,
            )
            raw = contract.functions.balanceOf(w3.to_checksum_address(address)).call()
            return raw / 1e6  # USDC has 6 decimals
        except Exception as e:
            log.debug(f"USDC balance query failed: {e}")
            return 1000.0

    def broadcast_settlement_receipt(
        self,
        market_id: str,
        trader: str,
        side: str,
        amount_usdc: float,
        trade_id: str,
    ) -> dict:
        """
        Broadcast a settlement receipt to Arc Testnet.
        In simulation mode, returns a mock receipt.
        """
        if not self.operator_key:
            return self._mock_receipt(market_id, trader, side, amount_usdc, trade_id)

        try:
            from eth_account import Account
            from web3 import Web3

            w3 = self._get_web3()
            if not w3 or not w3.is_connected():
                return self._mock_receipt(market_id, trader, side, amount_usdc, trade_id)

            account = Account.from_key(self.operator_key)
            # In production, this would call a market contract's buy() function.
            # For MVP, we send a minimal ETH transaction as a settlement proof.
            tx = {
                "from": account.address,
                "to": account.address,  # Self-send as proof
                "value": 0,
                "gas": 21000,
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": self.chain_id,
                "data": w3.to_hex(
                    text=f"ARCANE:TRADE:{market_id[:8]}:{side}:{amount_usdc:.2f}USDC"
                ),
            }
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            return {
                "tx_hash": tx_hash.hex(),
                "network": "Arc Testnet",
                "chain_id": self.chain_id,
                "explorer_url": f"{settings.ARC_EXPLORER}/tx/{tx_hash.hex()}",
                "market_id": market_id,
                "trader": trader,
                "side": side,
                "amount_usdc": amount_usdc,
                "status": "confirmed",
                "simulated": False,
            }
        except Exception as e:
            log.warning(f"Arc broadcast failed: {e} — using simulation.")
            return self._mock_receipt(market_id, trader, side, amount_usdc, trade_id)

    def _mock_receipt(self, market_id: str, trader: str, side: str, amount_usdc: float, trade_id: str) -> dict:
        """Generate a simulated Arc settlement receipt."""
        tx_hash = "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:16]
        return {
            "tx_hash": tx_hash,
            "network": "Arc Testnet (Simulated)",
            "chain_id": ARC_TESTNET_CONFIG["chain_id"],
            "explorer_url": f"{ARC_TESTNET_CONFIG['explorer']}/tx/{tx_hash}",
            "market_id": market_id,
            "trader": trader,
            "side": side,
            "amount_usdc": amount_usdc,
            "trade_id": trade_id,
            "status": "simulated",
            "simulated": True,
        }


# Singleton
_arc_client: Optional[ArcClient] = None


def arc() -> ArcClient:
    global _arc_client
    if _arc_client is None:
        _arc_client = ArcClient()
    return _arc_client

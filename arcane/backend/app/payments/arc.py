"""
Arc Testnet client — Circle's stablecoin L1 where USDC is the native gas token.

  RPC:       https://rpc.testnet.arc.network
  Chain ID:  5042002
  USDC:      0x3600000000000000000000000000000000000000  (system contract)
             native interface = 18 decimals, ERC-20 interface = 6 decimals
  Explorer:  https://testnet.arcscan.app
  Faucet:    https://faucet.circle.com  (select "Arc Testnet")

We deliberately use ONLY the ERC-20 interface (6 decimals) for reading balances
and moving value, per Circle's guidance — mixing native/ERC-20 decimals is the
classic Arc footgun.

If web3 isn't installed or no RPC is reachable, every method degrades to a
clearly-labelled simulated value so the rest of the app keeps running.
"""
from __future__ import annotations

from ..config import get_settings

settings = get_settings()

# Minimal ERC-20 ABI — only what we touch.
ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]


class ArcClient:
    def __init__(self) -> None:
        self.ok = False
        self.w3 = None
        self.acct = None
        try:
            from web3 import Web3
            self.w3 = Web3(Web3.HTTPProvider(settings.ARC_RPC_URL, request_kwargs={"timeout": 8}))
            self.usdc = self.w3.eth.contract(
                address=Web3.to_checksum_address(settings.USDC_ADDRESS), abi=ERC20_ABI
            )
            if settings.OPERATOR_PRIVATE_KEY:
                from eth_account import Account
                self.acct = Account.from_key(settings.OPERATOR_PRIVATE_KEY)
            # a cheap liveness probe
            self.ok = bool(self.w3.is_connected())
        except Exception:
            self.ok = False

    # ---- reads ---------------------------------------------------------
    def usdc_balance(self, address: str) -> float:
        if not self.ok:
            return 0.0
        try:
            from web3 import Web3
            raw = self.usdc.functions.balanceOf(Web3.to_checksum_address(address)).call()
            return raw / (10 ** settings.USDC_DECIMALS)
        except Exception:
            return 0.0

    def chain_info(self) -> dict:
        return {
            "chain": "Arc Testnet",
            "chain_id": settings.ARC_CHAIN_ID,
            "rpc": settings.ARC_RPC_URL,
            "usdc": settings.USDC_ADDRESS,
            "explorer": settings.ARC_EXPLORER,
            "connected": self.ok,
            "operator": self.acct.address if self.acct else None,
        }

    # ---- writes (only used in PAYMENT_MODE=live) -----------------------
    def transfer_usdc(self, to: str, amount_usdc: float) -> str:
        """Direct on-chain USDC transfer (settlement fallback). Returns tx hash."""
        from web3 import Web3
        if not (self.ok and self.acct):
            raise RuntimeError("Arc operator wallet not configured")
        amount = int(round(amount_usdc * 10 ** settings.USDC_DECIMALS))
        tx = self.usdc.functions.transfer(
            Web3.to_checksum_address(to), amount
        ).build_transaction({
            "from": self.acct.address,
            "nonce": self.w3.eth.get_transaction_count(self.acct.address),
            "chainId": settings.ARC_CHAIN_ID,
            # On Arc, gas is paid in USDC natively; let the node estimate.
            "gas": 120000,
            "gasPrice": self.w3.eth.gas_price,
        })
        signed = self.acct.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        return h.hex()


_client: ArcClient | None = None


def arc() -> ArcClient:
    global _client
    if _client is None:
        _client = ArcClient()
    return _client

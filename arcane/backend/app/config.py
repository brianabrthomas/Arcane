"""
Central configuration for Arcane.

Everything is environment-driven so the same code runs in three modes:

  * LLM_MODE       = "live"  -> agents call the Anthropic API (needs ANTHROPIC_API_KEY)
                     "sim"   -> agents use deterministic heuristics (no key needed)
  * PAYMENT_MODE   = "live"  -> nanopayments settle on Arc Testnet via web3 + EIP-3009
                     "sim"   -> nanopayments are recorded with synthetic receipts
  * COURTLISTENER  = token   -> pulls real dockets; without one we fall back to seed data

The demo boots and is fully interactive with NO keys at all (sim/sim + seed data).
Flip individual layers to "live" as you wire up credentials.
"""
from __future__ import annotations

import os
from functools import lru_cache


def _b(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


class Settings:
    # ---- general -------------------------------------------------------
    APP_NAME = "Arcane — Legal Alpha Exchange"
    DB_URL = os.getenv("DB_URL", "sqlite:///./arcane.db")

    # ---- LLM / agents --------------------------------------------------
    LLM_MODE = os.getenv("LLM_MODE", "sim")            # "live" | "sim"
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # ---- case data -----------------------------------------------------
    COURTLISTENER_TOKEN = os.getenv("COURTLISTENER_TOKEN", "")
    COURTLISTENER_BASE = "https://www.courtlistener.com/api/rest/v4"

    # ---- blockchain / Circle Arc --------------------------------------
    PAYMENT_MODE = os.getenv("PAYMENT_MODE", "sim")    # "live" | "sim"
    # Arc Testnet (Circle's stablecoin L1). USDC is the native gas token.
    ARC_RPC_URL = os.getenv("ARC_RPC_URL", "https://rpc.testnet.arc.network")
    ARC_CHAIN_ID = int(os.getenv("ARC_CHAIN_ID", "5042002"))
    ARC_EXPLORER = os.getenv("ARC_EXPLORER", "https://testnet.arcscan.app")
    # USDC system contract on Arc. Native interface = 18 decimals, ERC-20 = 6.
    USDC_ADDRESS = os.getenv("USDC_ADDRESS", "0x3600000000000000000000000000000000000000")
    USDC_DECIMALS = 6  # we always price/account in the 6-decimal ERC-20 interface

    # Operator wallet (the platform's settlement key). Only needed in live mode.
    OPERATOR_PRIVATE_KEY = os.getenv("OPERATOR_PRIVATE_KEY", "")

    # Circle Gateway (nanopayment batching / x402 verification endpoint).
    GATEWAY_API = os.getenv("GATEWAY_API", "https://gateway-api-testnet.circle.com")

    @property
    def llm_live(self) -> bool:
        return self.LLM_MODE == "live" and bool(self.ANTHROPIC_API_KEY)

    @property
    def payments_live(self) -> bool:
        return self.PAYMENT_MODE == "live" and bool(self.OPERATOR_PRIVATE_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()

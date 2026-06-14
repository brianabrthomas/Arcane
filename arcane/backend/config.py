"""
config.py — Application settings loaded from environment variables.
Mutable via .env file or shell environment.
"""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Arcane · Legal Alpha Exchange"

    # Database
    DATABASE_URL: str = "sqlite:///./arcane.db"

    # LLM (OpenAI-compatible endpoint)
    # Defaults pick up the sandbox-provided OPENAI_API_KEY / OPENAI_API_BASE env vars
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_LIVE: bool = True  # Set False to use simulated agent outputs

    # CourtListener API
    COURTLISTENER_TOKEN: str = ""
    COURTLISTENER_BASE: str = "https://www.courtlistener.com/api/rest/v3"

    # SEC EDGAR
    SEC_EDGAR_BASE: str = "https://efts.sec.gov/LATEST/search-index"

    # Arc Testnet
    ARC_RPC_URL: str = "https://rpc.testnet.arc.network"
    ARC_CHAIN_ID: int = 5042002
    ARC_CHAIN_NAME: str = "Arc Testnet"
    ARC_EXPLORER: str = "https://testnet.arcscan.app"
    ARC_OPERATOR_PRIVATE_KEY: str = ""  # Operator wallet for testnet receipts
    ARC_USDC_CONTRACT: str = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"  # Testnet USDC

    # Circle
    CIRCLE_API_KEY: str = ""
    CIRCLE_BASE_URL: str = "https://api-sandbox.circle.com/v1"

    # Ledger
    LEDGER_APPROVAL_THRESHOLD: float = 100.0  # USDC — above this requires Ledger approval

    # Payments
    PAYMENTS_LIVE: bool = False  # Set True to use real Arc/Circle transactions

    # ArcaneSettlement Contract (Phase 7)
    SETTLEMENT_CONTRACT_ADDRESS: str = ""  # Deployed contract address
    ARC_OPERATOR_ADDRESS: str = "0x4F6726d6FF89E42A8594D7C167AD2f11c8034577"  # Operator wallet address
    CONTRACT_MODE: str = "simulated"  # "local" | "testnet" | "simulated"
    LOCAL_RPC_URL: str = "http://localhost:8545"  # Anvil local fork URL

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Shell environment takes precedence over .env file
        env_file_override = False

    @property
    def llm_live(self) -> bool:
        return self.LLM_LIVE and bool(self.OPENAI_API_KEY)

    @property
    def payments_live(self) -> bool:
        return self.PAYMENTS_LIVE and bool(self.ARC_OPERATOR_PRIVATE_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()

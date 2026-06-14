"""
models.py — All SQLAlchemy ORM models for Arcane.

State variables are mutable via SQLAlchemy sessions.
Each model is connected to others via ForeignKey relationships.

Phase 4 additions:
- Market: contract_market_id, contract_address, on_chain_status, dispute_ends_at,
          escrowed_usdc, on_chain_yes_shares, on_chain_no_shares
- Trade: contract_tx_hash, contract_shares_out, on_chain_confirmed
- ContractEvent: full on-chain event log for ArcScan linkage
- PayoutClaim: per-user payout/refund claim tracking
- X402Payment: EIP-3009 nanopayment authorization records
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON, BigInteger
)
from sqlalchemy.orm import relationship
from .db import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Company & Legal Case
# ---------------------------------------------------------------------------

class Company(Base):
    __tablename__ = "companies"
    id = Column(String, primary_key=True, default=_id)
    name = Column(String, nullable=False)
    ticker = Column(String, index=True)
    exchange = Column(String)
    sector = Column(String)
    # Relationships
    cases = relationship("LegalCase", back_populates="company")


class LegalCase(Base):
    __tablename__ = "legal_cases"
    id = Column(String, primary_key=True, default=_id)
    company_id = Column(String, ForeignKey("companies.id"), index=True)
    caption = Column(String, nullable=False)
    court = Column(String)
    docket_number = Column(String, index=True)
    case_type = Column(String)  # patent | antitrust | securities | regulatory
    patent_numbers = Column(JSON, default=list)  # List of patent numbers
    source_url = Column(String)
    # Data source tracking
    source_mode = Column(String, default="seed")  # seed | live_api | manual_admin
    last_checked_at = Column(DateTime, default=_now)
    next_refresh_at = Column(DateTime)
    refresh_frequency = Column(String, default="daily")  # hourly | daily | deadline_sensitive
    created_at = Column(DateTime, default=_now)
    # Relationships
    company = relationship("Company", back_populates="cases")
    events = relationship("CaseEvent", back_populates="case", order_by="CaseEvent.filed_at")
    markets = relationship("Market", back_populates="case")


class CaseEvent(Base):
    __tablename__ = "case_events"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"), index=True)
    kind = Column(String)  # filing | hearing | order | settlement | appeal
    description = Column(Text)
    filed_at = Column(DateTime)
    source = Column(String)  # courtlistener | sec_edgar | manual | seed
    created_at = Column(DateTime, default=_now)
    # Relationships
    case = relationship("LegalCase", back_populates="events")


class Catalyst(Base):
    __tablename__ = "catalysts"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"), index=True)
    label = Column(String)  # "PTAB Institution Decision" | "ITC Target Date" | etc.
    statutory_basis = Column(String)
    deadline = Column(DateTime)
    created_at = Column(DateTime, default=_now)


# ---------------------------------------------------------------------------
# Markets — extended with on-chain settlement state
# ---------------------------------------------------------------------------

class Market(Base):
    __tablename__ = "markets"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"), index=True)
    question = Column(Text, nullable=False)
    status = Column(String, default="open")  # open | closed | resolved | voided
    approved = Column(Boolean, default=False)
    outcome = Column(String)  # YES | NO | VOID | null

    # LMSR AMM state — mutable on every trade (off-chain pricing engine)
    q_yes = Column(Float, default=0.0)
    q_no = Column(Float, default=0.0)
    liquidity_b = Column(Float, default=100.0)
    volume_usdc = Column(Float, default=0.0)

    # Market lifecycle timestamps
    deadline = Column(DateTime)
    close_time = Column(DateTime)
    resolution_deadline = Column(DateTime)
    next_refresh_at = Column(DateTime)
    next_resolution_check_at = Column(DateTime)
    deadline_type = Column(String)  # trial_start | hearing_date | statutory_deadline | admin_defined

    # Resolution rules
    yes_criteria = Column(Text)
    no_criteria = Column(Text)
    void_criteria = Column(Text)
    resolution_source = Column(Text)

    # Initial probability prior
    initial_probability = Column(Float, default=0.5)
    prior_basis = Column(JSON, default=dict)  # Stores weighted prior components

    # ── On-chain settlement state (Phase 4 additions) ──────────────────────
    # contract_market_id: the uint256 market ID in ArcaneSettlement.sol
    contract_market_id = Column(Integer, nullable=True)
    # contract_address: the deployed ArcaneSettlement contract address
    contract_address = Column(String, nullable=True)
    # on_chain_status mirrors the Solidity Status enum:
    #   0=Open | 1=Closed | 2=ResolutionProposed | 3=Disputed | 4=Finalized | 5=Voided
    on_chain_status = Column(Integer, nullable=True)
    # dispute_ends_at: Unix timestamp when the 24h dispute window closes
    dispute_ends_at = Column(DateTime, nullable=True)
    # Proposed outcome before finalization (0=None|1=Yes|2=No|3=Void)
    proposed_outcome_int = Column(Integer, nullable=True)
    # Final outcome after finalization
    final_outcome_int = Column(Integer, nullable=True)
    # USDC escrowed in the contract for this market (6-decimal integer)
    escrowed_usdc_raw = Column(BigInteger, default=0)
    # On-chain share totals (mirrors contract totalYesShares / totalNoShares)
    on_chain_yes_shares = Column(BigInteger, default=0)
    on_chain_no_shares = Column(BigInteger, default=0)
    # Evidence URI stored in the contract
    evidence_uri = Column(String, nullable=True)
    # ArcScan links
    create_market_tx = Column(String, nullable=True)   # tx hash of createMarket()
    close_market_tx = Column(String, nullable=True)    # tx hash of closeMarket()
    resolution_tx = Column(String, nullable=True)      # tx hash of proposeResolution()
    finalize_tx = Column(String, nullable=True)        # tx hash of finalizeResolution()
    # Whether this market has been synced to the contract
    contract_synced = Column(Boolean, default=False)

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    # Relationships
    case = relationship("LegalCase", back_populates="markets")
    trades = relationship("Trade", back_populates="market")
    signed_orders = relationship("SignedOrder", back_populates="market")
    agent_outputs = relationship("AgentOutput", back_populates="market")
    contract_events = relationship("ContractEvent", back_populates="market")
    payout_claims = relationship("PayoutClaim", back_populates="market")


# ---------------------------------------------------------------------------
# Trading — extended with on-chain confirmation fields
# ---------------------------------------------------------------------------

class Trade(Base):
    __tablename__ = "trades"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    trader = Column(String, index=True)  # wallet address
    side = Column(String)  # YES | NO
    shares = Column(Float)
    cost_usdc = Column(Float)
    price_before = Column(Float)
    price_after = Column(Float)
    avg_price = Column(Float)
    price_impact = Column(Float)
    tx_hash = Column(String)                # Legacy Arc receipt tx hash
    arc_receipt = Column(JSON)              # Arc settlement receipt (legacy)
    # ── On-chain trade fields (Phase 4 additions) ──────────────────────────
    # contract_tx_hash: the actual on-chain tx hash from ArcaneSettlement.buy()
    contract_tx_hash = Column(String, nullable=True)
    # contract_shares_out: shares credited by the contract (6-decimal integer)
    contract_shares_out = Column(BigInteger, nullable=True)
    # on_chain_confirmed: True once the tx receipt is confirmed on Arc
    on_chain_confirmed = Column(Boolean, default=False)
    # arcscan_url: direct link to the trade tx on ArcScan
    arcscan_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)
    # Relationships
    market = relationship("Market", back_populates="trades")


class SignedOrder(Base):
    __tablename__ = "signed_orders"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    wallet = Column(String, index=True)
    side = Column(String)
    budget_usdc = Column(Float)
    shares_requested = Column(Float)
    max_price = Column(Float)
    nonce = Column(String, unique=True)
    expiry = Column(DateTime)
    signature = Column(Text)
    order_hash = Column(String, index=True)
    status = Column(String, default="pending")  # pending | executed | expired | rejected
    requires_ledger = Column(Boolean, default=False)
    ledger_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)
    # Relationships
    market = relationship("Market", back_populates="signed_orders")


class WalletBalance(Base):
    __tablename__ = "wallet_balances"
    id = Column(String, primary_key=True, default=_id)
    wallet = Column(String, index=True, unique=True)
    available_usdc = Column(Float, default=1000.0)  # Demo faucet balance
    locked_usdc = Column(Float, default=0.0)
    aqua_yield_usdc = Column(Float, default=0.0)  # Simulated Aqua yield accrued
    simulated = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Position(Base):
    __tablename__ = "positions"
    id = Column(String, primary_key=True, default=_id)
    wallet = Column(String, index=True)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    side = Column(String)  # YES | NO
    shares = Column(Float, default=0.0)
    cost_basis_usdc = Column(Float, default=0.0)
    # Aqua yield offset
    aqua_eligible_usdc = Column(Float, default=0.0)
    aqua_projected_apy = Column(Float, default=0.052)  # 5.2% default
    aqua_accrued_usdc = Column(Float, default=0.0)
    aqua_status = Column(String, default="active")  # active | withdrawn | settled
    # ── On-chain position fields (Phase 4 additions) ───────────────────────
    # on_chain_yes_shares / on_chain_no_shares: shares in the contract
    on_chain_yes_shares = Column(BigInteger, default=0)
    on_chain_no_shares = Column(BigInteger, default=0)
    # payout_claimed: True once the user has called claimPayout() or claimRefund()
    payout_claimed = Column(Boolean, default=False)
    payout_tx_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------------------
# On-chain event log (Phase 4 addition)
# ---------------------------------------------------------------------------

class ContractEvent(Base):
    """
    Records every event emitted by ArcaneSettlement.sol.
    Provides a local cache of on-chain state for fast API responses.
    """
    __tablename__ = "contract_events"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"), index=True, nullable=True)
    event_name = Column(String, index=True)  # MarketCreated | TradeExecuted | ResolutionProposed | ...
    tx_hash = Column(String, index=True)
    block_number = Column(Integer)
    log_index = Column(Integer)
    args = Column(JSON)          # Raw event args as dict
    arcscan_url = Column(String)
    created_at = Column(DateTime, default=_now)
    # Relationships
    market = relationship("Market", back_populates="contract_events")


# ---------------------------------------------------------------------------
# Payout claim tracking (Phase 4 addition)
# ---------------------------------------------------------------------------

class PayoutClaim(Base):
    """
    Tracks whether a wallet has claimed their payout or refund from a finalized/voided market.
    Mirrors the `claimed` mapping in ArcaneSettlement.sol.
    """
    __tablename__ = "payout_claims"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    wallet = Column(String, index=True)
    claim_type = Column(String)      # payout | refund
    amount_usdc = Column(Float)
    tx_hash = Column(String, nullable=True)
    arcscan_url = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | submitted | confirmed | failed
    created_at = Column(DateTime, default=_now)
    # Relationships
    market = relationship("Market", back_populates="payout_claims")


# ---------------------------------------------------------------------------
# Agents & Payments
# ---------------------------------------------------------------------------

class AgentOutput(Base):
    __tablename__ = "agent_outputs"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"), index=True)
    agent = Column(String, index=True)
    input_sources = Column(JSON, default=list)  # ["seed_data", "CourtListener"]
    method = Column(String)
    output = Column(JSON)  # Structured output from the agent
    confidence = Column(Float)
    payment_usdc = Column(Float)
    trigger = Column(String)  # manual | scheduler | webhook | trade_volume
    source_mode = Column(String, default="seed")  # seed | live_api
    created_at = Column(DateTime, default=_now)
    # Relationships
    market = relationship("Market", back_populates="agent_outputs")


class AgentReputation(Base):
    __tablename__ = "agent_reputations"
    id = Column(String, primary_key=True, default=_id)
    agent = Column(String, unique=True, index=True)
    tasks_completed = Column(Integer, default=0)
    earnings_usdc = Column(Float, default=0.0)
    reliability = Column(Float, default=1.0)  # 0.0 - 1.0
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Payment(Base):
    __tablename__ = "payments"
    id = Column(String, primary_key=True, default=_id)
    payer = Column(String, index=True)
    payee = Column(String, index=True)
    amount_usdc = Column(Float)
    memo = Column(String)
    rail = Column(String, default="circle")  # circle | arc | internal | x402
    status = Column(String, default="confirmed")  # pending | confirmed | failed
    tx_hash = Column(String)
    auth_sig = Column(String)  # EIP-712 signature or Ledger approval hash
    arc_receipt = Column(JSON)  # Arc settlement receipt if applicable
    # ── x402 nanopayment fields (Phase 4 addition) ─────────────────────────
    x402_authorization = Column(JSON, nullable=True)   # EIP-3009 signed authorization
    x402_nonce = Column(String, nullable=True, index=True)  # EIP-3009 nonce (unique)
    x402_valid_after = Column(DateTime, nullable=True)
    x402_valid_before = Column(DateTime, nullable=True)
    x402_settled = Column(Boolean, default=False)      # True once batched settlement runs
    x402_settlement_batch_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)


class AgentPolicy(Base):
    __tablename__ = "agent_policies"
    id = Column(String, primary_key=True, default=_id)
    wallet = Column(String, index=True)
    agent = Column(String)
    max_payment_per_call = Column(Float, default=0.01)
    max_daily_spend = Column(Float, default=5.0)
    max_trade_size = Column(Float, default=100.0)
    require_ledger_above = Column(Float, default=100.0)
    is_active = Column(Boolean, default=True)
    signed_policy_hash = Column(String)
    created_at = Column(DateTime, default=_now)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class Resolution(Base):
    __tablename__ = "resolutions"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"), unique=True)
    outcome = Column(String)  # YES | NO | VOID
    evidence_url = Column(String)
    rationale = Column(Text)
    resolved_by = Column(String)  # wallet address of resolver
    ledger_sig = Column(String)   # Ledger approval signature
    arc_receipt = Column(JSON)
    # ── On-chain resolution fields (Phase 4 addition) ──────────────────────
    propose_tx_hash = Column(String, nullable=True)    # proposeResolution() tx
    finalize_tx_hash = Column(String, nullable=True)   # finalizeResolution() tx
    dispute_tx_hash = Column(String, nullable=True)    # disputeResolution() tx (if any)
    void_tx_hash = Column(String, nullable=True)       # voidMarket() tx (if any)
    dispute_ends_at = Column(DateTime, nullable=True)  # When dispute window closes
    is_disputed = Column(Boolean, default=False)
    is_finalized = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)

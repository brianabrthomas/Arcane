"""
Domain models for Arcane.

Schema mirrors the whitepaper:
  companies -> legal_cases -> case_events / catalysts -> markets -> trades / positions
  agents emit agent_outputs and earn nanopayments (payments)
  markets settle via resolutions; agents accrue reputation
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON
)
from sqlalchemy.orm import relationship

from .db import Base


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


class Company(Base):
    __tablename__ = "companies"
    id = Column(String, primary_key=True, default=_id)
    name = Column(String, nullable=False)
    ticker = Column(String, index=True)
    exchange = Column(String)
    sector = Column(String, default="Pharmaceuticals")
    cases = relationship("LegalCase", back_populates="company")


class LegalCase(Base):
    __tablename__ = "legal_cases"
    id = Column(String, primary_key=True, default=_id)
    company_id = Column(String, ForeignKey("companies.id"))
    caption = Column(String, nullable=False)         # "Amarin Corp v. Hikma Pharmaceuticals"
    court = Column(String)                            # "Fed. Cir." / "PTAB" / "ITC"
    docket_number = Column(String, index=True)
    case_type = Column(String, default="patent")     # patent | biosimilar | itc | ipr
    patent_numbers = Column(JSON, default=list)       # ["US10,300,032", ...]
    cl_docket_id = Column(String)                     # CourtListener docket id, if ingested
    source_url = Column(String)
    summary = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

    company = relationship("Company", back_populates="cases")
    events = relationship("CaseEvent", back_populates="case", order_by="CaseEvent.filed_at")
    markets = relationship("Market", back_populates="case")


class CaseEvent(Base):
    """Raw docket / filing activity ingested by the Docket Agent."""
    __tablename__ = "case_events"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"))
    kind = Column(String)            # motion_filed | hearing_set | order | opinion | 8k | appeal
    description = Column(Text)
    filed_at = Column(DateTime, default=_now)
    source = Column(String)          # "CourtListener", "SEC EDGAR", "press release"
    raw = Column(JSON, default=dict)
    case = relationship("LegalCase", back_populates="events")


class Catalyst(Base):
    """Market-moving event extracted by the Legal Catalyst Agent."""
    __tablename__ = "catalysts"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"))
    label = Column(String)           # "PTAB final written decision"
    deadline = Column(DateTime)      # statutory / scheduled date (the contract expiry)
    statutory_basis = Column(String) # "35 U.S.C. 316(a)(11) — 12-month IPR clock"
    materiality = Column(String, default="high")
    created_at = Column(DateTime, default=_now)


class Market(Base):
    __tablename__ = "markets"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"))
    question = Column(Text, nullable=False)
    market_key = Column(String, default="settle_before_trial")  # base-rate key
    yes_criteria = Column(Text)
    no_criteria = Column(Text)
    void_criteria = Column(Text)
    resolution_source = Column(String)
    deadline = Column(DateTime)
    status = Column(String, default="pending")   # pending | approved | open | resolved | void
    approved = Column(Boolean, default=False)    # admin-reviewed = institutional

    # LMSR market-maker state
    liquidity_b = Column(Float, default=120.0)   # liquidity parameter b
    q_yes = Column(Float, default=0.0)           # outstanding YES shares
    q_no = Column(Float, default=0.0)            # outstanding NO shares
    volume_usdc = Column(Float, default=0.0)

    outcome = Column(String)                     # "YES" | "NO" | "VOID" after resolution
    created_at = Column(DateTime, default=_now)

    case = relationship("LegalCase", back_populates="markets")
    trades = relationship("Trade", back_populates="market")
    positions = relationship("Position", back_populates="market")


class Trade(Base):
    __tablename__ = "trades"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"))
    trader = Column(String)          # wallet / agent handle
    side = Column(String)            # YES | NO
    action = Column(String, default="buy")  # buy | sell
    shares = Column(Float)
    cost_usdc = Column(Float)        # signed: +paid / -received
    price_before = Column(Float)
    price_after = Column(Float)
    created_at = Column(DateTime, default=_now)
    market = relationship("Market", back_populates="trades")


class Position(Base):
    __tablename__ = "positions"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"))
    holder = Column(String)
    yes_shares = Column(Float, default=0.0)
    no_shares = Column(Float, default=0.0)
    market = relationship("Market", back_populates="positions")


class AgentOutput(Base):
    """A unit of research produced by an agent (and paid for via nanopayment)."""
    __tablename__ = "agent_outputs"
    id = Column(String, primary_key=True, default=_id)
    case_id = Column(String, ForeignKey("legal_cases.id"))
    market_id = Column(String, ForeignKey("markets.id"), nullable=True)
    agent = Column(String)           # "ProbabilityAgent"
    task = Column(String)            # "forecast"
    output = Column(JSON, default=dict)
    confidence = Column(Float)
    payment_id = Column(String, ForeignKey("payments.id"), nullable=True)
    created_at = Column(DateTime, default=_now)


class Payment(Base):
    """A nanopayment between economic actors (agent <- agent / trader)."""
    __tablename__ = "payments"
    id = Column(String, primary_key=True, default=_id)
    payer = Column(String)
    payee = Column(String)
    amount_usdc = Column(Float)      # e.g. 0.002
    memo = Column(String)            # "docket summary"
    rail = Column(String)            # "arc-x402" | "sim"
    status = Column(String, default="settled")
    tx_hash = Column(String)         # on-chain settlement / batch hash
    auth_sig = Column(String)        # EIP-3009 signature (truncated)
    created_at = Column(DateTime, default=_now)


class Resolution(Base):
    __tablename__ = "resolutions"
    id = Column(String, primary_key=True, default=_id)
    market_id = Column(String, ForeignKey("markets.id"))
    outcome = Column(String)         # YES | NO | VOID
    evidence_url = Column(String)
    rationale = Column(Text)
    verified_by = Column(String, default="ResolutionAgent")
    created_at = Column(DateTime, default=_now)


class AgentReputation(Base):
    __tablename__ = "agent_reputation"
    id = Column(String, primary_key=True, default=_id)
    agent = Column(String, unique=True)
    tasks_completed = Column(Integer, default=0)
    earnings_usdc = Column(Float, default=0.0)
    brier_score = Column(Float)      # forecast calibration (lower is better)
    reliability = Column(Float, default=0.85)

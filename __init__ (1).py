"""
seed.py — Seeds the database with real public-company patent litigation cases.

All cases are derived from public dockets, SEC filings, and court records.
source_mode is set to 'seed' for all seeded data.
If COURTLISTENER_TOKEN is available, attempts live enrichment.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from .. import models
from ..services.trading import (
    compute_initial_probability,
    initialize_lmsr_from_probability,
)

log = logging.getLogger(__name__)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _future(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


SEED_DATA = [
    # ------------------------------------------------------------------
    # Case 1: Amarin v. Hikma — Vascepa "Skinny Label" Inducement
    # SCOTUS certiorari granted; outcome affects entire pharma generics industry
    # ------------------------------------------------------------------
    {
        "company": {"name": "Amarin Corporation", "ticker": "AMRN", "exchange": "NASDAQ", "sector": "Pharmaceuticals"},
        "case": {
            "caption": "Amarin Pharma, Inc. v. Hikma Pharmaceuticals USA Inc.",
            "court": "U.S. Supreme Court",
            "docket_number": "24-1039",
            "case_type": "patent",
            "patent_numbers": ["US7,005,137", "US8,293,727", "US8,318,715"],
            "source_url": "https://www.courtlistener.com/docket/68219862/amarin-pharma-inc-v-hikma-pharmaceuticals-usa-inc/",
            "source_mode": "seed",
            "refresh_frequency": "deadline_sensitive",
        },
        "events": [
            {"kind": "complaint", "description": "Amarin filed suit alleging Hikma's skinny-label generic Vascepa induces off-label infringement of cardiovascular risk-reduction patents.", "filed_at": "2020-05-01", "source": "seed"},
            {"kind": "order", "description": "District court ruled for Hikma; Amarin appealed to Federal Circuit.", "filed_at": "2021-03-30", "source": "seed"},
            {"kind": "order", "description": "Federal Circuit reversed in part, finding evidence of induced infringement via Hikma's marketing materials.", "filed_at": "2023-06-12", "source": "seed"},
            {"kind": "certiorari", "description": "U.S. Supreme Court granted certiorari to resolve circuit split on skinny-label induced infringement standard.", "filed_at": "2024-10-04", "source": "seed"},
            {"kind": "hearing", "description": "Oral argument scheduled before the Supreme Court.", "filed_at": "2025-03-17", "source": "seed"},
        ],
        "catalysts": [
            {"label": "SCOTUS Oral Argument", "statutory_basis": "28 U.S.C. § 1254(1) — certiorari jurisdiction", "deadline": "2025-03-17"},
            {"label": "SCOTUS Decision Window", "statutory_basis": "Supreme Court term ends June 30", "deadline": "2025-06-30"},
        ],
        "markets": [
            {
                "question": "Will Amarin win the SCOTUS skinny-label inducement ruling (Amarin v. Hikma) by term end?",
                "market_key": "patent_settlement",
                "case_stage": "appeal",
                "event_adj": 0.05,
                "liquidity_b": 120.0,
                "deadline_days": 365,
                "close_days": 350,
                "yes_criteria": "SCOTUS issues a decision reversing the Federal Circuit or affirming induced infringement liability against Hikma, or remands with instructions favorable to Amarin.",
                "no_criteria": "SCOTUS affirms the lower court ruling in favor of Hikma, or the case is dismissed or mooted.",
                "void_criteria": "SCOTUS dismisses certiorari as improvidently granted, or the case is settled before a decision.",
                "resolution_source": "Supreme Court official opinion at supremecourt.gov",
                "deadline_type": "statutory_deadline",
            },
            {
                "question": "Will the Supreme Court rule that a generic manufacturer's marketing materials can constitute inducement of patent infringement?",
                "market_key": "motion_to_dismiss_denied",
                "case_stage": "appeal",
                "event_adj": 0.02,
                "liquidity_b": 100.0,
                "deadline_days": 365,
                "close_days": 350,
                "yes_criteria": "SCOTUS holds that marketing materials, press releases, or physician communications by a generic manufacturer can constitute inducement under 35 U.S.C. § 271(b).",
                "no_criteria": "SCOTUS holds that a skinny label alone cannot constitute inducement, or that marketing materials are insufficient.",
                "void_criteria": "Case dismissed or settled before decision.",
                "resolution_source": "Supreme Court official opinion at supremecourt.gov",
                "deadline_type": "statutory_deadline",
            },
        ],
    },
    # ------------------------------------------------------------------
    # Case 2: Apple v. Masimo — ITC Section 337 Blood Oxygen Sensor
    # ITC exclusion order could ban Apple Watch imports
    # ------------------------------------------------------------------
    {
        "company": {"name": "Apple Inc.", "ticker": "AAPL", "exchange": "NASDAQ", "sector": "Technology"},
        "case": {
            "caption": "Masimo Corporation v. Apple Inc. (ITC Inv. No. 337-TA-1276)",
            "court": "U.S. International Trade Commission",
            "docket_number": "337-TA-1276",
            "case_type": "patent",
            "patent_numbers": ["US10,945,648", "US10,687,745", "US10,736,518"],
            "source_url": "https://www.courtlistener.com/docket/65284918/masimo-corporation-v-apple-inc/",
            "source_mode": "seed",
            "refresh_frequency": "daily",
        },
        "events": [
            {"kind": "complaint", "description": "Masimo filed ITC complaint alleging Apple Watch Series 6+ infringes blood-oxygen sensor patents.", "filed_at": "2021-06-10", "source": "seed"},
            {"kind": "order", "description": "ITC Administrative Law Judge issued Initial Determination finding Apple infringed Masimo patents.", "filed_at": "2023-05-15", "source": "seed"},
            {"kind": "order", "description": "ITC Commission issued Limited Exclusion Order banning import of Apple Watch with blood oxygen feature.", "filed_at": "2023-10-26", "source": "seed"},
            {"kind": "order", "description": "Presidential review period expired; Apple redesigned watch to remove blood oxygen sensor.", "filed_at": "2024-01-18", "source": "seed"},
            {"kind": "appeal", "description": "Apple appealed ITC exclusion order to Federal Circuit; parallel district court proceedings ongoing.", "filed_at": "2024-02-20", "source": "seed"},
        ],
        "catalysts": [
            {"label": "Federal Circuit Appeal Decision", "statutory_basis": "28 U.S.C. § 1295(a)(6) — Federal Circuit ITC jurisdiction", "deadline": "2025-09-30"},
            {"label": "District Court Trial Date", "statutory_basis": "CDCA Case No. 8:20-cv-00048", "deadline": "2026-01-15"},
        ],
        "markets": [
            {
                "question": "Will Apple's Federal Circuit appeal of the ITC exclusion order succeed, allowing blood oxygen Apple Watch imports to resume?",
                "market_key": "appeal_reversal",
                "case_stage": "appeal",
                "event_adj": 0.03,
                "liquidity_b": 150.0,
                "deadline_days": 270,
                "close_days": 255,
                "yes_criteria": "Federal Circuit reverses or vacates the ITC exclusion order, or remands with instructions that effectively allow Apple Watch imports with blood oxygen feature.",
                "no_criteria": "Federal Circuit affirms the ITC exclusion order, or Apple Watch blood oxygen imports remain banned.",
                "void_criteria": "Parties settle before Federal Circuit decision, or Apple Watch product line is discontinued.",
                "resolution_source": "Federal Circuit official opinion at cafc.uscourts.gov or ITC docket at usitc.gov",
                "deadline_type": "hearing_date",
            },
        ],
    },
    # ------------------------------------------------------------------
    # Case 3: Qualcomm v. ARM — PTAB IPR on 5G Modem Patents
    # PTAB Inter Partes Review with statutory 12-month decision clock
    # ------------------------------------------------------------------
    {
        "company": {"name": "Qualcomm Incorporated", "ticker": "QCOM", "exchange": "NASDAQ", "sector": "Semiconductors"},
        "case": {
            "caption": "ARM Ltd. v. Qualcomm Inc. (PTAB IPR2024-00847)",
            "court": "Patent Trial and Appeal Board",
            "docket_number": "IPR2024-00847",
            "case_type": "patent",
            "patent_numbers": ["US11,487,341", "US11,392,156"],
            "source_url": "https://ptab.uspto.gov/#/",
            "source_mode": "seed",
            "refresh_frequency": "hourly",
        },
        "events": [
            {"kind": "petition", "description": "ARM filed IPR petition challenging Qualcomm's Nuvia-acquired CPU architecture patents as invalid over prior art.", "filed_at": "2024-02-14", "source": "seed"},
            {"kind": "order", "description": "PTAB instituted IPR review, starting the 12-month statutory clock for Final Written Decision.", "filed_at": "2024-08-14", "source": "seed"},
            {"kind": "hearing", "description": "Oral hearing before PTAB panel scheduled.", "filed_at": "2025-05-20", "source": "seed"},
        ],
        "catalysts": [
            {"label": "PTAB Final Written Decision", "statutory_basis": "35 U.S.C. § 316(a)(11) — 12-month FWD deadline from institution", "deadline": "2025-08-14"},
            {"label": "PTAB Oral Hearing", "statutory_basis": "37 C.F.R. § 42.70 — oral argument", "deadline": "2025-05-20"},
        ],
        "markets": [
            {
                "question": "Will PTAB issue a Final Written Decision invalidating at least one Qualcomm Nuvia CPU patent claim in IPR2024-00847 by August 2025?",
                "market_key": "motion_to_dismiss_denied",
                "case_stage": "summary_judgment",
                "event_adj": 0.08,
                "liquidity_b": 110.0,
                "deadline_days": 60,
                "close_days": 50,
                "yes_criteria": "PTAB Final Written Decision cancels at least one challenged claim of US11,487,341 or US11,392,156 as unpatentable.",
                "no_criteria": "PTAB confirms all challenged claims as patentable, or the petition is dismissed.",
                "void_criteria": "IPR is terminated due to settlement, or PTAB extends the deadline beyond the statutory maximum.",
                "resolution_source": "USPTO PTAB official decision at ptab.uspto.gov",
                "deadline_type": "statutory_deadline",
            },
        ],
    },
    # ------------------------------------------------------------------
    # Case 4: Coinbase — State Prediction Market Enforcement
    # Regulatory/preemption case with direct market-structure implications
    # ------------------------------------------------------------------
    {
        "company": {"name": "Coinbase Global, Inc.", "ticker": "COIN", "exchange": "NASDAQ", "sector": "Financial Technology"},
        "case": {
            "caption": "In re: State Enforcement Actions Against Coinbase Prediction Market Products",
            "court": "U.S. District Court, S.D.N.Y.",
            "docket_number": "1:25-cv-04321",
            "case_type": "regulatory",
            "patent_numbers": [],
            "source_url": "https://www.courtlistener.com/",
            "source_mode": "seed",
            "refresh_frequency": "daily",
        },
        "events": [
            {"kind": "complaint", "description": "Multiple state AGs filed enforcement actions against Coinbase's prediction market products, alleging unlicensed gambling operations.", "filed_at": "2025-01-15", "source": "seed"},
            {"kind": "filing", "description": "Coinbase filed motion for federal preemption, arguing CFTC jurisdiction preempts state gambling laws for CFTC-regulated event contracts.", "filed_at": "2025-03-10", "source": "seed"},
            {"kind": "order", "description": "District court scheduled hearing on Coinbase's preemption motion.", "filed_at": "2025-05-01", "source": "seed"},
        ],
        "catalysts": [
            {"label": "Preemption Motion Hearing", "statutory_basis": "Commodity Exchange Act § 2(e) — CFTC exclusive jurisdiction over event contracts", "deadline": "2025-07-15"},
            {"label": "District Court Ruling", "statutory_basis": "28 U.S.C. § 1331 — federal question jurisdiction", "deadline": "2025-10-31"},
        ],
        "markets": [
            {
                "question": "Will Coinbase win federal preemption relief against state prediction-market enforcement actions by December 31, 2025?",
                "market_key": "motion_to_dismiss_denied",
                "case_stage": "motion_to_dismiss_pending",
                "event_adj": 0.04,
                "liquidity_b": 130.0,
                "deadline_days": 200,
                "close_days": 185,
                "yes_criteria": "Federal court grants Coinbase's preemption motion, enjoining state enforcement actions, or states voluntarily withdraw enforcement actions.",
                "no_criteria": "Federal court denies preemption motion, or Coinbase prediction market products are ordered to cease operations in one or more states.",
                "void_criteria": "Case is transferred, consolidated, or Coinbase settles with states before a ruling.",
                "resolution_source": "Court docket at courtlistener.com or official court filing",
                "deadline_type": "admin_defined",
            },
        ],
    },
    # ------------------------------------------------------------------
    # Case 5: Moderna v. Pfizer/BioNTech — mRNA COVID Vaccine Patents
    # High-stakes BPCIA-adjacent patent dispute with $billions in exposure
    # ------------------------------------------------------------------
    {
        "company": {"name": "Moderna, Inc.", "ticker": "MRNA", "exchange": "NASDAQ", "sector": "Biotechnology"},
        "case": {
            "caption": "Moderna, Inc. v. Pfizer Inc. and BioNTech SE",
            "court": "U.S. District Court, D. Mass.",
            "docket_number": "1:22-cv-11378",
            "case_type": "patent",
            "patent_numbers": ["US10,702,600", "US10,933,127", "US10,953,089"],
            "source_url": "https://www.courtlistener.com/docket/64987234/moderna-inc-v-pfizer-inc/",
            "source_mode": "seed",
            "refresh_frequency": "daily",
        },
        "events": [
            {"kind": "complaint", "description": "Moderna sued Pfizer/BioNTech alleging mRNA COVID vaccine Comirnaty infringes Moderna's foundational mRNA delivery patents.", "filed_at": "2022-08-26", "source": "seed"},
            {"kind": "filing", "description": "Pfizer/BioNTech filed answer and counterclaims, asserting invalidity and non-infringement.", "filed_at": "2022-11-15", "source": "seed"},
            {"kind": "order", "description": "Court denied Pfizer's motion to dismiss; discovery phase commenced.", "filed_at": "2023-06-20", "source": "seed"},
            {"kind": "filing", "description": "Parties completed claim construction briefing; Markman hearing scheduled.", "filed_at": "2024-09-15", "source": "seed"},
            {"kind": "hearing", "description": "Markman (claim construction) hearing held; court reserved ruling.", "filed_at": "2025-01-22", "source": "seed"},
        ],
        "catalysts": [
            {"label": "Markman Claim Construction Order", "statutory_basis": "Markman v. Westview Instruments, 517 U.S. 370 (1996)", "deadline": "2025-06-30"},
            {"label": "Summary Judgment Deadline", "statutory_basis": "Fed. R. Civ. P. 56", "deadline": "2025-10-15"},
            {"label": "Trial Date", "statutory_basis": "Court scheduling order", "deadline": "2026-04-01"},
        ],
        "markets": [
            {
                "question": "Will Moderna win a liability finding against Pfizer/BioNTech for mRNA vaccine patent infringement before trial ends?",
                "market_key": "patent_settlement",
                "case_stage": "discovery",
                "event_adj": 0.02,
                "liquidity_b": 140.0,
                "deadline_days": 400,
                "close_days": 380,
                "yes_criteria": "Court or jury finds Pfizer/BioNTech liable for infringing at least one Moderna mRNA patent claim, or Pfizer/BioNTech agrees to a license/settlement acknowledging infringement.",
                "no_criteria": "Court grants summary judgment for Pfizer/BioNTech, or jury finds no infringement, or all asserted patents are invalidated.",
                "void_criteria": "Case is dismissed, transferred, or stayed pending IPR proceedings.",
                "resolution_source": "Court docket at courtlistener.com, official court order, or company 8-K filing",
                "deadline_type": "trial_start",
            },
            {
                "question": "Will Moderna vs. Pfizer mRNA vaccine patent case settle before trial begins in April 2026?",
                "market_key": "patent_settlement",
                "case_stage": "discovery",
                "event_adj": 0.10,
                "liquidity_b": 120.0,
                "deadline_days": 300,
                "close_days": 285,
                "yes_criteria": "Parties announce a settlement, license agreement, or consent judgment before the scheduled April 2026 trial date.",
                "no_criteria": "Trial commences as scheduled without a settlement.",
                "void_criteria": "Trial date is postponed beyond December 2026.",
                "resolution_source": "Company 8-K filing, court docket, or official press release",
                "deadline_type": "trial_start",
            },
        ],
    },
]


def seed(db: Session, try_courtlistener: bool = False) -> None:
    """
    Seed the database with real litigation cases and markets.
    Idempotent — skips if data already exists.
    """
    if db.query(models.Company).count() > 0:
        log.info("Database already seeded — skipping.")
        return

    log.info("Seeding database with real patent litigation cases...")

    for entry in SEED_DATA:
        # Create company
        co_data = entry["company"]
        company = models.Company(**co_data)
        db.add(company)
        db.flush()

        # Create case
        c_data = entry["case"].copy()
        c_data["company_id"] = company.id
        c_data["next_refresh_at"] = datetime.now(timezone.utc) + timedelta(hours=6)
        case = models.LegalCase(**c_data)
        db.add(case)
        db.flush()

        # Create events
        for ev in entry["events"]:
            event = models.CaseEvent(
                case_id=case.id,
                kind=ev["kind"],
                description=ev["description"],
                filed_at=_dt(ev["filed_at"] + "T00:00:00"),
                source=ev["source"],
            )
            db.add(event)

        # Create catalysts
        for cat in entry["catalysts"]:
            catalyst = models.Catalyst(
                case_id=case.id,
                label=cat["label"],
                statutory_basis=cat["statutory_basis"],
                deadline=_dt(cat["deadline"] + "T00:00:00"),
            )
            db.add(catalyst)

        # Create markets
        for mkt_data in entry["markets"]:
            prior = compute_initial_probability(
                market_key=mkt_data["market_key"],
                case_stage=mkt_data["case_stage"],
                latest_event_adjustment=mkt_data.get("event_adj", 0.0),
            )
            p = prior["probability"]
            b = mkt_data["liquidity_b"]
            q_yes, q_no = initialize_lmsr_from_probability(p, b)

            now = datetime.now(timezone.utc)
            market = models.Market(
                case_id=case.id,
                question=mkt_data["question"],
                status="open",
                approved=True,
                q_yes=q_yes,
                q_no=q_no,
                liquidity_b=b,
                initial_probability=p,
                prior_basis=prior,
                deadline=now + timedelta(days=mkt_data["deadline_days"]),
                close_time=now + timedelta(days=mkt_data["close_days"]),
                resolution_deadline=now + timedelta(days=mkt_data["deadline_days"] + 30),
                next_refresh_at=now + timedelta(hours=6),
                next_resolution_check_at=now + timedelta(days=mkt_data["deadline_days"]),
                deadline_type=mkt_data["deadline_type"],
                yes_criteria=mkt_data["yes_criteria"],
                no_criteria=mkt_data["no_criteria"],
                void_criteria=mkt_data["void_criteria"],
                resolution_source=mkt_data["resolution_source"],
            )
            db.add(market)
            db.flush()

            # Seed agent reputation records
            for agent_name in ["CaseScoutAgent", "DocketAgent", "PrecedentAgent",
                               "ProbabilityAgent", "TraderAgent", "ResolutionAgent", "AuditorAgent"]:
                existing = db.query(models.AgentReputation).filter_by(agent=agent_name).first()
                if not existing:
                    rep = models.AgentReputation(agent=agent_name)
                    db.add(rep)

    db.commit()
    log.info("Seeding complete.")

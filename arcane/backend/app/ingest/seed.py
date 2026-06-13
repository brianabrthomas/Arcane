"""
Curated seed dataset of real public-company patent litigation, with statutory
catalysts and admin-approved binary markets. Used directly in the demo and as a
fallback when CourtListener is unavailable.

Cases are drawn from the whitepaper's MVP niche (pharma/biologics patent fights),
where deterministic statutory clocks (Hatch-Waxman 30-month stay, BPCIA patent
dance, PTAB IPR clocks, ITC target dates) give markets natural expiry dates.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

import math

from . import courtlistener
from .. import models
from ..agents.roster import PrecedentAgent


def _d(days: int) -> dt.datetime:
    return dt.datetime.utcnow() + dt.timedelta(days=days)


SEED = [
    {
        "company": {"name": "Amarin Corporation", "ticker": "AMRN",
                    "exchange": "NASDAQ", "sector": "Pharmaceuticals"},
        "case": {
            "caption": "Amarin Corp. v. Hikma Pharmaceuticals",
            "court": "Fed. Cir. / D. Del.", "docket_number": "1:20-cv-01630",
            "case_type": "patent",
            "patent_numbers": ["US8,318,715", "US10,568,861"],
            "summary": "Skinny-label inducement dispute over Vascepa (icosapent ethyl). "
                       "Hikma carved out the patented cardiovascular indication; Amarin "
                       "alleges marketing induced off-label CV prescribing.",
        },
        "events": [
            ("opinion", "Federal Circuit reverses dismissal; induced-infringement theory revived", -120),
            ("order", "Supreme Court grants certiorari on skinny-label inducement question", -30),
            ("hearing_set", "Oral argument scheduled before the Supreme Court", 45),
        ],
        "catalysts": [
            ("Supreme Court decision on skinny-label inducement", 160,
             "Cert granted; merits decision expected end of term"),
        ],
        "markets": [
            ("Will the Supreme Court rule for Amarin on skinny-label inducement by end of term?",
             "ipr_invalidation",
             "SCOTUS issues an opinion reversing/vacating in Amarin's favor before term ends.",
             "Trial term ends without a pro-Amarin ruling, or the Court rules for Hikma.",
             "Case is DIG'd (dismissed as improvidently granted) or settled before decision.",
             "Supreme Court opinion (supremecourt.gov) + parties' 8-K filings", 160, 130.0),
            ("Will Amarin and Hikma settle Vascepa litigation before the SCOTUS decision?",
             "settle_before_trial",
             "A joint stipulation, 8-K, or press release confirms settlement before the opinion.",
             "The Court issues its opinion with no settlement on record.",
             "Case is consolidated or restructured such that the question is moot.",
             "Docket stipulation + SEC 8-K", 150, 110.0),
        ],
    },
    {
        "company": {"name": "Amgen Inc.", "ticker": "AMGN",
                    "exchange": "NASDAQ", "sector": "Biotechnology"},
        "case": {
            "caption": "Amgen Inc. v. Samsung Bioepis (biosimilar)",
            "court": "D.N.J.", "docket_number": "2:24-cv-00891",
            "case_type": "biosimilar",
            "patent_numbers": ["US9,856,287", "US10,808,037"],
            "summary": "BPCIA 'patent dance' over a proposed biosimilar. 180-day "
                       "commercial-marketing notice triggers a preliminary-injunction sprint.",
        },
        "events": [
            ("motion_filed", "Biosimilar applicant files aBLA; manufacturing disclosure exchanged", -90),
            ("order", "Brand sponsor serves Paragraph (l)(3)(A) patent list", -40),
            ("motion_filed", "180-day notice of commercial marketing served", -10),
        ],
        "catalysts": [
            ("Preliminary-injunction ruling within 180-day notice window", 170,
             "BPCIA 42 U.S.C. 262(l)(8)(A) — 180-day pre-launch notice clock"),
        ],
        "markets": [
            ("Will the court grant Amgen a preliminary injunction before the 180-day notice expires?",
             "injunction_granted",
             "Court enters an order granting a preliminary injunction before the notice window closes.",
             "Notice window closes with no preliminary injunction granted.",
             "Parties settle or the biosimilar launch is withdrawn.",
             "PACER docket order + Amgen 8-K", 170, 100.0),
        ],
    },
    {
        "company": {"name": "Apple Inc.", "ticker": "AAPL",
                    "exchange": "NASDAQ", "sector": "Technology"},
        "case": {
            "caption": "Masimo Corp. v. Apple Inc. (ITC Section 337)",
            "court": "U.S. ITC", "docket_number": "Inv. No. 337-TA-1276",
            "case_type": "itc",
            "patent_numbers": ["US10,945,648", "US10,687,745"],
            "summary": "Section 337 investigation over pulse-oximetry patents; potential "
                       "exclusion order barring import of accused Apple Watch models.",
        },
        "events": [
            ("order", "ITC sets binding Target Date for investigation completion", -200),
            ("opinion", "ALJ issues Initial Determination finding a Section 337 violation", -60),
            ("order", "Commission review of remedy and exclusion order pending", 20),
        ],
        "catalysts": [
            ("Commission final determination on exclusion order", 60,
             "ITC Target Date (19 U.S.C. 1337) — statutory completion clock"),
        ],
        "markets": [
            ("Will the ITC issue an exclusion order against Apple in Inv. No. 337-TA-1276?",
             "itc_exclusion",
             "The Commission issues a limited or general exclusion order on the accused products.",
             "The Commission finds no violation or declines to issue an exclusion order.",
             "Investigation is terminated by settlement or consent order.",
             "ITC EDIS final determination + Apple 10-Q risk disclosure", 60, 120.0),
        ],
    },
]


def seed(db: Session, try_courtlistener: bool = True) -> dict:
    if db.query(models.Company).count() > 0:
        return {"status": "already_seeded"}

    created = {"companies": 0, "cases": 0, "markets": 0, "events": 0, "catalysts": 0}

    for entry in SEED:
        comp = models.Company(**entry["company"])
        db.add(comp); db.flush(); created["companies"] += 1

        cdata = dict(entry["case"])
        # try to enrich with a live CourtListener match (best-effort)
        if try_courtlistener:
            hits = courtlistener.search_dockets(cdata["caption"], limit=1)
            if hits and hits[0].get("cl_docket_id"):
                cdata["cl_docket_id"] = hits[0]["cl_docket_id"]
                cdata["source_url"] = hits[0]["source_url"]

        case = models.LegalCase(company_id=comp.id, **cdata)
        db.add(case); db.flush(); created["cases"] += 1

        for kind, desc, day in entry["events"]:
            db.add(models.CaseEvent(case_id=case.id, kind=kind, description=desc,
                                    filed_at=_d(day), source="seed"))
            created["events"] += 1

        for label, day, basis in entry["catalysts"]:
            db.add(models.Catalyst(case_id=case.id, label=label, deadline=_d(day),
                                   statutory_basis=basis))
            created["catalysts"] += 1

        for q, _key, yes, no, void, src, day, b in entry["markets"]:
            prior = PrecedentAgent.BASE_RATES.get(_key, 0.5)
            prior = min(0.95, max(0.05, prior))
            q_yes0 = b * math.log(prior / (1 - prior))   # logit -> opening price = prior
            db.add(models.Market(
                case_id=case.id, question=q, market_key=_key,
                yes_criteria=yes, no_criteria=no,
                void_criteria=void, resolution_source=src, deadline=_d(day),
                status="open", approved=True, liquidity_b=b,
                q_yes=q_yes0, q_no=0.0,
            ))
            created["markets"] += 1

    db.commit()
    return {"status": "seeded", **created}

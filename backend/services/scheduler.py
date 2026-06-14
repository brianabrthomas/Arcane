"""
scheduler.py — APScheduler background tasks for market refresh, resolution, and case ingestion.

Refresh schedule:
- Open markets: every 6 hours
- Markets within 7 days of deadline: every 1 hour
- Markets within 24 hours of deadline: every 10 minutes
- Resolved/closed markets: stop polling
- New case discovery: daily
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
    return _scheduler


def start_scheduler():
    """Start all background jobs."""
    sched = get_scheduler()

    # Main market refresh job — runs every 30 minutes, checks individual market timers
    sched.add_job(
        _refresh_markets_job,
        trigger=IntervalTrigger(minutes=30),
        id="refresh_markets",
        replace_existing=True,
        max_instances=1,
    )

    # Resolution check job — runs every hour
    sched.add_job(
        _resolution_check_job,
        trigger=IntervalTrigger(hours=1),
        id="resolution_check",
        replace_existing=True,
        max_instances=1,
    )

    # Case discovery job — runs daily
    sched.add_job(
        _case_discovery_job,
        trigger=IntervalTrigger(hours=24),
        id="case_discovery",
        replace_existing=True,
        max_instances=1,
    )

    if not sched.running:
        sched.start()
        log.info("APScheduler started.")


def stop_scheduler():
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
        log.info("APScheduler stopped.")


def _refresh_markets_job():
    """Refresh agent research for markets that are due for a refresh."""
    try:
        from ..db import SessionLocal
        from .. import models
        from ..agents.orchestrator import run_pipeline

        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            markets = (
                db.query(models.Market)
                .filter(models.Market.status == "open")
                .filter(models.Market.next_refresh_at <= now)
                .all()
            )

            for market in markets:
                try:
                    log.info(f"Scheduled refresh: market {market.id[:8]}")
                    run_pipeline(db, market, trigger="scheduler")

                    # Update next refresh time based on proximity to deadline
                    if market.deadline:
                        days_to_deadline = (market.deadline - now).days
                        if days_to_deadline <= 1:
                            interval = timedelta(minutes=10)
                        elif days_to_deadline <= 7:
                            interval = timedelta(hours=1)
                        else:
                            interval = timedelta(hours=6)
                    else:
                        interval = timedelta(hours=6)

                    market.next_refresh_at = now + interval
                    db.commit()

                except Exception as e:
                    log.error(f"Refresh failed for market {market.id}: {e}")

        finally:
            db.close()
    except Exception as e:
        log.error(f"Refresh markets job failed: {e}")


def _resolution_check_job():
    """Check if any markets are due for resolution."""
    try:
        from ..db import SessionLocal
        from .. import models
        from ..agents.orchestrator import run_resolution_check

        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            markets = (
                db.query(models.Market)
                .filter(models.Market.status == "open")
                .filter(models.Market.next_resolution_check_at <= now)
                .all()
            )

            for market in markets:
                try:
                    log.info(f"Resolution check: market {market.id[:8]}")
                    result = run_resolution_check(db, market)

                    # Auto-close if past close_time
                    if market.close_time and now >= market.close_time:
                        market.status = "closed"
                        log.info(f"Market {market.id[:8]} auto-closed.")

                    db.commit()
                except Exception as e:
                    log.error(f"Resolution check failed for market {market.id}: {e}")

        finally:
            db.close()
    except Exception as e:
        log.error(f"Resolution check job failed: {e}")


def _case_discovery_job():
    """Discover new cases from CourtListener."""
    try:
        from ..db import SessionLocal
        from ..ingest.courtlistener import CourtListenerClient
        from .. import models

        db = SessionLocal()
        try:
            client = CourtListenerClient()
            if not client.token:
                log.debug("CourtListener token not configured — skipping case discovery.")
                return

            # Refresh existing cases
            cases = db.query(models.LegalCase).filter(
                models.LegalCase.source_mode.in_(["seed", "live_api"])
            ).all()

            for case in cases:
                from ..ingest.courtlistener import enrich_case_from_courtlistener
                new_events = enrich_case_from_courtlistener(case, db)
                for ev_data in new_events:
                    event = models.CaseEvent(**ev_data)
                    db.add(event)

                case.last_checked_at = datetime.now(timezone.utc)
                case.source_mode = "live_api"

            db.commit()
            log.info(f"Case discovery complete. Checked {len(cases)} cases.")

        finally:
            db.close()
    except Exception as e:
        log.error(f"Case discovery job failed: {e}")

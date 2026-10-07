"""
The priority log (priority_changes table): every patient-priority
setting, written in the background so the simulation never waits for
the database. A database problem is logged and otherwise ignored; the
dashboard also keeps the entries of the current run in memory.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from backend.database import SessionLocal
from backend.models.priority_change import PriorityChange

logger = logging.getLogger(__name__)

_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="priority-db")


def record(entry):
    """Save one entry: {"trip_id", "ambulance_id", "ambulance_label",
    "condition", "level", "previous_condition", "source", "changed_by",
    "simulation_time"}."""
    _writer.submit(_save, dict(entry))


def _save(entry):
    db = SessionLocal()
    try:
        db.add(PriorityChange(
            **{
                key: entry.get(key)
                for key in (
                    "trip_id", "ambulance_id", "ambulance_label", "condition",
                    "level", "previous_condition", "source", "changed_by",
                    "simulation_time",
                )
            },
            created_at=datetime.now(),
        ))
        db.commit()
    except Exception as error:
        db.rollback()
        logger.warning("Could not save priority change: %s", error)
    finally:
        db.close()


def list_changes(db, limit=100):
    return (
        db.query(PriorityChange)
        .order_by(PriorityChange.created_at.desc())
        .limit(limit)
        .all()
    )

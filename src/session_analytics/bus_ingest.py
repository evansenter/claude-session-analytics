"""Event-bus ingestion for cross-session insights.

Reads events from ~/.claude/contrib/event-bus/data.db and stores them
in session-analytics for queryable cross-session insights.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from session_analytics.storage import SQLiteStorage

logger = logging.getLogger("session-analytics")

EVENT_BUS_DB = Path.home() / ".claude" / "contrib" / "event-bus" / "data.db"


def _extract_repo(channel: str | None) -> str | None:
    """Extract repo name from channel (e.g., 'repo:dotfiles' -> 'dotfiles')."""
    if channel and channel.startswith("repo:"):
        return channel[5:]
    return None


def ingest_bus_events(storage: SQLiteStorage, days: int = 7) -> dict:
    """Ingest events from event-bus database.

    Performs incremental ingestion by tracking the last ingested event ID.
    Events are read from the event-bus database in read-only mode.

    Args:
        storage: Session analytics storage instance
        days: Number of days to look back for initial ingestion

    Returns:
        Dict with ingestion stats including events_ingested count
    """
    if not EVENT_BUS_DB.exists():
        return {
            "status": "skipped",
            "reason": "event-bus database not found",
            "path": str(EVENT_BUS_DB),
        }

    # Get last ingested event_id for incremental updates
    last_event = storage.execute_query("SELECT MAX(event_id) as last_id FROM bus_events")
    last_id = last_event[0]["last_id"] if last_event and last_event[0]["last_id"] else 0

    # Calculate cutoff for first-run ingestion
    cutoff = datetime.now() - timedelta(days=days)

    # Read from event-bus DB (read-only mode)
    try:
        conn = sqlite3.connect(f"file:{EVENT_BUS_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        return {
            "status": "error",
            "reason": f"Failed to connect to event-bus database: {e}",
            "path": str(EVENT_BUS_DB),
        }

    try:
        # Query events newer than last ingested ID, or from cutoff on first run
        if last_id > 0:
            # Incremental: get events after last ID
            rows = conn.execute(
                """
                SELECT id, event_type, channel, session_id, timestamp, payload
                FROM events
                WHERE id > ?
                ORDER BY id
                """,
                (last_id,),
            ).fetchall()
        else:
            # First run: get events from cutoff
            rows = conn.execute(
                """
                SELECT id, event_type, channel, session_id, timestamp, payload
                FROM events
                WHERE timestamp >= ?
                ORDER BY id
                """,
                (cutoff.isoformat(),),
            ).fetchall()

        if not rows:
            return {
                "status": "ok",
                "events_ingested": 0,
                "last_event_id": last_id,
            }

        # Batch insert into analytics database
        events_data = [
            (
                row["id"],
                row["timestamp"],
                row["event_type"],
                row["channel"],
                row["session_id"],
                _extract_repo(row["channel"]),
                row["payload"],
            )
            for row in rows
        ]

        with storage._connect() as db_conn:
            db_conn.executemany(
                """
                INSERT OR IGNORE INTO bus_events
                (event_id, timestamp, event_type, channel, session_id, repo, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                events_data,
            )

        newest_id = rows[-1]["id"]
        oldest_ts = rows[0]["timestamp"]
        newest_ts = rows[-1]["timestamp"]

        logger.info(
            "Ingested %d event-bus events (IDs %d-%d)",
            len(rows),
            rows[0]["id"],
            newest_id,
        )

        return {
            "status": "ok",
            "events_ingested": len(rows),
            "last_event_id": newest_id,
            "oldest_event": oldest_ts,
            "newest_event": newest_ts,
        }

    finally:
        conn.close()

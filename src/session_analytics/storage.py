"""SQLite storage backend for session analytics."""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("session-analytics")

# Register datetime adapters/converters (required for Python 3.12+)


def _adapt_datetime(dt: datetime) -> str:
    """Convert datetime to ISO format string for SQLite storage."""
    return dt.isoformat()


def _convert_datetime(data: bytes) -> datetime:
    """Convert ISO format string from SQLite to datetime."""
    return datetime.fromisoformat(data.decode())


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)


@dataclass
class Event:
    """A parsed event from a Claude Code session log."""

    id: int | None
    uuid: str
    timestamp: datetime
    session_id: str
    project_path: str | None = None
    entry_type: str | None = None  # 'user', 'assistant', 'summary'

    # Tool-specific (null if not a tool call)
    tool_name: str | None = None
    tool_input_json: str | None = None
    tool_id: str | None = None
    is_error: bool = False

    # Denormalized for common filters
    command: str | None = None  # Bash: first word
    command_args: str | None = None  # Bash: remaining args
    file_path: str | None = None  # Read/Edit/Write target
    skill_name: str | None = None  # Skill invocation

    # Token tracking
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    model: str | None = None

    # Context
    git_branch: str | None = None
    cwd: str | None = None


@dataclass
class Session:
    """Metadata about a Claude Code session."""

    id: str
    project_path: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    entry_count: int = 0
    tool_use_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    primary_branch: str | None = None
    slug: str | None = None


@dataclass
class IngestionState:
    """Tracks the ingestion state of a JSONL file."""

    file_path: str
    file_size: int
    last_modified: datetime
    entries_processed: int
    last_processed: datetime


@dataclass
class Pattern:
    """A pre-computed pattern for fast querying."""

    id: int | None
    pattern_type: str  # 'tool_frequency', 'sequence', 'permission_gap', etc.
    pattern_key: str  # e.g., "Bash" or "Read → Edit"
    count: int = 0
    last_seen: datetime | None = None
    metadata: dict = field(default_factory=dict)
    computed_at: datetime | None = None


# Default database path
DEFAULT_DB_PATH = Path.home() / ".claude" / "contrib" / "analytics" / "data.db"

# Schema version for migrations
SCHEMA_VERSION = 1


class SQLiteStorage:
    """SQLite-backed storage for session analytics."""

    def __init__(self, db_path: str | Path | None = None):
        """Initialize storage with optional custom DB path."""
        if db_path is None:
            db_path = os.environ.get("SESSION_ANALYTICS_DB", str(DEFAULT_DB_PATH))

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    @contextmanager
    def _connect(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._connect() as conn:
            # Schema version tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)

            # Core events table (denormalized for fast queries)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    uuid TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    session_id TEXT NOT NULL,
                    project_path TEXT,
                    entry_type TEXT,

                    -- Tool-specific
                    tool_name TEXT,
                    tool_input_json TEXT,
                    tool_id TEXT,
                    is_error INTEGER DEFAULT 0,

                    -- Denormalized for common filters
                    command TEXT,
                    command_args TEXT,
                    file_path TEXT,
                    skill_name TEXT,

                    -- Token tracking
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_read_tokens INTEGER,
                    cache_creation_tokens INTEGER,
                    model TEXT,

                    -- Context
                    git_branch TEXT,
                    cwd TEXT,

                    UNIQUE(session_id, uuid)
                )
            """)

            # Indexes for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_tool ON events(tool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_path)")

            # Sessions metadata
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_path TEXT,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    entry_count INTEGER DEFAULT 0,
                    tool_use_count INTEGER DEFAULT 0,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0,
                    primary_branch TEXT,
                    slug TEXT
                )
            """)

            # Ingestion tracking (incremental updates)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_state (
                    file_path TEXT PRIMARY KEY,
                    file_size INTEGER,
                    last_modified TIMESTAMP,
                    entries_processed INTEGER,
                    last_processed TIMESTAMP
                )
            """)

            # Pre-computed patterns
            conn.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY,
                    pattern_type TEXT NOT NULL,
                    pattern_key TEXT NOT NULL,
                    count INTEGER DEFAULT 0,
                    last_seen TIMESTAMP,
                    metadata_json TEXT,
                    computed_at TIMESTAMP,
                    UNIQUE(pattern_type, pattern_key)
                )
            """)

            # Set schema version
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )

    # Event operations

    def add_event(self, event: Event) -> Event:
        """Add a new event and return it with assigned ID."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    uuid, timestamp, session_id, project_path, entry_type,
                    tool_name, tool_input_json, tool_id, is_error,
                    command, command_args, file_path, skill_name,
                    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, model,
                    git_branch, cwd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.uuid,
                    event.timestamp,
                    event.session_id,
                    event.project_path,
                    event.entry_type,
                    event.tool_name,
                    event.tool_input_json,
                    event.tool_id,
                    1 if event.is_error else 0,
                    event.command,
                    event.command_args,
                    event.file_path,
                    event.skill_name,
                    event.input_tokens,
                    event.output_tokens,
                    event.cache_read_tokens,
                    event.cache_creation_tokens,
                    event.model,
                    event.git_branch,
                    event.cwd,
                ),
            )
            event.id = cursor.lastrowid
            return event

    def add_events_batch(self, events: list[Event]) -> int:
        """Add multiple events in a single transaction. Returns count added."""
        with self._connect() as conn:
            cursor = conn.executemany(
                """
                INSERT OR IGNORE INTO events (
                    uuid, timestamp, session_id, project_path, entry_type,
                    tool_name, tool_input_json, tool_id, is_error,
                    command, command_args, file_path, skill_name,
                    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, model,
                    git_branch, cwd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e.uuid,
                        e.timestamp,
                        e.session_id,
                        e.project_path,
                        e.entry_type,
                        e.tool_name,
                        e.tool_input_json,
                        e.tool_id,
                        1 if e.is_error else 0,
                        e.command,
                        e.command_args,
                        e.file_path,
                        e.skill_name,
                        e.input_tokens,
                        e.output_tokens,
                        e.cache_read_tokens,
                        e.cache_creation_tokens,
                        e.model,
                        e.git_branch,
                        e.cwd,
                    )
                    for e in events
                ],
            )
            return cursor.rowcount

    def get_event_count(self) -> int:
        """Get total number of events."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM events").fetchone()
            return row["count"]

    def get_events_in_range(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        tool_name: str | None = None,
        project_path: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Get events within a time range with optional filters."""
        with self._connect() as conn:
            conditions = []
            params: list = []

            if start:
                conditions.append("timestamp >= ?")
                params.append(start)
            if end:
                conditions.append("timestamp <= ?")
                params.append(end)
            if tool_name:
                conditions.append("tool_name = ?")
                params.append(tool_name)
            if project_path:
                conditions.append("project_path = ?")
                params.append(project_path)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT * FROM events
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

            return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        """Convert a database row to an Event object."""
        return Event(
            id=row["id"],
            uuid=row["uuid"],
            timestamp=row["timestamp"],
            session_id=row["session_id"],
            project_path=row["project_path"],
            entry_type=row["entry_type"],
            tool_name=row["tool_name"],
            tool_input_json=row["tool_input_json"],
            tool_id=row["tool_id"],
            is_error=bool(row["is_error"]),
            command=row["command"],
            command_args=row["command_args"],
            file_path=row["file_path"],
            skill_name=row["skill_name"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cache_read_tokens=row["cache_read_tokens"],
            cache_creation_tokens=row["cache_creation_tokens"],
            model=row["model"],
            git_branch=row["git_branch"],
            cwd=row["cwd"],
        )

    # Session operations

    def upsert_session(self, session: Session) -> None:
        """Add or update a session."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    id, project_path, first_seen, last_seen,
                    entry_count, tool_use_count,
                    total_input_tokens, total_output_tokens,
                    primary_branch, slug
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.project_path,
                    session.first_seen,
                    session.last_seen,
                    session.entry_count,
                    session.tool_use_count,
                    session.total_input_tokens,
                    session.total_output_tokens,
                    session.primary_branch,
                    session.slug,
                ),
            )

    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                return self._row_to_session(row)
            return None

    def get_session_count(self) -> int:
        """Get total number of sessions."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM sessions").fetchone()
            return row["count"]

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert a database row to a Session object."""
        return Session(
            id=row["id"],
            project_path=row["project_path"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            entry_count=row["entry_count"],
            tool_use_count=row["tool_use_count"],
            total_input_tokens=row["total_input_tokens"],
            total_output_tokens=row["total_output_tokens"],
            primary_branch=row["primary_branch"],
            slug=row["slug"],
        )

    # Ingestion state operations

    def get_ingestion_state(self, file_path: str) -> IngestionState | None:
        """Get ingestion state for a file."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ingestion_state WHERE file_path = ?", (file_path,)
            ).fetchone()
            if row:
                return IngestionState(
                    file_path=row["file_path"],
                    file_size=row["file_size"],
                    last_modified=row["last_modified"],
                    entries_processed=row["entries_processed"],
                    last_processed=row["last_processed"],
                )
            return None

    def update_ingestion_state(self, state: IngestionState) -> None:
        """Update ingestion state for a file."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ingestion_state (
                    file_path, file_size, last_modified, entries_processed, last_processed
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    state.file_path,
                    state.file_size,
                    state.last_modified,
                    state.entries_processed,
                    state.last_processed,
                ),
            )

    def get_last_ingestion_time(self) -> datetime | None:
        """Get the most recent ingestion time across all files."""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(last_processed) as last FROM ingestion_state").fetchone()
            if not row or not row["last"]:
                return None
            # Handle both datetime objects and ISO strings (SQLite aggregates return strings)
            val = row["last"]
            return datetime.fromisoformat(val) if isinstance(val, str) else val

    # Pattern operations

    def upsert_pattern(self, pattern: Pattern) -> None:
        """Add or update a pattern."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO patterns (
                    pattern_type, pattern_key, count, last_seen, metadata_json, computed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern.pattern_type,
                    pattern.pattern_key,
                    pattern.count,
                    pattern.last_seen,
                    json.dumps(pattern.metadata) if pattern.metadata else None,
                    pattern.computed_at,
                ),
            )

    def get_patterns(self, pattern_type: str | None = None) -> list[Pattern]:
        """Get patterns, optionally filtered by type."""
        with self._connect() as conn:
            if pattern_type:
                rows = conn.execute(
                    "SELECT * FROM patterns WHERE pattern_type = ? ORDER BY count DESC",
                    (pattern_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM patterns ORDER BY pattern_type, count DESC"
                ).fetchall()

            return [
                Pattern(
                    id=row["id"],
                    pattern_type=row["pattern_type"],
                    pattern_key=row["pattern_key"],
                    count=row["count"],
                    last_seen=row["last_seen"],
                    metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    computed_at=row["computed_at"],
                )
                for row in rows
            ]

    def clear_patterns(self, pattern_type: str | None = None) -> int:
        """Clear patterns, optionally filtered by type. Returns count deleted."""
        with self._connect() as conn:
            if pattern_type:
                cursor = conn.execute(
                    "DELETE FROM patterns WHERE pattern_type = ?", (pattern_type,)
                )
            else:
                cursor = conn.execute("DELETE FROM patterns")
            return cursor.rowcount

    # Utility operations

    def get_db_stats(self) -> dict:
        """Get database statistics."""
        with self._connect() as conn:
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            pattern_count = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            file_count = conn.execute("SELECT COUNT(*) FROM ingestion_state").fetchone()[0]

            # Get date range
            date_range = conn.execute(
                "SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM events"
            ).fetchone()

            # Get DB file size
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

            # Helper to convert datetime or string to ISO string
            def to_iso(val):
                if val is None:
                    return None
                return val if isinstance(val, str) else val.isoformat()

            return {
                "event_count": event_count,
                "session_count": session_count,
                "pattern_count": pattern_count,
                "files_processed": file_count,
                "earliest_event": to_iso(date_range["min_ts"]),
                "latest_event": to_iso(date_range["max_ts"]),
                "db_size_bytes": db_size,
                "db_path": str(self.db_path),
            }

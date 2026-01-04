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

    # RFC #17 Phase 1 additions
    user_message_text: str | None = None  # For user journey tracking
    # TODO(Phase 4): exit_code is not currently available in Claude Code JSONL format.
    # The toolUseResult has stdout/stderr/interrupted but no exit code.
    # This field is reserved for future extraction when format changes or
    # we implement heuristic detection (e.g., stderr patterns, "Exit code: N" in output).
    exit_code: int | None = None  # For failure detection (Bash commands)

    # RFC #41: Agent tracking and token deduplication
    parent_uuid: str | None = None  # Links tool_use events to their assistant event
    agent_id: str | None = None  # Agent ID from agent-*.jsonl files (Task subagents)
    is_sidechain: bool = False  # True for agent/background work
    version: str | None = None  # Claude Code version from entry


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

    # RFC #26: Session enrichment fields (observable data only, no interpretation)
    context_switch_count: int = 0  # Number of mid-session topic changes


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


@dataclass(frozen=True)
class GitCommit:
    """A git commit for correlation with session activity.

    Immutable dataclass representing a git commit. The SHA is validated
    on construction to ensure it's a valid hexadecimal string.
    """

    sha: str
    timestamp: datetime | None = None
    message: str | None = None
    session_id: str | None = None  # Inferred from timestamp proximity
    project_path: str | None = None

    def __post_init__(self):
        """Validate SHA format on construction."""
        if not self.sha:
            raise ValueError("SHA cannot be empty")
        if not (7 <= len(self.sha) <= 40):
            raise ValueError(f"SHA must be 7-40 characters, got {len(self.sha)}")
        if not all(c in "0123456789abcdefABCDEF" for c in self.sha):
            raise ValueError(f"SHA must be hexadecimal, got '{self.sha}'")


# Default database path
DEFAULT_DB_PATH = Path.home() / ".claude" / "contrib" / "analytics" / "data.db"

# Schema version for migrations
SCHEMA_VERSION = 5

# Migration functions: dict of version -> (migration_name, migration_func)
# Each migration upgrades FROM version-1 TO version
# e.g., MIGRATIONS[2] upgrades from version 1 to version 2
#
# NOTE: Schema elements (tables, indexes, triggers) are defined in BOTH migrations
# AND _init_db(). This is intentional:
# - _init_db() defines the complete current schema for fresh installs
# - Migrations incrementally upgrade existing databases to the current schema
# Both paths must result in identical schemas. When adding new schema elements,
# add them to both places and use IF NOT EXISTS for idempotency.
MIGRATIONS: dict[int, tuple[str, callable]] = {}


def migration(version: int, name: str):
    """Decorator to register a schema migration."""

    def decorator(func: callable):
        MIGRATIONS[version] = (name, func)
        return func

    return decorator


@migration(2, "add_rfc17_phase1_columns")
def migrate_v2(conn):
    """Add columns for RFC #17 Phase 1: user_message_text, exit_code, and git_commits table."""
    # Check if columns already exist (for fresh installs that already have them)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}

    # Add user_message_text for user journey tracking
    if "user_message_text" not in existing_cols:
        conn.execute("ALTER TABLE events ADD COLUMN user_message_text TEXT")
    # Add exit_code for failure detection
    if "exit_code" not in existing_cols:
        conn.execute("ALTER TABLE events ADD COLUMN exit_code INTEGER")

    # Create git_commits table for git correlation
    conn.execute("""
        CREATE TABLE IF NOT EXISTS git_commits (
            sha TEXT PRIMARY KEY,
            timestamp TIMESTAMP,
            message TEXT,
            session_id TEXT,
            project_path TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_git_commits_timestamp ON git_commits(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_git_commits_session ON git_commits(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_git_commits_project ON git_commits(project_path)")


@migration(3, "add_user_message_fts")
def migrate_v3(conn):
    """Add FTS5 full-text search index on user_message_text for efficient text search."""
    # Create FTS5 virtual table (content= points to external events table)
    # Using content-less FTS (no redundant storage) with events.id as rowid
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
            user_message_text,
            content='events',
            content_rowid='id'
        )
    """)

    # Populate FTS index from existing events with non-null user_message_text
    conn.execute("""
        INSERT INTO events_fts(rowid, user_message_text)
        SELECT id, user_message_text FROM events WHERE user_message_text IS NOT NULL
    """)

    # Create triggers to keep FTS in sync with events table
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS events_fts_insert AFTER INSERT ON events
        WHEN NEW.user_message_text IS NOT NULL
        BEGIN
            INSERT INTO events_fts(rowid, user_message_text) VALUES (NEW.id, NEW.user_message_text);
        END
    """)

    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS events_fts_delete AFTER DELETE ON events
        WHEN OLD.user_message_text IS NOT NULL
        BEGIN
            INSERT INTO events_fts(events_fts, rowid, user_message_text)
            VALUES ('delete', OLD.id, OLD.user_message_text);
        END
    """)

    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS events_fts_update AFTER UPDATE OF user_message_text ON events
        WHEN OLD.user_message_text IS NOT NULL OR NEW.user_message_text IS NOT NULL
        BEGIN
            INSERT INTO events_fts(events_fts, rowid, user_message_text)
            SELECT 'delete', OLD.id, OLD.user_message_text WHERE OLD.user_message_text IS NOT NULL;
            INSERT INTO events_fts(rowid, user_message_text)
            SELECT NEW.id, NEW.user_message_text WHERE NEW.user_message_text IS NOT NULL;
        END
    """)

    # Partial index for efficiently querying events with user messages
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_has_user_message
        ON events(id) WHERE user_message_text IS NOT NULL
    """)


@migration(4, "add_session_enrichment")
def migrate_v4(conn):
    """Add columns for RFC #26: session enrichment with observable data.

    Adds:
    - Session-commit junction table for time-to-commit metrics
    - Context switch count for tracking topic changes

    Note: This migration intentionally does NOT add outcome/satisfaction columns.
    Per RFC #17 design principle: "Don't over-distill - raw data with light
    structure beats heavily processed summaries. The LLM can handle context."
    Outcome classification should be done by the consuming LLM, not pre-computed.
    """
    # Check existing session columns
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}

    # Add observable data columns (no interpretation)
    if "context_switch_count" not in existing_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN context_switch_count INTEGER DEFAULT 0")

    # Create session_commits junction table for detailed commit tracking
    # This allows tracking time_to_commit and multiple commits per session
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_commits (
            session_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            time_to_commit_seconds INTEGER,
            is_first_commit INTEGER DEFAULT 0,
            PRIMARY KEY (session_id, commit_sha),
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (commit_sha) REFERENCES git_commits(sha)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_commits_session ON session_commits(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_commits_commit ON session_commits(commit_sha)"
    )


@migration(5, "add_agent_tracking")
def migrate_v5(conn):
    """Add columns for RFC #41: Agent tracking and token deduplication.

    Adds:
    - parent_uuid: Links tool_use events to their parent assistant event
    - agent_id: Agent ID from agent-*.jsonl files (Task subagents)
    - is_sidechain: Boolean for agent/background work
    - version: Claude Code version from entry

    This migration supports the new event hierarchy where:
    - assistant events have tokens (not duplicated)
    - tool_use events link to parent via parent_uuid (no tokens)
    """
    # Check existing columns
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}

    # Add parent_uuid for event hierarchy
    if "parent_uuid" not in existing_cols:
        conn.execute("ALTER TABLE events ADD COLUMN parent_uuid TEXT")

    # Add agent_id for Task subagent tracking
    if "agent_id" not in existing_cols:
        conn.execute("ALTER TABLE events ADD COLUMN agent_id TEXT")

    # Add is_sidechain for agent/background work
    if "is_sidechain" not in existing_cols:
        conn.execute("ALTER TABLE events ADD COLUMN is_sidechain INTEGER DEFAULT 0")

    # Add version for Claude Code version tracking
    if "version" not in existing_cols:
        conn.execute("ALTER TABLE events ADD COLUMN version TEXT")

    # Add indexes for efficient querying
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_parent_uuid ON events(parent_uuid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_agent_id ON events(agent_id)")


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

    def execute_query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        """Execute a SQL query and return all results.

        This is the public API for raw SQL queries. Use this instead of
        accessing _connect() directly.

        Args:
            sql: SQL query string
            params: Query parameters (tuple or list)

        Returns:
            List of sqlite3.Row objects
        """
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def execute_write(self, sql: str, params: tuple | list = ()) -> int:
        """Execute a SQL write operation and return rows affected.

        This is the public API for INSERT/UPDATE/DELETE operations.

        Args:
            sql: SQL statement
            params: Query parameters (tuple or list)

        Returns:
            Number of rows affected
        """
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    def executemany(self, sql: str, params_seq: list[tuple]) -> int:
        """Execute a SQL statement with multiple parameter sets.

        This is the public API for batch INSERT/UPDATE/DELETE operations.

        Args:
            sql: SQL statement with placeholders
            params_seq: Sequence of parameter tuples

        Returns:
            Total number of rows affected
        """
        with self._connect() as conn:
            cursor = conn.executemany(sql, params_seq)
            return cursor.rowcount

    def _get_schema_version(self, conn: sqlite3.Connection) -> int:
        """Get current schema version from database."""
        try:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            # Table doesn't exist yet
            return 0

    def _run_migrations(self, conn: sqlite3.Connection, current_version: int):
        """Run all pending migrations."""
        for version in range(current_version + 1, SCHEMA_VERSION + 1):
            if version in MIGRATIONS:
                name, migration_func = MIGRATIONS[version]
                logger.info(f"Running migration {version}: {name}")
                migration_func(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )

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

                    -- RFC #17 Phase 1 additions
                    user_message_text TEXT,
                    exit_code INTEGER,

                    -- RFC #41: Agent tracking and token deduplication
                    parent_uuid TEXT,
                    agent_id TEXT,
                    is_sidechain INTEGER DEFAULT 0,
                    version TEXT,

                    UNIQUE(session_id, uuid)
                )
            """)

            # Indexes for common queries (columns that exist in initial schema)
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
                    slug TEXT,
                    -- RFC #26: Observable session data (no interpretation)
                    context_switch_count INTEGER DEFAULT 0
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

            # Git commits for correlation (RFC #17 Phase 1)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS git_commits (
                    sha TEXT PRIMARY KEY,
                    timestamp TIMESTAMP,
                    message TEXT,
                    session_id TEXT,
                    project_path TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_git_commits_timestamp ON git_commits(timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_git_commits_session ON git_commits(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_git_commits_project ON git_commits(project_path)"
            )

            # Session-commit junction table for detailed commit tracking (RFC #26)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_commits (
                    session_id TEXT NOT NULL,
                    commit_sha TEXT NOT NULL,
                    time_to_commit_seconds INTEGER,
                    is_first_commit INTEGER DEFAULT 0,
                    PRIMARY KEY (session_id, commit_sha),
                    FOREIGN KEY (session_id) REFERENCES sessions(id),
                    FOREIGN KEY (commit_sha) REFERENCES git_commits(sha)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_commits_session "
                "ON session_commits(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_commits_commit "
                "ON session_commits(commit_sha)"
            )

            # FTS5 full-text search on user_message_text (RFC #17 Phase 1)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    user_message_text,
                    content='events',
                    content_rowid='id'
                )
            """)

            # Triggers to keep FTS in sync with events table
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS events_fts_insert AFTER INSERT ON events
                WHEN NEW.user_message_text IS NOT NULL
                BEGIN
                    INSERT INTO events_fts(rowid, user_message_text) VALUES (NEW.id, NEW.user_message_text);
                END
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS events_fts_delete AFTER DELETE ON events
                WHEN OLD.user_message_text IS NOT NULL
                BEGIN
                    INSERT INTO events_fts(events_fts, rowid, user_message_text)
                    VALUES ('delete', OLD.id, OLD.user_message_text);
                END
            """)

            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS events_fts_update AFTER UPDATE OF user_message_text ON events
                WHEN OLD.user_message_text IS NOT NULL OR NEW.user_message_text IS NOT NULL
                BEGIN
                    INSERT INTO events_fts(events_fts, rowid, user_message_text)
                    SELECT 'delete', OLD.id, OLD.user_message_text WHERE OLD.user_message_text IS NOT NULL;
                    INSERT INTO events_fts(rowid, user_message_text)
                    SELECT NEW.id, NEW.user_message_text WHERE NEW.user_message_text IS NOT NULL;
                END
            """)

            # Partial index for efficiently querying events with user messages
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_has_user_message
                ON events(id) WHERE user_message_text IS NOT NULL
            """)

            # Run migrations AFTER all tables are created
            # Only existing databases need migrations - fresh databases have full schema
            current_version = self._get_schema_version(conn)
            if current_version > 0 and current_version < SCHEMA_VERSION:
                self._run_migrations(conn, current_version)
            elif current_version == 0:
                # Fresh database - just set the version, no migrations needed
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

            # RFC #41: Create indexes for agent tracking columns
            # These run AFTER migrations so columns exist on both fresh and migrated DBs
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_parent_uuid ON events(parent_uuid)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_agent_id ON events(agent_id)")

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
                    git_branch, cwd, user_message_text, exit_code,
                    parent_uuid, agent_id, is_sidechain, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    event.user_message_text,
                    event.exit_code,
                    event.parent_uuid,
                    event.agent_id,
                    1 if event.is_sidechain else 0,
                    event.version,
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
                    git_branch, cwd, user_message_text, exit_code,
                    parent_uuid, agent_id, is_sidechain, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        e.user_message_text,
                        e.exit_code,
                        e.parent_uuid,
                        e.agent_id,
                        1 if e.is_sidechain else 0,
                        e.version,
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
        session_id: str | None = None,
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
            if session_id:
                conditions.append("session_id = ?")
                params.append(session_id)

            # Safe: where_clause is built from hardcoded condition strings, not user input
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

        # Helper to safely get column that might not exist in older schema
        def get_col(name: str, default=None):
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

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
            user_message_text=get_col("user_message_text"),
            exit_code=get_col("exit_code"),
            # RFC #41: Agent tracking
            parent_uuid=get_col("parent_uuid"),
            agent_id=get_col("agent_id"),
            is_sidechain=bool(get_col("is_sidechain", 0)),
            version=get_col("version"),
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
                    primary_branch, slug,
                    context_switch_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    session.context_switch_count,
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

        # Helper to safely get column that might not exist in older schema
        def get_col(name: str, default=None):
            try:
                return row[name]
            except (IndexError, KeyError):
                logger.debug("Column '%s' not found in row, using default %s", name, default)
                return default

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
            # RFC #26: Session enrichment (observable data only, no interpretation)
            context_switch_count=get_col("context_switch_count", 0),
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

    # Git commit operations (RFC #17 Phase 1)

    def add_git_commit(self, commit: GitCommit) -> None:
        """Add a git commit for correlation."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO git_commits (
                    sha, timestamp, message, session_id, project_path
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    commit.sha,
                    commit.timestamp,
                    commit.message,
                    commit.session_id,
                    commit.project_path,
                ),
            )

    def add_git_commits_batch(self, commits: list[GitCommit]) -> int:
        """Add multiple git commits in a single transaction. Returns count added."""
        with self._connect() as conn:
            cursor = conn.executemany(
                """
                INSERT OR REPLACE INTO git_commits (
                    sha, timestamp, message, session_id, project_path
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [(c.sha, c.timestamp, c.message, c.session_id, c.project_path) for c in commits],
            )
            return cursor.rowcount

    def get_git_commits(
        self,
        project_path: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[GitCommit]:
        """Get git commits, optionally filtered by project and time range."""
        with self._connect() as conn:
            conditions = []
            params: list = []

            if project_path:
                conditions.append("project_path = ?")
                params.append(project_path)
            if start:
                conditions.append("timestamp >= ?")
                params.append(start)
            if end:
                conditions.append("timestamp <= ?")
                params.append(end)

            # Safe: where_clause is built from hardcoded condition strings, not user input
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT sha, timestamp, message, session_id, project_path
                FROM git_commits
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

            return [
                GitCommit(
                    sha=row["sha"],
                    timestamp=row["timestamp"],
                    message=row["message"],
                    session_id=row["session_id"],
                    project_path=row["project_path"],
                )
                for row in rows
            ]

    def get_git_commit_count(self) -> int:
        """Get total number of git commits."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM git_commits").fetchone()
            return row["count"]

    # Session-commit correlation operations (RFC #26)

    def add_session_commit(
        self,
        session_id: str,
        commit_sha: str,
        time_to_commit_seconds: int | None = None,
        is_first_commit: bool = False,
    ) -> None:
        """Link a commit to a session with timing metadata."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_commits (
                    session_id, commit_sha, time_to_commit_seconds, is_first_commit
                ) VALUES (?, ?, ?, ?)
                """,
                (session_id, commit_sha, time_to_commit_seconds, 1 if is_first_commit else 0),
            )

    def add_session_commits_batch(self, links: list[tuple[str, str, int | None, bool]]) -> int:
        """Add multiple session-commit links in a batch.

        Args:
            links: List of (session_id, commit_sha, time_to_commit_seconds, is_first_commit)

        Returns:
            Number of rows affected
        """
        with self._connect() as conn:
            cursor = conn.executemany(
                """
                INSERT OR REPLACE INTO session_commits (
                    session_id, commit_sha, time_to_commit_seconds, is_first_commit
                ) VALUES (?, ?, ?, ?)
                """,
                [(s, c, t, 1 if f else 0) for s, c, t, f in links],
            )
            return cursor.rowcount

    def get_session_commits(self, session_id: str) -> list[dict]:
        """Get all commits associated with a session.

        Returns:
            List of dicts with commit info and timing
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sc.commit_sha, sc.time_to_commit_seconds, sc.is_first_commit,
                       gc.timestamp, gc.message
                FROM session_commits sc
                LEFT JOIN git_commits gc ON sc.commit_sha = gc.sha
                WHERE sc.session_id = ?
                ORDER BY gc.timestamp
                """,
                (session_id,),
            ).fetchall()

            return [
                {
                    "sha": row["commit_sha"],
                    "time_to_commit_seconds": row["time_to_commit_seconds"],
                    "is_first_commit": bool(row["is_first_commit"]),
                    "timestamp": row["timestamp"],
                    "message": row["message"],
                }
                for row in rows
            ]

    def get_commits_for_sessions(
        self, session_ids: list[str] | None = None
    ) -> dict[str, list[dict]]:
        """Get commits grouped by session.

        Args:
            session_ids: Optional list of session IDs to filter by

        Returns:
            Dict mapping session_id to list of commit info dicts
        """
        with self._connect() as conn:
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                rows = conn.execute(
                    f"""
                    SELECT sc.session_id, sc.commit_sha, sc.time_to_commit_seconds,
                           sc.is_first_commit, gc.timestamp, gc.message
                    FROM session_commits sc
                    LEFT JOIN git_commits gc ON sc.commit_sha = gc.sha
                    WHERE sc.session_id IN ({placeholders})
                    ORDER BY sc.session_id, gc.timestamp
                    """,
                    session_ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT sc.session_id, sc.commit_sha, sc.time_to_commit_seconds,
                           sc.is_first_commit, gc.timestamp, gc.message
                    FROM session_commits sc
                    LEFT JOIN git_commits gc ON sc.commit_sha = gc.sha
                    ORDER BY sc.session_id, gc.timestamp
                    """
                ).fetchall()

            result: dict[str, list[dict]] = {}
            for row in rows:
                sid = row["session_id"]
                if sid not in result:
                    result[sid] = []
                result[sid].append(
                    {
                        "sha": row["commit_sha"],
                        "time_to_commit_seconds": row["time_to_commit_seconds"],
                        "is_first_commit": bool(row["is_first_commit"]),
                        "timestamp": row["timestamp"],
                        "message": row["message"],
                    }
                )
            return result

    # Full-text search operations

    def search_user_messages(
        self, query: str, limit: int = 100, project: str | None = None
    ) -> list[Event]:
        """Search user messages using full-text search.

        Args:
            query: FTS5 query string (supports AND, OR, NOT, phrases, etc.)
            limit: Maximum number of results
            project: Optional project path filter (LIKE %project%)

        Returns:
            List of Event objects matching the search query
        """
        with self._connect() as conn:
            # Use FTS5 MATCH to search, join back to events for full data
            if project:
                rows = conn.execute(
                    """
                    SELECT events.* FROM events
                    INNER JOIN events_fts ON events.id = events_fts.rowid
                    WHERE events_fts MATCH ?
                      AND events.project_path LIKE ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, f"%{project}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT events.* FROM events
                    INNER JOIN events_fts ON events.id = events_fts.rowid
                    WHERE events_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()

            return [self._row_to_event(row) for row in rows]

    # Utility operations

    def get_db_stats(self) -> dict:
        """Get database statistics."""
        with self._connect() as conn:
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            pattern_count = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            file_count = conn.execute("SELECT COUNT(*) FROM ingestion_state").fetchone()[0]

            # Git commit count (may not exist in older schemas)
            try:
                git_commit_count = conn.execute("SELECT COUNT(*) FROM git_commits").fetchone()[0]
            except sqlite3.OperationalError:
                git_commit_count = 0

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
                "git_commit_count": git_commit_count,
                "files_processed": file_count,
                "earliest_event": to_iso(date_range["min_ts"]),
                "latest_event": to_iso(date_range["max_ts"]),
                "db_size_bytes": db_size,
                "db_path": str(self.db_path),
            }

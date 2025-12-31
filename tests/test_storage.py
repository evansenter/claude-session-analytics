"""Tests for the SQLite storage layer."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from session_analytics.storage import (
    Event,
    IngestionState,
    Pattern,
    Session,
    SQLiteStorage,
)


@pytest.fixture
def storage():
    """Create a temporary storage instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield SQLiteStorage(db_path)


@pytest.fixture
def sample_event():
    """Create a sample event for testing."""
    return Event(
        id=None,
        uuid="test-uuid-12345",
        timestamp=datetime(2025, 1, 1, 12, 0, 0),
        session_id="session-abc123",
        project_path="/encoded/project/path",
        entry_type="assistant",
        tool_name="Bash",
        tool_input_json='{"command": "git status"}',
        tool_id="tool-123",
        is_error=False,
        command="git",
        command_args="status",
    )


class TestEventOperations:
    """Tests for event CRUD operations."""

    def test_add_event(self, storage, sample_event):
        """Test adding a single event."""
        result = storage.add_event(sample_event)
        assert result.id is not None
        assert result.uuid == sample_event.uuid

    def test_add_event_dedup(self, storage, sample_event):
        """Test that duplicate events are ignored."""
        storage.add_event(sample_event)
        storage.add_event(sample_event)  # Same uuid + session_id
        assert storage.get_event_count() == 1

    def test_add_events_batch(self, storage):
        """Test adding multiple events in batch."""
        events = [
            Event(
                id=None,
                uuid=f"uuid-{i}",
                timestamp=datetime(2025, 1, 1, 12, i, 0),
                session_id="session-1",
            )
            for i in range(5)
        ]
        count = storage.add_events_batch(events)
        assert count == 5
        assert storage.get_event_count() == 5

    def test_get_events_in_range(self, storage):
        """Test filtering events by time range."""
        # Add events across different times
        for i in range(5):
            storage.add_event(
                Event(
                    id=None,
                    uuid=f"uuid-{i}",
                    timestamp=datetime(2025, 1, i + 1, 12, 0, 0),
                    session_id="session-1",
                )
            )

        # Query a subset (start/end are inclusive, events are at 12:00)
        events = storage.get_events_in_range(
            start=datetime(2025, 1, 2, 0, 0, 0),
            end=datetime(2025, 1, 4, 23, 59, 59),
        )
        assert len(events) == 3

    def test_get_events_by_tool(self, storage):
        """Test filtering events by tool name."""
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-1",
                timestamp=datetime.now(),
                session_id="s1",
                tool_name="Bash",
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-2",
                timestamp=datetime.now(),
                session_id="s1",
                tool_name="Read",
            )
        )

        bash_events = storage.get_events_in_range(tool_name="Bash")
        assert len(bash_events) == 1
        assert bash_events[0].tool_name == "Bash"


class TestSessionOperations:
    """Tests for session CRUD operations."""

    def test_upsert_session(self, storage):
        """Test adding and updating a session."""
        session = Session(
            id="session-1",
            project_path="/test/project",
            first_seen=datetime(2025, 1, 1),
            last_seen=datetime(2025, 1, 1),
            entry_count=10,
        )
        storage.upsert_session(session)

        retrieved = storage.get_session("session-1")
        assert retrieved is not None
        assert retrieved.entry_count == 10

        # Update
        session.entry_count = 20
        storage.upsert_session(session)

        retrieved = storage.get_session("session-1")
        assert retrieved.entry_count == 20

    def test_get_session_count(self, storage):
        """Test counting sessions."""
        for i in range(3):
            storage.upsert_session(Session(id=f"session-{i}"))
        assert storage.get_session_count() == 3


class TestIngestionState:
    """Tests for ingestion state tracking."""

    def test_update_and_get_ingestion_state(self, storage):
        """Test tracking file ingestion state."""
        state = IngestionState(
            file_path="/path/to/file.jsonl",
            file_size=1024,
            last_modified=datetime(2025, 1, 1),
            entries_processed=100,
            last_processed=datetime(2025, 1, 1, 12, 0),
        )
        storage.update_ingestion_state(state)

        retrieved = storage.get_ingestion_state("/path/to/file.jsonl")
        assert retrieved is not None
        assert retrieved.file_size == 1024
        assert retrieved.entries_processed == 100

    def test_get_last_ingestion_time(self, storage):
        """Test getting most recent ingestion time."""
        storage.update_ingestion_state(
            IngestionState(
                file_path="/file1.jsonl",
                file_size=100,
                last_modified=datetime(2025, 1, 1),
                entries_processed=10,
                last_processed=datetime(2025, 1, 1, 10, 0),
            )
        )
        storage.update_ingestion_state(
            IngestionState(
                file_path="/file2.jsonl",
                file_size=200,
                last_modified=datetime(2025, 1, 2),
                entries_processed=20,
                last_processed=datetime(2025, 1, 2, 10, 0),  # More recent
            )
        )

        last_time = storage.get_last_ingestion_time()
        assert last_time == datetime(2025, 1, 2, 10, 0)


class TestPatternOperations:
    """Tests for pattern CRUD operations."""

    def test_upsert_pattern(self, storage):
        """Test adding and updating patterns."""
        pattern = Pattern(
            id=None,
            pattern_type="tool_frequency",
            pattern_key="Bash",
            count=100,
            last_seen=datetime(2025, 1, 1),
            metadata={"avg_duration": 1.5},
        )
        storage.upsert_pattern(pattern)

        patterns = storage.get_patterns("tool_frequency")
        assert len(patterns) == 1
        assert patterns[0].count == 100
        assert patterns[0].metadata["avg_duration"] == 1.5

    def test_get_patterns_by_type(self, storage):
        """Test filtering patterns by type."""
        storage.upsert_pattern(
            Pattern(id=None, pattern_type="tool_frequency", pattern_key="Bash", count=50)
        )
        storage.upsert_pattern(
            Pattern(id=None, pattern_type="sequence", pattern_key="Read→Edit", count=30)
        )

        tool_patterns = storage.get_patterns("tool_frequency")
        assert len(tool_patterns) == 1

        all_patterns = storage.get_patterns()
        assert len(all_patterns) == 2

    def test_clear_patterns(self, storage):
        """Test clearing patterns."""
        storage.upsert_pattern(
            Pattern(id=None, pattern_type="tool_frequency", pattern_key="Bash", count=50)
        )
        storage.upsert_pattern(
            Pattern(id=None, pattern_type="sequence", pattern_key="Read→Edit", count=30)
        )

        # Clear just one type
        deleted = storage.clear_patterns("tool_frequency")
        assert deleted == 1
        assert len(storage.get_patterns()) == 1

        # Clear all
        storage.upsert_pattern(
            Pattern(id=None, pattern_type="tool_frequency", pattern_key="Read", count=40)
        )
        deleted = storage.clear_patterns()
        assert deleted == 2


class TestDbStats:
    """Tests for database statistics."""

    def test_get_db_stats(self, storage, sample_event):
        """Test getting database statistics."""
        storage.add_event(sample_event)
        storage.upsert_session(Session(id="session-1"))
        storage.upsert_pattern(Pattern(id=None, pattern_type="test", pattern_key="key", count=1))

        stats = storage.get_db_stats()
        assert stats["event_count"] == 1
        assert stats["session_count"] == 1
        assert stats["pattern_count"] == 1
        assert stats["db_path"] is not None

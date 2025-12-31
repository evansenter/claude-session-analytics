"""Tests for the SQLite storage layer."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from session_analytics.storage import (
    Event,
    GitCommit,
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

    def test_add_events_batch_empty(self, storage):
        """Test batch add with empty list."""
        count = storage.add_events_batch([])
        assert count == 0
        assert storage.get_event_count() == 0

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


class TestGitCommitValidation:
    """Tests for GitCommit validation (RFC #17 Phase 1)."""

    def test_valid_short_sha(self):
        """Test that 7-character short SHA is valid."""
        commit = GitCommit(sha="abc1234")
        assert commit.sha == "abc1234"

    def test_valid_full_sha(self):
        """Test that 40-character full SHA is valid."""
        full_sha = "a" * 40
        commit = GitCommit(sha=full_sha)
        assert commit.sha == full_sha

    def test_invalid_sha_empty(self):
        """Test that empty SHA raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            GitCommit(sha="")

    def test_invalid_sha_too_short(self):
        """Test that SHA shorter than 7 chars raises ValueError."""
        with pytest.raises(ValueError, match="must be 7-40 characters"):
            GitCommit(sha="abc123")

    def test_invalid_sha_too_long(self):
        """Test that SHA longer than 40 chars raises ValueError."""
        with pytest.raises(ValueError, match="must be 7-40 characters"):
            GitCommit(sha="a" * 41)

    def test_invalid_sha_non_hex(self):
        """Test that non-hexadecimal SHA raises ValueError."""
        with pytest.raises(ValueError, match="must be hexadecimal"):
            GitCommit(sha="ghijklm")

    def test_gitcommit_is_frozen(self):
        """Test that GitCommit is immutable."""
        commit = GitCommit(sha="abc1234")
        with pytest.raises(AttributeError):
            commit.sha = "def5678"


class TestGitCommitOperations:
    """Tests for git commit operations (RFC #17 Phase 1)."""

    def test_add_git_commit(self, storage):
        """Test adding a git commit."""
        commit = GitCommit(
            sha="abc1234",
            timestamp=datetime.now(),
            message="Test commit",
            session_id="session-1",
            project_path="test-project",
        )
        storage.add_git_commit(commit)

        commits = storage.get_git_commits()
        assert len(commits) == 1
        assert commits[0].sha == "abc1234"
        assert commits[0].message == "Test commit"
        assert commits[0].session_id == "session-1"
        assert commits[0].project_path == "test-project"

    def test_add_git_commit_deduplication(self, storage):
        """Test that duplicate SHA overwrites existing commit (INSERT OR REPLACE behavior)."""
        # Add initial commit
        storage.add_git_commit(
            GitCommit(sha="abc1234", message="Original message", project_path="project-1")
        )

        # Add commit with same SHA but different data
        storage.add_git_commit(
            GitCommit(sha="abc1234", message="Updated message", project_path="project-2")
        )

        # Should still have only one commit, with updated data
        commits = storage.get_git_commits()
        assert len(commits) == 1
        assert commits[0].sha == "abc1234"
        assert commits[0].message == "Updated message"
        assert commits[0].project_path == "project-2"

    def test_add_git_commits_batch(self, storage):
        """Test batch adding git commits."""
        commits = [
            GitCommit(sha="aaa1111", timestamp=datetime.now(), message="Commit 1"),
            GitCommit(sha="bbb2222", timestamp=datetime.now(), message="Commit 2"),
            GitCommit(sha="ccc3333", timestamp=datetime.now(), message="Commit 3"),
        ]
        count = storage.add_git_commits_batch(commits)
        assert count == 3

        stored = storage.get_git_commits()
        assert len(stored) == 3

    def test_add_git_commits_batch_empty(self, storage):
        """Test batch add with empty list."""
        count = storage.add_git_commits_batch([])
        assert count == 0
        assert storage.get_git_commit_count() == 0

    def test_get_git_commits_with_filters(self, storage):
        """Test filtering git commits by project, start, and end time."""
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)
        commits = [
            GitCommit(sha="aaa1111", timestamp=two_days_ago, project_path="project-a"),
            GitCommit(sha="bbb2222", timestamp=yesterday, project_path="project-a"),
            GitCommit(sha="ccc3333", timestamp=now, project_path="project-a"),
            GitCommit(sha="ddd4444", timestamp=now, project_path="project-b"),
        ]
        storage.add_git_commits_batch(commits)

        # Filter by project
        project_a = storage.get_git_commits(project_path="project-a")
        assert len(project_a) == 3

        # Filter by start time
        recent = storage.get_git_commits(start=now - timedelta(hours=1))
        assert len(recent) == 2

        # Filter by end time
        old = storage.get_git_commits(end=yesterday + timedelta(hours=1))
        assert len(old) == 2

        # Combined filters: project AND time range
        project_a_recent = storage.get_git_commits(
            project_path="project-a", start=yesterday - timedelta(hours=1), end=now
        )
        assert len(project_a_recent) == 2  # mid1 and new1

    def test_git_commit_count(self, storage):
        """Test getting git commit count."""
        assert storage.get_git_commit_count() == 0

        storage.add_git_commit(GitCommit(sha="abcdef1"))
        assert storage.get_git_commit_count() == 1


class TestNewEventFields:
    """Tests for RFC #17 Phase 1 Event fields (user_message_text, exit_code)."""

    def test_event_with_user_message_text(self, storage):
        """Test storing and retrieving user_message_text."""
        event = Event(
            id=None,
            uuid="test-uuid",
            timestamp=datetime.now(),
            session_id="session-1",
            entry_type="user",
            user_message_text="Hello, please help me with something",
        )
        stored = storage.add_event(event)
        assert stored.id is not None

        events = storage.get_events_in_range()
        assert len(events) == 1
        assert events[0].user_message_text == "Hello, please help me with something"

    def test_event_with_exit_code(self, storage):
        """Test storing and retrieving exit_code."""
        event = Event(
            id=None,
            uuid="bash-uuid",
            timestamp=datetime.now(),
            session_id="session-1",
            entry_type="tool_result",
            tool_name="Bash",
            exit_code=1,
        )
        storage.add_event(event)

        events = storage.get_events_in_range()
        assert len(events) == 1
        assert events[0].exit_code == 1

    def test_event_with_all_new_fields(self, storage):
        """Test event with all new fields populated."""
        event = Event(
            id=None,
            uuid="full-uuid",
            timestamp=datetime.now(),
            session_id="session-1",
            entry_type="user",
            user_message_text="Run a command",
            exit_code=0,
        )
        storage.add_event(event)

        events = storage.get_events_in_range()
        assert events[0].user_message_text == "Run a command"
        assert events[0].exit_code == 0

    def test_event_with_null_new_fields(self, storage):
        """Test that events with NULL user_message_text and exit_code are handled correctly."""
        event = Event(
            id=None,
            uuid="null-fields-uuid",
            timestamp=datetime.now(),
            session_id="session-1",
            entry_type="assistant",
            # user_message_text and exit_code are None by default
        )
        storage.add_event(event)

        events = storage.get_events_in_range()
        assert len(events) == 1
        assert events[0].user_message_text is None
        assert events[0].exit_code is None


class TestFullTextSearch:
    """Tests for full-text search on user_message_text."""

    def test_search_user_messages_basic(self, storage):
        """Test basic full-text search on user messages."""
        # Add events with searchable text
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-1",
                timestamp=datetime.now(),
                session_id="session-1",
                entry_type="user",
                user_message_text="Help me debug the authentication error",
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-2",
                timestamp=datetime.now(),
                session_id="session-1",
                entry_type="user",
                user_message_text="Fix the database connection issue",
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-3",
                timestamp=datetime.now(),
                session_id="session-1",
                entry_type="user",
                user_message_text="Another error message to debug",
            )
        )

        # Search for "debug"
        results = storage.search_user_messages("debug")
        assert len(results) == 2
        assert all("debug" in r.user_message_text.lower() for r in results)

        # Search for "authentication"
        results = storage.search_user_messages("authentication")
        assert len(results) == 1
        assert "authentication" in results[0].user_message_text.lower()

    def test_search_user_messages_no_match(self, storage):
        """Test search returns empty when no matches found."""
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-1",
                timestamp=datetime.now(),
                session_id="session-1",
                entry_type="user",
                user_message_text="This is a test message",
            )
        )

        results = storage.search_user_messages("nonexistent")
        assert len(results) == 0

    def test_search_user_messages_phrase(self, storage):
        """Test searching for exact phrases."""
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-1",
                timestamp=datetime.now(),
                session_id="session-1",
                entry_type="user",
                user_message_text="Run the unit tests",
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="uuid-2",
                timestamp=datetime.now(),
                session_id="session-1",
                entry_type="user",
                user_message_text="Unit testing is important",
            )
        )

        # Search for phrase "unit tests"
        results = storage.search_user_messages('"unit tests"')
        assert len(results) == 1
        assert "unit tests" in results[0].user_message_text.lower()

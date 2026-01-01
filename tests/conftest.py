"""Pytest configuration and shared fixtures."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from session_analytics.storage import Event, Session, SQLiteStorage


@pytest.fixture
def storage():
    """Create a temporary storage instance for testing.

    This is the base fixture for all storage-dependent tests.
    Use this when you need an empty database.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield SQLiteStorage(db_path)


@pytest.fixture
def sample_event():
    """Sample event for basic testing."""
    return Event(
        id=None,
        uuid="test-event-uuid",
        timestamp=datetime.now(),
        session_id="test-session",
        project_path="-test-project",
        entry_type="tool_use",
        tool_name="Read",
        file_path="/path/to/file.py",
    )


@pytest.fixture
def populated_storage(storage):
    """Storage instance with sample data suitable for most query/pattern tests.

    Contains:
    - 2 sessions (session-1, session-2) within 7 days
    - 1 session (session-3) older than 7 days
    - Mix of tools: Bash, Read, Edit
    - Token counts for aggregation tests
    """
    now = datetime.now()

    events = [
        Event(
            id=None,
            uuid="event-1",
            timestamp=now - timedelta(hours=1),
            session_id="session-1",
            project_path="-test-project",
            entry_type="tool_use",
            tool_name="Bash",
            command="git",
            command_args="status",
            input_tokens=100,
            output_tokens=50,
            model="claude-opus-4-5",
        ),
        Event(
            id=None,
            uuid="event-2",
            timestamp=now - timedelta(hours=2),
            session_id="session-1",
            project_path="-test-project",
            entry_type="tool_use",
            tool_name="Read",
            file_path="/path/to/file.py",
            input_tokens=80,
            output_tokens=30,
            model="claude-opus-4-5",
        ),
        Event(
            id=None,
            uuid="event-3",
            timestamp=now - timedelta(hours=3),
            session_id="session-1",
            project_path="-test-project",
            entry_type="tool_use",
            tool_name="Bash",
            command="git",
            command_args="diff",
            input_tokens=120,
            output_tokens=60,
            model="claude-opus-4-5",
        ),
        Event(
            id=None,
            uuid="event-4",
            timestamp=now - timedelta(hours=4),
            session_id="session-2",
            project_path="-other-project",
            entry_type="tool_use",
            tool_name="Edit",
            file_path="/path/to/other.py",
            input_tokens=200,
            output_tokens=100,
            model="claude-sonnet-4-20250514",
        ),
        Event(
            id=None,
            uuid="event-5",
            timestamp=now - timedelta(days=10),
            session_id="session-3",
            project_path="-old-project",
            entry_type="tool_use",
            tool_name="Bash",
            command="make",
            input_tokens=50,
            output_tokens=25,
            model="claude-opus-4-5",
        ),
        # User messages for search tests (FTS)
        Event(
            id=None,
            uuid="user-msg-1",
            timestamp=now - timedelta(hours=1, minutes=30),
            session_id="session-1",
            project_path="-test-project",
            entry_type="user",
            user_message_text="Fix the authentication bug in the login flow",
        ),
        Event(
            id=None,
            uuid="user-msg-2",
            timestamp=now - timedelta(hours=2, minutes=30),
            session_id="session-1",
            project_path="-test-project",
            entry_type="user",
            user_message_text="Add unit tests for the API endpoints",
        ),
    ]
    storage.add_events_batch(events)

    # Add sessions
    storage.upsert_session(
        Session(
            id="session-1",
            project_path="-test-project",
            first_seen=now - timedelta(hours=3),
            last_seen=now - timedelta(hours=1),
            entry_count=3,
            tool_use_count=3,
            total_input_tokens=300,
            total_output_tokens=140,
            primary_branch="main",
        )
    )
    storage.upsert_session(
        Session(
            id="session-2",
            project_path="-other-project",
            first_seen=now - timedelta(hours=4),
            last_seen=now - timedelta(hours=4),
            entry_count=1,
            tool_use_count=1,
            total_input_tokens=200,
            total_output_tokens=100,
            primary_branch="feature",
        )
    )

    return storage


@pytest.fixture
def pattern_storage(storage):
    """Storage with data specifically for pattern detection tests.

    Contains:
    - 3 sessions with Read -> Edit sequences
    - Multiple Bash commands for permission gap testing
    """
    now = datetime.now()

    events = [
        # Session 1: Read -> Edit -> Bash sequence
        Event(
            id=None,
            uuid="e1",
            timestamp=now - timedelta(hours=1),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Read",
        ),
        Event(
            id=None,
            uuid="e2",
            timestamp=now - timedelta(hours=1, minutes=-1),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Edit",
        ),
        Event(
            id=None,
            uuid="e3",
            timestamp=now - timedelta(hours=1, minutes=-2),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="git",
        ),
        # Session 2: Read -> Edit sequence
        Event(
            id=None,
            uuid="e4",
            timestamp=now - timedelta(hours=2),
            session_id="s2",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Read",
        ),
        Event(
            id=None,
            uuid="e5",
            timestamp=now - timedelta(hours=2, minutes=-1),
            session_id="s2",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Edit",
        ),
        # Session 3: Read -> Edit sequence
        Event(
            id=None,
            uuid="e6",
            timestamp=now - timedelta(hours=3),
            session_id="s3",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Read",
        ),
        Event(
            id=None,
            uuid="e7",
            timestamp=now - timedelta(hours=3, minutes=-1),
            session_id="s3",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Edit",
        ),
        # Multiple make commands for permission gap testing
        Event(
            id=None,
            uuid="e8",
            timestamp=now - timedelta(hours=4),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="make",
        ),
        Event(
            id=None,
            uuid="e9",
            timestamp=now - timedelta(hours=4, minutes=-1),
            session_id="s2",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="make",
        ),
        Event(
            id=None,
            uuid="e10",
            timestamp=now - timedelta(hours=4, minutes=-2),
            session_id="s3",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="make",
        ),
        Event(
            id=None,
            uuid="e11",
            timestamp=now - timedelta(hours=4, minutes=-3),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="make",
        ),
        Event(
            id=None,
            uuid="e12",
            timestamp=now - timedelta(hours=4, minutes=-4),
            session_id="s2",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="make",
        ),
    ]

    storage.add_events_batch(events)
    return storage


@pytest.fixture
def sample_session_log_entry():
    """Sample JSONL entry from a Claude Code session log."""
    return {
        "uuid": "test-uuid-12345",
        "timestamp": "2025-01-01T12:00:00.000Z",
        "sessionId": "session-abc123",
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-123",
                    "name": "Bash",
                    "input": {"command": "git status", "description": "Check git status"},
                }
            ],
        },
    }

"""Tests for the CLI module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from session_analytics.cli import (
    cmd_commands,
    cmd_frequency,
    cmd_insights,
    cmd_permissions,
    cmd_search,
    cmd_sequences,
    cmd_session_commits,
    cmd_sessions,
    cmd_signals,
    cmd_status,
    cmd_tokens,
    format_output,
)
from session_analytics.storage import Event, GitCommit, Session, SQLiteStorage


@pytest.fixture
def storage():
    """Create a temporary storage instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield SQLiteStorage(db_path)


@pytest.fixture
def populated_storage(storage):
    """Create a storage instance with sample data."""
    now = datetime.now()

    events = [
        Event(
            id=None,
            uuid="e1",
            timestamp=now - timedelta(hours=1),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Bash",
            command="git",
            input_tokens=100,
            output_tokens=50,
        ),
        Event(
            id=None,
            uuid="e2",
            timestamp=now - timedelta(hours=2),
            session_id="s1",
            project_path="-test",
            entry_type="tool_use",
            tool_name="Read",
            input_tokens=80,
            output_tokens=30,
        ),
        Event(
            id=None,
            uuid="u1",
            timestamp=now - timedelta(hours=1, minutes=30),
            session_id="s1",
            project_path="-test",
            entry_type="user",
            user_message_text="Fix the authentication bug in the login flow",
        ),
        Event(
            id=None,
            uuid="u2",
            timestamp=now - timedelta(hours=2, minutes=30),
            session_id="s1",
            project_path="-test",
            entry_type="user",
            user_message_text="Add unit tests for the API endpoints",
        ),
    ]
    storage.add_events_batch(events)

    storage.upsert_session(
        Session(
            id="s1",
            project_path="-test",
            first_seen=now - timedelta(hours=2),
            last_seen=now - timedelta(hours=1),
            entry_count=2,
            tool_use_count=2,
            total_input_tokens=180,
            total_output_tokens=80,
        )
    )

    return storage


class TestFormatOutput:
    """Tests for output formatting."""

    def test_json_output(self):
        """Test JSON output mode."""
        data = {"key": "value", "count": 42}
        result = format_output(data, json_output=True)
        assert '"key": "value"' in result
        assert '"count": 42' in result

    def test_tool_frequency_format(self):
        """Test tool frequency formatting."""
        data = {
            "total_tool_calls": 100,
            "tools": [
                {"tool": "Bash", "count": 50},
                {"tool": "Read", "count": 30},
            ],
        }
        result = format_output(data)
        assert "Total tool calls: 100" in result
        assert "Bash: 50" in result
        assert "Read: 30" in result

    def test_command_frequency_format(self):
        """Test command frequency formatting."""
        data = {
            "total_commands": 50,
            "commands": [
                {"command": "git", "count": 30},
                {"command": "make", "count": 20},
            ],
        }
        result = format_output(data)
        assert "Total commands: 50" in result
        assert "git: 30" in result

    def test_status_format(self):
        """Test status formatting."""
        data = {
            "db_path": "/path/to/db",
            "db_size_bytes": 10240,
            "event_count": 1000,
            "session_count": 10,
            "pattern_count": 50,
            "earliest_event": "2025-01-01T00:00:00",
            "latest_event": "2025-01-31T23:59:59",
        }
        result = format_output(data)
        assert "Database:" in result
        assert "Events: 1000" in result
        assert "Sessions: 10" in result

    def test_sessions_format(self):
        """Test sessions formatting."""
        data = {
            "session_count": 5,
            "total_entries": 100,
            "total_input_tokens": 5000,
            "total_output_tokens": 2500,
        }
        result = format_output(data)
        assert "Sessions: 5" in result
        assert "Total entries: 100" in result

    def test_insights_format(self):
        """Test insights formatting."""
        data = {
            "summary": {
                "total_tools": 10,
                "total_commands": 5,
                "total_sequences": 3,
                "permission_gaps_found": 2,
            }
        }
        result = format_output(data)
        assert "Insights summary:" in result
        assert "Tools: 10" in result


class TestCliCommands:
    """Tests for CLI command functions."""

    def test_cmd_status(self, populated_storage, capsys):
        """Test status command."""

        class Args:
            json = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_status(Args())

        captured = capsys.readouterr()
        assert "Events:" in captured.out

    def test_cmd_frequency(self, populated_storage, capsys):
        """Test frequency command."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_frequency(Args())

        captured = capsys.readouterr()
        assert "Total tool calls:" in captured.out

    def test_cmd_commands(self, populated_storage, capsys):
        """Test commands command."""

        class Args:
            json = False
            days = 7
            project = None
            prefix = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_commands(Args())

        captured = capsys.readouterr()
        assert "Total commands:" in captured.out

    def test_cmd_sessions(self, populated_storage, capsys):
        """Test sessions command."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_sessions(Args())

        captured = capsys.readouterr()
        assert "Sessions:" in captured.out

    def test_cmd_tokens(self, populated_storage, capsys):
        """Test tokens command."""

        class Args:
            json = False
            days = 7
            project = None
            by = "day"

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_tokens(Args())

        captured = capsys.readouterr()
        assert "Token usage" in captured.out

    def test_cmd_sequences(self, populated_storage, capsys):
        """Test sequences command."""

        class Args:
            json = False
            days = 7
            min_count = 1
            length = 2

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_sequences(Args())

        captured = capsys.readouterr()
        assert "Common tool sequences:" in captured.out

    def test_cmd_permissions(self, populated_storage, capsys):
        """Test permissions command."""

        class Args:
            json = False
            days = 7
            threshold = 1

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_permissions(Args())

        captured = capsys.readouterr()
        assert "Permission gaps" in captured.out

    def test_cmd_insights(self, populated_storage, capsys):
        """Test insights command."""

        class Args:
            json = False
            days = 7
            refresh = True
            basic = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_insights(Args())

        captured = capsys.readouterr()
        assert "Insights summary:" in captured.out

    def test_json_output_mode(self, populated_storage, capsys):
        """Test JSON output mode."""

        class Args:
            json = True
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_frequency(Args())

        captured = capsys.readouterr()
        assert '"total_tool_calls"' in captured.out

    def test_cmd_search(self, populated_storage, capsys):
        """Test search command."""

        class Args:
            json = False
            query = "authentication"
            limit = 50
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_search(Args())

        captured = capsys.readouterr()
        assert "Search: authentication" in captured.out
        assert "Results:" in captured.out

    def test_cmd_search_no_results(self, populated_storage, capsys):
        """Test search command with no results."""

        class Args:
            json = False
            query = "nonexistent_query_xyz"
            limit = 50
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_search(Args())

        captured = capsys.readouterr()
        assert "Results: 0" in captured.out

    def test_cmd_search_json_output(self, populated_storage, capsys):
        """Test search command with JSON output."""

        class Args:
            json = True
            query = "authentication"
            limit = 50
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_search(Args())

        captured = capsys.readouterr()
        assert '"query": "authentication"' in captured.out
        assert '"count":' in captured.out
        assert '"messages":' in captured.out

    def test_cmd_search_malformed_query(self, populated_storage, capsys):
        """Test search command with malformed FTS5 query."""

        class Args:
            json = False
            query = '"unclosed quote'
            limit = 50
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_search(Args())

        captured = capsys.readouterr()
        # Should show error instead of crashing
        assert "error" in captured.out.lower() or "Error" in captured.out

    def test_cmd_search_with_project_filter(self, populated_storage, capsys):
        """Test search command with project filter."""

        class Args:
            json = False
            query = "authentication"
            limit = 50
            project = "-test"

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_search(Args())

        captured = capsys.readouterr()
        assert "Search: authentication" in captured.out
        assert "Results:" in captured.out

    def test_cmd_signals(self, populated_storage, capsys):
        """Test signals command (RFC #26, revised per RFC #17)."""

        class Args:
            json = False
            days = 7
            min_events = 1
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_signals(Args())

        captured = capsys.readouterr()
        assert "Session Signals" in captured.out
        assert "Sessions analyzed:" in captured.out

    def test_cmd_signals_json(self, populated_storage, capsys):
        """Test signals command with JSON output."""

        class Args:
            json = True
            days = 7
            min_events = 1
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_signals(Args())

        captured = capsys.readouterr()
        assert '"sessions_analyzed"' in captured.out
        assert '"sessions"' in captured.out

    def test_cmd_session_commits(self, populated_storage, capsys):
        """Test session-commits command (RFC #26)."""
        # Add a commit and link it to the session
        now = datetime.now()
        populated_storage.add_git_commit(
            GitCommit(sha="abc1234def", timestamp=now, message="Test commit")
        )
        populated_storage.add_session_commit("s1", "abc1234def", 300, True)

        class Args:
            json = False
            days = 7
            session_id = None
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_session_commits(Args())

        captured = capsys.readouterr()
        assert "Session Commits" in captured.out
        assert "Total commits:" in captured.out

    def test_cmd_session_commits_specific_session(self, populated_storage, capsys):
        """Test session-commits command for specific session."""
        now = datetime.now()
        populated_storage.add_git_commit(
            GitCommit(sha="def5678abc", timestamp=now, message="Test commit 2")
        )
        populated_storage.add_session_commit("s1", "def5678abc", 600, False)

        class Args:
            json = False
            days = 7
            session_id = "s1"
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_session_commits(Args())

        captured = capsys.readouterr()
        assert "Session Commits" in captured.out
        assert "Total commits:" in captured.out


class TestRFC26Formatters:
    """Tests for RFC #26 output formatters (revised per RFC #17 - raw signals only)."""

    def test_signals_format(self):
        """Test signals formatting (raw data, no interpretation)."""
        data = {
            "days": 7,
            "sessions_analyzed": 5,
            "sessions": [
                {
                    "session_id": "session-1-abc",
                    "project_path": "/test",
                    "event_count": 50,
                    "error_count": 2,
                    "edit_count": 10,
                    "git_count": 5,
                    "skill_count": 3,
                    "commit_count": 2,
                    "error_rate": 0.04,
                    "duration_minutes": 45.0,
                    "has_rework": False,
                    "has_pr_activity": True,
                },
                {
                    "session_id": "session-2-def",
                    "project_path": "/test",
                    "event_count": 20,
                    "error_count": 5,
                    "edit_count": 8,
                    "git_count": 1,
                    "skill_count": 0,
                    "commit_count": 0,
                    "error_rate": 0.25,
                    "duration_minutes": 30.0,
                    "has_rework": True,
                    "has_pr_activity": False,
                },
            ],
        }
        result = format_output(data)
        assert "Session Signals" in result
        assert "Sessions analyzed: 5" in result
        assert "session-1-abc" in result
        assert "50 events" in result
        assert "[PR]" in result
        assert "[rework]" in result

    def test_session_commits_format(self):
        """Test session commits formatting."""
        data = {
            "days": 7,
            "session_id": None,
            "total_commits": 3,
            "commits": [
                {
                    "session_id": "session-1",
                    "sha": "abc1234def5678",
                    "time_to_commit_seconds": 300,
                    "is_first_commit": True,
                },
                {
                    "session_id": "session-1",
                    "sha": "def5678abc1234",
                    "time_to_commit_seconds": 600,
                    "is_first_commit": False,
                },
            ],
        }
        result = format_output(data)
        assert "Session Commits" in result
        assert "Total commits: 3" in result
        assert "abc1234d" in result  # First 8 chars of SHA
        assert "300s" in result
        assert "(first)" in result

    def test_session_commits_format_specific_session(self):
        """Test session commits formatting for specific session."""
        data = {
            "days": 7,
            "session_id": "session-specific",
            "total_commits": 1,
            "commits": [
                {
                    "sha": "abc1234def5678",
                    "time_to_commit_seconds": 450,
                    "is_first_commit": True,
                },
            ],
        }
        result = format_output(data)
        assert "session-specific" in result
        assert "450s" in result

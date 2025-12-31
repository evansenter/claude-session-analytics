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
    cmd_sequences,
    cmd_sessions,
    cmd_status,
    cmd_tokens,
    format_output,
)
from session_analytics.storage import Event, Session, SQLiteStorage


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

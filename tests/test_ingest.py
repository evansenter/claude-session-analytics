"""Tests for the JSONL ingestion module."""

import json
import tempfile
from pathlib import Path

import pytest

from session_analytics.ingest import (
    find_log_files,
    ingest_file,
    parse_entry,
    parse_tool_use,
)
from session_analytics.storage import SQLiteStorage


@pytest.fixture
def storage():
    """Create a temporary storage instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield SQLiteStorage(db_path)


@pytest.fixture
def sample_logs_dir():
    """Create a temporary directory with sample JSONL files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir)
        project_dir = logs_dir / "-test-project"
        project_dir.mkdir()

        # Create a sample JSONL file
        jsonl_file = project_dir / "test-session.jsonl"
        entries = [
            {
                "type": "user",
                "uuid": "user-1",
                "sessionId": "session-1",
                "timestamp": "2025-01-01T12:00:00.000Z",
                "cwd": "/test/project",
                "gitBranch": "main",
                "message": {"role": "user", "content": "Hello"},
            },
            {
                "type": "assistant",
                "uuid": "assistant-1",
                "sessionId": "session-1",
                "timestamp": "2025-01-01T12:00:05.000Z",
                "cwd": "/test/project",
                "gitBranch": "main",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-5-20251101",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {"command": "git status"},
                        }
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 1000,
                    },
                },
            },
            {
                "type": "user",
                "uuid": "result-1",
                "sessionId": "session-1",
                "timestamp": "2025-01-01T12:00:10.000Z",
                "cwd": "/test/project",
                "gitBranch": "main",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "On branch main",
                        }
                    ],
                },
            },
        ]

        with open(jsonl_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        yield logs_dir


class TestParseToolUse:
    """Tests for tool_use parsing."""

    def test_parse_bash_command(self):
        """Test extracting command from Bash tool."""
        tool_use = {
            "name": "Bash",
            "id": "tool-1",
            "input": {"command": "git status --short"},
        }
        result = parse_tool_use(tool_use)
        assert result["tool_name"] == "Bash"
        assert result["command"] == "git"
        assert result["command_args"] == "status --short"

    def test_parse_read_file(self):
        """Test extracting file_path from Read tool."""
        tool_use = {
            "name": "Read",
            "id": "tool-2",
            "input": {"file_path": "/path/to/file.py"},
        }
        result = parse_tool_use(tool_use)
        assert result["tool_name"] == "Read"
        assert result["file_path"] == "/path/to/file.py"

    def test_parse_skill(self):
        """Test extracting skill_name from Skill tool."""
        tool_use = {
            "name": "Skill",
            "id": "tool-3",
            "input": {"skill": "commit"},
        }
        result = parse_tool_use(tool_use)
        assert result["tool_name"] == "Skill"
        assert result["skill_name"] == "commit"

    def test_parse_mcp_tool(self):
        """Test parsing MCP tool names."""
        tool_use = {
            "name": "mcp__event-bus__register_session",
            "id": "tool-4",
            "input": {"name": "test"},
        }
        result = parse_tool_use(tool_use)
        assert result["tool_name"] == "mcp__event-bus__register_session"


class TestParseEntry:
    """Tests for entry parsing."""

    def test_parse_user_message(self):
        """Test parsing a user message."""
        entry = {
            "type": "user",
            "uuid": "user-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "cwd": "/test",
            "gitBranch": "main",
            "message": {"role": "user", "content": "Hello"},
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 1
        assert events[0].entry_type == "user"
        assert events[0].session_id == "session-1"

    def test_parse_assistant_with_tool(self):
        """Test parsing an assistant message with tool_use."""
        entry = {
            "type": "assistant",
            "uuid": "assistant-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "message": {
                "model": "claude-opus-4-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": "ls -la"},
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 1
        assert events[0].entry_type == "tool_use"
        assert events[0].tool_name == "Bash"
        assert events[0].command == "ls"
        assert events[0].input_tokens == 100

    def test_parse_tool_result(self):
        """Test parsing a tool_result entry."""
        entry = {
            "type": "user",
            "uuid": "result-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "output",
                    }
                ],
            },
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 1
        assert events[0].entry_type == "tool_result"
        assert events[0].tool_id == "tool-1"

    def test_skip_file_history_snapshot(self):
        """Test that file-history-snapshot entries are skipped."""
        entry = {
            "type": "file-history-snapshot",
            "uuid": "snapshot-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 0

    def test_skip_malformed_entry(self):
        """Test that entries without required fields are skipped."""
        entry = {"type": "user"}  # Missing uuid, sessionId, timestamp
        events = parse_entry(entry, "test-project")
        assert len(events) == 0


class TestIngestFile:
    """Tests for file ingestion."""

    def test_ingest_file(self, storage, sample_logs_dir):
        """Test ingesting a JSONL file."""
        project_dir = sample_logs_dir / "-test-project"
        jsonl_file = project_dir / "test-session.jsonl"

        result = ingest_file(jsonl_file, storage)
        assert result["entries_processed"] == 3
        assert result["events_added"] == 3
        assert result["skipped"] is False

    def test_incremental_ingestion(self, storage, sample_logs_dir):
        """Test that unchanged files are skipped on re-ingestion."""
        project_dir = sample_logs_dir / "-test-project"
        jsonl_file = project_dir / "test-session.jsonl"

        # First ingestion
        result1 = ingest_file(jsonl_file, storage)
        assert result1["skipped"] is False

        # Second ingestion should skip
        result2 = ingest_file(jsonl_file, storage)
        assert result2["skipped"] is True

    def test_force_reingestion(self, storage, sample_logs_dir):
        """Test force re-ingestion."""
        project_dir = sample_logs_dir / "-test-project"
        jsonl_file = project_dir / "test-session.jsonl"

        # First ingestion
        ingest_file(jsonl_file, storage)

        # Force re-ingestion should process again
        result = ingest_file(jsonl_file, storage, force=True)
        assert result["skipped"] is False


class TestFindLogFiles:
    """Tests for log file discovery."""

    def test_find_log_files(self, sample_logs_dir):
        """Test finding JSONL files in logs directory."""
        files = find_log_files(logs_dir=sample_logs_dir, days=7)
        assert len(files) == 1
        assert files[0].suffix == ".jsonl"

    def test_filter_by_project(self, sample_logs_dir):
        """Test filtering by project name."""
        # Create another project
        other_project = sample_logs_dir / "-other-project"
        other_project.mkdir()
        (other_project / "other.jsonl").write_text('{"type":"user"}\n')

        # Should find both
        all_files = find_log_files(logs_dir=sample_logs_dir, days=7)
        assert len(all_files) == 2

        # Should only find matching project
        filtered = find_log_files(logs_dir=sample_logs_dir, days=7, project_filter="test")
        assert len(filtered) == 1
        assert "test" in str(filtered[0])


class TestIngestLogs:
    """Tests for full ingestion flow."""

    def test_ingest_logs(self, storage, sample_logs_dir):
        """Test full ingestion flow."""
        # Use find_log_files with explicit logs_dir
        from session_analytics.ingest import ingest_file as do_ingest_file
        from session_analytics.ingest import update_session_stats

        files = find_log_files(logs_dir=sample_logs_dir, days=7)
        assert len(files) == 1

        # Ingest the file
        result = do_ingest_file(files[0], storage)
        assert result["events_added"] == 3

        # Update session stats
        sessions = update_session_stats(storage)
        assert sessions >= 1

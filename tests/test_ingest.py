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

    def test_user_message_text_truncation_at_boundary(self):
        """Test that user_message_text is truncated at USER_MESSAGE_MAX_LENGTH (2000 chars)."""
        from session_analytics.ingest import USER_MESSAGE_MAX_LENGTH

        # Test content exactly at the limit - should not be truncated
        exact_limit_content = "x" * USER_MESSAGE_MAX_LENGTH
        entry_exact = {
            "type": "user",
            "uuid": "user-exact",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "message": {"role": "user", "content": exact_limit_content},
        }
        events = parse_entry(entry_exact, "test-project")
        assert len(events) == 1
        assert len(events[0].user_message_text) == USER_MESSAGE_MAX_LENGTH

        # Test content over the limit - should be truncated
        over_limit_content = "y" * (USER_MESSAGE_MAX_LENGTH + 500)
        entry_over = {
            "type": "user",
            "uuid": "user-over",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:05.000Z",
            "message": {"role": "user", "content": over_limit_content},
        }
        events = parse_entry(entry_over, "test-project")
        assert len(events) == 1
        assert len(events[0].user_message_text) == USER_MESSAGE_MAX_LENGTH
        assert events[0].user_message_text == "y" * USER_MESSAGE_MAX_LENGTH

    def test_user_message_text_truncation_with_list_content(self):
        """Test truncation when content is a list of text blocks."""
        from session_analytics.ingest import USER_MESSAGE_MAX_LENGTH

        # Create content with multiple text blocks that exceed limit when joined
        text_block = "z" * 1500
        entry = {
            "type": "user",
            "uuid": "user-list",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_block},
                    {"type": "text", "text": text_block},  # Combined: 3001 chars with space
                ],
            },
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 1
        assert len(events[0].user_message_text) == USER_MESSAGE_MAX_LENGTH


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


class TestIngestGitHistory:
    """Tests for git history ingestion."""

    def test_not_a_git_repo(self, storage):
        """Test with non-git directory."""
        from session_analytics.ingest import ingest_git_history

        with tempfile.TemporaryDirectory() as tmpdir:
            result = ingest_git_history(storage, repo_path=tmpdir)

            assert "error" in result
            assert "Not a git repository" in result["error"]
            assert result["commits_added"] == 0

    def test_git_ingest_returns_stats(self, storage):
        """Test that git ingest returns proper stats structure."""
        from session_analytics.ingest import ingest_git_history

        # Using current directory which is a git repo
        result = ingest_git_history(storage, days=1)

        # Should have proper structure even if no recent commits
        assert "commits_found" in result or "error" in result
        if "commits_found" in result:
            assert "commits_added" in result


class TestCorrelateGitWithSessions:
    """Tests for git-session correlation."""

    def test_empty_database(self, storage):
        """Test with empty database."""
        from session_analytics.ingest import correlate_git_with_sessions

        result = correlate_git_with_sessions(storage, days=7)

        assert result["days"] == 7
        assert result["sessions_analyzed"] == 0
        assert result["commits_checked"] == 0
        assert result["commits_correlated"] == 0

    def test_correlation_with_matching_session(self, storage):
        """Test that commits during sessions are correlated."""
        from datetime import datetime, timedelta

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()

        # Add a session event
        events = [
            Event(
                id=None,
                uuid="e1",
                timestamp=now - timedelta(hours=1),
                session_id="test-session",
                project_path="/test/repo",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            Event(
                id=None,
                uuid="e2",
                timestamp=now - timedelta(minutes=30),
                session_id="test-session",
                project_path="/test/repo",
                entry_type="tool_use",
                tool_name="Read",
            ),
        ]
        storage.add_events_batch(events)

        # Add a git commit during the session (no session_id yet)
        commit = GitCommit(
            sha="abc123def456789012345678901234567890abcd",
            message="Test commit",
            timestamp=now - timedelta(minutes=45),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        # Run correlation
        result = correlate_git_with_sessions(storage, days=7)

        assert result["sessions_analyzed"] == 1
        assert result["commits_checked"] == 1
        assert result["commits_correlated"] == 1

        # Verify commit was updated with session ID
        commits = storage.get_git_commits()
        assert len(commits) == 1
        assert commits[0].session_id == "test-session"

    def test_commit_at_session_boundary(self, storage):
        """Test commit exactly 5 minutes after session end is included."""
        from datetime import datetime, timedelta

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()
        session_end = now - timedelta(minutes=30)

        # Session event
        storage.add_events_batch(
            [
                Event(
                    id=None,
                    uuid="boundary-e1",
                    timestamp=session_end - timedelta(minutes=30),
                    session_id="boundary-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Edit",
                ),
                Event(
                    id=None,
                    uuid="boundary-e2",
                    timestamp=session_end,
                    session_id="boundary-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
            ]
        )

        # Commit exactly 5 minutes after session end (should be included)
        commit = GitCommit(
            sha="a" * 40,  # 40 hex characters
            message="Boundary test commit",
            timestamp=session_end + timedelta(minutes=5),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        result = correlate_git_with_sessions(storage, days=7)

        assert result["commits_correlated"] == 1

    def test_commit_just_outside_buffer(self, storage):
        """Test commit 6 minutes after session end is NOT included."""
        from datetime import datetime, timedelta

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()
        session_end = now - timedelta(minutes=30)

        # Session event
        storage.add_events_batch(
            [
                Event(
                    id=None,
                    uuid="outside-e1",
                    timestamp=session_end - timedelta(minutes=30),
                    session_id="outside-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Edit",
                ),
                Event(
                    id=None,
                    uuid="outside-e2",
                    timestamp=session_end,
                    session_id="outside-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
            ]
        )

        # Commit 6 minutes after session end (should NOT be included)
        commit = GitCommit(
            sha="b" * 40,  # 40 hex characters
            message="Outside buffer test commit",
            timestamp=session_end + timedelta(minutes=6),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        result = correlate_git_with_sessions(storage, days=7)

        assert result["commits_correlated"] == 0

    def test_overlapping_sessions_picks_first_match(self, storage):
        """Test behavior when commit falls within multiple session windows."""
        from datetime import datetime, timedelta

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()

        # Two overlapping sessions
        events = [
            # Session 1: starts at -2h, ends at -30min
            Event(
                id=None,
                uuid="overlap-s1-e1",
                timestamp=now - timedelta(hours=2),
                session_id="session-first",
                project_path="/test/repo",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="overlap-s1-e2",
                timestamp=now - timedelta(minutes=30),
                session_id="session-first",
                project_path="/test/repo",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            # Session 2: starts at -1h, ends at -20min
            Event(
                id=None,
                uuid="overlap-s2-e1",
                timestamp=now - timedelta(hours=1),
                session_id="session-second",
                project_path="/test/repo",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="overlap-s2-e2",
                timestamp=now - timedelta(minutes=20),
                session_id="session-second",
                project_path="/test/repo",
                entry_type="tool_use",
                tool_name="Edit",
            ),
        ]
        storage.add_events_batch(events)

        # Commit during overlap period (45 min ago - in both sessions)
        commit = GitCommit(
            sha="c" * 40,  # 40 hex characters
            message="Overlap test commit",
            timestamp=now - timedelta(minutes=45),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        result = correlate_git_with_sessions(storage, days=7)

        # Should correlate (deterministic - picks first matching session)
        assert result["commits_correlated"] == 1
        commits = storage.get_git_commits()
        assert commits[0].session_id is not None

    def test_commit_before_session_start_within_buffer(self, storage):
        """Test commit 5 minutes BEFORE session start IS included (pre-session buffer)."""
        from datetime import datetime, timedelta

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()
        session_start = now - timedelta(hours=1)
        session_end = now - timedelta(minutes=30)

        # Session events
        storage.add_events_batch(
            [
                Event(
                    id=None,
                    uuid="presession-e1",
                    timestamp=session_start,
                    session_id="presession-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
                Event(
                    id=None,
                    uuid="presession-e2",
                    timestamp=session_end,
                    session_id="presession-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Edit",
                ),
            ]
        )

        # Commit 3 minutes BEFORE session start (should be included with pre-buffer)
        commit = GitCommit(
            sha="d" * 40,
            message="Pre-session commit",
            timestamp=session_start - timedelta(minutes=3),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        result = correlate_git_with_sessions(storage, days=7)

        assert result["commits_correlated"] == 1
        commits = storage.get_git_commits()
        assert commits[0].session_id == "presession-session"

    def test_commit_before_session_outside_pre_buffer(self, storage):
        """Test commit 6 minutes BEFORE session start is NOT included."""
        from datetime import datetime, timedelta

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()
        session_start = now - timedelta(hours=1)
        session_end = now - timedelta(minutes=30)

        # Session events
        storage.add_events_batch(
            [
                Event(
                    id=None,
                    uuid="preoutside-e1",
                    timestamp=session_start,
                    session_id="preoutside-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
                Event(
                    id=None,
                    uuid="preoutside-e2",
                    timestamp=session_end,
                    session_id="preoutside-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Edit",
                ),
            ]
        )

        # Commit 6 minutes BEFORE session start (outside 5-min buffer)
        commit = GitCommit(
            sha="e" * 40,
            message="Too early commit",
            timestamp=session_start - timedelta(minutes=6),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        result = correlate_git_with_sessions(storage, days=7)

        assert result["commits_correlated"] == 0


class TestBatchCorrelationErrorHandling:
    """Tests for batch correlation error handling."""

    def test_batch_correlation_error_logged(self, storage, caplog):
        """Test that batch correlation errors are logged."""
        from datetime import datetime, timedelta
        from unittest.mock import patch

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()
        session_start = now - timedelta(hours=1)
        session_end = now - timedelta(minutes=30)

        # Session events (need 2+ to define a session range)
        storage.add_events_batch(
            [
                Event(
                    id=None,
                    uuid="batcherr-e1",
                    timestamp=session_start,
                    session_id="batcherr-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
                Event(
                    id=None,
                    uuid="batcherr-e2",
                    timestamp=session_end,
                    session_id="batcherr-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Edit",
                ),
            ]
        )

        # Commit during session (should correlate)
        commit = GitCommit(
            sha="f" * 40,
            message="Batch error test",
            timestamp=session_start + timedelta(minutes=15),
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        # Mock executemany to raise an error
        with patch.object(storage, "executemany", side_effect=Exception("DB write failed")):
            import logging

            with caplog.at_level(logging.ERROR):
                result = correlate_git_with_sessions(storage, days=7)

        assert result["correlation_errors"] == 1
        assert result["commits_correlated"] == 0
        assert "Failed to batch correlate" in caplog.text

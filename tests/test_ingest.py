"""Tests for the JSONL ingestion module."""

import json
import tempfile
from pathlib import Path

import pytest

from session_analytics.ingest import (
    extract_command_name,
    find_log_files,
    ingest_file,
    parse_entry,
    parse_tool_use,
)

# Uses fixtures from conftest.py: storage


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


class TestExtractCommandName:
    """Tests for extracting command names from isMeta entries."""

    def test_extract_from_markdown_heading(self):
        """Test extracting command from markdown heading."""
        content = "# Status Report\n\nGenerate repo status summary..."
        assert extract_command_name(content) == "status-report"

    def test_extract_multi_word_command(self):
        """Test extracting multi-word commands."""
        content = "# I'm Lost\n\nShow current workflow position..."
        assert extract_command_name(content) == "i'm-lost"

    def test_extract_from_list_content(self):
        """Test extracting from list content with text blocks."""
        content = [{"type": "text", "text": "# PR Review\n\nReview code..."}]
        assert extract_command_name(content) == "pr-review"

    def test_no_heading_returns_none(self):
        """Test that content without heading returns None."""
        content = "Just a regular message"
        assert extract_command_name(content) is None

    def test_non_command_heading_filtered(self):
        """Test that common non-command headings are filtered."""
        content = "# Context\n\nSome context..."
        assert extract_command_name(content) is None

    def test_empty_content_returns_none(self):
        """Test that empty content returns None."""
        assert extract_command_name("") is None
        assert extract_command_name([]) is None


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
        """Test parsing an assistant message with tool_use.

        RFC #41: Now creates both an assistant event (with tokens) and tool_use events
        (without tokens) to fix token duplication.
        """
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

        # RFC #41: Should create 2 events: 1 assistant + 1 tool_use
        assert len(events) == 2

        # First event is assistant with tokens
        assert events[0].entry_type == "assistant"
        assert events[0].uuid == "assistant-1"
        assert events[0].input_tokens == 100
        assert events[0].output_tokens == 50
        assert events[0].parent_uuid is None  # Assistant has no parent

        # Second event is tool_use WITHOUT tokens, linked to parent
        assert events[1].entry_type == "tool_use"
        assert events[1].tool_name == "Bash"
        assert events[1].command == "ls"
        assert events[1].input_tokens is None  # No tokens on tool_use
        assert events[1].output_tokens is None
        assert events[1].parent_uuid == "assistant-1"  # Links to parent

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

    def test_parse_ismeta_command(self):
        """Test parsing an isMeta user message (slash command expansion)."""
        entry = {
            "type": "user",
            "uuid": "cmd-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "isMeta": True,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "# Status Report\n\nGenerate status..."}],
            },
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 1
        assert events[0].entry_type == "command"
        assert events[0].skill_name == "status-report"

    def test_parse_ismeta_without_command_heading(self):
        """Test that isMeta without valid command heading stays as user."""
        entry = {
            "type": "user",
            "uuid": "msg-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "isMeta": True,
            "message": {
                "role": "user",
                "content": "Just some meta text without a heading",
            },
        }
        events = parse_entry(entry, "test-project")
        assert len(events) == 1
        assert events[0].entry_type == "user"
        assert events[0].skill_name is None

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
        """Test ingesting a JSONL file.

        RFC #41: Assistant with tool_use now creates 2 events (assistant + tool_use),
        so 3 entries → 4 events (1 user + 2 from assistant + 1 tool_result).
        """
        project_dir = sample_logs_dir / "-test-project"
        jsonl_file = project_dir / "test-session.jsonl"

        result = ingest_file(jsonl_file, storage)
        assert result["entries_processed"] == 3
        assert result["events_added"] == 4  # RFC #41: assistant creates 2 events now
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
        """Test full ingestion flow.

        RFC #41: Assistant with tool_use creates 2 events, so 3 entries → 4 events.
        """
        # Use find_log_files with explicit logs_dir
        from session_analytics.ingest import ingest_file as do_ingest_file
        from session_analytics.ingest import update_session_stats

        files = find_log_files(logs_dir=sample_logs_dir, days=7)
        assert len(files) == 1

        # Ingest the file
        result = do_ingest_file(files[0], storage)
        assert result["events_added"] == 4  # RFC #41: assistant creates 2 events

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

    def test_git_ingest_handles_malformed_output(self, storage):
        """Test that malformed git log lines are skipped and counted."""
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from session_analytics.ingest import ingest_git_history

        # Create a fake git directory
        with tempfile.TemporaryDirectory() as tmpdir:
            git_dir = Path(tmpdir) / ".git"
            git_dir.mkdir()

            # Mock subprocess to return malformed output
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "\n".join(
                [
                    # Valid line (40 hex chars)
                    "abc123def456789012345678901234567890abcd|Author|2025-01-15T10:00:00|Valid commit",
                    # Missing fields (only 2 parts instead of 4)
                    "def456|Author",
                    # Empty line (should be skipped gracefully)
                    "",
                    # Missing message (only 3 parts)
                    "1234567890abcdef1234567890abcdef12345678|Author|2025-01-15T11:00:00",
                    # Invalid date format
                    "fedcba0987654321fedcba0987654321fedcba09|Author|not-a-date|Bad date commit",
                    # Another valid line (40 hex chars)
                    "0123456789abcdef0123456789abcdef01234567|Author|2025-01-15T12:00:00|Another valid",
                ]
            )

            with patch("subprocess.run", return_value=mock_result):
                result = ingest_git_history(storage, repo_path=tmpdir, days=7)

            # Should report skipped entries
            assert result.get("skipped_malformed", 0) >= 2  # "def456|Author" and 3-part line
            assert result.get("skipped_date_parse", 0) >= 1  # "not-a-date"
            # Should still process valid commits (commits_parsed is successful parses)
            assert result.get("commits_parsed", 0) == 2  # Two valid commits parsed
            assert result.get("commits_added", 0) == 2  # Both added to storage


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

    def test_timezone_aware_commit_correlates_correctly(self, storage):
        """Test that timezone-aware git commits correlate with naive session timestamps.

        Regression test for issue #34: TypeError when comparing timezone-aware and
        naive datetime objects.
        """
        from datetime import datetime, timedelta, timezone

        from session_analytics.ingest import correlate_git_with_sessions
        from session_analytics.storage import Event, GitCommit

        now = datetime.now()
        session_start = now - timedelta(hours=1)
        session_end = now - timedelta(minutes=30)

        # Session events with naive timestamps (no timezone)
        storage.add_events_batch(
            [
                Event(
                    id=None,
                    uuid="tz-aware-e1",
                    timestamp=session_start,  # naive
                    session_id="tz-aware-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
                Event(
                    id=None,
                    uuid="tz-aware-e2",
                    timestamp=session_end,  # naive
                    session_id="tz-aware-session",
                    project_path="/test/repo",
                    entry_type="tool_use",
                    tool_name="Edit",
                ),
            ]
        )

        # Git commit with timezone-aware timestamp (typical of git log output)
        commit_time = session_start + timedelta(minutes=10)
        commit_time_aware = commit_time.replace(tzinfo=timezone.utc)

        commit = GitCommit(
            sha="f" * 40,
            message="Commit with timezone",
            timestamp=commit_time_aware,  # timezone-aware
            project_path="/test/repo",
            session_id=None,
        )
        storage.add_git_commit(commit)

        # Should not raise TypeError
        result = correlate_git_with_sessions(storage, days=7)

        # Should successfully correlate
        assert result["commits_correlated"] == 1
        commits = storage.get_git_commits()
        assert commits[0].session_id == "tz-aware-session"


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


class TestRFC41AgentTracking:
    """Tests for RFC #41: Agent tracking and token deduplication.

    These tests verify:
    - Assistant messages with tools create both assistant + tool_use events
    - Tokens are only on assistant events (not duplicated to tool_use)
    - Agent tracking fields (agentId, isSidechain, version) are captured
    - parent_uuid links tool_use events to their parent assistant
    """

    def test_parse_assistant_creates_both_events(self):
        """Assistant with tools creates assistant + tool_use events.

        RFC #41: Previously only tool_use events were created, leading to
        token duplication when multiple tools were in one message.
        """
        entry = {
            "type": "assistant",
            "uuid": "multi-tool-assist",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "message": {
                "model": "claude-opus-4-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                    {
                        "type": "tool_use",
                        "id": "tool-2",
                        "name": "Read",
                        "input": {"file_path": "/x.py"},
                    },
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        }
        events = parse_entry(entry, "test-project")

        # Should create 3 events: 1 assistant + 2 tool_use
        assert len(events) == 3

        # First event is assistant with tokens
        assert events[0].entry_type == "assistant"
        assert events[0].uuid == "multi-tool-assist"
        assert events[0].input_tokens == 100
        assert events[0].output_tokens == 50
        assert events[0].parent_uuid is None

        # Tool events have NO tokens, linked via parent_uuid
        assert events[1].entry_type == "tool_use"
        assert events[1].tool_name == "Bash"
        assert events[1].input_tokens is None
        assert events[1].output_tokens is None
        assert events[1].parent_uuid == "multi-tool-assist"

        assert events[2].entry_type == "tool_use"
        assert events[2].tool_name == "Read"
        assert events[2].input_tokens is None
        assert events[2].parent_uuid == "multi-tool-assist"

    def test_parse_assistant_without_tools(self):
        """Assistant without tools creates single assistant event with tokens."""
        entry = {
            "type": "assistant",
            "uuid": "text-only-assist",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "message": {
                "model": "claude-opus-4-5",
                "content": [{"type": "text", "text": "Hello, how can I help?"}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        }
        events = parse_entry(entry, "test-project")

        # Should create only 1 event (assistant)
        assert len(events) == 1
        assert events[0].entry_type == "assistant"
        assert events[0].input_tokens == 100
        assert events[0].output_tokens == 50
        assert events[0].parent_uuid is None

    def test_parse_agent_entry(self):
        """Test agentId extraction from agent file entries."""
        entry = {
            "type": "assistant",
            "uuid": "agent-assist-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "agentId": "a07519c",
            "isSidechain": True,
            "version": "2.0.76",
            "message": {
                "model": "claude-opus-4-5",
                "content": [{"type": "text", "text": "Agent response"}],
                "usage": {"input_tokens": 50, "output_tokens": 25},
            },
        }
        events = parse_entry(entry, "test-project")

        assert len(events) == 1
        assert events[0].agent_id == "a07519c"
        assert events[0].is_sidechain is True
        assert events[0].version == "2.0.76"

    def test_parse_main_session_no_agent(self):
        """Main session entries have no agentId."""
        entry = {
            "type": "assistant",
            "uuid": "main-assist-1",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "isSidechain": False,
            "version": "2.0.76",
            "message": {
                "model": "claude-opus-4-5",
                "content": [{"type": "text", "text": "Main response"}],
                "usage": {"input_tokens": 50, "output_tokens": 25},
            },
        }
        events = parse_entry(entry, "test-project")

        assert events[0].agent_id is None
        assert events[0].is_sidechain is False
        assert events[0].version == "2.0.76"

    def test_agent_fields_propagate_to_tool_uses(self):
        """Agent tracking fields propagate from assistant to tool_use events."""
        entry = {
            "type": "assistant",
            "uuid": "agent-with-tools",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "agentId": "b123456",
            "isSidechain": True,
            "version": "2.0.80",
            "message": {
                "model": "claude-opus-4-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Edit",
                        "input": {"file_path": "/x.py"},
                    },
                ],
                "usage": {"input_tokens": 200, "output_tokens": 100},
            },
        }
        events = parse_entry(entry, "test-project")

        assert len(events) == 2

        # Assistant event has agent fields
        assert events[0].agent_id == "b123456"
        assert events[0].is_sidechain is True
        assert events[0].version == "2.0.80"

        # Tool event inherits agent fields
        assert events[1].agent_id == "b123456"
        assert events[1].is_sidechain is True
        assert events[1].version == "2.0.80"
        assert events[1].parent_uuid == "agent-with-tools"

    def test_token_deduplication_on_ingest(self, storage, tmp_path):
        """Verify tokens are not duplicated when ingesting multi-tool messages.

        RFC #41: Before this fix, a message with 3 tool_uses would count
        tokens 3x (once per tool). Now tokens are only on the assistant event.
        """
        import json

        from session_analytics.ingest import ingest_file

        # Create JSONL with assistant having 3 tool_uses
        jsonl_content = json.dumps(
            {
                "type": "assistant",
                "uuid": "dedup-1",
                "sessionId": "dedup-session",
                "timestamp": "2025-01-01T12:00:00.000Z",
                "message": {
                    "model": "claude-opus-4-5",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Read",
                            "input": {"file_path": "/x"},
                        },
                        {
                            "type": "tool_use",
                            "id": "t3",
                            "name": "Edit",
                            "input": {"file_path": "/y"},
                        },
                    ],
                    "usage": {"input_tokens": 900, "output_tokens": 300},
                },
            }
        )

        project_dir = tmp_path / "-test-project"
        project_dir.mkdir()
        (project_dir / "test.jsonl").write_text(jsonl_content)

        ingest_file(project_dir / "test.jsonl", storage)

        # Query total tokens - should be 900, not 2700 (3x duplication)
        rows = storage.execute_query("SELECT SUM(input_tokens) as total FROM events")
        assert rows[0]["total"] == 900

        # Query output tokens too
        rows = storage.execute_query("SELECT SUM(output_tokens) as total FROM events")
        assert rows[0]["total"] == 300

    def test_user_entry_gets_agent_fields(self):
        """User entries also capture agent tracking fields."""
        entry = {
            "type": "user",
            "uuid": "user-in-agent",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "agentId": "c789012",
            "isSidechain": True,
            "version": "2.0.76",
            "message": {"role": "user", "content": "User message in agent context"},
        }
        events = parse_entry(entry, "test-project")

        assert len(events) == 1
        assert events[0].agent_id == "c789012"
        assert events[0].is_sidechain is True
        assert events[0].version == "2.0.76"

    def test_tool_result_gets_agent_fields(self):
        """Tool result entries capture agent tracking fields."""
        entry = {
            "type": "user",
            "uuid": "result-in-agent",
            "sessionId": "session-1",
            "timestamp": "2025-01-01T12:00:00.000Z",
            "agentId": "d345678",
            "isSidechain": True,
            "version": "2.0.76",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "result"},
                ],
            },
        }
        events = parse_entry(entry, "test-project")

        assert len(events) == 1
        assert events[0].entry_type == "tool_result"
        assert events[0].agent_id == "d345678"
        assert events[0].is_sidechain is True
        assert events[0].version == "2.0.76"

"""Tests for the CLI module."""

from datetime import datetime
from unittest.mock import patch

from session_analytics.cli import (
    cmd_benchmark,
    cmd_classify,
    cmd_commands,
    cmd_failures,
    cmd_file_activity,
    cmd_frequency,
    cmd_git_correlate,
    cmd_git_ingest,
    cmd_handoff,
    cmd_ingest,
    cmd_insights,
    cmd_journey,
    cmd_languages,
    cmd_mcp_usage,
    cmd_parallel,
    cmd_permissions,
    cmd_projects,
    cmd_related,
    cmd_sample_sequences,
    cmd_search,
    cmd_sequences,
    cmd_session_commits,
    cmd_sessions,
    cmd_signals,
    cmd_status,
    cmd_tokens,
    cmd_trends,
    format_output,
)
from session_analytics.storage import GitCommit

# Uses fixtures from conftest.py: storage, populated_storage


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
        assert "Events: 1,000" in result  # Comma-formatted
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
        assert "Pre-computed patterns" in result
        assert "Tools tracked: 10" in result

    def test_benchmark_format(self):
        """Test benchmark formatting."""
        data = {
            "benchmarks": [
                {"tool": "get_status", "median": 0.005, "p95": 0.006, "p99": 0.006},
                {"tool": "get_tool_frequency", "median": 0.123, "p95": 0.145, "p99": 0.145},
            ],
            "total_tools": 2,
            "slow_tools": 0,
            "iterations": 3,
        }
        result = format_output(data)
        assert "Benchmark Results" in result
        assert "Total tools: 2" in result
        assert "Slow tools" in result
        assert "get_status" in result


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
        assert "Token consumption" in captured.out

    def test_cmd_sequences(self, populated_storage, capsys):
        """Test sequences command."""

        class Args:
            json = False
            days = 7
            min_count = 1
            length = 2
            expand = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_sequences(Args())

        captured = capsys.readouterr()
        assert "Tool chains showing workflow patterns" in captured.out

    def test_cmd_permissions(self, populated_storage, capsys):
        """Test permissions command."""

        class Args:
            json = False
            days = 7
            min_count = 1

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
        assert "Pre-computed patterns" in captured.out

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
            min_count = 1
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_signals(Args())

        captured = capsys.readouterr()
        assert "Session metrics" in captured.out
        assert "Sessions analyzed:" in captured.out

    def test_cmd_signals_json(self, populated_storage, capsys):
        """Test signals command with JSON output."""

        class Args:
            json = True
            days = 7
            min_count = 1
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

    def test_cmd_ingest(self, populated_storage, capsys):
        """Test ingest command."""

        class Args:
            json = False
            days = 7
            project = None
            force = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_ingest(Args())

        captured = capsys.readouterr()
        # Ingest should complete without error
        assert "files" in captured.out.lower() or "events" in captured.out.lower()

    def test_cmd_file_activity(self, populated_storage, capsys):
        """Test file-activity command."""

        class Args:
            json = False
            days = 7
            project = None
            limit = 20
            collapse_worktrees = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_file_activity(Args())

        captured = capsys.readouterr()
        assert "Files" in captured.out or "file" in captured.out.lower()

    def test_cmd_languages(self, populated_storage, capsys):
        """Test languages command."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_languages(Args())

        captured = capsys.readouterr()
        assert "Language" in captured.out or "operations" in captured.out.lower()

    def test_cmd_projects(self, populated_storage, capsys):
        """Test projects command."""

        class Args:
            json = False
            days = 7

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_projects(Args())

        captured = capsys.readouterr()
        assert "Project" in captured.out or "project" in captured.out.lower()

    def test_cmd_mcp_usage(self, populated_storage, capsys):
        """Test mcp-usage command."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_mcp_usage(Args())

        captured = capsys.readouterr()
        assert "MCP" in captured.out or "calls" in captured.out.lower()

    def test_cmd_sample_sequences(self, populated_storage, capsys):
        """Test sample-sequences command."""

        class Args:
            json = False
            pattern = "Read → Edit"
            limit = 5
            context = 2
            days = 7

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_sample_sequences(Args())

        captured = capsys.readouterr()
        assert "Read → Edit" in captured.out or "pattern" in captured.out.lower()

    def test_cmd_journey(self, populated_storage, capsys):
        """Test journey command."""

        class Args:
            json = False
            days = 1  # days * 24 = hours
            no_projects = False
            session_id = None
            limit = 100

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_journey(Args())

        captured = capsys.readouterr()
        # Journey output shows messages or message count
        assert "message" in captured.out.lower() or "journey" in captured.out.lower()

    def test_cmd_parallel(self, populated_storage, capsys):
        """Test parallel command."""

        class Args:
            json = False
            days = 1  # days * 24 = hours
            min_overlap = 5

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_parallel(Args())

        captured = capsys.readouterr()
        assert "parallel" in captured.out.lower() or "session" in captured.out.lower()

    def test_cmd_related(self, populated_storage, capsys):
        """Test related command."""

        class Args:
            json = False
            session_id = "nonexistent-session"
            method = "files"
            days = 7
            limit = 10

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_related(Args())

        captured = capsys.readouterr()
        assert "related" in captured.out.lower() or "session" in captured.out.lower()

    def test_cmd_failures(self, populated_storage, capsys):
        """Test failures command."""

        class Args:
            json = False
            days = 7
            rework_window = 10

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_failures(Args())

        captured = capsys.readouterr()
        assert "error" in captured.out.lower() or "failure" in captured.out.lower()

    def test_cmd_classify(self, populated_storage, capsys):
        """Test classify command."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_classify(Args())

        captured = capsys.readouterr()
        assert "session" in captured.out.lower() or "class" in captured.out.lower()

    def test_cmd_handoff(self, populated_storage, capsys):
        """Test handoff command."""

        class Args:
            json = False
            session_id = None
            days = 0.17  # days * 24 = ~4 hours
            limit = 10

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_handoff(Args())

        captured = capsys.readouterr()
        # Handoff output shows session info or error if no recent sessions
        assert "session" in captured.out.lower() or "error" in captured.out.lower()

    def test_cmd_trends(self, populated_storage, capsys):
        """Test trends command."""

        class Args:
            json = False
            days = 7
            compare_to = "previous"

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_trends(Args())

        captured = capsys.readouterr()
        assert "trend" in captured.out.lower() or "period" in captured.out.lower()

    def test_cmd_git_ingest(self, populated_storage, capsys):
        """Test git-ingest command."""

        class Args:
            json = False
            repo_path = None
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_git_ingest(Args())

        captured = capsys.readouterr()
        assert "commit" in captured.out.lower() or "git" in captured.out.lower()

    def test_cmd_git_correlate(self, populated_storage, capsys):
        """Test git-correlate command."""

        class Args:
            json = False
            days = 7

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_git_correlate(Args())

        captured = capsys.readouterr()
        assert "correlat" in captured.out.lower() or "commit" in captured.out.lower()

    def test_cmd_benchmark(self, populated_storage, capsys):
        """Test benchmark command."""

        class Args:
            json = False
            iterations = 1  # Minimal iterations for speed

        with patch("session_analytics.cli.SQLiteStorage", return_value=populated_storage):
            cmd_benchmark(Args())

        captured = capsys.readouterr()
        assert "Benchmark Results" in captured.out
        assert "Total tools:" in captured.out


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
        assert "Session metrics" in result
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


class TestCLIErrorPaths:
    """Tests for CLI error handling and edge cases."""

    def test_cmd_frequency_empty_database(self, storage, capsys):
        """Test frequency command with empty database."""

        class Args:
            json = False
            days = 7
            project = None
            no_expand = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_frequency(Args())

        captured = capsys.readouterr()
        # Should output zero counts, not crash
        assert "Total tool calls: 0" in captured.out

    def test_cmd_sessions_empty_database(self, storage, capsys):
        """Test sessions command with empty database."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_sessions(Args())

        captured = capsys.readouterr()
        # Should output zero sessions, not crash
        assert "Sessions: 0" in captured.out

    def test_cmd_commands_empty_database(self, storage, capsys):
        """Test commands command with empty database."""

        class Args:
            json = False
            days = 7
            project = None
            prefix = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_commands(Args())

        captured = capsys.readouterr()
        # Should output zero commands, not crash
        assert "Total commands: 0" in captured.out

    def test_cmd_sequences_empty_database(self, storage, capsys):
        """Test sequences command with empty database."""

        class Args:
            json = False
            days = 7
            min_count = 1
            length = 2
            expand = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_sequences(Args())

        captured = capsys.readouterr()
        # Should output empty sequences list, not crash
        assert "Sequences:" in captured.out

    def test_cmd_insights_empty_database(self, storage, capsys):
        """Test insights command with empty database."""

        class Args:
            json = False
            days = 7
            refresh = False
            basic = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_insights(Args())

        captured = capsys.readouterr()
        # Should output zero counts, not crash
        assert "Permission gaps: 0" in captured.out

    def test_cmd_journey_empty_database(self, storage, capsys):
        """Test journey command with empty database."""

        class Args:
            json = False
            days = 1
            no_projects = False
            session_id = None
            limit = 100

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_journey(Args())

        captured = capsys.readouterr()
        # Should output empty journey, not crash
        assert "journey" in captured.out.lower() or "message" in captured.out.lower()

    def test_cmd_signals_empty_database(self, storage, capsys):
        """Test signals command with empty database."""

        class Args:
            json = False
            days = 7
            min_count = 1
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_signals(Args())

        captured = capsys.readouterr()
        # Should output zero sessions, not crash
        assert "Sessions analyzed: 0" in captured.out

    def test_cmd_file_activity_empty_database(self, storage, capsys):
        """Test file-activity command with empty database."""

        class Args:
            json = False
            days = 7
            project = None
            limit = 20
            collapse_worktrees = False

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_file_activity(Args())

        captured = capsys.readouterr()
        # Should output zero files, not crash
        assert "Files touched: 0" in captured.out

    def test_cmd_languages_empty_database(self, storage, capsys):
        """Test languages command with empty database."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_languages(Args())

        captured = capsys.readouterr()
        # Should output zero operations, not crash
        assert "Total file operations: 0" in captured.out

    def test_cmd_projects_empty_database(self, storage, capsys):
        """Test projects command with empty database."""

        class Args:
            json = False
            days = 7

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_projects(Args())

        captured = capsys.readouterr()
        # Should output zero projects, not crash
        assert "Projects: 0" in captured.out

    def test_cmd_mcp_usage_empty_database(self, storage, capsys):
        """Test mcp-usage command with empty database."""

        class Args:
            json = False
            days = 7
            project = None

        with patch("session_analytics.cli.SQLiteStorage", return_value=storage):
            cmd_mcp_usage(Args())

        captured = capsys.readouterr()
        # Should output zero MCP calls, not crash
        assert "Total MCP calls: 0" in captured.out

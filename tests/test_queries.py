"""Tests for the query implementations."""

from datetime import datetime, timedelta

from session_analytics.queries import (
    ensure_fresh_data,
    get_cutoff,
    query_agent_activity,
    query_commands,
    query_error_details,
    query_file_activity,
    query_languages,
    query_mcp_usage,
    query_projects,
    query_sessions,
    query_timeline,
    query_tokens,
    query_tool_frequency,
)
from session_analytics.storage import Event, Session

# Uses fixtures from conftest.py: storage, populated_storage


class TestQueryToolFrequency:
    """Tests for tool frequency queries."""

    def test_basic_frequency(self, populated_storage):
        """Test basic tool frequency query."""
        result = query_tool_frequency(populated_storage, days=7)
        assert result["total_tool_calls"] == 4  # 5 events, but 1 is 10 days old
        assert len(result["tools"]) > 0

        # Check that Bash is most frequent
        tools = {t["tool"]: t["count"] for t in result["tools"]}
        assert tools.get("Bash", 0) == 2
        assert tools.get("Read", 0) == 1
        assert tools.get("Edit", 0) == 1

    def test_frequency_with_project_filter(self, populated_storage):
        """Test tool frequency with project filter."""
        result = query_tool_frequency(populated_storage, days=7, project="test")
        assert result["project"] == "test"
        # Should only include test-project events
        assert result["total_tool_calls"] == 3

    def test_frequency_days_filter(self, populated_storage):
        """Test that days filter works."""
        result = query_tool_frequency(populated_storage, days=30)
        assert result["total_tool_calls"] == 5  # All events including old one


class TestQueryTimeline:
    """Tests for timeline queries."""

    def test_basic_timeline(self, populated_storage):
        """Test basic timeline query."""
        result = query_timeline(populated_storage, limit=10)
        assert "events" in result
        assert len(result["events"]) <= 10

    def test_timeline_with_tool_filter(self, populated_storage):
        """Test timeline with tool filter."""
        result = query_timeline(populated_storage, tool="Bash", limit=10)
        for event in result["events"]:
            assert event["tool_name"] == "Bash"

    def test_timeline_with_time_range(self, populated_storage):
        """Test timeline with time range."""
        now = datetime.now()
        start = now - timedelta(hours=2)
        end = now

        result = query_timeline(populated_storage, start=start, end=end, limit=10)
        # Should only include events within range
        for event in result["events"]:
            ts = datetime.fromisoformat(event["timestamp"])
            assert ts >= start
            assert ts <= end

    def test_timeline_with_session_id_filter(self, populated_storage):
        """Test timeline with session_id filter."""
        result = query_timeline(populated_storage, session_id="session-1", limit=100)
        assert result["session_id"] == "session-1"
        for event in result["events"]:
            assert event["session_id"] == "session-1"


class TestQueryCommands:
    """Tests for command queries."""

    def test_basic_commands(self, populated_storage):
        """Test basic command query."""
        result = query_commands(populated_storage, days=7)
        assert result["total_commands"] >= 2  # At least 2 git commands

        # Check that git is present
        commands = {c["command"]: c["count"] for c in result["commands"]}
        assert "git" in commands
        assert commands["git"] == 2

    def test_commands_with_prefix(self, populated_storage):
        """Test command query with prefix filter."""
        result = query_commands(populated_storage, days=7, prefix="gi")
        # Should only include git commands
        for cmd in result["commands"]:
            assert cmd["command"].startswith("gi")

    def test_commands_with_project_filter(self, populated_storage):
        """Test command query with project filter."""
        result = query_commands(populated_storage, days=7, project="test")
        assert result["project"] == "test"


class TestQuerySessions:
    """Tests for session queries."""

    def test_basic_sessions(self, populated_storage):
        """Test basic session query."""
        result = query_sessions(populated_storage, days=7)
        assert result["session_count"] == 2  # 2 sessions within 7 days
        assert len(result["sessions"]) == 2

    def test_sessions_with_project_filter(self, populated_storage):
        """Test session query with project filter."""
        result = query_sessions(populated_storage, days=7, project="test")
        # Should only include test-project session
        assert result["session_count"] == 1
        assert result["sessions"][0]["project"] == "-test-project"

    def test_session_totals(self, populated_storage):
        """Test session totals calculation."""
        result = query_sessions(populated_storage, days=7)
        assert result["total_entries"] == 4  # 3 + 1
        assert result["total_tool_uses"] == 4  # 3 + 1
        assert result["total_input_tokens"] == 500  # 300 + 200
        assert result["total_output_tokens"] == 240  # 140 + 100


class TestQueryTokens:
    """Tests for token queries."""

    def test_tokens_by_day(self, populated_storage):
        """Test token query grouped by day."""
        result = query_tokens(populated_storage, days=7, by="day")
        assert result["group_by"] == "day"
        assert "breakdown" in result
        assert result["total_input_tokens"] >= 0
        assert result["total_output_tokens"] >= 0

    def test_tokens_by_session(self, populated_storage):
        """Test token query grouped by session."""
        result = query_tokens(populated_storage, days=7, by="session")
        assert result["group_by"] == "session"
        # Should have entries for each session
        assert len(result["breakdown"]) >= 1

    def test_tokens_by_model(self, populated_storage):
        """Test token query grouped by model."""
        result = query_tokens(populated_storage, days=7, by="model")
        assert result["group_by"] == "model"

        # Should have entries for each model
        models = {b["model"] for b in result["breakdown"]}
        assert "claude-opus-4-5" in models

    def test_tokens_invalid_grouping(self, populated_storage):
        """Test token query with invalid grouping."""
        result = query_tokens(populated_storage, days=7, by="invalid")
        assert "error" in result


class TestEnsureFreshData:
    """Tests for data freshness checking."""

    def test_fresh_data_not_refreshed(self, populated_storage):
        """Test that fresh data is not refreshed."""
        # First, update ingestion state to make data appear fresh
        from session_analytics.storage import IngestionState

        populated_storage.update_ingestion_state(
            IngestionState(
                file_path="/test/file.jsonl",
                file_size=1000,
                last_modified=datetime.now(),
                entries_processed=10,
                last_processed=datetime.now(),
            )
        )

        # Data should be fresh
        refreshed = ensure_fresh_data(populated_storage, max_age_minutes=5)
        assert not refreshed

    def test_force_refresh(self, populated_storage):
        """Test that force=True always refreshes."""
        refreshed = ensure_fresh_data(populated_storage, force=True)
        assert refreshed


# Phase 3: Cross-Session Timeline Tests


class TestGetUserJourney:
    """Tests for get_user_journey function."""

    def test_basic_journey(self, storage):
        """Test basic user journey extraction."""
        from session_analytics.queries import get_user_journey

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="j1",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="project-a",
                entry_type="user",
                user_message_text="Start working on feature",
            ),
            Event(
                id=None,
                uuid="j2",
                timestamp=now - timedelta(hours=1),
                session_id="s2",
                project_path="project-b",
                entry_type="user",
                user_message_text="Fix bug in other project",
            ),
        ]
        storage.add_events_batch(events)

        result = get_user_journey(storage, hours=24)

        assert result["message_count"] == 2
        assert len(result["projects_visited"]) == 2
        assert result["project_switches"] == 1

    def test_journey_excludes_tool_events(self, storage):
        """Test that journey only includes user messages."""
        from session_analytics.queries import get_user_journey

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="u1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="user",
                user_message_text="User message",
            ),
            Event(
                id=None,
                uuid="t1",
                timestamp=now - timedelta(minutes=30),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
            ),
        ]
        storage.add_events_batch(events)

        result = get_user_journey(storage, hours=24)

        # Should only have the user message, not the tool use
        assert result["message_count"] == 1

    def test_journey_with_session_id_filter(self, storage):
        """Test get_user_journey with session_id filter."""
        from session_analytics.queries import get_user_journey

        now = datetime.now()
        # Add user messages from two different sessions
        storage.add_event(
            Event(
                id=None,
                uuid="journey-1",
                timestamp=now - timedelta(hours=1),
                session_id="session-target",
                project_path="project-a",
                entry_type="user",
                user_message_text="Message from target session",
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="journey-2",
                timestamp=now - timedelta(hours=1),
                session_id="session-other",
                project_path="project-a",
                entry_type="user",
                user_message_text="Message from other session",
            )
        )

        # Filter to only target session
        result = get_user_journey(storage, hours=24, session_id="session-target")

        assert result["session_id"] == "session-target"
        assert result["message_count"] == 1
        assert result["journey"][0]["session_id"] == "session-target"


class TestDetectParallelSessions:
    """Tests for detect_parallel_sessions function."""

    def test_detect_overlapping_sessions(self, storage):
        """Test detection of overlapping sessions."""
        from session_analytics.queries import detect_parallel_sessions

        now = datetime.now()
        # Two sessions that overlap
        events = [
            # Session 1: 2h ago to 30min ago
            Event(
                id=None,
                uuid="p1",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="project-a",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="p2",
                timestamp=now - timedelta(minutes=30),
                session_id="s1",
                project_path="project-a",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            # Session 2: 1h ago to now (overlaps with s1)
            Event(
                id=None,
                uuid="p3",
                timestamp=now - timedelta(hours=1),
                session_id="s2",
                project_path="project-b",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="p4",
                timestamp=now,
                session_id="s2",
                project_path="project-b",
                entry_type="tool_use",
                tool_name="Edit",
            ),
        ]
        storage.add_events_batch(events)

        result = detect_parallel_sessions(storage, hours=24, min_overlap_minutes=1)

        assert result["total_sessions"] == 2
        assert result["parallel_period_count"] >= 1

    def test_no_parallel_sessions(self, storage):
        """Test when sessions don't overlap."""
        from session_analytics.queries import detect_parallel_sessions

        now = datetime.now()
        # Two non-overlapping sessions
        events = [
            Event(
                id=None,
                uuid="n1",
                timestamp=now - timedelta(hours=5),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="n2",
                timestamp=now - timedelta(hours=4),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            Event(
                id=None,
                uuid="n3",
                timestamp=now - timedelta(hours=2),
                session_id="s2",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="n4",
                timestamp=now - timedelta(hours=1),
                session_id="s2",
                entry_type="tool_use",
                tool_name="Edit",
            ),
        ]
        storage.add_events_batch(events)

        result = detect_parallel_sessions(storage, hours=24, min_overlap_minutes=5)

        assert result["parallel_period_count"] == 0


class TestFindRelatedSessions:
    """Tests for find_related_sessions function."""

    def test_find_by_files(self, storage):
        """Test finding related sessions by shared files."""
        from session_analytics.queries import find_related_sessions

        now = datetime.now()
        events = [
            # Session 1 touches file.py
            Event(
                id=None,
                uuid="r1",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="project",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/path/to/file.py",
            ),
            # Session 2 also touches file.py
            Event(
                id=None,
                uuid="r2",
                timestamp=now - timedelta(hours=1),
                session_id="s2",
                project_path="project",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
        ]
        storage.add_events_batch(events)

        result = find_related_sessions(storage, session_id="s1", method="files", days=7)

        assert result["related_count"] == 1
        assert result["related_sessions"][0]["session_id"] == "s2"

    def test_find_by_commands(self, storage):
        """Test finding related sessions by shared commands."""
        from session_analytics.queries import find_related_sessions

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="c1",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Bash",
                command="make",
            ),
            Event(
                id=None,
                uuid="c2",
                timestamp=now - timedelta(hours=1),
                session_id="s2",
                entry_type="tool_use",
                tool_name="Bash",
                command="make",
            ),
        ]
        storage.add_events_batch(events)

        result = find_related_sessions(storage, session_id="s1", method="commands", days=7)

        assert result["related_count"] == 1

    def test_find_by_temporal(self, storage):
        """Test finding related sessions by temporal proximity."""
        from session_analytics.queries import find_related_sessions

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="t1",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="t2",
                timestamp=now - timedelta(hours=2, minutes=30),
                session_id="s2",
                entry_type="tool_use",
                tool_name="Edit",
            ),
        ]
        storage.add_events_batch(events)

        result = find_related_sessions(storage, session_id="s1", method="temporal", days=7)

        assert result["related_count"] == 1

    def test_invalid_method(self, storage):
        """Test that invalid method returns error."""
        from session_analytics.queries import find_related_sessions

        result = find_related_sessions(storage, session_id="s1", method="invalid", days=7)

        assert "error" in result

    def test_find_by_files_no_files_in_target(self, storage):
        """Test when target session has no file_path values."""
        from session_analytics.queries import find_related_sessions

        now = datetime.now()
        events = [
            # Target session with no file_path (only Bash commands)
            Event(
                id=None,
                uuid="nofile-1",
                timestamp=now - timedelta(hours=1),
                session_id="target-session",
                entry_type="tool_use",
                tool_name="Bash",
                command="git",
            ),
            # Other session with file_path
            Event(
                id=None,
                uuid="hasfile-1",
                timestamp=now - timedelta(hours=2),
                session_id="other-session",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/some/file.py",
            ),
        ]
        storage.add_events_batch(events)

        result = find_related_sessions(storage, session_id="target-session", method="files", days=7)

        # Should return empty related_sessions, not error
        assert "error" not in result
        assert result["related_count"] == 0
        assert result["related_sessions"] == []

    def test_find_by_commands_no_commands_in_target(self, storage):
        """Test when target session has no command values."""
        from session_analytics.queries import find_related_sessions

        now = datetime.now()
        events = [
            # Target session with no commands (only Read/Edit)
            Event(
                id=None,
                uuid="nocmd-1",
                timestamp=now - timedelta(hours=1),
                session_id="target-session",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/file.py",
            ),
            # Other session with commands
            Event(
                id=None,
                uuid="hascmd-1",
                timestamp=now - timedelta(hours=2),
                session_id="other-session",
                entry_type="tool_use",
                tool_name="Bash",
                command="make",
            ),
        ]
        storage.add_events_batch(events)

        result = find_related_sessions(
            storage, session_id="target-session", method="commands", days=7
        )

        # Should return empty related_sessions, not error
        assert "error" not in result
        assert result["related_count"] == 0
        assert result["related_sessions"] == []


class TestGetHandoffContext:
    """Tests for get_handoff_context()."""

    def test_no_recent_sessions(self, storage):
        """Test when no recent sessions exist."""
        from session_analytics.queries import get_handoff_context

        result = get_handoff_context(storage, hours=1)

        assert "error" in result
        assert "No recent sessions" in result["error"]

    def test_specific_session_not_found(self, storage):
        """Test when specified session doesn't exist."""
        from session_analytics.queries import get_handoff_context

        result = get_handoff_context(storage, session_id="nonexistent-session")

        assert "error" in result
        assert "Session not found" in result["error"]

    def test_returns_session_info(self, storage):
        """Test that session info is returned correctly."""
        from session_analytics.queries import get_handoff_context

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="h1",
                timestamp=now - timedelta(hours=1),
                session_id="test-session",
                project_path="/test/project",
                entry_type="user",
                user_message_text="Hello, let's start",
            ),
            Event(
                id=None,
                uuid="h2",
                timestamp=now - timedelta(minutes=30),
                session_id="test-session",
                project_path="/test/project",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/test/file.py",
            ),
            Event(
                id=None,
                uuid="h3",
                timestamp=now - timedelta(minutes=15),
                session_id="test-session",
                project_path="/test/project",
                entry_type="tool_use",
                tool_name="Bash",
                command="git",
            ),
        ]
        storage.add_events_batch(events)

        result = get_handoff_context(storage, session_id="test-session")

        assert result["session_id"] == "test-session"
        assert result["project"] == "/test/project"
        assert "duration_minutes" in result
        assert result["total_events"] == 3

    def test_returns_recent_messages(self, storage):
        """Test that recent user messages are returned."""
        from session_analytics.queries import get_handoff_context

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="m1",
                timestamp=now - timedelta(hours=1),
                session_id="msg-session",
                entry_type="user",
                user_message_text="First message",
            ),
            Event(
                id=None,
                uuid="m2",
                timestamp=now - timedelta(minutes=30),
                session_id="msg-session",
                entry_type="user",
                user_message_text="Second message",
            ),
        ]
        storage.add_events_batch(events)

        result = get_handoff_context(storage, session_id="msg-session", message_limit=5)

        assert len(result["recent_messages"]) == 2
        # Messages should be in reverse chronological order
        assert "Second message" in result["recent_messages"][0]["message"]

    def test_returns_modified_files(self, storage):
        """Test that modified files are returned."""
        from session_analytics.queries import get_handoff_context

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="f1",
                timestamp=now - timedelta(hours=1),
                session_id="file-session",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/src/main.py",
            ),
            Event(
                id=None,
                uuid="f2",
                timestamp=now - timedelta(minutes=30),
                session_id="file-session",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/src/main.py",
            ),
            Event(
                id=None,
                uuid="f3",
                timestamp=now - timedelta(minutes=15),
                session_id="file-session",
                entry_type="tool_use",
                tool_name="Write",
                file_path="/src/new.py",
            ),
        ]
        storage.add_events_batch(events)

        result = get_handoff_context(storage, session_id="file-session")

        assert len(result["modified_files"]) == 2
        # Most edited file should be first
        assert result["modified_files"][0]["file"] == "/src/main.py"
        assert result["modified_files"][0]["touches"] == 2

    def test_auto_selects_most_recent_session(self, storage):
        """Test that most recent session is auto-selected."""
        from session_analytics.queries import get_handoff_context

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="old1",
                timestamp=now - timedelta(hours=2),
                session_id="old-session",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="new1",
                timestamp=now - timedelta(minutes=10),
                session_id="new-session",
                entry_type="tool_use",
                tool_name="Edit",
            ),
        ]
        storage.add_events_batch(events)

        result = get_handoff_context(storage, hours=4)

        assert result["session_id"] == "new-session"


class TestClassifySessions:
    """Tests for classify_sessions function."""

    def test_debugging_classification(self, storage):
        """Test sessions with high error rate are classified as debugging."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = []
        # Create session with >15% error rate (6 tools, 2 errors = 33%)
        for i in range(6):
            events.append(
                Event(
                    id=None,
                    uuid=f"debug-tool-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i),
                    session_id="debug-session",
                    project_path="/debug/project",
                    entry_type="tool_use",
                    tool_name="Bash",
                    tool_id=f"tool-{i}",
                )
            )
        # Add 2 error results
        for i in range(2):
            events.append(
                Event(
                    id=None,
                    uuid=f"debug-error-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i + 10),
                    session_id="debug-session",
                    project_path="/debug/project",
                    entry_type="tool_result",
                    tool_id=f"tool-{i}",
                    is_error=True,
                )
            )
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        assert result["session_count"] >= 1
        # Find debug-session in sessions
        session = next(
            (s for s in result["sessions"] if s["session_id"] == "debug-session"),
            None,
        )
        assert session is not None
        assert session["category"] == "debugging"

    def test_development_classification(self, storage):
        """Test sessions with high edit percentage are classified as development."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = []
        # Create session with >30% Edit tools (4 Edits, 2 other = 67%)
        for i in range(4):
            events.append(
                Event(
                    id=None,
                    uuid=f"dev-edit-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i),
                    session_id="dev-session",
                    project_path="/dev/project",
                    entry_type="tool_use",
                    tool_name="Edit",
                    file_path=f"/file{i}.py",
                )
            )
        events.extend(
            [
                Event(
                    id=None,
                    uuid="dev-read-1",
                    timestamp=now - timedelta(hours=1, minutes=10),
                    session_id="dev-session",
                    project_path="/dev/project",
                    entry_type="tool_use",
                    tool_name="Read",
                ),
                Event(
                    id=None,
                    uuid="dev-bash-1",
                    timestamp=now - timedelta(hours=1, minutes=11),
                    session_id="dev-session",
                    project_path="/dev/project",
                    entry_type="tool_use",
                    tool_name="Bash",
                    command="ls",
                ),
            ]
        )
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        session = next(
            (s for s in result["sessions"] if s["session_id"] == "dev-session"),
            None,
        )
        assert session is not None
        assert session["category"] == "development"

    def test_research_classification(self, storage):
        """Test sessions with Read/search heavy usage are classified as research."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = []
        # Create session with >40% Read+Grep+WebSearch (5 reads, 2 other = 71%)
        for i in range(4):
            events.append(
                Event(
                    id=None,
                    uuid=f"research-read-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i),
                    session_id="research-session",
                    project_path="/research/project",
                    entry_type="tool_use",
                    tool_name="Read",
                )
            )
        events.append(
            Event(
                id=None,
                uuid="research-grep-1",
                timestamp=now - timedelta(hours=1, minutes=5),
                session_id="research-session",
                project_path="/research/project",
                entry_type="tool_use",
                tool_name="Grep",
            )
        )
        events.append(
            Event(
                id=None,
                uuid="research-bash-1",
                timestamp=now - timedelta(hours=1, minutes=6),
                session_id="research-session",
                project_path="/research/project",
                entry_type="tool_use",
                tool_name="Bash",
                command="ls",
            )
        )
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        session = next(
            (s for s in result["sessions"] if s["session_id"] == "research-session"),
            None,
        )
        assert session is not None
        assert session["category"] == "research"

    def test_maintenance_classification(self, storage):
        """Test sessions with git/build commands are classified as maintenance."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = []
        # Create session with >50% git/gh/make commands (5 git, 1 other = 83%)
        for i in range(5):
            events.append(
                Event(
                    id=None,
                    uuid=f"maint-git-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i),
                    session_id="maint-session",
                    project_path="/maint/project",
                    entry_type="tool_use",
                    tool_name="Bash",
                    command="git",
                )
            )
        events.append(
            Event(
                id=None,
                uuid="maint-read-1",
                timestamp=now - timedelta(hours=1, minutes=6),
                session_id="maint-session",
                project_path="/maint/project",
                entry_type="tool_use",
                tool_name="Read",
            )
        )
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        session = next(
            (s for s in result["sessions"] if s["session_id"] == "maint-session"),
            None,
        )
        assert session is not None
        assert session["category"] == "maintenance"

    def test_mixed_classification(self, storage):
        """Test sessions without dominant patterns are classified as mixed."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = [
            # Even mix of different activities - none dominant
            Event(
                id=None,
                uuid="mixed-1",
                timestamp=now - timedelta(hours=1, minutes=1),
                session_id="mixed-session",
                project_path="/mixed/project",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="mixed-2",
                timestamp=now - timedelta(hours=1, minutes=2),
                session_id="mixed-session",
                project_path="/mixed/project",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/file.py",
            ),
            Event(
                id=None,
                uuid="mixed-3",
                timestamp=now - timedelta(hours=1, minutes=3),
                session_id="mixed-session",
                project_path="/mixed/project",
                entry_type="tool_use",
                tool_name="Bash",
                command="python",
            ),
            Event(
                id=None,
                uuid="mixed-4",
                timestamp=now - timedelta(hours=1, minutes=4),
                session_id="mixed-session",
                project_path="/mixed/project",
                entry_type="tool_use",
                tool_name="Bash",
                command="ls",
            ),
            Event(
                id=None,
                uuid="mixed-5",
                timestamp=now - timedelta(hours=1, minutes=5),
                session_id="mixed-session",
                project_path="/mixed/project",
                entry_type="tool_use",
                tool_name="Write",
                file_path="/new.txt",
            ),
        ]
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        session = next(
            (s for s in result["sessions"] if s["session_id"] == "mixed-session"),
            None,
        )
        assert session is not None
        assert session["category"] == "mixed"

    def test_project_filter(self, storage):
        """Test that project filter correctly limits results."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = []
        # Two different projects
        for i in range(6):
            events.append(
                Event(
                    id=None,
                    uuid=f"proj-a-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i),
                    session_id="proj-a-session",
                    project_path="/project-alpha",
                    entry_type="tool_use",
                    tool_name="Edit",
                )
            )
        for i in range(6):
            events.append(
                Event(
                    id=None,
                    uuid=f"proj-b-{i}",
                    timestamp=now - timedelta(hours=2, minutes=i),
                    session_id="proj-b-session",
                    project_path="/project-beta",
                    entry_type="tool_use",
                    tool_name="Read",
                )
            )
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7, project="alpha")

        assert result["session_count"] == 1
        assert result["sessions"][0]["session_id"] == "proj-a-session"

    def test_min_event_threshold(self, storage):
        """Test that sessions with <5 events are excluded."""
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = [
            # Only 3 events - should be excluded
            Event(
                id=None,
                uuid="small-1",
                timestamp=now - timedelta(hours=1),
                session_id="small-session",
                project_path="/small/project",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="small-2",
                timestamp=now - timedelta(hours=1, minutes=1),
                session_id="small-session",
                project_path="/small/project",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            Event(
                id=None,
                uuid="small-3",
                timestamp=now - timedelta(hours=1, minutes=2),
                session_id="small-session",
                project_path="/small/project",
                entry_type="tool_use",
                tool_name="Bash",
                command="ls",
            ),
        ]
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        # Session with only 3 events should be excluded
        assert result["session_count"] == 0

    def test_classification_factors_included(self, storage):
        """Test that classification_factors explains WHY sessions were categorized.

        RFC #49: Without classification_factors, an LLM seeing 'category: debugging'
        cannot explain to the user why it was classified that way.
        """
        from session_analytics.queries import classify_sessions

        now = datetime.now()
        events = []
        # Create session with >15% error rate to trigger debugging classification
        for i in range(6):
            events.append(
                Event(
                    id=None,
                    uuid=f"factors-tool-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i),
                    session_id="factors-session",
                    project_path="/factors/project",
                    entry_type="tool_use",
                    tool_name="Bash",
                    tool_id=f"tool-{i}",
                )
            )
        # Add 2 error results (33% error rate)
        for i in range(2):
            events.append(
                Event(
                    id=None,
                    uuid=f"factors-error-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i + 10),
                    session_id="factors-session",
                    project_path="/factors/project",
                    entry_type="tool_result",
                    tool_id=f"tool-{i}",
                    is_error=True,
                )
            )
        storage.add_events_batch(events)

        result = classify_sessions(storage, days=7)

        session = next(
            (s for s in result["sessions"] if s["session_id"] == "factors-session"),
            None,
        )
        assert session is not None
        assert session["category"] == "debugging"

        # Verify classification_factors exists and explains WHY
        assert "classification_factors" in session
        factors = session["classification_factors"]

        # Should include the trigger that caused this classification
        assert "trigger" in factors
        assert "error_rate" in factors["trigger"] or "error_count" in factors["trigger"]

        # Should include the relevant metrics
        assert "error_rate" in factors
        assert factors["error_rate"] > 15  # Should be ~33%


class TestGetUserJourneyIncludeProjects:
    """Test for get_user_journey with include_projects=False."""

    def test_journey_without_projects(self, storage):
        """Test that include_projects=False excludes project info."""
        from session_analytics.queries import get_user_journey

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="np1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="project-a",
                entry_type="user",
                user_message_text="First message",
            ),
            Event(
                id=None,
                uuid="np2",
                timestamp=now - timedelta(minutes=30),
                session_id="s2",
                project_path="project-b",
                entry_type="user",
                user_message_text="Second message",
            ),
        ]
        storage.add_events_batch(events)

        result = get_user_journey(storage, hours=24, include_projects=False)

        assert result["message_count"] == 2
        assert result["projects_visited"] is None
        assert result["project_switches"] is None
        for event in result["journey"]:
            assert "project" not in event


class TestQueryFileActivity:
    """Tests for file activity queries."""

    def test_basic_file_activity(self, storage):
        """Test basic file activity query."""
        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="f1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="f2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="f3",
                timestamp=now - timedelta(hours=3),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Write",
                file_path="/path/to/new.py",
            ),
        ]
        storage.add_events_batch(events)

        result = query_file_activity(storage, days=7)
        assert result["file_count"] == 2
        assert len(result["files"]) == 2

        # file.py should have 2 operations (1 read, 1 edit)
        file_py = next(f for f in result["files"] if "file.py" in f["file"])
        assert file_py["reads"] == 1
        assert file_py["edits"] == 1
        assert file_py["writes"] == 0
        assert file_py["total"] == 2

    def test_collapse_worktrees(self, storage):
        """Test worktree path collapsing."""
        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="w1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/projects/myrepo/src/main.rs",
            ),
            Event(
                id=None,
                uuid="w2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/projects/myrepo/.worktrees/feature-branch/src/main.rs",
            ),
        ]
        storage.add_events_batch(events)

        # Without collapse, should be 2 files
        result_no_collapse = query_file_activity(storage, days=7, collapse_worktrees=False)
        assert result_no_collapse["file_count"] == 2

        # With collapse, should be 1 file (worktree path collapsed)
        result_collapse = query_file_activity(storage, days=7, collapse_worktrees=True)
        assert result_collapse["file_count"] == 1
        assert result_collapse["files"][0]["total"] == 2


class TestQueryLanguages:
    """Tests for language distribution queries."""

    def test_basic_languages(self, storage):
        """Test basic language distribution."""
        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="l1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="l2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="l3",
                timestamp=now - timedelta(hours=3),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/path/to/code.rs",
            ),
            Event(
                id=None,
                uuid="l4",
                timestamp=now - timedelta(hours=4),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/path/to/doc.md",
            ),
        ]
        storage.add_events_batch(events)

        result = query_languages(storage, days=7)
        assert result["total_operations"] == 4

        langs = {lang["language"]: lang["count"] for lang in result["languages"]}
        assert langs.get("Python") == 2
        assert langs.get("Rust") == 1
        assert langs.get("Markdown") == 1


class TestQueryProjects:
    """Tests for project activity queries."""

    def test_basic_projects(self, storage):
        """Test basic project activity."""
        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="p1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-Users-dev-projects-myapp",
                entry_type="tool_use",
                tool_name="Read",
            ),
            Event(
                id=None,
                uuid="p2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-Users-dev-projects-myapp",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            Event(
                id=None,
                uuid="p3",
                timestamp=now - timedelta(hours=3),
                session_id="s2",
                project_path="-Users-dev-projects-other",
                entry_type="tool_use",
                tool_name="Read",
            ),
        ]
        storage.add_events_batch(events)

        storage.upsert_session(
            Session(
                id="s1",
                project_path="-Users-dev-projects-myapp",
                first_seen=now - timedelta(hours=2),
                last_seen=now - timedelta(hours=1),
                entry_count=2,
            )
        )
        storage.upsert_session(
            Session(
                id="s2",
                project_path="-Users-dev-projects-other",
                first_seen=now - timedelta(hours=3),
                last_seen=now - timedelta(hours=3),
                entry_count=1,
            )
        )

        result = query_projects(storage, days=7)
        assert result["project_count"] == 2

        # project names are extracted from project_path using get_repo_name()
        # which falls back to last component when no known markers found
        projects = {p["name"]: p for p in result["projects"]}
        assert projects["-Users-dev-projects-myapp"]["events"] == 2
        assert projects["-Users-dev-projects-myapp"]["sessions"] == 1
        assert projects["-Users-dev-projects-other"]["events"] == 1


class TestQueryMcpUsage:
    """Tests for MCP usage queries."""

    def test_basic_mcp_usage(self, storage):
        """Test basic MCP usage breakdown."""
        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="m1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="mcp__github__get_issue",
            ),
            Event(
                id=None,
                uuid="m2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="mcp__github__create_pr",
            ),
            Event(
                id=None,
                uuid="m3",
                timestamp=now - timedelta(hours=3),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="mcp__event-bus__publish_event",
            ),
            Event(
                id=None,
                uuid="m4",
                timestamp=now - timedelta(hours=4),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",  # Non-MCP tool, should be ignored
            ),
        ]
        storage.add_events_batch(events)

        result = query_mcp_usage(storage, days=7)
        assert result["total_mcp_calls"] == 3

        servers = {s["server"]: s for s in result["servers"]}
        assert "github" in servers
        assert "event-bus" in servers

        assert servers["github"]["total"] == 2
        github_tools = {t["tool"]: t["count"] for t in servers["github"]["tools"]}
        assert github_tools.get("get_issue") == 1
        assert github_tools.get("create_pr") == 1

        assert servers["event-bus"]["total"] == 1


class TestGetCutoff:
    """Tests for get_cutoff() helper function."""

    def test_cutoff_days_only(self):
        """Test cutoff with days parameter."""
        cutoff = get_cutoff(days=7)
        expected = datetime.now() - timedelta(days=7)
        # Allow 1 second tolerance for test execution time
        assert abs((cutoff - expected).total_seconds()) < 1

    def test_cutoff_hours_only(self):
        """Test cutoff with hours parameter (days=0)."""
        cutoff = get_cutoff(days=0, hours=12)
        expected = datetime.now() - timedelta(hours=12)
        assert abs((cutoff - expected).total_seconds()) < 1

    def test_cutoff_days_and_hours_combined(self):
        """Test cutoff with both days and hours."""
        cutoff = get_cutoff(days=1, hours=6)
        expected = datetime.now() - timedelta(hours=30)  # 24 + 6 = 30 hours
        assert abs((cutoff - expected).total_seconds()) < 1

    def test_cutoff_fractional_days(self):
        """Test cutoff with fractional days (e.g., 0.5 = 12 hours)."""
        cutoff = get_cutoff(days=0.5)
        expected = datetime.now() - timedelta(hours=12)
        assert abs((cutoff - expected).total_seconds()) < 1

    def test_cutoff_default_values(self):
        """Test cutoff with default parameters (7 days, 0 hours)."""
        cutoff = get_cutoff()
        expected = datetime.now() - timedelta(days=7)
        assert abs((cutoff - expected).total_seconds()) < 1


class TestNormalizeDatetime:
    """Tests for normalize_datetime() helper function."""

    def test_naive_datetime_unchanged(self):
        """Test that naive datetime is returned unchanged."""
        from session_analytics.queries import normalize_datetime

        naive_dt = datetime(2024, 1, 15, 12, 30, 45)
        result = normalize_datetime(naive_dt)
        assert result == naive_dt
        assert result.tzinfo is None

    def test_utc_timezone_stripped(self):
        """Test that UTC timezone is stripped."""
        from datetime import timezone

        from session_analytics.queries import normalize_datetime

        aware_dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)
        result = normalize_datetime(aware_dt)

        assert result.tzinfo is None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 12
        assert result.minute == 30
        assert result.second == 45

    def test_non_utc_timezone_stripped(self):
        """Test that non-UTC timezone is stripped, preserving local time values.

        We intentionally preserve the time values (hour/minute/second) rather
        than converting to UTC. This is correct because session timestamps in
        SQLite are stored as naive local time - a commit at 12:30 local time
        should correlate with sessions running at 12:00-13:00 local time,
        regardless of the timezone offset attached to the commit timestamp.
        """
        from datetime import timezone

        from session_analytics.queries import normalize_datetime

        # Create a timezone offset (e.g., +05:30)
        tz_offset = timezone(timedelta(hours=5, minutes=30))
        aware_dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=tz_offset)
        result = normalize_datetime(aware_dt)

        assert result.tzinfo is None
        # The time values should remain the same (we strip, not convert)
        assert result.hour == 12
        assert result.minute == 30

    def test_comparison_after_normalization(self):
        """Test that normalized datetimes can be compared safely."""
        from datetime import timezone

        from session_analytics.queries import normalize_datetime

        naive_dt = datetime(2024, 1, 15, 12, 30, 45)
        aware_dt = datetime(2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc)

        # Direct comparison would raise TypeError
        # After normalization, comparison should work
        normalized_aware = normalize_datetime(aware_dt)
        normalized_naive = normalize_datetime(naive_dt)

        assert normalized_aware == normalized_naive


class TestQueryAgentActivity:
    """Tests for query_agent_activity().

    RFC #41: Tracks agent activity from Task tool invocations,
    distinguishing work done by agents vs main session.
    """

    def test_main_session_only(self, storage):
        """Test with only main session events (no agent_id)."""
        now = datetime.now()
        storage.add_event(
            Event(
                id=None,
                uuid="main-1",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="assistant",
                input_tokens=100,
                output_tokens=50,
                agent_id=None,  # Main session
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="main-2",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="tool_use",
                tool_name="Read",
                input_tokens=None,  # tool_use has no tokens
                agent_id=None,
            )
        )

        result = query_agent_activity(storage, days=1)

        assert result["days"] == 1
        assert result["main_session"] is not None
        assert result["main_session"]["event_count"] == 2
        assert result["main_session"]["input_tokens"] == 100
        assert result["agents"] == []
        assert result["summary"]["agent_count"] == 0
        assert result["summary"]["agent_token_percentage"] == 0

    def test_agent_and_main_session(self, storage):
        """Test with both main session and agent events."""
        now = datetime.now()

        # Main session events
        storage.add_event(
            Event(
                id=None,
                uuid="main-1",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="assistant",
                input_tokens=200,
                output_tokens=100,
                agent_id=None,
            )
        )

        # Agent events
        storage.add_event(
            Event(
                id=None,
                uuid="agent-1",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="assistant",
                input_tokens=300,
                output_tokens=150,
                agent_id="a123456",
                is_sidechain=True,
            )
        )
        storage.add_event(
            Event(
                id=None,
                uuid="agent-2",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="tool_use",
                tool_name="Bash",
                agent_id="a123456",
                is_sidechain=True,
            )
        )

        result = query_agent_activity(storage, days=1)

        # Check main session
        assert result["main_session"]["event_count"] == 1
        assert result["main_session"]["input_tokens"] == 200

        # Check agent
        assert len(result["agents"]) == 1
        agent = result["agents"][0]
        assert agent["agent_id"] == "a123456"
        assert agent["event_count"] == 2
        assert agent["tool_use_count"] == 1
        assert agent["input_tokens"] == 300
        assert agent["sidechain_events"] == 2  # Both agent events have is_sidechain=True

        # Check summary
        assert result["summary"]["agent_count"] == 1
        assert result["summary"]["total_agent_tokens"] == 300
        assert result["summary"]["total_main_tokens"] == 200
        # 300 / (300 + 200) = 60%
        assert result["summary"]["agent_token_percentage"] == 60.0

    def test_multiple_agents(self, storage):
        """Test with multiple agents."""
        now = datetime.now()

        # Agent A
        storage.add_event(
            Event(
                id=None,
                uuid="agent-a-1",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="assistant",
                input_tokens=400,
                agent_id="agent-a",
            )
        )

        # Agent B
        storage.add_event(
            Event(
                id=None,
                uuid="agent-b-1",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="assistant",
                input_tokens=100,
                agent_id="agent-b",
            )
        )

        result = query_agent_activity(storage, days=1)

        # Agents should be ordered by input_tokens DESC
        assert len(result["agents"]) == 2
        assert result["agents"][0]["agent_id"] == "agent-a"
        assert result["agents"][0]["input_tokens"] == 400
        assert result["agents"][1]["agent_id"] == "agent-b"
        assert result["agents"][1]["input_tokens"] == 100

        assert result["summary"]["agent_count"] == 2
        assert result["summary"]["total_agent_tokens"] == 500

    def test_top_tools_per_agent(self, storage):
        """Test that top tools are calculated per agent."""
        now = datetime.now()

        # Agent with multiple tool uses
        storage.add_event(
            Event(
                id=None,
                uuid="agent-assist",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="assistant",
                input_tokens=100,
                agent_id="agent-1",
            )
        )
        for i, tool in enumerate(["Read", "Read", "Read", "Edit", "Bash"]):
            storage.add_event(
                Event(
                    id=None,
                    uuid=f"agent-tool-{i}",
                    timestamp=now,
                    session_id="s1",
                    project_path="test-project",
                    entry_type="tool_use",
                    tool_name=tool,
                    agent_id="agent-1",
                )
            )

        result = query_agent_activity(storage, days=1)

        assert len(result["agents"]) == 1
        agent = result["agents"][0]
        assert "top_tools" in agent
        assert len(agent["top_tools"]) == 3  # Read, Edit, Bash

        # Read should be first (count=3)
        assert agent["top_tools"][0]["tool"] == "Read"
        assert agent["top_tools"][0]["count"] == 3

    def test_project_filter(self, storage):
        """Test project filter works."""
        now = datetime.now()

        # Project A events
        storage.add_event(
            Event(
                id=None,
                uuid="project-a",
                timestamp=now,
                session_id="s1",
                project_path="project-a",
                entry_type="assistant",
                input_tokens=100,
                agent_id="agent-1",
            )
        )

        # Project B events
        storage.add_event(
            Event(
                id=None,
                uuid="project-b",
                timestamp=now,
                session_id="s2",
                project_path="project-b",
                entry_type="assistant",
                input_tokens=200,
                agent_id="agent-2",
            )
        )

        result = query_agent_activity(storage, days=1, project="project-a")

        # Should only see agent-1 from project-a
        assert len(result["agents"]) == 1
        assert result["agents"][0]["agent_id"] == "agent-1"

    def test_empty_results(self, storage):
        """Test with no matching events."""
        result = query_agent_activity(storage, days=1)

        assert result["main_session"] is None
        assert result["agents"] == []
        assert result["summary"]["agent_count"] == 0
        assert result["summary"]["agent_token_percentage"] == 0

    def test_zero_division_protection(self, storage):
        """Test that percentage calculation handles zero tokens."""
        now = datetime.now()

        # Event with zero tokens
        storage.add_event(
            Event(
                id=None,
                uuid="zero-tokens",
                timestamp=now,
                session_id="s1",
                project_path="test-project",
                entry_type="tool_use",
                tool_name="Read",
                input_tokens=None,  # No tokens
                agent_id=None,
            )
        )

        result = query_agent_activity(storage, days=1)

        # Should not raise ZeroDivisionError
        assert result["summary"]["agent_token_percentage"] == 0


class TestQueryErrorDetails:
    """Tests for query_error_details().

    RFC #60: Shows which specific parameters (patterns, commands, files)
    caused tool errors, enabling drill-down from aggregate error counts.
    """

    def test_basic_error_aggregation(self, storage):
        """Test basic error aggregation by tool and parameter."""
        import json

        now = datetime.now()

        # Create tool_use events with tool_input_json
        events = [
            # Glob error with pattern
            Event(
                id=None,
                uuid="glob-use-1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Glob",
                tool_id="tool-glob-1",
                tool_input_json=json.dumps({"pattern": "*.py", "path": "/src"}),
            ),
            Event(
                id=None,
                uuid="glob-result-1",
                timestamp=now - timedelta(hours=1, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-glob-1",
                is_error=True,
            ),
            # Another Glob error with same pattern (should aggregate)
            Event(
                id=None,
                uuid="glob-use-2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Glob",
                tool_id="tool-glob-2",
                tool_input_json=json.dumps({"pattern": "*.py", "path": "/src"}),
            ),
            Event(
                id=None,
                uuid="glob-result-2",
                timestamp=now - timedelta(hours=2, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-glob-2",
                is_error=True,
            ),
            # Bash error with command
            Event(
                id=None,
                uuid="bash-use-1",
                timestamp=now - timedelta(hours=3),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Bash",
                tool_id="tool-bash-1",
                command="git",
                command_args="status",
            ),
            Event(
                id=None,
                uuid="bash-result-1",
                timestamp=now - timedelta(hours=3, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-bash-1",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = query_error_details(storage, days=7)

        assert result["days"] == 7
        assert result["total_errors"] == 3
        assert "Glob" in result["errors_by_tool"]
        assert "Bash" in result["errors_by_tool"]

        # Glob should have aggregated the 2 errors with same pattern
        glob_errors = result["errors_by_tool"]["Glob"]
        assert len(glob_errors) == 1
        assert glob_errors[0]["param_type"] == "pattern"
        assert glob_errors[0]["param_value"] == "*.py"
        assert glob_errors[0]["error_count"] == 2

        # Bash should have 1 error
        bash_errors = result["errors_by_tool"]["Bash"]
        assert len(bash_errors) == 1
        assert bash_errors[0]["param_type"] == "command"
        assert bash_errors[0]["param_value"] == "git"
        assert bash_errors[0]["error_count"] == 1

    def test_tool_filter(self, storage):
        """Test filtering errors by specific tool."""
        import json

        now = datetime.now()

        events = [
            # Glob error
            Event(
                id=None,
                uuid="glob-use",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Glob",
                tool_id="tool-glob",
                tool_input_json=json.dumps({"pattern": "*.rs"}),
            ),
            Event(
                id=None,
                uuid="glob-result",
                timestamp=now - timedelta(hours=1, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-glob",
                is_error=True,
            ),
            # Bash error
            Event(
                id=None,
                uuid="bash-use",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Bash",
                tool_id="tool-bash",
                command="make",
            ),
            Event(
                id=None,
                uuid="bash-result",
                timestamp=now - timedelta(hours=2, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-bash",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        # Filter to only Glob errors
        result = query_error_details(storage, days=7, tool="Glob")

        assert result["tool_filter"] == "Glob"
        assert result["total_errors"] == 1
        assert "Glob" in result["errors_by_tool"]
        assert "Bash" not in result["errors_by_tool"]

    def test_limit_parameter(self, storage):
        """Test that limit parameter caps errors per tool."""
        import json

        now = datetime.now()

        events = []
        # Create 5 different Glob errors with different patterns
        for i in range(5):
            events.extend(
                [
                    Event(
                        id=None,
                        uuid=f"glob-use-{i}",
                        timestamp=now - timedelta(hours=i),
                        session_id="s1",
                        project_path="-test-project",
                        entry_type="tool_use",
                        tool_name="Glob",
                        tool_id=f"tool-glob-{i}",
                        tool_input_json=json.dumps({"pattern": f"pattern-{i}"}),
                    ),
                    Event(
                        id=None,
                        uuid=f"glob-result-{i}",
                        timestamp=now - timedelta(hours=i, seconds=1),
                        session_id="s1",
                        project_path="-test-project",
                        entry_type="tool_result",
                        tool_id=f"tool-glob-{i}",
                        is_error=True,
                    ),
                ]
            )
        storage.add_events_batch(events)

        # Limit to 2 per tool
        result = query_error_details(storage, days=7, limit=2)

        # Should only have 2 errors in the details, but total should reflect all
        assert len(result["errors_by_tool"]["Glob"]) == 2
        assert result["tool_totals"]["Glob"] == 5

    def test_file_path_errors(self, storage):
        """Test that file operation errors show file_path."""
        now = datetime.now()

        events = [
            Event(
                id=None,
                uuid="edit-use",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Edit",
                tool_id="tool-edit",
                file_path="/path/to/missing.py",
            ),
            Event(
                id=None,
                uuid="edit-result",
                timestamp=now - timedelta(hours=1, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-edit",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = query_error_details(storage, days=7)

        assert "Edit" in result["errors_by_tool"]
        edit_errors = result["errors_by_tool"]["Edit"]
        assert len(edit_errors) == 1
        assert edit_errors[0]["param_type"] == "file_path"
        assert edit_errors[0]["param_value"] == "/path/to/missing.py"

    def test_grep_pattern_with_search_path(self, storage):
        """Test that Grep errors include search_path when available."""
        import json

        now = datetime.now()

        events = [
            Event(
                id=None,
                uuid="grep-use",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Grep",
                tool_id="tool-grep",
                tool_input_json=json.dumps({"pattern": "TODO", "path": "/src"}),
            ),
            Event(
                id=None,
                uuid="grep-result",
                timestamp=now - timedelta(hours=1, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-grep",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = query_error_details(storage, days=7)

        grep_errors = result["errors_by_tool"]["Grep"]
        assert len(grep_errors) == 1
        assert grep_errors[0]["param_type"] == "pattern"
        assert grep_errors[0]["param_value"] == "TODO"
        assert grep_errors[0]["search_path"] == "/src"

    def test_no_errors(self, storage):
        """Test with no errors in the database."""
        result = query_error_details(storage, days=7)

        assert result["total_errors"] == 0
        assert result["errors_by_tool"] == {}

    def test_days_filter(self, storage):
        """Test that days filter excludes old errors."""
        import json

        now = datetime.now()

        events = [
            # Recent error (1 hour ago)
            Event(
                id=None,
                uuid="recent-use",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Glob",
                tool_id="tool-recent",
                tool_input_json=json.dumps({"pattern": "recent"}),
            ),
            Event(
                id=None,
                uuid="recent-result",
                timestamp=now - timedelta(hours=1, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-recent",
                is_error=True,
            ),
            # Old error (10 days ago)
            Event(
                id=None,
                uuid="old-use",
                timestamp=now - timedelta(days=10),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_use",
                tool_name="Glob",
                tool_id="tool-old",
                tool_input_json=json.dumps({"pattern": "old"}),
            ),
            Event(
                id=None,
                uuid="old-result",
                timestamp=now - timedelta(days=10, seconds=1),
                session_id="s1",
                project_path="-test-project",
                entry_type="tool_result",
                tool_id="tool-old",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        # 7 days should only get recent error
        result = query_error_details(storage, days=7)
        assert result["total_errors"] == 1
        assert result["errors_by_tool"]["Glob"][0]["param_value"] == "recent"

        # 30 days should get both errors
        result_30 = query_error_details(storage, days=30)
        assert result_30["total_errors"] == 2

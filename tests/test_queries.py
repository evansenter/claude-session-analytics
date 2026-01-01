"""Tests for the query implementations."""

from datetime import datetime, timedelta

from session_analytics.queries import (
    ensure_fresh_data,
    get_cutoff,
    query_commands,
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

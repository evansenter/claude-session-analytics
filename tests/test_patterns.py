"""Tests for the pattern detection module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from session_analytics.patterns import (
    compute_all_patterns,
    compute_command_patterns,
    compute_permission_gaps,
    compute_sequence_patterns,
    compute_tool_frequency_patterns,
    get_insights,
    load_allowed_commands,
    sample_sequences,
)
from session_analytics.storage import Event

# Uses fixtures from conftest.py: storage, pattern_storage


class TestToolFrequencyPatterns:
    """Tests for tool frequency pattern detection."""

    def test_compute_tool_frequency(self, pattern_storage):
        """Test computing tool frequency patterns."""
        patterns = compute_tool_frequency_patterns(pattern_storage, days=7)

        # Should have patterns for Read, Edit, Bash
        pattern_keys = {p.pattern_key for p in patterns}
        assert "Read" in pattern_keys
        assert "Edit" in pattern_keys
        assert "Bash" in pattern_keys

    def test_frequency_counts(self, pattern_storage):
        """Test that frequency counts are accurate."""
        patterns = compute_tool_frequency_patterns(pattern_storage, days=7)
        pattern_dict = {p.pattern_key: p.count for p in patterns}

        assert pattern_dict["Read"] == 3
        assert pattern_dict["Edit"] == 3
        assert pattern_dict["Bash"] == 6  # 1 git + 5 make


class TestCommandPatterns:
    """Tests for command pattern detection."""

    def test_compute_command_patterns(self, pattern_storage):
        """Test computing command patterns."""
        patterns = compute_command_patterns(pattern_storage, days=7)

        pattern_dict = {p.pattern_key: p.count for p in patterns}
        assert pattern_dict.get("git", 0) == 1
        assert pattern_dict.get("make", 0) == 5


class TestSequencePatterns:
    """Tests for sequence pattern detection."""

    def test_compute_sequences(self, pattern_storage):
        """Test computing sequence patterns."""
        patterns = compute_sequence_patterns(
            pattern_storage, days=7, sequence_length=2, min_count=2
        )

        # Should find Read -> Edit pattern (occurs 3 times)
        pattern_keys = {p.pattern_key for p in patterns}
        assert "Read → Edit" in pattern_keys

    def test_sequence_counts(self, pattern_storage):
        """Test that sequence counts are accurate."""
        patterns = compute_sequence_patterns(
            pattern_storage, days=7, sequence_length=2, min_count=1
        )

        pattern_dict = {p.pattern_key: p.count for p in patterns}
        assert pattern_dict["Read → Edit"] == 3

    def test_min_count_filter(self, pattern_storage):
        """Test that min_count filter works."""
        # With min_count=5, should have no sequences
        patterns = compute_sequence_patterns(
            pattern_storage, days=7, sequence_length=2, min_count=5
        )
        assert len(patterns) == 0


class TestPermissionGaps:
    """Tests for permission gap detection."""

    def test_load_allowed_commands_missing_file(self):
        """Test loading allowed commands from non-existent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "nonexistent.json"
            base_commands, glob_patterns = load_allowed_commands(missing_path)
            assert base_commands == set()
            assert glob_patterns == []

    def test_load_allowed_commands(self):
        """Test loading allowed commands from settings.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": ["Bash(git:*)", "Bash(make:*)"]}}')
            base_commands, glob_patterns = load_allowed_commands(settings_path)
            assert "git" in base_commands
            assert "make" in base_commands

    def test_compute_permission_gaps(self, pattern_storage):
        """Test computing permission gaps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty settings.json
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": []}}')

            patterns = compute_permission_gaps(
                pattern_storage, days=7, threshold=3, settings_path=settings_path
            )

            # Should find make (5 uses) but maybe not git (1 use) depending on threshold
            pattern_keys = {p.pattern_key for p in patterns}
            assert "make" in pattern_keys

    def test_permission_gaps_respects_allowed(self, pattern_storage):
        """Test that allowed commands are not reported as gaps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": ["Bash(make:*)"]}}')

            patterns = compute_permission_gaps(
                pattern_storage, days=7, threshold=1, settings_path=settings_path
            )

            # make is allowed, so should only find git
            pattern_keys = {p.pattern_key for p in patterns}
            assert "make" not in pattern_keys
            assert "git" in pattern_keys

    def test_load_allowed_commands_extracts_base_from_subcommands(self):
        """Test that subcommand patterns extract the base command.

        Patterns like Bash(gh pr view:*) should extract 'gh' as the base command,
        so that 'gh' isn't reported as a gap when subcommand patterns exist.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(
                '{"permissions": {"allow": ['
                '"Bash(gh pr view:*)", '
                '"Bash(gh issue list:*)", '
                '"Bash(git status:*)", '
                '"Bash(cargo build:*)"'
                "]}}"
            )
            base_commands, glob_patterns = load_allowed_commands(settings_path)

            # Should extract base commands, not full subcommands
            assert "gh" in base_commands
            assert "git" in base_commands
            assert "cargo" in base_commands

            # Should NOT contain full subcommand strings
            assert "gh pr view" not in base_commands
            assert "git status" not in base_commands

    def test_permission_gaps_filters_subcommand_patterns(self, pattern_storage):
        """Test that gaps are filtered when subcommand patterns exist.

        If settings.json has Bash(gh pr view:*), then 'gh' should not be
        reported as a permission gap even though it's used frequently.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            # Only git subcommand patterns configured, make is NOT configured
            settings_path.write_text(
                '{"permissions": {"allow": ["Bash(git status:*)", "Bash(git diff:*)"]}}'
            )

            patterns = compute_permission_gaps(
                pattern_storage, days=7, threshold=1, settings_path=settings_path
            )

            pattern_keys = {p.pattern_key for p in patterns}
            # git has subcommand patterns, should be filtered out
            assert "git" not in pattern_keys
            # make has no patterns, should still be a gap
            assert "make" in pattern_keys

    def test_load_allowed_commands_handles_glob_patterns(self):
        """Test that glob patterns (without :*) are handled correctly.

        Patterns like Bash(make*) should be recognized and used for
        fnmatch-based matching.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(
                '{"permissions": {"allow": ['
                '"Bash(make*)", '
                '"Bash(./scripts/*.sh:*)", '
                '"Bash(cargo)"'
                "]}}"
            )
            base_commands, glob_patterns = load_allowed_commands(settings_path)

            # Should extract base commands
            assert "make" in base_commands
            assert "cargo" in base_commands

            # Glob patterns should be stored for fnmatch
            assert "make*" in glob_patterns
            assert "cargo" in glob_patterns

    def test_permission_gaps_uses_fnmatch(self, pattern_storage):
        """Test that permission gaps uses fnmatch for glob pattern matching.

        If settings has Bash(make*), then 'make' should NOT be reported
        as a permission gap because it matches the glob pattern.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            # Use glob pattern without :*
            settings_path.write_text('{"permissions": {"allow": ["Bash(make*)"]}}')

            patterns = compute_permission_gaps(
                pattern_storage, days=7, threshold=1, settings_path=settings_path
            )

            pattern_keys = {p.pattern_key for p in patterns}
            # make should be filtered out by fnmatch against "make*"
            assert "make" not in pattern_keys
            # git has no matching pattern, should still be a gap
            assert "git" in pattern_keys


class TestComputeAllPatterns:
    """Tests for computing all patterns."""

    def test_compute_all_patterns(self, pattern_storage):
        """Test computing all pattern types."""
        stats = compute_all_patterns(pattern_storage, days=7)

        assert stats["tool_frequency_patterns"] > 0
        assert stats["command_patterns"] > 0
        assert stats["total_patterns"] > 0


class TestGetInsights:
    """Tests for the get_insights function."""

    def test_get_insights(self, pattern_storage):
        """Test getting insights."""
        insights = get_insights(pattern_storage, refresh=True, days=7)

        assert "tool_frequency" in insights
        assert "command_frequency" in insights
        assert "sequences" in insights
        assert "permission_gaps" in insights
        assert "summary" in insights

    def test_insights_summary(self, pattern_storage):
        """Test that insights include summary stats."""
        insights = get_insights(pattern_storage, refresh=True, days=7)

        assert "total_tools" in insights["summary"]
        assert "total_commands" in insights["summary"]
        assert "total_sequences" in insights["summary"]


class TestSampleSequences:
    """Tests for the sample_sequences function (Phase 2: N-gram Sampling)."""

    def test_sample_sequences_basic(self, pattern_storage):
        """Test sampling a known sequence pattern."""
        result = sample_sequences(pattern_storage, pattern="Read → Edit", days=7)

        assert result["pattern"] == "Read → Edit"
        assert result["parsed_tools"] == ["Read", "Edit"]
        assert result["total_occurrences"] == 3  # 3 Read -> Edit sequences
        assert result["sample_count"] <= 5  # Default sample size

    def test_sample_sequences_comma_separator(self, pattern_storage):
        """Test that comma separator also works."""
        result = sample_sequences(pattern_storage, pattern="Read,Edit", days=7)

        assert result["parsed_tools"] == ["Read", "Edit"]
        assert result["total_occurrences"] == 3

    def test_sample_sequences_with_context(self, pattern_storage):
        """Test that context events are included."""
        result = sample_sequences(pattern_storage, pattern="Read → Edit", context_events=1, days=7)

        # Each sample should have events
        for sample in result["samples"]:
            assert "events" in sample
            assert len(sample["events"]) >= 2  # At least the matched pattern

    def test_sample_sequences_limits_count(self, pattern_storage):
        """Test that count parameter limits samples."""
        result = sample_sequences(pattern_storage, pattern="Read → Edit", count=1, days=7)

        assert result["sample_count"] == 1

    def test_sample_sequences_no_match(self, pattern_storage):
        """Test with a pattern that doesn't exist."""
        result = sample_sequences(pattern_storage, pattern="Write → Grep", days=7)

        assert result["total_occurrences"] == 0
        assert result["sample_count"] == 0
        assert result["samples"] == []

    def test_sample_sequences_invalid_pattern(self, storage):
        """Test with an invalid single-tool pattern."""
        result = sample_sequences(storage, pattern="Read", days=7)

        assert "error" in result
        assert result["total_occurrences"] == 0

    def test_sample_sequences_includes_file_paths(self, storage):
        """Test that file paths are included when available."""
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
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
        ]
        storage.add_events_batch(events)

        result = sample_sequences(storage, pattern="Read → Edit", days=7)

        assert result["total_occurrences"] == 1
        assert result["sample_count"] == 1
        sample_events = result["samples"][0]["events"]
        assert any(e.get("file") == "/path/to/file.py" for e in sample_events)

    def test_sample_sequences_includes_commands(self, storage):
        """Test that Bash commands are included when available."""
        now = datetime.now()

        events = [
            Event(
                id=None,
                uuid="c1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Bash",
                command="git",
            ),
            Event(
                id=None,
                uuid="c2",
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Bash",
                command="make",
            ),
        ]
        storage.add_events_batch(events)

        result = sample_sequences(storage, pattern="Bash → Bash", days=7)

        assert result["total_occurrences"] == 1
        sample_events = result["samples"][0]["events"]
        commands = [e.get("command") for e in sample_events if e.get("command")]
        assert "git" in commands
        assert "make" in commands

    def test_sample_sequences_marks_match_events(self, pattern_storage):
        """Test that matched events are marked with is_match=True."""
        result = sample_sequences(pattern_storage, pattern="Read → Edit", context_events=1, days=7)

        for sample in result["samples"]:
            matched_events = [e for e in sample["events"] if e.get("is_match")]
            # Should have exactly 2 matched events (Read and Edit)
            assert len(matched_events) == 2
            tools = [e["tool"] for e in matched_events]
            assert tools == ["Read", "Edit"]


class TestAnalyzeFailures:
    """Tests for the analyze_failures function (Phase 4: Failure Analysis)."""

    def test_analyze_failures_basic(self, storage):
        """Test basic failure analysis with errors."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            # Tool use followed by error result
            Event(
                id=None,
                uuid="e1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
                tool_id="t1",
            ),
            Event(
                id=None,
                uuid="e2",
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="s1",
                entry_type="tool_result",
                tool_id="t1",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        assert result["total_errors"] == 1
        assert result["sessions_with_errors"] == 1

    def test_analyze_failures_no_errors(self, storage):
        """Test when there are no errors."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="ok1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        assert result["total_errors"] == 0
        assert result["sessions_with_errors"] == 0

    def test_rework_detection(self, storage):
        """Test detection of rework patterns (multiple edits to same file)."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        # 4 edits to the same file within 10 minutes - should be detected as rework
        events = [
            Event(
                id=None,
                uuid="r1",
                timestamp=now - timedelta(minutes=10),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="r2",
                timestamp=now - timedelta(minutes=8),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="r3",
                timestamp=now - timedelta(minutes=6),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
            Event(
                id=None,
                uuid="r4",
                timestamp=now - timedelta(minutes=4),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file.py",
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7, rework_window_minutes=15)

        rework = result["rework_patterns"]
        assert rework["instances_detected"] >= 1
        assert len(rework["examples"]) >= 1
        assert rework["examples"][0]["edit_count"] >= 3

    def test_rework_not_detected_different_files(self, storage):
        """Test that edits to different files aren't counted as rework."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            Event(
                id=None,
                uuid="d1",
                timestamp=now - timedelta(minutes=3),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file1.py",
            ),
            Event(
                id=None,
                uuid="d2",
                timestamp=now - timedelta(minutes=2),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file2.py",
            ),
            Event(
                id=None,
                uuid="d3",
                timestamp=now - timedelta(minutes=1),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/path/to/file3.py",
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        # Different files shouldn't count as rework
        assert result["rework_patterns"]["instances_detected"] == 0

    def test_analyze_failures_error_examples(self, storage):
        """Test that error_examples provides drill-down to specific failing commands/files.

        RFC #49: When errors_by_tool shows 'Bash: 5 errors', error_examples should
        reveal WHICH commands failed, enabling actionable diagnosis.
        """
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            # Bash error with command
            Event(
                id=None,
                uuid="bash-use-1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Bash",
                tool_id="bash-1",
                command="make test",
            ),
            Event(
                id=None,
                uuid="bash-result-1",
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="s1",
                entry_type="tool_result",
                tool_id="bash-1",
                is_error=True,
            ),
            # Read error with file_path
            Event(
                id=None,
                uuid="read-use-1",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
                tool_id="read-1",
                file_path="/nonexistent/file.py",
            ),
            Event(
                id=None,
                uuid="read-result-1",
                timestamp=now - timedelta(hours=2, minutes=-1),
                session_id="s1",
                entry_type="tool_result",
                tool_id="read-1",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        # Verify error_examples exists
        assert "error_examples" in result

        # Bash errors should include the failing command
        bash_examples = result["error_examples"].get("Bash", [])
        assert len(bash_examples) >= 1
        assert any(ex.get("command") == "make test" for ex in bash_examples)

        # Read errors should include the failing file
        read_examples = result["error_examples"].get("Read", [])
        assert len(read_examples) >= 1
        assert any(ex.get("file") == "/nonexistent/file.py" for ex in read_examples)


class TestAnalyzeTrends:
    """Tests for the analyze_trends function (Phase 7: Trend Analysis)."""

    def test_empty_database(self, storage):
        """Test with empty database."""
        from session_analytics.patterns import analyze_trends

        result = analyze_trends(storage, days=7)

        assert result["days"] == 7
        assert result["compare_to"] == "previous"
        assert "current_period" in result
        assert "previous_period" in result
        assert "metrics" in result
        assert result["metrics"]["events"]["current"] == 0
        assert result["metrics"]["events"]["previous"] == 0

    def test_trend_metrics(self, storage):
        """Test that trends are calculated correctly."""
        from session_analytics.patterns import analyze_trends

        now = datetime.now()

        # Add events in current period
        current_events = [
            Event(
                id=None,
                uuid=f"c{i}",
                timestamp=now - timedelta(days=2),
                session_id="current-session",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(10)
        ]

        # Add events in previous period
        previous_events = [
            Event(
                id=None,
                uuid=f"p{i}",
                timestamp=now - timedelta(days=10),
                session_id="previous-session",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(5)
        ]

        storage.add_events_batch(current_events + previous_events)

        result = analyze_trends(storage, days=7)

        # Current period should have 10 events, previous should have 5
        assert result["metrics"]["events"]["current"] == 10
        assert result["metrics"]["events"]["previous"] == 5
        assert result["metrics"]["events"]["direction"] == "up"
        assert result["metrics"]["events"]["change_pct"] == 100.0

    def test_tool_changes_included(self, storage):
        """Test that tool-specific changes are included."""
        from session_analytics.patterns import analyze_trends

        now = datetime.now()

        events = [
            Event(
                id=None,
                uuid="t1",
                timestamp=now - timedelta(days=2),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Edit",
            ),
            Event(
                id=None,
                uuid="t2",
                timestamp=now - timedelta(days=10),
                session_id="s2",
                entry_type="tool_use",
                tool_name="Read",
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_trends(storage, days=7)

        assert "tool_changes" in result
        assert len(result["tool_changes"]) >= 1

    def test_compare_to_previous(self, storage):
        """Test compare_to='previous' mode."""
        from session_analytics.patterns import analyze_trends

        result = analyze_trends(storage, days=7, compare_to="previous")

        assert result["compare_to"] == "previous"

    def test_compare_to_same_last_month(self, storage):
        """Test compare_to='same_last_month' mode compares to same week last month."""
        from session_analytics.patterns import analyze_trends

        now = datetime.now()

        # Add events in current week
        current_events = [
            Event(
                id=None,
                uuid=f"slm-c{i}",
                timestamp=now - timedelta(days=2),
                session_id="current-session",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(10)
        ]

        # Add events in same week last month (~28 days ago)
        last_month_events = [
            Event(
                id=None,
                uuid=f"slm-p{i}",
                timestamp=now - timedelta(days=30),
                session_id="last-month-session",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(8)
        ]

        storage.add_events_batch(current_events + last_month_events)

        result = analyze_trends(storage, days=7, compare_to="same_last_month")

        assert result["compare_to"] == "same_last_month"
        # Should compare current 7 days to 7 days starting ~28 days ago
        assert result["metrics"]["events"]["current"] == 10
        assert result["metrics"]["events"]["previous"] == 8
        assert result["metrics"]["events"]["direction"] == "up"

    def test_trend_unchanged_threshold(self, storage):
        """Test that changes within +/- 5% are marked as 'unchanged'."""
        from session_analytics.patterns import analyze_trends

        now = datetime.now()

        # Add 100 events in current period
        current_events = [
            Event(
                id=None,
                uuid=f"unch-c{i}",
                timestamp=now - timedelta(days=2),
                session_id="current-session",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(100)
        ]

        # Add 98 events in previous period (2% less - should be unchanged)
        previous_events = [
            Event(
                id=None,
                uuid=f"unch-p{i}",
                timestamp=now - timedelta(days=10),
                session_id="previous-session",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(98)
        ]

        storage.add_events_batch(current_events + previous_events)

        result = analyze_trends(storage, days=7)

        # 2% change should be marked as unchanged
        assert result["metrics"]["events"]["direction"] == "unchanged"


class TestSampleSequencesThreeToolPattern:
    """Tests for sample_sequences with 3-tool patterns."""

    def test_sample_sequences_three_tool_pattern(self, storage):
        """Test sampling 3-tool patterns like Read -> Edit -> Bash."""
        now = datetime.now()

        # Create events with 3-tool pattern
        events = [
            Event(
                id=None,
                uuid="3t-1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Read",
                file_path="/file.py",
            ),
            Event(
                id=None,
                uuid="3t-2",
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/file.py",
            ),
            Event(
                id=None,
                uuid="3t-3",
                timestamp=now - timedelta(hours=1, minutes=-2),
                session_id="s1",
                project_path="-test",
                entry_type="tool_use",
                tool_name="Bash",
                command="make",
            ),
        ]
        storage.add_events_batch(events)

        result = sample_sequences(storage, pattern="Read → Edit → Bash", days=7)

        assert result["pattern"] == "Read → Edit → Bash"
        assert result["parsed_tools"] == ["Read", "Edit", "Bash"]
        assert result["total_occurrences"] == 1
        assert result["sample_count"] == 1

        # Check that all 3 tools are matched
        matched = [e for e in result["samples"][0]["events"] if e.get("is_match")]
        assert len(matched) == 3

    def test_sample_sequences_across_multiple_sessions(self, storage):
        """Test that patterns found in different sessions are all counted."""
        now = datetime.now()

        events = []
        # Create the same 3-tool pattern in 3 different sessions
        for i, session in enumerate(["sess-a", "sess-b", "sess-c"]):
            events.extend(
                [
                    Event(
                        id=None,
                        uuid=f"multi-{session}-1",
                        timestamp=now - timedelta(hours=i + 1),
                        session_id=session,
                        project_path="-test",
                        entry_type="tool_use",
                        tool_name="Grep",
                    ),
                    Event(
                        id=None,
                        uuid=f"multi-{session}-2",
                        timestamp=now - timedelta(hours=i + 1, minutes=-1),
                        session_id=session,
                        project_path="-test",
                        entry_type="tool_use",
                        tool_name="Read",
                    ),
                    Event(
                        id=None,
                        uuid=f"multi-{session}-3",
                        timestamp=now - timedelta(hours=i + 1, minutes=-2),
                        session_id=session,
                        project_path="-test",
                        entry_type="tool_use",
                        tool_name="Edit",
                    ),
                ]
            )
        storage.add_events_batch(events)

        result = sample_sequences(storage, pattern="Grep → Read → Edit", days=7)

        assert result["total_occurrences"] == 3
        assert result["sample_count"] == 3  # All 3 since count=5 default


class TestAnalyzeFailuresJoinLogic:
    """Tests for analyze_failures errors_by_tool join logic."""

    def test_errors_by_tool_join(self, storage):
        """Test that errors are properly joined to their tool_use events."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            # Read tool use with error result
            Event(
                id=None,
                uuid="join-use-1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
                tool_id="tool-read-1",
            ),
            Event(
                id=None,
                uuid="join-result-1",
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="s1",
                entry_type="tool_result",
                tool_id="tool-read-1",
                is_error=True,
            ),
            # Bash tool use with error result
            Event(
                id=None,
                uuid="join-use-2",
                timestamp=now - timedelta(hours=2),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Bash",
                tool_id="tool-bash-1",
                command="make",
            ),
            Event(
                id=None,
                uuid="join-result-2",
                timestamp=now - timedelta(hours=2, minutes=-1),
                session_id="s1",
                entry_type="tool_result",
                tool_id="tool-bash-1",
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        assert result["total_errors"] == 2
        # Check errors_by_tool has both tools
        tool_errors = {e["tool"]: e["errors"] for e in result["errors_by_tool"]}
        assert "Read" in tool_errors
        assert "Bash" in tool_errors
        assert tool_errors["Read"] == 1
        assert tool_errors["Bash"] == 1

    def test_errors_without_tool_id_not_in_errors_by_tool(self, storage):
        """Test that errors without tool_id are in total but not in errors_by_tool."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            # Error result without tool_id
            Event(
                id=None,
                uuid="orphan-error",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="tool_result",
                tool_id=None,
                is_error=True,
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        assert result["total_errors"] == 1
        # Without tool_id, can't join to tool_use, so errors_by_tool should be empty
        assert len(result["errors_by_tool"]) == 0

    def test_rework_not_detected_across_sessions(self, storage):
        """Test that same file edits in different sessions aren't counted as rework."""
        from session_analytics.patterns import analyze_failures

        now = datetime.now()
        events = [
            # Same file edited in session 1
            Event(
                id=None,
                uuid="cross-s1-1",
                timestamp=now - timedelta(hours=1),
                session_id="session-1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/same/file.py",
            ),
            Event(
                id=None,
                uuid="cross-s1-2",
                timestamp=now - timedelta(hours=1, minutes=-1),
                session_id="session-1",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/same/file.py",
            ),
            # Same file edited in session 2 (should NOT count as rework with session 1)
            Event(
                id=None,
                uuid="cross-s2-1",
                timestamp=now - timedelta(hours=2),
                session_id="session-2",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/same/file.py",
            ),
            Event(
                id=None,
                uuid="cross-s2-2",
                timestamp=now - timedelta(hours=2, minutes=-1),
                session_id="session-2",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/same/file.py",
            ),
        ]
        storage.add_events_batch(events)

        result = analyze_failures(storage, days=7)

        # Neither session has 3+ edits, so no rework should be detected
        assert result["rework_patterns"]["instances_detected"] == 0


class TestGetInsightsIntegration:
    """Tests for get_insights integration with advanced analytics."""

    def test_insights_with_advanced_analytics(self, storage):
        """Test that advanced analytics are included when include_advanced=True."""
        now = datetime.now()

        # Add enough events to have data for all analytics
        events = []
        for i in range(10):
            events.append(
                Event(
                    id=None,
                    uuid=f"adv-{i}",
                    timestamp=now - timedelta(hours=i),
                    session_id="test-session",
                    project_path="/test/project",
                    entry_type="tool_use",
                    tool_name="Edit" if i % 2 == 0 else "Read",
                    file_path=f"/file{i}.py",
                )
            )
        storage.add_events_batch(events)

        insights = get_insights(storage, refresh=True, days=7, include_advanced=True)

        assert "summary" in insights
        # These should be present (may be True or False depending on data)
        assert "has_trends" in insights["summary"]
        assert "has_failure_analysis" in insights["summary"]
        assert "has_classification" in insights["summary"]

    def test_insights_without_advanced_analytics(self, storage):
        """Test that advanced analytics are excluded when include_advanced=False."""
        now = datetime.now()

        events = [
            Event(
                id=None,
                uuid="basic-1",
                timestamp=now - timedelta(hours=1),
                session_id="s1",
                entry_type="tool_use",
                tool_name="Read",
            ),
        ]
        storage.add_events_batch(events)

        insights = get_insights(storage, refresh=True, days=7, include_advanced=False)

        # These keys should not be present when include_advanced=False
        assert "has_trends" not in insights["summary"]
        assert "has_failure_analysis" not in insights["summary"]
        assert "has_classification" not in insights["summary"]
        assert "trends" not in insights
        assert "failure_summary" not in insights
        assert "session_types" not in insights

    def test_insights_graceful_degradation(self, storage):
        """Test that insights work even when advanced analytics fail."""
        # Empty storage should still return basic insights
        insights = get_insights(storage, refresh=True, days=7, include_advanced=True)

        # Basic structure should still exist
        assert "tool_frequency" in insights
        assert "command_frequency" in insights
        assert "sequences" in insights
        assert "permission_gaps" in insights
        assert "summary" in insights

        # Advanced flags should be present (possibly False if failed)
        assert "has_trends" in insights["summary"]
        assert "has_failure_analysis" in insights["summary"]
        assert "has_classification" in insights["summary"]


class TestGetSessionSignals:
    """Tests for RFC #26 session signals (revised per RFC #17 - raw data only)."""

    def test_get_signals_empty_database(self, storage):
        """Test with empty database."""
        from session_analytics.patterns import get_session_signals

        result = get_session_signals(storage, days=7)

        assert result["sessions_analyzed"] == 0
        assert result["sessions"] == []

    def test_get_signals_with_commits(self, storage):
        """Test that commit counts are included in signals."""
        from session_analytics.patterns import get_session_signals
        from session_analytics.storage import GitCommit, Session

        now = datetime.now()

        # Create session with events
        events = [
            Event(
                id=None,
                uuid=f"sig-{i}",
                timestamp=now - timedelta(hours=1, minutes=i),
                session_id="signal-session",
                project_path="/project",
                entry_type="tool_use",
                tool_name="Edit" if i % 2 == 0 else "Read",
                file_path=f"/file{i}.py",
            )
            for i in range(15)
        ]
        storage.add_events_batch(events)

        # Create session record
        storage.upsert_session(Session(id="signal-session", project_path="/project"))

        # Add commit and link it
        storage.add_git_commit(GitCommit(sha="abc1234", timestamp=now))
        storage.add_session_commit("signal-session", "abc1234", 300, True)

        result = get_session_signals(storage, days=7, min_count=5)

        # Should have raw signals, no outcome classification
        assert result["sessions_analyzed"] == 1
        session = result["sessions"][0]
        assert session["session_id"] == "signal-session"
        assert session["commit_count"] == 1
        assert session["event_count"] == 15
        assert "outcome" not in session  # No interpretation
        assert "confidence" not in session  # No interpretation

    def test_get_signals_with_errors(self, storage):
        """Test that error rates are included in signals."""
        from session_analytics.patterns import get_session_signals

        now = datetime.now()

        # Create session with some errors
        events = []
        for i in range(10):
            events.append(
                Event(
                    id=None,
                    uuid=f"err-use-{i}",
                    timestamp=now - timedelta(hours=1, minutes=i * 2),
                    session_id="error-session",
                    project_path="/project",
                    entry_type="tool_use",
                    tool_name="Edit",
                    tool_id=f"tool-{i}",
                    file_path="/file.py",
                    is_error=(i < 3),  # 3 errors out of 10
                )
            )
        storage.add_events_batch(events)

        result = get_session_signals(storage, days=7, min_count=5)

        # Should include error rate as raw signal
        assert result["sessions_analyzed"] == 1
        session = result["sessions"][0]
        assert session["error_count"] == 3
        assert session["error_rate"] == 0.3
        assert "outcome" not in session  # No interpretation

    def test_get_signals_min_count_filter(self, storage):
        """Test that sessions below min_count threshold are excluded."""
        from session_analytics.patterns import get_session_signals

        now = datetime.now()

        # Create session with only 3 events
        events = [
            Event(
                id=None,
                uuid=f"small-{i}",
                timestamp=now - timedelta(hours=1, minutes=i),
                session_id="small-session",
                project_path="/project",
                entry_type="tool_use",
                tool_name="Read",
            )
            for i in range(3)
        ]
        storage.add_events_batch(events)

        result = get_session_signals(storage, days=7, min_count=5)

        # Session should be excluded due to min_count
        assert result["sessions_analyzed"] == 0

    def test_get_signals_includes_all_raw_fields(self, storage):
        """Test that all expected raw signal fields are present."""
        from session_analytics.patterns import get_session_signals
        from session_analytics.storage import Session

        now = datetime.now()

        # Create session with various activity
        events = [
            Event(
                id=None,
                uuid=f"full-{i}",
                timestamp=now - timedelta(hours=1, minutes=i),
                session_id="full-session",
                project_path="/project",
                entry_type="tool_use",
                tool_name="Edit",
                file_path="/file.py",
                command="git" if i == 0 else None,
                skill_name="commit" if i == 1 else None,
            )
            for i in range(10)
        ]
        storage.add_events_batch(events)
        storage.upsert_session(Session(id="full-session", project_path="/project"))

        result = get_session_signals(storage, days=7, min_count=5)

        assert result["sessions_analyzed"] == 1
        session = result["sessions"][0]

        # Verify all expected raw signal fields
        expected_fields = [
            "session_id",
            "project_path",
            "event_count",
            "error_count",
            "edit_count",
            "git_count",
            "skill_count",
            "commit_count",
            "error_rate",
            "duration_minutes",
            "has_rework",
            "has_pr_activity",
        ]
        for field in expected_fields:
            assert field in session, f"Missing field: {field}"

        # Verify NO interpretation fields
        interpretation_fields = ["outcome", "confidence", "satisfaction_score"]
        for field in interpretation_fields:
            assert field not in session, f"Unexpected interpretation field: {field}"

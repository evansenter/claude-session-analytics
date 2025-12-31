"""Tests for the pattern detection module."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from session_analytics.patterns import (
    compute_all_patterns,
    compute_command_patterns,
    compute_permission_gaps,
    compute_sequence_patterns,
    compute_tool_frequency_patterns,
    get_insights,
    load_allowed_commands,
)
from session_analytics.storage import Event, SQLiteStorage


@pytest.fixture
def storage():
    """Create a temporary storage instance for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield SQLiteStorage(db_path)


@pytest.fixture
def populated_storage(storage):
    """Create a storage instance with sample data for pattern detection."""
    now = datetime.now()

    # Add events that will create patterns
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
        # Session 2: Read -> Edit sequence (same as s1)
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
        # Session 3: Read -> Edit sequence (third occurrence)
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
        # More Bash commands for permission gap testing
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


class TestToolFrequencyPatterns:
    """Tests for tool frequency pattern detection."""

    def test_compute_tool_frequency(self, populated_storage):
        """Test computing tool frequency patterns."""
        patterns = compute_tool_frequency_patterns(populated_storage, days=7)

        # Should have patterns for Read, Edit, Bash
        pattern_keys = {p.pattern_key for p in patterns}
        assert "Read" in pattern_keys
        assert "Edit" in pattern_keys
        assert "Bash" in pattern_keys

    def test_frequency_counts(self, populated_storage):
        """Test that frequency counts are accurate."""
        patterns = compute_tool_frequency_patterns(populated_storage, days=7)
        pattern_dict = {p.pattern_key: p.count for p in patterns}

        assert pattern_dict["Read"] == 3
        assert pattern_dict["Edit"] == 3
        assert pattern_dict["Bash"] == 6  # 1 git + 5 make


class TestCommandPatterns:
    """Tests for command pattern detection."""

    def test_compute_command_patterns(self, populated_storage):
        """Test computing command patterns."""
        patterns = compute_command_patterns(populated_storage, days=7)

        pattern_dict = {p.pattern_key: p.count for p in patterns}
        assert pattern_dict.get("git", 0) == 1
        assert pattern_dict.get("make", 0) == 5


class TestSequencePatterns:
    """Tests for sequence pattern detection."""

    def test_compute_sequences(self, populated_storage):
        """Test computing sequence patterns."""
        patterns = compute_sequence_patterns(
            populated_storage, days=7, sequence_length=2, min_count=2
        )

        # Should find Read -> Edit pattern (occurs 3 times)
        pattern_keys = {p.pattern_key for p in patterns}
        assert "Read → Edit" in pattern_keys

    def test_sequence_counts(self, populated_storage):
        """Test that sequence counts are accurate."""
        patterns = compute_sequence_patterns(
            populated_storage, days=7, sequence_length=2, min_count=1
        )

        pattern_dict = {p.pattern_key: p.count for p in patterns}
        assert pattern_dict["Read → Edit"] == 3

    def test_min_count_filter(self, populated_storage):
        """Test that min_count filter works."""
        # With min_count=5, should have no sequences
        patterns = compute_sequence_patterns(
            populated_storage, days=7, sequence_length=2, min_count=5
        )
        assert len(patterns) == 0


class TestPermissionGaps:
    """Tests for permission gap detection."""

    def test_load_allowed_commands_missing_file(self):
        """Test loading allowed commands from non-existent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "nonexistent.json"
            allowed = load_allowed_commands(missing_path)
            assert allowed == set()

    def test_load_allowed_commands(self):
        """Test loading allowed commands from settings.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": ["Bash(git:*)", "Bash(make:*)"]}}')
            allowed = load_allowed_commands(settings_path)
            assert "git" in allowed
            assert "make" in allowed

    def test_compute_permission_gaps(self, populated_storage):
        """Test computing permission gaps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty settings.json
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": []}}')

            patterns = compute_permission_gaps(
                populated_storage, days=7, threshold=3, settings_path=settings_path
            )

            # Should find make (5 uses) but maybe not git (1 use) depending on threshold
            pattern_keys = {p.pattern_key for p in patterns}
            assert "make" in pattern_keys

    def test_permission_gaps_respects_allowed(self, populated_storage):
        """Test that allowed commands are not reported as gaps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text('{"permissions": {"allow": ["Bash(make:*)"]}}')

            patterns = compute_permission_gaps(
                populated_storage, days=7, threshold=1, settings_path=settings_path
            )

            # make is allowed, so should only find git
            pattern_keys = {p.pattern_key for p in patterns}
            assert "make" not in pattern_keys
            assert "git" in pattern_keys


class TestComputeAllPatterns:
    """Tests for computing all patterns."""

    def test_compute_all_patterns(self, populated_storage):
        """Test computing all pattern types."""
        stats = compute_all_patterns(populated_storage, days=7)

        assert stats["tool_frequency_patterns"] > 0
        assert stats["command_patterns"] > 0
        assert stats["total_patterns"] > 0


class TestGetInsights:
    """Tests for the get_insights function."""

    def test_get_insights(self, populated_storage):
        """Test getting insights."""
        insights = get_insights(populated_storage, refresh=True, days=7)

        assert "tool_frequency" in insights
        assert "command_frequency" in insights
        assert "sequences" in insights
        assert "permission_gaps" in insights
        assert "summary" in insights

    def test_insights_summary(self, populated_storage):
        """Test that insights include summary stats."""
        insights = get_insights(populated_storage, refresh=True, days=7)

        assert "total_tools" in insights["summary"]
        assert "total_commands" in insights["summary"]
        assert "total_sequences" in insights["summary"]

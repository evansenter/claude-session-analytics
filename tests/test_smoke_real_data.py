"""Smoke tests that validate assumptions against real database data.

These tests are skipped by default (no real database) and only run when
SESSION_ANALYTICS_SMOKE_TEST=1 is set. They catch issues like:
- Compaction detection not finding entries
- result_size_bytes not being populated
- Entry type distribution anomalies
- Token count reasonableness

Run with: SESSION_ANALYTICS_SMOKE_TEST=1 pytest tests/test_smoke_real_data.py -v
"""

import os
from pathlib import Path

import pytest

# Skip all tests in this file unless smoke test env var is set
pytestmark = pytest.mark.skipif(
    os.environ.get("SESSION_ANALYTICS_SMOKE_TEST") != "1",
    reason="Smoke tests require SESSION_ANALYTICS_SMOKE_TEST=1 and real database",
)


@pytest.fixture
def real_storage():
    """Get storage instance pointing to real database."""
    from session_analytics.storage import SQLiteStorage

    db_path = Path.home() / ".claude" / "contrib" / "analytics" / "data.db"
    if not db_path.exists():
        pytest.skip("Real database not found")
    return SQLiteStorage(db_path)


class TestCompactionDetection:
    """Validate compaction detection is working."""

    def test_compaction_entries_exist(self, real_storage):
        """Compaction entries should exist if sessions have context resets."""
        rows = real_storage.execute_query(
            "SELECT COUNT(*) as count FROM events WHERE entry_type = 'compaction'"
        )
        compaction_count = rows[0]["count"]

        # Also check for undetected compactions (marker in user entries)
        rows = real_storage.execute_query(
            """
            SELECT COUNT(*) as count FROM events
            WHERE entry_type = 'user'
              AND message_text LIKE '%continued from a previous conversation%'
            """
        )
        undetected = rows[0]["count"]

        # Fail if there are undetected compactions
        assert undetected == 0, (
            f"Found {undetected} user entries with compaction marker "
            f"that should have entry_type='compaction'. Run migration 11."
        )

        # Info: how many compactions were detected
        print(f"\nCompaction entries detected: {compaction_count}")

    def test_compaction_marker_not_in_tool_results(self, real_storage):
        """Tool results shouldn't be mis-detected as compactions."""
        # The marker text may appear in tool results (e.g., GitHub issue body)
        # These should NOT be marked as compaction
        rows = real_storage.execute_query(
            """
            SELECT COUNT(*) as count FROM events
            WHERE entry_type = 'compaction'
              AND tool_name IS NOT NULL
            """
        )
        tool_compactions = rows[0]["count"]
        assert tool_compactions == 0, (
            f"Found {tool_compactions} compaction entries with tool_name set. "
            "Compactions should only be user messages, not tool results."
        )


class TestResultSizeBytes:
    """Validate result_size_bytes is populated."""

    def test_result_size_populated_for_message_text(self, real_storage):
        """Entries with message_text should have result_size_bytes."""
        rows = real_storage.execute_query(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN result_size_bytes IS NOT NULL THEN 1 ELSE 0 END) as populated
            FROM events
            WHERE message_text IS NOT NULL
            """
        )
        total = rows[0]["total"]
        populated = rows[0]["populated"]

        # Allow some tolerance for entries added before migration
        population_rate = populated / total if total > 0 else 0
        assert population_rate > 0.95, (
            f"Only {population_rate:.1%} of entries with message_text have result_size_bytes. "
            f"Expected >95%. Run migration 10 to backfill."
        )

    def test_result_size_reasonable_values(self, real_storage):
        """result_size_bytes should have reasonable values."""
        rows = real_storage.execute_query(
            """
            SELECT
                MIN(result_size_bytes) as min_size,
                MAX(result_size_bytes) as max_size,
                AVG(result_size_bytes) as avg_size
            FROM events
            WHERE result_size_bytes IS NOT NULL
            """
        )
        min_size = rows[0]["min_size"]
        max_size = rows[0]["max_size"]
        avg_size = rows[0]["avg_size"]

        assert min_size >= 0, "result_size_bytes should not be negative"
        assert max_size < 100_000_000, f"Suspiciously large result: {max_size} bytes"
        print(f"\nResult sizes: min={min_size}, max={max_size:,}, avg={avg_size:,.0f}")


class TestEntryTypeDistribution:
    """Validate entry type distribution looks reasonable."""

    def test_entry_types_present(self, real_storage):
        """Core entry types should be present."""
        rows = real_storage.execute_query(
            """
            SELECT entry_type, COUNT(*) as count
            FROM events
            GROUP BY entry_type
            ORDER BY count DESC
            """
        )
        entry_types = {r["entry_type"]: r["count"] for r in rows}

        # These should always exist in real usage
        assert "assistant" in entry_types, "No assistant entries found"
        assert "tool_use" in entry_types, "No tool_use entries found"
        assert "tool_result" in entry_types, "No tool_result entries found"
        assert "user" in entry_types, "No user entries found"

        # Print distribution
        print("\nEntry type distribution:")
        for entry_type, count in entry_types.items():
            print(f"  {entry_type}: {count:,}")

    def test_tool_use_tool_result_balance(self, real_storage):
        """tool_use and tool_result counts should be similar."""
        rows = real_storage.execute_query(
            """
            SELECT entry_type, COUNT(*) as count
            FROM events
            WHERE entry_type IN ('tool_use', 'tool_result')
            GROUP BY entry_type
            """
        )
        counts = {r["entry_type"]: r["count"] for r in rows}

        tool_use = counts.get("tool_use", 0)
        tool_result = counts.get("tool_result", 0)

        if tool_use > 0:
            ratio = tool_result / tool_use
            # Allow some tolerance - tool_result might be slightly less
            # if some tools don't return results
            assert 0.8 < ratio < 1.2, (
                f"tool_use ({tool_use}) and tool_result ({tool_result}) "
                f"counts are imbalanced (ratio: {ratio:.2f})"
            )


class TestTokenData:
    """Validate token data looks reasonable."""

    def test_tokens_on_assistant_entries(self, real_storage):
        """Assistant entries should have token data."""
        rows = real_storage.execute_query(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN input_tokens > 0 OR output_tokens > 0 THEN 1 ELSE 0 END) as with_tokens
            FROM events
            WHERE entry_type = 'assistant'
              AND model IS NOT NULL
              AND model != 'unknown'
            """
        )
        total = rows[0]["total"]
        with_tokens = rows[0]["with_tokens"]

        if total > 0:
            token_rate = with_tokens / total
            assert token_rate > 0.9, (
                f"Only {token_rate:.1%} of assistant entries have tokens. Expected >90%."
            )

    def test_token_values_reasonable(self, real_storage):
        """Token values should be in reasonable ranges."""
        rows = real_storage.execute_query(
            """
            SELECT
                MAX(input_tokens) as max_input,
                MAX(output_tokens) as max_output,
                AVG(input_tokens) as avg_input,
                AVG(output_tokens) as avg_output
            FROM events
            WHERE entry_type = 'assistant'
              AND (input_tokens > 0 OR output_tokens > 0)
            """
        )
        max_input = rows[0]["max_input"] or 0
        max_output = rows[0]["max_output"] or 0

        # Claude's context window is ~200K, individual responses much smaller
        assert max_input < 500_000, f"Suspiciously high input_tokens: {max_input}"
        assert max_output < 100_000, f"Suspiciously high output_tokens: {max_output}"

        print(f"\nToken stats: max_input={max_input:,}, max_output={max_output:,}")


class TestToolIdJoins:
    """Validate tool_id enables proper joins."""

    def test_tool_result_joins_to_tool_use(self, real_storage):
        """tool_result entries should join to tool_use via tool_id."""
        rows = real_storage.execute_query(
            """
            SELECT COUNT(*) as count
            FROM events e1
            LEFT JOIN events e2 ON e1.tool_id = e2.tool_id AND e2.entry_type = 'tool_use'
            WHERE e1.entry_type = 'tool_result'
              AND e1.tool_id IS NOT NULL
              AND e2.id IS NULL
            """
        )
        orphan_results = rows[0]["count"]
        assert orphan_results == 0, (
            f"Found {orphan_results} tool_result entries that don't join to tool_use. "
            "This breaks error attribution."
        )


class TestErrorClassification:
    """Validate error data quality."""

    def test_warmup_not_counted_as_errors(self, real_storage):
        """Warmup events should not be marked as errors."""
        rows = real_storage.execute_query(
            """
            SELECT COUNT(*) as count
            FROM events
            WHERE is_error = 1
              AND message_text = 'Warmup'
            """
        )
        warmup_errors = rows[0]["count"]

        # After migration 12, warmup events should not be marked as errors
        assert warmup_errors == 0, (
            f"Found {warmup_errors} warmup events marked as errors. "
            "Migration 12 should have fixed this. Re-run migrations or re-ingest."
        )

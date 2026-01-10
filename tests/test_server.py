"""Tests for the MCP server."""

from session_analytics.server import (
    analyze_failures,
    analyze_pre_compaction_patterns,
    analyze_trends,
    classify_sessions,
    correlate_git_with_sessions,
    detect_parallel_sessions,
    find_related_sessions,
    get_command_frequency,
    get_compaction_events,
    get_file_activity,
    get_handoff_context,
    get_insights,
    get_languages,
    get_mcp_usage,
    get_permission_gaps,
    get_projects,
    get_session_commits,
    get_session_efficiency,
    get_session_events,
    get_session_messages,
    get_session_signals,
    get_status,
    get_token_usage,
    get_tool_frequency,
    get_tool_sequences,
    ingest_git_history,
    ingest_logs,
    list_sessions,
    sample_sequences,
    search_messages,
)


def test_get_status():
    """Test that get_status returns expected fields."""
    # FastMCP wraps functions - access the underlying fn
    result = get_status.fn()
    assert result["status"] == "ok"
    assert "version" in result
    assert "db_path" in result
    assert "event_count" in result
    assert "session_count" in result


def test_ingest_logs():
    """Test that ingest_logs runs and returns stats."""
    result = ingest_logs.fn(days=1)
    assert result["status"] == "ok"
    assert "files_found" in result
    assert "events_added" in result


def test_get_tool_frequency():
    """Test that get_tool_frequency returns tool counts."""
    result = get_tool_frequency.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "total_tool_calls" in result
    assert "tools" in result
    assert isinstance(result["tools"], list)


def test_get_session_events():
    """Test that get_session_events returns events."""
    result = get_session_events.fn(limit=10)
    assert result["status"] == "ok"
    assert "start" in result
    assert "end" in result
    assert "events" in result
    assert isinstance(result["events"], list)


def test_get_command_frequency():
    """Test that get_command_frequency returns command counts."""
    result = get_command_frequency.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "total_commands" in result
    assert "commands" in result
    assert isinstance(result["commands"], list)


def test_list_sessions():
    """Test that list_sessions returns session info."""
    result = list_sessions.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "session_count" in result
    assert "sessions" in result
    assert isinstance(result["sessions"], list)


def test_get_token_usage():
    """Test that get_token_usage returns token breakdown."""
    result = get_token_usage.fn(days=7, by="day")
    assert result["status"] == "ok"
    assert "days" in result
    assert "group_by" in result
    assert "breakdown" in result
    assert isinstance(result["breakdown"], list)


def test_get_tool_sequences():
    """Test that get_tool_sequences returns sequence patterns."""
    result = get_tool_sequences.fn(days=7, min_count=1, length=2)
    assert result["status"] == "ok"
    assert "days" in result
    assert "sequences" in result
    assert isinstance(result["sequences"], list)


def test_get_permission_gaps():
    """Test that get_permission_gaps returns gap analysis."""
    result = get_permission_gaps.fn(days=7, min_count=1)
    assert result["status"] == "ok"
    assert "days" in result
    assert "gaps" in result
    assert isinstance(result["gaps"], list)


def test_get_insights():
    """Test that get_insights returns organized patterns."""
    result = get_insights.fn(refresh=True, days=7)
    assert result["status"] == "ok"
    assert "tool_frequency" in result
    assert "sequences" in result
    assert "permission_gaps" in result
    assert "summary" in result


def test_search_messages():
    """Test that search_messages returns FTS results."""
    result = search_messages.fn(query="test", limit=10)
    assert result["status"] == "ok"
    assert "query" in result
    assert result["query"] == "test"
    assert "count" in result
    assert "messages" in result
    assert isinstance(result["messages"], list)


def test_sample_sequences():
    """Test that sample_sequences returns sequence samples with context."""
    result = sample_sequences.fn(pattern="Read → Edit", limit=5, context_events=2, days=7)
    assert result["status"] == "ok"
    assert "pattern" in result
    assert "parsed_tools" in result
    assert "total_occurrences" in result
    assert "samples" in result
    assert isinstance(result["samples"], list)


def test_get_session_messages():
    """Test that get_session_messages returns user messages."""
    result = get_session_messages.fn(days=1, limit=10)
    assert result["status"] == "ok"
    assert "hours" in result
    assert "journey" in result
    assert isinstance(result["journey"], list)


def test_detect_parallel_sessions():
    """Test that detect_parallel_sessions finds overlapping sessions."""
    result = detect_parallel_sessions.fn(days=1, min_overlap_minutes=5)
    assert result["status"] == "ok"
    assert "hours" in result
    assert "parallel_periods" in result
    assert isinstance(result["parallel_periods"], list)


def test_find_related_sessions():
    """Test that find_related_sessions finds sessions sharing files/commands."""
    # This needs a session_id, but we may not have one - test with empty result
    result = find_related_sessions.fn(session_id="nonexistent-session", method="files", days=7)
    assert result["status"] == "ok"
    assert "session_id" in result
    assert "method" in result
    assert "related_sessions" in result
    assert isinstance(result["related_sessions"], list)


def test_analyze_failures():
    """Test that analyze_failures returns failure analysis."""
    result = analyze_failures.fn(days=7, rework_window_minutes=10)
    assert result["status"] == "ok"
    assert "days" in result
    assert "total_errors" in result
    assert "rework_patterns" in result


def test_classify_sessions():
    """Test that classify_sessions categorizes sessions."""
    result = classify_sessions.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "sessions" in result
    assert isinstance(result["sessions"], list)


def test_get_handoff_context():
    """Test that get_handoff_context returns session context."""
    result = get_handoff_context.fn(session_id=None, days=0.17, limit=10)
    assert result["status"] == "ok"
    # Returns either session_id + recent_messages or error if no recent sessions
    assert "session_id" in result or "error" in result


def test_analyze_trends():
    """Test that analyze_trends compares time periods."""
    result = analyze_trends.fn(days=7, compare_to="previous")
    assert result["status"] == "ok"
    assert "days" in result
    assert "compare_to" in result
    assert "metrics" in result


def test_ingest_git_history():
    """Test that ingest_git_history ingests git commits."""
    result = ingest_git_history.fn(repo_path=None, days=7)
    assert result["status"] == "ok"
    assert "commits_found" in result
    assert "commits_added" in result


def test_correlate_git_with_sessions():
    """Test that correlate_git_with_sessions links commits to sessions."""
    result = correlate_git_with_sessions.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "commits_correlated" in result


def test_get_session_signals():
    """Test that get_session_signals returns raw session metrics."""
    result = get_session_signals.fn(days=7, min_count=1)
    assert result["status"] == "ok"
    assert "days" in result
    assert "sessions_analyzed" in result
    assert "sessions" in result
    assert isinstance(result["sessions"], list)


def test_get_session_commits():
    """Test that get_session_commits returns commit associations."""
    result = get_session_commits.fn(session_id=None, days=7)
    assert result["status"] == "ok"
    # Without session_id, returns session_count and sessions dict
    assert "session_count" in result
    assert "total_commits" in result
    assert "sessions" in result
    assert isinstance(result["sessions"], dict)


def test_get_file_activity():
    """Test that get_file_activity returns file read/write stats."""
    result = get_file_activity.fn(days=7, limit=20, collapse_worktrees=False)
    assert result["status"] == "ok"
    assert "days" in result
    assert "file_count" in result
    assert "files" in result
    assert isinstance(result["files"], list)


def test_get_languages():
    """Test that get_languages returns language distribution."""
    result = get_languages.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "total_operations" in result
    assert "languages" in result
    assert isinstance(result["languages"], list)


def test_get_projects():
    """Test that get_projects returns project activity."""
    result = get_projects.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "project_count" in result
    assert "projects" in result
    assert isinstance(result["projects"], list)


def test_get_mcp_usage():
    """Test that get_mcp_usage returns MCP server/tool stats."""
    result = get_mcp_usage.fn(days=7)
    assert result["status"] == "ok"
    assert "days" in result
    assert "total_mcp_calls" in result
    assert "servers" in result
    assert isinstance(result["servers"], list)


# Issue #77: Limit parameters for verbose endpoints


def test_get_tool_sequences_limit():
    """Test that get_tool_sequences respects limit parameter."""
    result = get_tool_sequences.fn(days=7, limit=5)
    assert result["status"] == "ok"
    assert result["limit"] == 5
    assert "total_patterns" in result
    assert len(result["sequences"]) <= 5


def test_get_compaction_events():
    """Test that get_compaction_events returns compaction data."""
    result = get_compaction_events.fn(days=7, limit=10)
    assert result["status"] == "ok"
    assert result["limit"] == 10
    assert "total_compaction_count" in result
    assert "compaction_count" in result
    assert "compactions" in result
    assert isinstance(result["compactions"], list)
    assert len(result["compactions"]) <= 10


def test_get_session_efficiency():
    """Test that get_session_efficiency returns efficiency metrics."""
    result = get_session_efficiency.fn(days=7, limit=10)
    assert result["status"] == "ok"
    assert result["limit"] == 10
    assert "session_count" in result
    assert "sessions" in result
    assert isinstance(result["sessions"], list)


# Issue #78: Efficiency metrics in analyze_trends


def test_analyze_trends_efficiency():
    """Test that analyze_trends includes efficiency metrics."""
    result = analyze_trends.fn(days=7, compare_to="previous")
    assert result["status"] == "ok"
    assert "efficiency" in result
    efficiency = result["efficiency"]
    assert "compactions" in efficiency
    assert "avg_compactions_per_session" in efficiency
    assert "files_read_multiple_times" in efficiency
    assert "avg_result_mb_per_session" in efficiency
    # Each should have current/previous/change_pct structure
    assert "current" in efficiency["compactions"]
    assert "previous" in efficiency["compactions"]
    assert "change_pct" in efficiency["compactions"]


# Issue #79: Efficiency metrics in classify_sessions


def test_classify_sessions_efficiency():
    """Test that classify_sessions includes efficiency metrics."""
    result = classify_sessions.fn(days=7)
    assert result["status"] == "ok"
    assert "sessions" in result
    # Check that sessions have efficiency data (if any sessions exist)
    if result["sessions"]:
        session = result["sessions"][0]
        assert "efficiency" in session
        efficiency = session["efficiency"]
        assert "compaction_count" in efficiency
        assert "total_result_mb" in efficiency
        assert "files_read_multiple_times" in efficiency
        assert "burn_rate" in efficiency
        assert efficiency["burn_rate"] in ["high", "medium", "low"]


# Issue #81: Compaction aggregation and pre-compaction patterns


def test_get_compaction_events_aggregate():
    """Test that get_compaction_events aggregate mode returns session-level data."""
    result = get_compaction_events.fn(days=7, limit=10, aggregate=True)
    assert result["status"] == "ok"
    assert result["aggregate"] is True
    assert "total_compaction_count" in result
    assert "total_sessions_with_compactions" in result
    assert "session_count" in result
    assert "sessions" in result
    assert isinstance(result["sessions"], list)
    # If sessions exist, verify structure
    if result["sessions"]:
        session = result["sessions"][0]
        assert "session_id" in session
        assert "compaction_count" in session
        assert "first_compaction" in session
        assert "last_compaction" in session
        assert "total_summary_kb" in session


def test_analyze_pre_compaction_patterns():
    """Test that analyze_pre_compaction_patterns returns pattern data."""
    result = analyze_pre_compaction_patterns.fn(days=7, events_before=50, limit=20)
    assert result["status"] == "ok"
    assert "compactions_analyzed" in result
    assert "patterns" in result
    assert "recommendations" in result
    assert isinstance(result["recommendations"], list)
    # If patterns exist, verify structure
    if result.get("compactions_analyzed", 0) > 0:
        patterns = result["patterns"]
        assert "avg_consecutive_reads" in patterns
        assert "avg_files_read_multiple_times" in patterns
        assert "avg_large_results" in patterns
        assert "tool_distribution" in patterns
        assert isinstance(patterns["tool_distribution"], list)

"""Tests for the MCP server."""

from session_analytics.server import get_status, ingest_logs, query_tool_frequency


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


def test_query_tool_frequency_placeholder():
    """Test that query_tool_frequency returns placeholder response."""
    result = query_tool_frequency.fn(days=14, project="/some/path")
    assert result["status"] == "not_implemented"
    assert result["days"] == 14
    assert result["project"] == "/some/path"

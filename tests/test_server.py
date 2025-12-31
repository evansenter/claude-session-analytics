"""Tests for the session analytics server."""

from session_analytics.server import get_status


def test_get_status():
    """Test that get_status returns expected structure."""
    result = get_status()
    assert result["status"] == "ok"
    assert "message" in result

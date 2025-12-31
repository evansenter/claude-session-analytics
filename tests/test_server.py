"""Tests for the session analytics server."""

from session_analytics.server import _get_status_impl


def test_get_status():
    """Test that get_status returns expected structure."""
    result = _get_status_impl()
    assert result["status"] == "ok"
    assert "message" in result

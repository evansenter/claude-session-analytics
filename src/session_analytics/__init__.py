"""Claude Session Analytics - MCP server for queryable session log analytics."""

from importlib.metadata import version

try:
    __version__ = version("claude-session-analytics")
except Exception:
    __version__ = "0.1.0"  # Fallback for development

# Re-export public API
from session_analytics.storage import (
    Event,
    GitCommit,
    IngestionState,
    Pattern,
    Session,
    SQLiteStorage,
)

__all__ = [
    # Version
    "__version__",
    # Storage
    "SQLiteStorage",
    "Event",
    "Session",
    "Pattern",
    "IngestionState",
    "GitCommit",
]

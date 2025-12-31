"""MCP Session Analytics Server.

Provides tools for querying Claude Code session logs:
- ingest_logs: Refresh data from JSONL files
- query_timeline: Events in time window
- query_tool_frequency: Tool usage counts
- query_commands: Bash command breakdown
- query_sequences: Common tool patterns
- query_permission_gaps: Commands needing settings.json
- query_sessions: Session metadata
- query_tokens: Token usage analysis
- get_insights: Pre-computed patterns for /improve-workflow
- get_status: Ingestion status + DB stats
"""

import logging
import os
from pathlib import Path

from fastmcp import FastMCP

from session_analytics.storage import SQLiteStorage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("session-analytics")
if os.environ.get("DEV_MODE"):
    logger.setLevel(logging.DEBUG)

# Initialize MCP server
mcp = FastMCP("session-analytics")

# Initialize storage
storage = SQLiteStorage()


@mcp.resource("session-analytics://guide", description="Usage guide and best practices")
def usage_guide() -> str:
    """Return the session analytics usage guide from external markdown file."""
    guide_path = Path(__file__).parent / "guide.md"
    try:
        return guide_path.read_text()
    except FileNotFoundError:
        return "# Session Analytics Usage Guide\n\nGuide file not found. See CLAUDE.md for usage."


@mcp.tool()
def get_status() -> dict:
    """Get ingestion status and database stats.

    Returns:
        Status info including last ingestion time, event count, and DB size
    """
    stats = storage.get_db_stats()
    last_ingest = storage.get_last_ingestion_time()

    return {
        "status": "ok",
        "version": "0.1.0",
        "last_ingestion": last_ingest.isoformat() if last_ingest else None,
        **stats,
    }


@mcp.tool()
def ingest_logs(days: int = 7, project: str | None = None, force: bool = False) -> dict:
    """Refresh data from JSONL session log files.

    Args:
        days: Number of days to look back (default: 7)
        project: Optional project path filter
        force: Force re-ingestion even if data is fresh

    Returns:
        Ingestion stats (files processed, entries added, etc.)
    """
    # Placeholder - will be implemented in Phase 3
    return {
        "status": "not_implemented",
        "message": "Ingestion will be implemented in Phase 3",
        "days": days,
        "project": project,
        "force": force,
    }


@mcp.tool()
def query_tool_frequency(days: int = 7, project: str | None = None) -> dict:
    """Get tool usage frequency counts.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter

    Returns:
        Tool frequency breakdown
    """
    # Placeholder - will be implemented in Phase 4
    return {
        "status": "not_implemented",
        "message": "Query will be implemented in Phase 4",
        "days": days,
        "project": project,
    }


def create_app():
    """Create the ASGI app for uvicorn."""
    # stateless_http=True allows resilience to server restarts
    return mcp.http_app(stateless_http=True)


def main():
    """Run the MCP server."""
    import uvicorn

    port = int(os.environ.get("PORT", 8081))
    host = os.environ.get("HOST", "127.0.0.1")

    print(f"Starting Claude Session Analytics on {host}:{port}")
    print(
        f"Add to Claude Code: claude mcp add --transport http --scope user session-analytics http://{host}:{port}/mcp"
    )

    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()

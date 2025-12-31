"""MCP server for Claude Code session analytics."""

from fastmcp import FastMCP

mcp = FastMCP("session-analytics")


def _get_status_impl() -> dict:
    """Get ingestion status and database stats."""
    return {
        "status": "ok",
        "message": "Session analytics server is running",
    }


@mcp.tool()
def get_status() -> dict:
    """Get ingestion status and database stats."""
    return _get_status_impl()


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()

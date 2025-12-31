"""MCP server for Claude Code session analytics."""

from fastmcp import FastMCP

mcp = FastMCP("session-analytics")


@mcp.tool()
def get_status() -> dict:
    """Get ingestion status and database stats."""
    return {
        "status": "ok",
        "message": "Session analytics server is running",
    }


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()

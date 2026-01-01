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
- get_user_journey: User messages across sessions
- search_messages: Full-text search on user messages
- get_session_signals: Raw session signals for LLM interpretation (RFC #26)
- get_session_commits: Session-commit mappings (RFC #26)
"""

import logging
import os
import sqlite3
from importlib.metadata import version
from pathlib import Path

# Read version from package metadata
try:
    __version__ = version("claude-session-analytics")
except Exception:
    __version__ = "0.1.0"  # Fallback for development

from fastmcp import FastMCP

from session_analytics import ingest, patterns, queries
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
        "version": __version__,
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
    result = ingest.ingest_logs(storage, days=days, project=project, force=force)
    return {
        "status": "ok",
        **result,
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
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_tool_frequency(storage, days=days, project=project)
    return {"status": "ok", **result}


@mcp.tool()
def query_timeline(
    start: str | None = None,
    end: str | None = None,
    tool: str | None = None,
    project: str | None = None,
    limit: int = 100,
) -> dict:
    """Get events in a time window.

    Args:
        start: Start time (ISO format, default: 24 hours ago)
        end: End time (ISO format, default: now)
        tool: Optional tool name filter
        project: Optional project path filter
        limit: Maximum events to return (default: 100)

    Returns:
        Timeline of events
    """
    from datetime import datetime

    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None

    queries.ensure_fresh_data(storage)
    result = queries.query_timeline(
        storage, start=start_dt, end=end_dt, tool=tool, project=project, limit=limit
    )
    return {"status": "ok", **result}


@mcp.tool()
def query_commands(days: int = 7, project: str | None = None, prefix: str | None = None) -> dict:
    """Get Bash command breakdown.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter
        prefix: Optional command prefix filter (e.g., "git")

    Returns:
        Command frequency breakdown
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_commands(storage, days=days, project=project, prefix=prefix)
    return {"status": "ok", **result}


@mcp.tool()
def query_sessions(days: int = 7, project: str | None = None) -> dict:
    """Get session metadata.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter

    Returns:
        Session information
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_sessions(storage, days=days, project=project)
    return {"status": "ok", **result}


@mcp.tool()
def query_tokens(days: int = 7, project: str | None = None, by: str = "day") -> dict:
    """Get token usage analysis.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter
        by: Grouping: 'day', 'session', or 'model' (default: 'day')

    Returns:
        Token usage breakdown
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_tokens(storage, days=days, project=project, by=by)
    return {"status": "ok", **result}


@mcp.tool()
def query_sequences(days: int = 7, min_count: int = 3, length: int = 2) -> dict:
    """Get common tool patterns (sequences).

    Args:
        days: Number of days to analyze (default: 7)
        min_count: Minimum occurrences to include (default: 3)
        length: Sequence length (default: 2)

    Returns:
        Common tool sequences
    """
    queries.ensure_fresh_data(storage, days=days)
    sequence_patterns = patterns.compute_sequence_patterns(
        storage, days=days, sequence_length=length, min_count=min_count
    )
    return {
        "status": "ok",
        "days": days,
        "min_count": min_count,
        "sequence_length": length,
        "sequences": [{"pattern": p.pattern_key, "count": p.count} for p in sequence_patterns],
    }


@mcp.tool()
def sample_sequences(pattern: str, count: int = 5, context_events: int = 2, days: int = 7) -> dict:
    """Get random samples of a sequence pattern with surrounding context.

    Instead of just counting "Read → Edit" occurrences, returns actual examples
    with context for LLM interpretation of workflow patterns.

    Args:
        pattern: Sequence pattern (e.g., "Read → Edit" or "Read,Edit")
        count: Number of random samples to return (default: 5)
        context_events: Number of events before/after to include (default: 2)
        days: Number of days to analyze (default: 7)

    Returns:
        Pattern info, total occurrences, and sampled instances with context
    """
    queries.ensure_fresh_data(storage, days=days)
    result = patterns.sample_sequences(
        storage, pattern=pattern, count=count, context_events=context_events, days=days
    )
    return {"status": "ok", **result}


@mcp.tool()
def query_permission_gaps(days: int = 7, threshold: int = 5) -> dict:
    """Find commands that may need to be added to settings.json.

    Args:
        days: Number of days to analyze (default: 7)
        threshold: Minimum usage count to suggest (default: 5)

    Returns:
        Commands that are frequently used but not in allowed list
    """
    queries.ensure_fresh_data(storage, days=days)
    gap_patterns = patterns.compute_permission_gaps(storage, days=days, threshold=threshold)
    return {
        "status": "ok",
        "days": days,
        "threshold": threshold,
        "gaps": [
            {
                "command": p.pattern_key,
                "count": p.count,
                "suggestion": p.metadata.get("suggestion", ""),
            }
            for p in gap_patterns
        ],
    }


@mcp.tool()
def get_user_journey(hours: int = 24, include_projects: bool = True, limit: int = 100) -> dict:
    """Get all user messages chronologically across sessions.

    Shows how the user moved across sessions and projects over time,
    revealing task switching, project interleaving, and work patterns.

    Args:
        hours: Number of hours to look back (default: 24)
        include_projects: Include project info in output (default: True)
        limit: Maximum messages to return (default: 100)

    Returns:
        Journey events with timestamps, sessions, and messages
    """
    queries.ensure_fresh_data(storage, days=max(1, hours // 24 + 1))
    result = queries.get_user_journey(
        storage, hours=hours, include_projects=include_projects, limit=limit
    )
    return {"status": "ok", **result}


@mcp.tool()
def search_messages(query: str, limit: int = 50, project: str | None = None) -> dict:
    """Search user messages using full-text search.

    Uses FTS5 to efficiently search across all user messages. Useful for finding
    discussions about specific topics, decisions, or patterns across sessions.

    Note: Searches user messages only, not assistant responses.

    Args:
        query: FTS5 query string. Supports:
            - Simple terms: "authentication"
            - Phrases: '"fix the bug"'
            - Boolean: "auth AND error", "skip OR defer"
            - Prefix: "implement*"
        limit: Maximum results to return (default: 50)
        project: Optional project path filter

    Returns:
        Matching messages with session context and timestamps
    """
    queries.ensure_fresh_data(storage)
    try:
        results = storage.search_user_messages(query, limit=limit, project=project)
    except sqlite3.OperationalError as e:
        # Catch FTS5-related errors (syntax, unterminated strings, etc.)
        return {
            "status": "error",
            "query": query,
            "error": f"Invalid FTS5 query syntax: {e}",
        }
    return {
        "status": "ok",
        "query": query,
        "project": project,
        "count": len(results),
        "messages": [
            {
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "session_id": e.session_id,
                "project": e.project_path,
                "message": e.user_message_text,
            }
            for e in results
        ],
    }


@mcp.tool()
def detect_parallel_sessions(hours: int = 24, min_overlap_minutes: int = 5) -> dict:
    """Find sessions that were active simultaneously.

    Identifies when multiple sessions were active at the same time,
    indicating worktree usage, waiting on CI, or multi-task work.

    Args:
        hours: Number of hours to look back (default: 24)
        min_overlap_minutes: Minimum overlap to consider parallel (default: 5)

    Returns:
        Parallel session periods with timing and session details
    """
    queries.ensure_fresh_data(storage, days=max(1, hours // 24 + 1))
    result = queries.detect_parallel_sessions(
        storage, hours=hours, min_overlap_minutes=min_overlap_minutes
    )
    return {"status": "ok", **result}


@mcp.tool()
def find_related_sessions(
    session_id: str, method: str = "files", days: int = 7, limit: int = 10
) -> dict:
    """Find sessions related to a given session.

    Identifies sessions that share common files, commands, or temporal proximity.

    Args:
        session_id: The session ID to find related sessions for
        method: How to find related: 'files', 'commands', or 'temporal' (default: 'files')
        days: Number of days to search (default: 7)
        limit: Maximum related sessions to return (default: 10)

    Returns:
        Related sessions with their connection details
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.find_related_sessions(
        storage, session_id=session_id, method=method, days=days, limit=limit
    )
    return {"status": "ok", **result}


@mcp.tool()
def get_insights(refresh: bool = False, days: int = 7, include_advanced: bool = True) -> dict:
    """Get pre-computed patterns for /improve-workflow.

    Includes traditional pattern analysis plus advanced analytics from RFC #17:
    trends, failure analysis, and session classification summaries.

    Args:
        refresh: Force recomputation of patterns (default: False)
        days: Number of days to analyze if refreshing (default: 7)
        include_advanced: Include trends, failures, classification (default: True)

    Returns:
        Insights organized by type with optional advanced analytics
    """
    queries.ensure_fresh_data(storage, days=days)
    result = patterns.get_insights(
        storage, refresh=refresh, days=days, include_advanced=include_advanced
    )
    return {"status": "ok", **result}


@mcp.tool()
def analyze_failures(days: int = 7, rework_window_minutes: int = 10) -> dict:
    """Analyze failure patterns and recovery behavior.

    Identifies tool errors, rework patterns (same file edited multiple times),
    and error clustering by tool/command.

    Args:
        days: Number of days to analyze (default: 7)
        rework_window_minutes: Time window for detecting rework (default: 10)

    Returns:
        Failure analysis including error counts, rework patterns, and recovery times
    """
    queries.ensure_fresh_data(storage, days=days)
    result = patterns.analyze_failures(
        storage, days=days, rework_window_minutes=rework_window_minutes
    )
    return {"status": "ok", **result}


@mcp.tool()
def classify_sessions(days: int = 7, project: str | None = None) -> dict:
    """Classify sessions based on their dominant activity patterns.

    Categories include: debugging (high error rate), development (edit-heavy),
    research (read/search heavy), maintenance (CI/git heavy), mixed.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project filter

    Returns:
        Session classifications with category distribution
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.classify_sessions(storage, days=days, project=project)
    return {"status": "ok", **result}


@mcp.tool()
def get_handoff_context(
    session_id: str | None = None, hours: int = 4, message_limit: int = 10
) -> dict:
    """Get context for session handoff (useful for /status-report).

    Provides recent activity summary including last user messages,
    files modified, commands run, and session duration/activity stats.

    Args:
        session_id: Specific session ID (default: most recent session)
        hours: Hours to look back if no session specified (default: 4)
        message_limit: Maximum messages to return (default: 10)

    Returns:
        Handoff context including messages, files, commands, and activity summary
    """
    queries.ensure_fresh_data(storage, days=max(1, hours // 24 + 1))
    result = queries.get_handoff_context(
        storage, session_id=session_id, hours=hours, message_limit=message_limit
    )
    return {"status": "ok", **result}


@mcp.tool()
def analyze_trends(days: int = 7, compare_to: str = "previous") -> dict:
    """Analyze trends by comparing current period to previous period.

    Compares metrics between two time periods to identify changes in usage patterns.

    Args:
        days: Length of current period in days (default: 7)
        compare_to: 'previous' (same length before current) or 'same_last_month' (default: previous)

    Returns:
        Trend analysis including percentage changes and direction for events, sessions, errors, tokens
    """
    queries.ensure_fresh_data(storage, days=days * 2)
    result = patterns.analyze_trends(storage, days=days, compare_to=compare_to)
    return {"status": "ok", **result}


@mcp.tool()
def ingest_git_history(
    repo_path: str | None = None, days: int = 7, project_path: str | None = None
) -> dict:
    """Ingest git commit history from a repository.

    Parses git log and stores commits for correlation with session activity.

    Args:
        repo_path: Path to git repository (default: current directory)
        days: Number of days of history to ingest (default: 7)
        project_path: Optional project path to associate commits with

    Returns:
        Ingestion stats including commits found and added
    """
    result = ingest.ingest_git_history(
        storage, repo_path=repo_path, days=days, project_path=project_path
    )
    return {"status": "ok", **result}


@mcp.tool()
def correlate_git_with_sessions(days: int = 7) -> dict:
    """Correlate git commits with session activity.

    Associates commits with sessions based on timing.

    Args:
        days: Number of days to correlate (default: 7)

    Returns:
        Correlation stats including commits correlated
    """
    result = ingest.correlate_git_with_sessions(storage, days=days)
    return {"status": "ok", **result}


@mcp.tool()
def get_session_signals(days: int = 7, min_events: int = 5) -> dict:
    """Get raw session signals for LLM interpretation.

    RFC #26 (revised per RFC #17 principle): Extracts observable session data
    without interpretation. Per RFC #17: "Don't over-distill - raw data with
    light structure beats heavily processed summaries. The LLM can handle context."

    Returns raw signals like event counts, error rates, commit counts, and
    boolean flags (has_rework, has_pr_activity). The consuming LLM should
    interpret these to determine outcomes like success or abandonment.

    Args:
        days: Number of days to analyze (default: 7)
        min_events: Minimum events for a session to be included (default: 5)

    Returns:
        Raw session signals for LLM interpretation
    """
    queries.ensure_fresh_data(storage, days=days)
    result = patterns.get_session_signals(storage, days=days, min_events=min_events)
    return {"status": "ok", **result}


@mcp.tool()
def get_session_commits(session_id: str | None = None, days: int = 7) -> dict:
    """Get commits associated with sessions.

    RFC #26: Returns commits linked to sessions with timing metadata:
    - time_to_commit_seconds: Time from session start to commit
    - is_first_commit: Whether this was the first commit in the session

    Args:
        session_id: Specific session ID (optional, returns all if not specified)
        days: Number of days to look back (default: 7)

    Returns:
        Session-commit mappings with timing metadata
    """
    queries.ensure_fresh_data(storage, days=days)

    if session_id:
        commits = storage.get_session_commits(session_id)
        return {
            "status": "ok",
            "session_id": session_id,
            "commit_count": len(commits),
            "commits": commits,
        }
    else:
        # Get all session commits
        result = storage.get_commits_for_sessions()
        total_commits = sum(len(commits) for commits in result.values())
        return {
            "status": "ok",
            "session_count": len(result),
            "total_commits": total_commits,
            "sessions": result,
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

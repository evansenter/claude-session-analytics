"""MCP Session Analytics Server.

Provides tools for querying Claude Code session logs:
- ingest_logs: Refresh data from JSONL files
- list_sessions: Session metadata
- get_session_events: Events for a session/time window
- get_session_messages: User messages across sessions
- get_session_signals: Raw session signals for LLM interpretation
- get_session_commits: Session-commit mappings
- get_tool_frequency: Tool usage counts
- get_command_frequency: Bash command breakdown
- get_tool_sequences: Common tool patterns
- get_token_usage: Token usage analysis
- get_permission_gaps: Commands needing settings.json
- get_insights: Pre-computed patterns for /improve-workflow
- get_status: Ingestion status + DB stats
- search_messages: Full-text search on user messages
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
def get_tool_frequency(days: int = 7, project: str | None = None, expand: bool = True) -> dict:
    """Get tool usage frequency counts.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter
        expand: Include breakdown for Skill (by skill_name), Task (by subagent_type),
                and Bash (by command). Default: True

    Returns:
        Tool frequency breakdown with optional nested breakdowns
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_tool_frequency(storage, days=days, project=project, expand=expand)
    return {"status": "ok", **result}


@mcp.tool()
def get_session_events(
    start: str | None = None,
    end: str | None = None,
    tool: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    limit: int = 100,
) -> dict:
    """Get events in a time window or for a specific session.

    Args:
        start: Start time (ISO format, default: 24 hours ago)
        end: End time (ISO format, default: now)
        tool: Optional tool name filter
        project: Optional project path filter
        session_id: Optional session ID filter (get full session trace)
        limit: Maximum events to return (default: 100)

    Returns:
        Timeline of events
    """
    from datetime import datetime

    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None

    queries.ensure_fresh_data(storage)
    result = queries.query_timeline(
        storage,
        start=start_dt,
        end=end_dt,
        tool=tool,
        project=project,
        session_id=session_id,
        limit=limit,
    )
    return {"status": "ok", **result}


@mcp.tool()
def get_command_frequency(
    days: int = 7, project: str | None = None, prefix: str | None = None
) -> dict:
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
def list_sessions(days: int = 7, project: str | None = None) -> dict:
    """List all sessions with metadata.

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
def get_token_usage(days: int = 7, project: str | None = None, by: str = "day") -> dict:
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
def get_tool_sequences(
    days: int = 7,
    min_count: int = 3,
    length: int = 2,
    expand: bool = False,
    limit: int = 50,
) -> dict:
    """Get common tool patterns (sequences).

    Args:
        days: Number of days to analyze (default: 7)
        min_count: Minimum occurrences to include (default: 3)
        length: Sequence length (default: 2)
        expand: Expand Bash→commands, Skill→skill names, Task→subagent types (default: False)
        limit: Maximum patterns to return (default: 50)

    Returns:
        Common tool sequences
    """
    queries.ensure_fresh_data(storage, days=days)
    sequence_patterns = patterns.compute_sequence_patterns(
        storage, days=days, sequence_length=length, min_count=min_count, expand=expand
    )
    # Apply limit to prevent large responses
    limited_patterns = sequence_patterns[:limit] if limit > 0 else sequence_patterns
    return {
        "status": "ok",
        "days": days,
        "min_count": min_count,
        "sequence_length": length,
        "expanded": expand,
        "limit": limit,
        "total_patterns": len(sequence_patterns),
        "sequences": [{"pattern": p.pattern_key, "count": p.count} for p in limited_patterns],
    }


@mcp.tool()
def sample_sequences(
    pattern: str,
    limit: int = 5,
    context_events: int = 2,
    days: int = 7,
    expand: bool = False,
) -> dict:
    """Get random samples of a sequence pattern with surrounding context.

    Instead of just counting "Read → Edit" occurrences, returns actual examples
    with context for LLM interpretation of workflow patterns.

    Args:
        pattern: Sequence pattern (e.g., "Read → Edit" or "Read,Edit")
        limit: Number of random samples to return (default: 5)
        context_events: Number of events before/after to include (default: 2)
        days: Number of days to analyze (default: 7)
        expand: If True, match expanded tool names (Bash→command, Skill→skill_name,
                Task→subagent_type). Use with patterns from get_tool_sequences(expand=True).

    Returns:
        Pattern info, total occurrences, and sampled instances with context
    """
    queries.ensure_fresh_data(storage, days=days)
    return patterns.sample_sequences(
        storage,
        pattern=pattern,
        count=limit,
        context_events=context_events,
        days=days,
        expand=expand,
    )


@mcp.tool()
def get_permission_gaps(days: int = 7, min_count: int = 5) -> dict:
    """Find commands that may need to be added to settings.json.

    Args:
        days: Number of days to analyze (default: 7)
        min_count: Minimum usage count to suggest (default: 5)

    Returns:
        Commands that are frequently used but not in allowed list
    """
    queries.ensure_fresh_data(storage, days=days)
    gap_patterns = patterns.compute_permission_gaps(storage, days=days, threshold=min_count)
    return {
        "status": "ok",
        "days": days,
        "min_count": min_count,
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
def get_session_messages(
    days: float = 1,
    include_projects: bool = True,
    session_id: str | None = None,
    limit: int = 100,
    entry_types: list[str] | None = None,
    max_message_length: int = 500,
) -> dict:
    """Get messages chronologically across sessions.

    Shows how the user moved across sessions and projects over time,
    revealing task switching, project interleaving, and work patterns.
    Includes both user messages and assistant responses for conversation replay.

    Args:
        days: Number of days to look back (default: 1, supports fractions like 0.5 for 12h)
        include_projects: Include project info in output (default: True)
        session_id: Optional session ID filter (get messages from specific session)
        limit: Maximum messages to return (default: 100)
        entry_types: Which entry types to include (default: ["user", "assistant"])
        max_message_length: Truncate messages to this length (default: 500, 0=no limit)

    Returns:
        Journey events with timestamps, sessions, and messages
    """
    hours = int(days * 24)
    queries.ensure_fresh_data(storage, days=max(1, int(days) + 1))
    result = queries.get_user_journey(
        storage,
        hours=hours,
        include_projects=include_projects,
        session_id=session_id,
        limit=limit,
        entry_types=entry_types,
        max_message_length=max_message_length,
    )
    return {"status": "ok", **result}


@mcp.tool()
def search_messages(
    query: str,
    limit: int = 50,
    project: str | None = None,
    entry_types: list[str] | None = None,
) -> dict:
    """Search messages using full-text search.

    Uses FTS5 to efficiently search across all message types (user, assistant,
    tool_result, summary). Useful for finding discussions about specific topics,
    decisions, or patterns across sessions.

    Args:
        query: FTS5 query string. Supports:
            - Simple terms: "authentication"
            - Phrases: '"fix the bug"'
            - Boolean: "auth AND error", "skip OR defer"
            - Prefix: "implement*"
        limit: Maximum results to return (default: 50)
        project: Optional project path filter
        entry_types: Optional list of entry types to filter (e.g., ["user", "assistant"])

    Returns:
        Matching messages with session context and timestamps
    """
    queries.ensure_fresh_data(storage)
    try:
        results = storage.search_messages(
            query, limit=limit, project=project, entry_types=entry_types
        )
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
        "entry_types": entry_types,
        "count": len(results),
        "messages": [
            {
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "session_id": e.session_id,
                "project": e.project_path,
                "type": e.entry_type,
                "message": e.message_text,
            }
            for e in results
        ],
    }


@mcp.tool()
def detect_parallel_sessions(days: float = 1, min_overlap_minutes: int = 5) -> dict:
    """Find sessions that were active simultaneously.

    Identifies when multiple sessions were active at the same time,
    indicating worktree usage, waiting on CI, or multi-task work.

    Args:
        days: Number of days to look back (default: 1, supports fractions like 0.5 for 12h)
        min_overlap_minutes: Minimum overlap to consider parallel (default: 5)

    Returns:
        Parallel session periods with timing and session details
    """
    hours = int(days * 24)
    queries.ensure_fresh_data(storage, days=max(1, int(days) + 1))
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
def get_error_details(days: int = 7, tool: str | None = None, limit: int = 50) -> dict:
    """Get detailed error information including tool parameters that caused failures.

    Shows which specific patterns (Glob/Grep), commands (Bash), or files caused errors.
    Use this to drill down from analyze_failures() counts to actionable specifics.

    Args:
        days: Number of days to analyze (default: 7)
        tool: Optional filter by tool name (e.g., "Glob", "Bash", "Edit")
        limit: Maximum errors to return per tool (default: 50)

    Returns:
        Error details grouped by tool with the failing parameter (pattern/command/file)
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.query_error_details(storage, days=days, tool=tool, limit=limit)
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
def get_handoff_context(session_id: str | None = None, days: float = 0.17, limit: int = 10) -> dict:
    """Get context for session handoff (useful for /status-report).

    Provides recent activity summary including last user messages,
    files modified, commands run, and session duration/activity stats.

    Args:
        session_id: Specific session ID (default: most recent session)
        days: Days to look back if no session specified (default: 0.17 = ~4 hours)
        limit: Maximum messages to return (default: 10)

    Returns:
        Handoff context including messages, files, commands, and activity summary
    """
    hours = int(days * 24)
    queries.ensure_fresh_data(storage, days=max(1, int(days) + 1))
    result = queries.get_handoff_context(
        storage, session_id=session_id, hours=hours, message_limit=limit
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
def ingest_git_history_all_projects(days: int = 7) -> dict:
    """Ingest git commit history from all known projects.

    Scans unique project paths from the events table, decodes them to filesystem
    paths, and runs git ingestion on each that has a .git directory.

    This is more comprehensive than ingest_git_history() which only processes
    the current directory.

    Args:
        days: Number of days of history to ingest (default: 7)

    Returns:
        Aggregate stats across all projects including total commits added
    """
    result = ingest.ingest_git_history_all_projects(storage, days=days)
    return {"status": "ok", **result}


@mcp.tool()
def get_session_signals(days: int = 7, min_count: int = 1) -> dict:
    """Get raw session signals for LLM interpretation.

    RFC #26 (revised per RFC #17 principle): Extracts observable session data
    without interpretation. Per RFC #17: "Don't over-distill - raw data with
    light structure beats heavily processed summaries. The LLM can handle context."

    Returns raw signals like event counts, error rates, commit counts, and
    boolean flags (has_rework, has_pr_activity). The consuming LLM should
    interpret these to determine outcomes like success or abandonment.

    Args:
        days: Number of days to analyze (default: 7)
        min_count: Minimum events for a session to be included (default: 1)

    Returns:
        Raw session signals for LLM interpretation
    """
    queries.ensure_fresh_data(storage, days=days)
    result = patterns.get_session_signals(storage, days=days, min_count=min_count)
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


@mcp.tool()
def get_file_activity(
    days: int = 7,
    project: str | None = None,
    limit: int = 20,
    collapse_worktrees: bool = False,
) -> dict:
    """Get file activity (reads, edits, writes) with breakdown.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter
        limit: Maximum files to return (default: 20)
        collapse_worktrees: If True, consolidate .worktrees/<branch>/ paths

    Returns:
        File activity data with read/edit/write breakdown per file
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_file_activity(
        storage,
        days=days,
        project=project,
        limit=limit,
        collapse_worktrees=collapse_worktrees,
    )
    return {"status": "ok", **result}


@mcp.tool()
def get_languages(days: int = 7, project: str | None = None) -> dict:
    """Get language distribution from file extensions.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter

    Returns:
        Language distribution with counts and percentages
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_languages(storage, days=days, project=project)
    return {"status": "ok", **result}


@mcp.tool()
def get_projects(days: int = 7) -> dict:
    """Get activity breakdown by project.

    Note: No project filter - this shows activity *across* all projects.

    Args:
        days: Number of days to analyze (default: 7)

    Returns:
        Project activity data with event counts and session counts per project
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.query_projects(storage, days=days)
    return {"status": "ok", **result}


@mcp.tool()
def get_mcp_usage(days: int = 7, project: str | None = None) -> dict:
    """Get MCP server and tool usage breakdown.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter

    Returns:
        MCP usage grouped by server with tool breakdown
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_mcp_usage(storage, days=days, project=project)
    return {"status": "ok", **result}


@mcp.tool()
def get_agent_activity(days: int = 7, project: str | None = None) -> dict:
    """Get activity breakdown by Task subagent.

    RFC #41: Tracks agent activity from Task tool invocations,
    distinguishing work done by agents vs main session.

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter

    Returns:
        Dict with agent activity breakdown including:
        - Main session stats (agent_id IS NULL)
        - Per-agent stats with token usage and top tools
        - Summary with agent vs main session token percentage
    """
    queries.ensure_fresh_data(storage, days=days, project=project)
    result = queries.query_agent_activity(storage, days=days, project=project)
    return {"status": "ok", **result}


@mcp.tool()
def ingest_bus_events(days: int = 7) -> dict:
    """Ingest events from event-bus for cross-session insights.

    Reads from ~/.claude/contrib/event-bus/data.db and stores
    events for correlation with session activity.

    Args:
        days: Number of days to ingest on first run (default: 7)

    Returns:
        Ingestion statistics including events_ingested count
    """
    from session_analytics.bus_ingest import ingest_bus_events as do_ingest

    result = do_ingest(storage, days=days)
    return {"status": "ok", **result}


@mcp.tool()
def get_bus_events(
    days: int = 7,
    event_type: str | None = None,
    session_id: str | None = None,
    repo: str | None = None,
    limit: int = 100,
) -> dict:
    """Get event-bus events with optional filters.

    Returns raw events from the event-bus for cross-session insights.
    Events include gotcha_discovered, pattern_found, help_needed, etc.

    Args:
        days: Number of days to analyze (default: 7)
        event_type: Filter by event type (e.g., 'gotcha_discovered')
        session_id: Filter by session ID
        repo: Filter by repo name
        limit: Maximum events to return (default: 100)

    Returns:
        Event-bus events with breakdown by type
    """
    result = queries.query_bus_events(
        storage,
        days=days,
        event_type=event_type,
        session_id=session_id,
        repo=repo,
        limit=limit,
    )
    return {"status": "ok", **result}


# Issue #69: Compaction detection and context efficiency tools


@mcp.tool()
def get_compaction_events(
    days: int = 7,
    session_id: str | None = None,
    limit: int = 50,
    aggregate: bool = False,
) -> dict:
    """List compaction events (context resets) across sessions.

    Compactions occur when Claude's context window fills and is summarized.
    This helps identify sessions that hit context limits.

    Args:
        days: Number of days to analyze (default: 7)
        session_id: Filter to specific session
        limit: Maximum events to return (default: 50)
        aggregate: If True, group by session with counts instead of individual events

    Returns:
        List of compaction events with timestamps and session info
        (or session aggregates if aggregate=True)
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.get_compaction_events(
        storage, days=days, session_id=session_id, limit=limit, aggregate=aggregate
    )
    return {"status": "ok", **result}


@mcp.tool()
def get_pre_compaction_events(
    session_id: str,
    compaction_timestamp: str,
    limit: int = 50,
) -> dict:
    """Get events that occurred before a compaction event.

    Use this to understand what was happening in the session
    leading up to a context reset.

    Args:
        session_id: The session to analyze
        compaction_timestamp: ISO timestamp of the compaction event
        limit: Maximum events to return (default: 50)

    Returns:
        Events before the compaction, ordered by timestamp descending (most recent first)
    """
    queries.ensure_fresh_data(storage, days=7)
    result = queries.get_pre_compaction_events(
        storage,
        session_id=session_id,
        compaction_timestamp=compaction_timestamp,
        limit=limit,
    )
    return {"status": "ok", **result}


@mcp.tool()
def analyze_pre_compaction_patterns(
    days: int = 7,
    events_before: int = 50,
    limit: int = 20,
) -> dict:
    """Analyze patterns in events leading up to compactions.

    RFC #81: Identifies antipatterns that accelerate context exhaustion:
    - Consecutive reads without edits (exploration without action)
    - Files read multiple times before compaction
    - Large tool results that bloated context
    - Tool distribution before compaction

    Args:
        days: Number of days to analyze (default: 7)
        events_before: Events to analyze before each compaction (default: 50)
        limit: Max compactions to analyze (default: 20)

    Returns:
        Dict with aggregated patterns and recommendations
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.analyze_pre_compaction_patterns(
        storage, days=days, events_before=events_before, limit=limit
    )
    return {"status": "ok", **result}


@mcp.tool()
def get_large_tool_results(
    days: int = 7,
    min_size_kb: int = 10,
    limit: int = 50,
) -> dict:
    """Find tool results that consumed significant context space.

    Helps identify bloat patterns - large file reads, verbose command
    outputs, or other operations that accelerate context exhaustion.

    Args:
        days: Number of days to analyze (default: 7)
        min_size_kb: Minimum result size in KB to include (default: 10)
        limit: Maximum results to return (default: 50)

    Returns:
        Large tool results with size, tool name, and parameters
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.get_large_tool_results(
        storage, days=days, min_size_kb=min_size_kb, limit=limit
    )
    return {"status": "ok", **result}


@mcp.tool()
def get_session_efficiency(
    days: int = 7,
    project: str | None = None,
    limit: int = 50,
) -> dict:
    """Analyze context efficiency and burn rate across sessions.

    Calculates metrics like:
    - Total context bytes consumed per session
    - Average result size
    - Compaction count (context resets)
    - Efficiency ratio (output/input bytes)

    Args:
        days: Number of days to analyze (default: 7)
        project: Optional project path filter
        limit: Maximum sessions to return (default: 50)

    Returns:
        Session efficiency metrics sorted by total bytes consumed
    """
    queries.ensure_fresh_data(storage, days=days)
    result = queries.get_session_efficiency(storage, days=days, project=project, limit=limit)
    return {"status": "ok", **result}


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

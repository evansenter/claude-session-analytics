"""Query implementations for session analytics."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from session_analytics.storage import SQLiteStorage


def _format_timestamp(ts) -> str | None:
    """Format a timestamp value for output.

    Handles both datetime objects and strings from SQLite.
    """
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts  # Already a string
    return ts.isoformat()


def build_where_clause(
    cutoff: datetime | None = None,
    cutoff_column: str = "timestamp",
    project: str | None = None,
    extra_conditions: list[str] | None = None,
) -> tuple[str, list]:
    """Build a WHERE clause with common query filters.

    Args:
        cutoff: Datetime for cutoff filter (>= comparison)
        cutoff_column: Column name for cutoff (default: "timestamp")
        project: Optional project path filter (LIKE %project%)
        extra_conditions: Additional WHERE conditions to include

    Returns:
        Tuple of (where_clause_string, params_list)
    """
    conditions = []
    params: list = []

    if cutoff:
        conditions.append(f"{cutoff_column} >= ?")
        params.append(cutoff)

    if project:
        conditions.append("project_path LIKE ?")
        params.append(f"%{project}%")

    if extra_conditions:
        conditions.extend(extra_conditions)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    return where_clause, params


def get_cutoff(days: int | float = 7, hours: float = 0) -> datetime:
    """Calculate cutoff datetime from days/hours ago.

    Args:
        days: Number of days to look back (can be fractional)
        hours: Additional hours to look back

    Returns:
        datetime representing the cutoff point
    """
    total_hours = (days * 24) + hours
    return datetime.now() - timedelta(hours=total_hours)


def normalize_datetime(dt: datetime) -> datetime:
    """Normalize a datetime to naive (no timezone) for comparison.

    Git commits may have timezone info while session timestamps from SQLite
    may not. This strips timezone info to enable safe comparisons.

    Args:
        dt: datetime that may or may not have timezone info

    Returns:
        Naive datetime (tzinfo=None)
    """
    if dt.tzinfo is not None:
        # Strip timezone info, preserving local time values.
        # We intentionally don't convert to UTC because session timestamps
        # in SQLite are naive local time, and git commits represent the same
        # local time just with timezone info attached.
        return dt.replace(tzinfo=None)
    return dt


def ensure_fresh_data(
    storage: SQLiteStorage,
    max_age_minutes: int = 5,
    days: int = 7,
    project: str | None = None,
    force: bool = False,
) -> bool:
    """Check if data is stale and refresh if needed.

    Args:
        storage: Storage instance
        max_age_minutes: Maximum age of data before refresh
        days: Number of days to look back when refreshing
        project: Optional project filter for refresh
        force: Force refresh regardless of age

    Returns:
        True if data was refreshed, False if data was fresh
    """
    if force:
        from session_analytics.ingest import ingest_logs

        ingest_logs(storage, days=days, project=project)
        return True

    last_ingest = storage.get_last_ingestion_time()
    if last_ingest is None or (datetime.now() - last_ingest) > timedelta(minutes=max_age_minutes):
        from session_analytics.ingest import ingest_logs

        ingest_logs(storage, days=days, project=project)
        return True

    return False


def query_tool_frequency(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
    expand: bool = True,
) -> dict:
    """Get tool usage frequency counts.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter
        expand: Include breakdown for Skill, Task, and Bash (default: True)

    Returns:
        Dict with tool frequency breakdown
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name IS NOT NULL"],
    )

    # Get tool frequency counts
    rows = storage.execute_query(
        f"""
        SELECT tool_name, COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY tool_name
        ORDER BY count DESC
        """,
        params,
    )

    tools = [{"tool": row["tool_name"], "count": row["count"]} for row in rows]

    # Get command count (slash commands from ~/.claude/commands)
    # These are tracked separately as entry_type='command', not tool_name
    cmd_where, cmd_params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["entry_type = 'command'"],
    )
    cmd_rows = storage.execute_query(
        f"SELECT COUNT(*) as count FROM events WHERE {cmd_where}",
        cmd_params,
    )
    command_count = cmd_rows[0]["count"] if cmd_rows else 0

    # Add breakdowns if expand=True
    command_breakdown = []
    if expand:
        # Build breakdown queries with same filters
        skill_breakdown = _get_skill_breakdown(storage, cutoff, project)
        task_breakdown = _get_task_breakdown(storage, cutoff, project)
        bash_breakdown = _get_bash_breakdown(storage, cutoff, project)
        command_breakdown = _get_command_breakdown(storage, cutoff, project)

        # Attach breakdowns to respective tools
        for tool in tools:
            if tool["tool"] == "Skill" and skill_breakdown:
                tool["breakdown"] = skill_breakdown
            elif tool["tool"] == "Task" and task_breakdown:
                tool["breakdown"] = task_breakdown
            elif tool["tool"] == "Bash" and bash_breakdown:
                tool["breakdown"] = bash_breakdown

    # Insert Command entry in sorted position (by count)
    if command_count > 0:
        command_entry = {"tool": "Command", "count": command_count}
        if command_breakdown:
            command_entry["breakdown"] = command_breakdown
        # Find insertion point to maintain sorted order
        insert_idx = 0
        for i, t in enumerate(tools):
            if t["count"] < command_count:
                insert_idx = i
                break
            insert_idx = i + 1
        tools.insert(insert_idx, command_entry)

    return {
        "days": days,
        "project": project,
        "total_tool_calls": sum(t["count"] for t in tools),
        "tools": tools,
    }


def _get_skill_breakdown(
    storage: SQLiteStorage,
    cutoff: datetime,
    project: str | None = None,
) -> list[dict]:
    """Get Skill usage breakdown by skill_name."""
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name = 'Skill'", "skill_name IS NOT NULL"],
    )

    rows = storage.execute_query(
        f"""
        SELECT skill_name, COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY skill_name
        ORDER BY count DESC
        """,
        params,
    )

    return [{"name": row["skill_name"], "count": row["count"]} for row in rows]


def _get_command_breakdown(
    storage: SQLiteStorage,
    cutoff: datetime,
    project: str | None = None,
) -> list[dict]:
    """Get Command usage breakdown by command name (slash commands from ~/.claude/commands)."""
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["entry_type = 'command'", "skill_name IS NOT NULL"],
    )

    rows = storage.execute_query(
        f"""
        SELECT skill_name as command_name, COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY skill_name
        ORDER BY count DESC
        """,
        params,
    )

    return [{"name": row["command_name"], "count": row["count"]} for row in rows]


def _get_task_breakdown(
    storage: SQLiteStorage,
    cutoff: datetime,
    project: str | None = None,
) -> list[dict]:
    """Get Task usage breakdown by subagent_type."""
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name = 'Task'", "tool_input_json IS NOT NULL"],
    )

    rows = storage.execute_query(
        f"""
        SELECT
            json_extract(tool_input_json, '$.subagent_type') as subagent_type,
            COUNT(*) as count
        FROM events
        WHERE {where_clause}
          AND json_extract(tool_input_json, '$.subagent_type') IS NOT NULL
        GROUP BY subagent_type
        ORDER BY count DESC
        """,
        params,
    )

    return [{"name": row["subagent_type"], "count": row["count"]} for row in rows]


def _get_bash_breakdown(
    storage: SQLiteStorage,
    cutoff: datetime,
    project: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Get Bash usage breakdown by command prefix."""
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name = 'Bash'", "command IS NOT NULL"],
    )

    rows = storage.execute_query(
        f"""
        SELECT command, COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY command
        ORDER BY count DESC
        LIMIT ?
        """,
        (*params, limit),
    )

    return [{"name": row["command"], "count": row["count"]} for row in rows]


def query_timeline(
    storage: SQLiteStorage,
    start: datetime | None = None,
    end: datetime | None = None,
    tool: str | None = None,
    project: str | None = None,
    session_id: str | None = None,
    limit: int = 100,
) -> dict:
    """Get events in a time window.

    Args:
        storage: Storage instance
        start: Start of time window (default: 24 hours ago)
        end: End of time window (default: now)
        tool: Optional tool name filter
        project: Optional project path filter
        session_id: Optional session ID filter (get full session trace)
        limit: Maximum events to return

    Returns:
        Dict with timeline events
    """
    if start is None:
        start = get_cutoff(days=1)
    if end is None:
        end = datetime.now()

    events = storage.get_events_in_range(
        start=start,
        end=end,
        tool_name=tool,
        project_path=project,
        session_id=session_id,
        limit=limit,
    )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "tool": tool,
        "project": project,
        "session_id": session_id,
        "count": len(events),
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "session_id": e.session_id,
                "entry_type": e.entry_type,
                "tool_name": e.tool_name,
                "command": e.command,
                "file_path": e.file_path,
                "skill_name": e.skill_name,
                "is_error": e.is_error,
            }
            for e in events
        ],
    }


def query_commands(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
    prefix: str | None = None,
) -> dict:
    """Get Bash command breakdown.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter
        prefix: Optional command prefix filter (e.g., "git")

    Returns:
        Dict with command breakdown
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name = 'Bash'", "command IS NOT NULL"],
    )

    # Add prefix filter if specified
    if prefix:
        where_clause += " AND command LIKE ?"
        params.append(f"{prefix}%")

    # Get command frequency counts
    rows = storage.execute_query(
        f"""
        SELECT command, COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY command
        ORDER BY count DESC
        """,
        params,
    )

    commands = [{"command": row["command"], "count": row["count"]} for row in rows]

    return {
        "days": days,
        "project": project,
        "prefix": prefix,
        "total_commands": sum(c["count"] for c in commands),
        "commands": commands,
    }


def query_sessions(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
) -> dict:
    """Get session metadata.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter

    Returns:
        Dict with session information
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        cutoff_column="last_seen",
        project=project,
    )

    rows = storage.execute_query(
        f"""
        SELECT
            id, project_path, first_seen, last_seen,
            entry_count, tool_use_count,
            total_input_tokens, total_output_tokens,
            primary_branch
        FROM sessions
        WHERE {where_clause}
        ORDER BY last_seen DESC
        """,
        params,
    )

    sessions = [
        {
            "id": row["id"],
            "project": row["project_path"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "entry_count": row["entry_count"],
            "tool_use_count": row["tool_use_count"],
            "input_tokens": row["total_input_tokens"],
            "output_tokens": row["total_output_tokens"],
            "branch": row["primary_branch"],
        }
        for row in rows
    ]

    # Calculate totals
    total_entries = sum(s["entry_count"] for s in sessions)
    total_tools = sum(s["tool_use_count"] for s in sessions)
    total_input = sum(s["input_tokens"] or 0 for s in sessions)
    total_output = sum(s["output_tokens"] or 0 for s in sessions)

    return {
        "days": days,
        "project": project,
        "session_count": len(sessions),
        "total_entries": total_entries,
        "total_tool_uses": total_tools,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "sessions": sessions,
    }


def query_tokens(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
    by: str = "day",
) -> dict:
    """Get token usage analysis.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter
        by: Grouping: 'day', 'session', or 'model'

    Returns:
        Dict with token usage breakdown
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
    )

    if by == "day":
        # Group by day
        rows = storage.execute_query(
            f"""
            SELECT
                DATE(timestamp) as day,
                SUM(COALESCE(input_tokens, 0)) as input_tokens,
                SUM(COALESCE(output_tokens, 0)) as output_tokens,
                SUM(COALESCE(cache_read_tokens, 0)) as cache_read_tokens,
                SUM(COALESCE(cache_creation_tokens, 0)) as cache_creation_tokens,
                COUNT(*) as event_count
            FROM events
            WHERE {where_clause}
            GROUP BY DATE(timestamp)
            ORDER BY day DESC
            """,
            params,
        )

        breakdown = [
            {
                "day": row["day"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "cache_creation_tokens": row["cache_creation_tokens"],
                "event_count": row["event_count"],
            }
            for row in rows
        ]
        group_key = "day"

    elif by == "session":
        # Group by session
        rows = storage.execute_query(
            f"""
            SELECT
                session_id,
                project_path,
                SUM(COALESCE(input_tokens, 0)) as input_tokens,
                SUM(COALESCE(output_tokens, 0)) as output_tokens,
                SUM(COALESCE(cache_read_tokens, 0)) as cache_read_tokens,
                SUM(COALESCE(cache_creation_tokens, 0)) as cache_creation_tokens,
                COUNT(*) as event_count
            FROM events
            WHERE {where_clause}
            GROUP BY session_id
            ORDER BY input_tokens DESC
            """,
            params,
        )

        breakdown = [
            {
                "session_id": row["session_id"],
                "project": row["project_path"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "cache_creation_tokens": row["cache_creation_tokens"],
                "event_count": row["event_count"],
            }
            for row in rows
        ]
        group_key = "session"

    elif by == "model":
        # Group by model
        rows = storage.execute_query(
            f"""
            SELECT
                COALESCE(model, 'unknown') as model,
                SUM(COALESCE(input_tokens, 0)) as input_tokens,
                SUM(COALESCE(output_tokens, 0)) as output_tokens,
                SUM(COALESCE(cache_read_tokens, 0)) as cache_read_tokens,
                SUM(COALESCE(cache_creation_tokens, 0)) as cache_creation_tokens,
                COUNT(*) as event_count
            FROM events
            WHERE {where_clause}
            GROUP BY model
            ORDER BY input_tokens DESC
            """,
            params,
        )

        breakdown = [
            {
                "model": row["model"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "cache_creation_tokens": row["cache_creation_tokens"],
                "event_count": row["event_count"],
            }
            for row in rows
        ]
        group_key = "model"

    else:
        return {
            "error": f"Invalid grouping: {by}. Use 'day', 'session', or 'model'.",
        }

    # Calculate totals
    total_input = sum(b["input_tokens"] for b in breakdown)
    total_output = sum(b["output_tokens"] for b in breakdown)
    total_cache_read = sum(b["cache_read_tokens"] for b in breakdown)
    total_cache_creation = sum(b["cache_creation_tokens"] for b in breakdown)

    return {
        "days": days,
        "project": project,
        "group_by": group_key,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_creation_tokens": total_cache_creation,
        "breakdown": breakdown,
    }


# Phase 3: Cross-Session Timeline Functions


def get_user_journey(
    storage: SQLiteStorage,
    hours: int = 24,
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
        storage: Storage instance
        hours: Number of hours to look back (default: 24)
        include_projects: Include project info in output (default: True)
        session_id: Optional session ID filter (get messages from specific session)
        limit: Maximum messages to return (default: 100)
        entry_types: Which entry types to include (default: ["user", "assistant"])
        max_message_length: Truncate messages to this length (default: 500, 0=no limit)

    Returns:
        Dict with journey events and pattern analysis
    """
    cutoff = get_cutoff(hours=hours)

    # Default to user and assistant messages
    if entry_types is None:
        entry_types = ["user", "assistant"]

    # Build query with optional session_id filter
    session_filter = ""
    params: list = [cutoff]
    if session_id:
        session_filter = "AND session_id = ?"
        params.append(session_id)

    # Build entry_type filter
    type_placeholders = ",".join("?" * len(entry_types))
    params.extend(entry_types)
    params.append(limit)

    # Query messages ordered by timestamp
    rows = storage.execute_query(
        f"""
        SELECT
            timestamp,
            session_id,
            project_path,
            entry_type,
            message_text
        FROM events
        WHERE timestamp >= ?
          AND message_text IS NOT NULL
          {session_filter}
          AND entry_type IN ({type_placeholders})
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        tuple(params),
    )

    # Build journey events
    journey = []
    projects_seen = set()
    project_switches = 0
    last_project = None

    for row in rows:
        project = row["project_path"]
        if project:
            projects_seen.add(project)
            if last_project and project != last_project:
                project_switches += 1
            last_project = project

        # Truncate message if max_message_length is set
        message_text = row["message_text"]
        if message_text and max_message_length > 0:
            message_text = message_text[:max_message_length]

        event = {
            "timestamp": row["timestamp"].isoformat() if row["timestamp"] else None,
            "session_id": row["session_id"],
            "type": row["entry_type"],
            "message": message_text,
        }
        if include_projects:
            event["project"] = project
        journey.append(event)

    return {
        "hours": hours,
        "session_id": session_id,
        "entry_types": entry_types,
        "message_count": len(journey),
        "projects_visited": list(projects_seen) if include_projects else None,
        "project_switches": project_switches if include_projects else None,
        "journey": journey,
    }


def detect_parallel_sessions(
    storage: SQLiteStorage,
    hours: int = 24,
    min_overlap_minutes: int = 5,
) -> dict:
    """Find sessions that were active simultaneously.

    Identifies when multiple sessions were active at the same time,
    indicating worktree usage, waiting on CI, or multi-task work.

    Args:
        storage: Storage instance
        hours: Number of hours to look back (default: 24)
        min_overlap_minutes: Minimum overlap to consider parallel (default: 5)

    Returns:
        Dict with parallel session periods and analysis
    """
    cutoff = get_cutoff(hours=hours)

    # Get session activity ranges
    rows = storage.execute_query(
        """
        SELECT
            session_id,
            project_path,
            MIN(timestamp) as start_time,
            MAX(timestamp) as end_time,
            COUNT(*) as event_count
        FROM events
        WHERE timestamp >= ?
        GROUP BY session_id
        HAVING COUNT(*) > 1
        ORDER BY start_time
        """,
        (cutoff,),
    )

    sessions = []
    for row in rows:
        # Parse timestamps - they come from storage as datetime objects
        start_time = row["start_time"]
        end_time = row["end_time"]
        # Handle case where timestamps are strings (shouldn't happen but defensive)
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)

        sessions.append(
            {
                "session_id": row["session_id"],
                "project": row["project_path"],
                "start": start_time,
                "end": end_time,
                "event_count": row["event_count"],
            }
        )

    # Find overlapping periods
    parallel_periods = []
    min_overlap = timedelta(minutes=min_overlap_minutes)

    for i, s1 in enumerate(sessions):
        for s2 in sessions[i + 1 :]:
            # Calculate overlap
            overlap_start = max(s1["start"], s2["start"])
            overlap_end = min(s1["end"], s2["end"])

            if overlap_end > overlap_start:
                overlap_duration = overlap_end - overlap_start
                if overlap_duration >= min_overlap:
                    parallel_periods.append(
                        {
                            "start": overlap_start.isoformat(),
                            "end": overlap_end.isoformat(),
                            "duration_minutes": int(overlap_duration.total_seconds() / 60),
                            "sessions": [
                                {
                                    "session_id": s1["session_id"],
                                    "project": s1["project"],
                                },
                                {
                                    "session_id": s2["session_id"],
                                    "project": s2["project"],
                                },
                            ],
                        }
                    )

    # Sort by duration descending
    parallel_periods.sort(key=lambda x: x["duration_minutes"], reverse=True)

    return {
        "hours": hours,
        "min_overlap_minutes": min_overlap_minutes,
        "total_sessions": len(sessions),
        "parallel_period_count": len(parallel_periods),
        "parallel_periods": parallel_periods,
    }


def find_related_sessions(
    storage: SQLiteStorage,
    session_id: str,
    method: str = "files",
    days: int = 7,
    limit: int = 10,
) -> dict:
    """Find sessions related to a given session.

    Identifies sessions that share common files, commands, or temporal proximity.

    Args:
        storage: Storage instance
        session_id: The session ID to find related sessions for
        method: How to find related sessions: 'files', 'commands', or 'temporal'
        days: Number of days to search (default: 7)
        limit: Maximum related sessions to return (default: 10)

    Returns:
        Dict with related sessions and their connection strength
    """
    cutoff = get_cutoff(days=days)

    if method == "files":
        # Find sessions that touched the same files
        # First get files touched by the target session
        target_files = storage.execute_query(
            """
            SELECT DISTINCT file_path
            FROM events
            WHERE session_id = ? AND file_path IS NOT NULL
            """,
            (session_id,),
        )
        file_paths = [r["file_path"] for r in target_files]

        if not file_paths:
            return {
                "session_id": session_id,
                "method": method,
                "related_count": 0,
                "related_sessions": [],
            }

        # Find other sessions that touched these files
        placeholders = ",".join("?" * len(file_paths))
        rows = storage.execute_query(
            f"""
            SELECT
                session_id,
                project_path,
                COUNT(DISTINCT file_path) as shared_files,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen
            FROM events
            WHERE session_id != ?
              AND timestamp >= ?
              AND file_path IN ({placeholders})
            GROUP BY session_id
            ORDER BY shared_files DESC
            LIMIT ?
            """,
            (session_id, cutoff, *file_paths, limit),
        )

        related = [
            {
                "session_id": r["session_id"],
                "project": r["project_path"],
                "shared_files": r["shared_files"],
                "first_seen": _format_timestamp(r["first_seen"]),
                "last_seen": _format_timestamp(r["last_seen"]),
            }
            for r in rows
        ]

    elif method == "commands":
        # Find sessions that used the same commands
        target_commands = storage.execute_query(
            """
            SELECT DISTINCT command
            FROM events
            WHERE session_id = ? AND command IS NOT NULL
            """,
            (session_id,),
        )
        commands = [r["command"] for r in target_commands]

        if not commands:
            return {
                "session_id": session_id,
                "method": method,
                "related_count": 0,
                "related_sessions": [],
            }

        placeholders = ",".join("?" * len(commands))
        rows = storage.execute_query(
            f"""
            SELECT
                session_id,
                project_path,
                COUNT(DISTINCT command) as shared_commands,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen
            FROM events
            WHERE session_id != ?
              AND timestamp >= ?
              AND command IN ({placeholders})
            GROUP BY session_id
            ORDER BY shared_commands DESC
            LIMIT ?
            """,
            (session_id, cutoff, *commands, limit),
        )

        related = [
            {
                "session_id": r["session_id"],
                "project": r["project_path"],
                "shared_commands": r["shared_commands"],
                "first_seen": _format_timestamp(r["first_seen"]),
                "last_seen": _format_timestamp(r["last_seen"]),
            }
            for r in rows
        ]

    elif method == "temporal":
        # Find sessions that were active around the same time
        # Get the time range of the target session
        target_range = storage.execute_query(
            """
            SELECT MIN(timestamp) as start_time, MAX(timestamp) as end_time
            FROM events
            WHERE session_id = ?
            """,
            (session_id,),
        )

        if not target_range or not target_range[0]["start_time"]:
            return {
                "session_id": session_id,
                "method": method,
                "related_count": 0,
                "related_sessions": [],
            }

        target_start = target_range[0]["start_time"]
        target_end = target_range[0]["end_time"]
        # Parse timestamps if strings
        if isinstance(target_start, str):
            target_start = datetime.fromisoformat(target_start)
        if isinstance(target_end, str):
            target_end = datetime.fromisoformat(target_end)

        # Expand window by 1 hour each direction
        window_start = target_start - timedelta(hours=1)
        window_end = target_end + timedelta(hours=1)

        rows = storage.execute_query(
            """
            SELECT
                session_id,
                project_path,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen,
                COUNT(*) as event_count
            FROM events
            WHERE session_id != ?
              AND timestamp >= ?
              AND timestamp <= ?
            GROUP BY session_id
            ORDER BY first_seen
            LIMIT ?
            """,
            (session_id, window_start, window_end, limit),
        )

        related = [
            {
                "session_id": r["session_id"],
                "project": r["project_path"],
                "event_count": r["event_count"],
                "first_seen": _format_timestamp(r["first_seen"]),
                "last_seen": _format_timestamp(r["last_seen"]),
            }
            for r in rows
        ]

    else:
        return {
            "error": f"Invalid method: {method}. Use 'files', 'commands', or 'temporal'.",
        }

    return {
        "session_id": session_id,
        "method": method,
        "related_count": len(related),
        "related_sessions": related,
    }


def classify_sessions(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
) -> dict:
    """Classify sessions based on their dominant activity patterns.

    Categories:
    - debugging: High error rate, repeated tool failures
    - development: Edit-heavy, file modifications
    - research: Read/search heavy, exploring codebase
    - maintenance: CI/git heavy, infrastructure work

    Each session includes `classification_factors` explaining WHY it was
    categorized, including the trigger threshold and relevant metrics.

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        project: Optional project filter

    Returns:
        Dict with:
        - sessions: List with category, confidence, classification_factors, and stats
        - category_distribution: Count of sessions per category
    """
    cutoff = get_cutoff(days=days)

    # Build where clause
    where_parts = ["timestamp >= ?"]
    params: list = [cutoff]

    if project:
        where_parts.append("project_path LIKE ?")
        params.append(f"%{project}%")

    where_clause = " AND ".join(where_parts)

    # Get activity stats per session (including efficiency metrics for #79)
    # Safe: where_clause is built from hardcoded condition strings above
    rows = storage.execute_query(
        f"""
        SELECT
            session_id,
            project_path,
            COUNT(*) as total_events,
            SUM(CASE WHEN tool_name = 'Edit' THEN 1 ELSE 0 END) as edit_count,
            SUM(CASE WHEN tool_name = 'Read' THEN 1 ELSE 0 END) as read_count,
            SUM(CASE WHEN tool_name = 'Write' THEN 1 ELSE 0 END) as write_count,
            SUM(CASE WHEN tool_name IN ('Grep', 'Glob', 'WebSearch') THEN 1 ELSE 0 END) as search_count,
            SUM(CASE WHEN tool_name = 'Bash' AND command IN ('git', 'gh') THEN 1 ELSE 0 END) as git_count,
            SUM(CASE WHEN tool_name = 'Bash' AND command IN ('make', 'cargo', 'npm', 'pytest') THEN 1 ELSE 0 END) as build_count,
            SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END) as error_count,
            SUM(CASE WHEN entry_type = 'compaction' THEN 1 ELSE 0 END) as compaction_count,
            COALESCE(SUM(result_size_bytes), 0) as total_result_bytes,
            MIN(timestamp) as first_seen,
            MAX(timestamp) as last_seen
        FROM events
        WHERE {where_clause}
        GROUP BY session_id
        HAVING COUNT(*) >= 5
        ORDER BY first_seen DESC
        """,
        tuple(params),
    )

    # Get files read multiple times per session
    session_ids = [row["session_id"] for row in rows]
    files_read_multiple: dict[str, int] = {}
    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        multi_read_rows = storage.execute_query(
            f"""
            SELECT session_id, COUNT(*) as multi_read_files
            FROM (
                SELECT session_id, file_path, COUNT(*) as read_count
                FROM events
                WHERE session_id IN ({placeholders})
                  AND tool_name = 'Read'
                  AND file_path IS NOT NULL
                GROUP BY session_id, file_path
                HAVING COUNT(*) > 1
            )
            GROUP BY session_id
            """,
            tuple(session_ids),
        )
        files_read_multiple = {r["session_id"]: r["multi_read_files"] for r in multi_read_rows}

    classifications = []
    category_counts = {
        "debugging": 0,
        "development": 0,
        "research": 0,
        "maintenance": 0,
        "mixed": 0,
    }

    for row in rows:
        total = row["total_events"] or 1
        edit_pct = (row["edit_count"] or 0) / total
        read_pct = (row["read_count"] or 0) / total
        search_pct = (row["search_count"] or 0) / total
        git_pct = (row["git_count"] or 0) / total
        build_pct = (row["build_count"] or 0) / total
        error_pct = (row["error_count"] or 0) / total

        # Classification heuristics based on activity ratios
        # Thresholds derived from typical session patterns:
        # - Debugging: High error rate signals troubleshooting (>15% or 5+ errors)
        # - Development: Heavy editing indicates feature work (>30% edits or 3+ writes)
        # - Maintenance: Git/build focus without editing (>30% combined)
        # - Research: Mostly reading/searching codebase (>50% combined)
        # - Mixed: No dominant pattern, balanced activity
        error_count = row["error_count"] or 0
        write_count = row["write_count"] or 0

        if error_pct > 0.15 or error_count > 5:
            category = "debugging"
            confidence = min(1.0, error_pct * 3)
            classification_factors = {
                "trigger": "error_rate > 15%" if error_pct > 0.15 else "error_count > 5",
                "error_rate": round(error_pct * 100, 1),
                "error_count": error_count,
            }
        elif edit_pct > 0.3 or write_count > 3:
            category = "development"
            confidence = min(1.0, (edit_pct + write_count / total) * 2)
            classification_factors = {
                "trigger": "edit_rate > 30%" if edit_pct > 0.3 else "write_count > 3",
                "edit_rate": round(edit_pct * 100, 1),
                "write_count": write_count,
            }
        elif git_pct + build_pct > 0.3:
            category = "maintenance"
            confidence = min(1.0, (git_pct + build_pct) * 2)
            classification_factors = {
                "trigger": "git_build_rate > 30%",
                "git_rate": round(git_pct * 100, 1),
                "build_rate": round(build_pct * 100, 1),
            }
        elif read_pct + search_pct > 0.5:
            category = "research"
            confidence = min(1.0, (read_pct + search_pct) * 1.5)
            classification_factors = {
                "trigger": "read_search_rate > 50%",
                "read_rate": round(read_pct * 100, 1),
                "search_rate": round(search_pct * 100, 1),
            }
        else:
            category = "mixed"
            confidence = 0.5
            classification_factors = {
                "trigger": "no_dominant_pattern",
                "top_activities": {
                    "edit_rate": round(edit_pct * 100, 1),
                    "read_rate": round(read_pct * 100, 1),
                    "search_rate": round(search_pct * 100, 1),
                },
            }

        category_counts[category] += 1

        # Issue #79: Add efficiency metrics
        compaction_count = row["compaction_count"] or 0
        total_bytes = row["total_result_bytes"] or 0
        multi_read_files = files_read_multiple.get(row["session_id"], 0)

        # Calculate burn_rate based on compactions per hour
        first_seen = row["first_seen"]
        last_seen = row["last_seen"]
        if first_seen and last_seen:
            try:
                # Parse timestamps and calculate duration (datetime already imported at module level)
                first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
                last_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                duration_hours = (last_dt - first_dt).total_seconds() / 3600
                compactions_per_hour = (
                    compaction_count / duration_hours if duration_hours > 0 else 0
                )
            except (ValueError, TypeError):
                compactions_per_hour = 0
        else:
            compactions_per_hour = 0

        # Classify burn rate: high (>2/hr), medium (0.5-2/hr), low (<0.5/hr)
        if compactions_per_hour > 2:
            burn_rate = "high"
        elif compactions_per_hour > 0.5:
            burn_rate = "medium"
        else:
            burn_rate = "low"

        classifications.append(
            {
                "session_id": row["session_id"],
                "project": row["project_path"],
                "category": category,
                "confidence": round(confidence, 2),
                "classification_factors": classification_factors,
                "stats": {
                    "total_events": row["total_events"],
                    "edit_count": row["edit_count"] or 0,
                    "read_count": row["read_count"] or 0,
                    "search_count": row["search_count"] or 0,
                    "git_count": row["git_count"] or 0,
                    "error_count": error_count,
                },
                "efficiency": {
                    "compaction_count": compaction_count,
                    "total_result_mb": round(total_bytes / 1024 / 1024, 2),
                    "files_read_multiple_times": multi_read_files,
                    "burn_rate": burn_rate,
                },
                "first_seen": _format_timestamp(row["first_seen"]),
                "last_seen": _format_timestamp(row["last_seen"]),
            }
        )

    return {
        "days": days,
        "project": project,
        "session_count": len(classifications),
        "category_distribution": category_counts,
        "sessions": classifications[:50],  # Limit output
    }


def get_handoff_context(
    storage: SQLiteStorage,
    session_id: str | None = None,
    hours: int = 4,
    message_limit: int = 10,
) -> dict:
    """Get context for session handoff (useful for /status-report).

    Provides recent activity summary including:
    - Last N user messages
    - Files modified
    - Commands run
    - Session duration and activity stats

    Args:
        storage: Storage instance
        session_id: Optional specific session ID (default: most recent session)
        hours: Hours to look back if no session specified (default: 4)
        message_limit: Maximum messages to return (default: 10)

    Returns:
        Dict with handoff context including messages, files, and activity summary
    """
    cutoff = get_cutoff(hours=hours)

    # If no session specified, get the most recent session
    if not session_id:
        recent = storage.execute_query(
            """
            SELECT DISTINCT session_id, MAX(timestamp) as last_activity
            FROM events
            WHERE timestamp >= ?
            GROUP BY session_id
            ORDER BY last_activity DESC
            LIMIT 1
            """,
            (cutoff,),
        )
        if not recent:
            return {
                "error": "No recent sessions found",
                "session_id": None,
                "hours": hours,
            }
        session_id = recent[0]["session_id"]

    # Get session boundaries
    session_info = storage.execute_query(
        """
        SELECT
            MIN(timestamp) as first_seen,
            MAX(timestamp) as last_seen,
            COUNT(*) as total_events,
            project_path
        FROM events
        WHERE session_id = ?
        GROUP BY session_id
        """,
        (session_id,),
    )

    if not session_info:
        return {
            "error": f"Session not found: {session_id}",
            "session_id": session_id,
        }

    info = session_info[0]
    first_seen = info["first_seen"]
    last_seen = info["last_seen"]
    if isinstance(first_seen, str):
        first_seen = datetime.fromisoformat(first_seen)
    if isinstance(last_seen, str):
        last_seen = datetime.fromisoformat(last_seen)

    duration_minutes = (
        int((last_seen - first_seen).total_seconds() / 60) if last_seen and first_seen else 0
    )

    # Get recent user messages
    messages = storage.execute_query(
        """
        SELECT timestamp, message_text
        FROM events
        WHERE session_id = ?
          AND entry_type = 'user'
          AND message_text IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (session_id, message_limit),
    )

    recent_messages = [
        {
            "timestamp": _format_timestamp(m["timestamp"]),
            "message": m["message_text"][:200] if m["message_text"] else None,
        }
        for m in messages
    ]

    # Get files modified
    files = storage.execute_query(
        """
        SELECT DISTINCT file_path, COUNT(*) as touch_count
        FROM events
        WHERE session_id = ?
          AND file_path IS NOT NULL
          AND tool_name IN ('Edit', 'Write')
        GROUP BY file_path
        ORDER BY touch_count DESC
        LIMIT 10
        """,
        (session_id,),
    )

    modified_files = [{"file": f["file_path"], "touches": f["touch_count"]} for f in files]

    # Get commands run
    commands = storage.execute_query(
        """
        SELECT command, COUNT(*) as run_count
        FROM events
        WHERE session_id = ?
          AND command IS NOT NULL
        GROUP BY command
        ORDER BY run_count DESC
        LIMIT 10
        """,
        (session_id,),
    )

    recent_commands = [{"command": c["command"], "count": c["run_count"]} for c in commands]

    # Get tool usage summary
    tools = storage.execute_query(
        """
        SELECT tool_name, COUNT(*) as use_count
        FROM events
        WHERE session_id = ?
          AND tool_name IS NOT NULL
        GROUP BY tool_name
        ORDER BY use_count DESC
        LIMIT 10
        """,
        (session_id,),
    )

    tool_summary = [{"tool": t["tool_name"], "count": t["use_count"]} for t in tools]

    return {
        "session_id": session_id,
        "project": info["project_path"],
        "first_seen": _format_timestamp(first_seen),
        "last_seen": _format_timestamp(last_seen),
        "duration_minutes": duration_minutes,
        "total_events": info["total_events"],
        "recent_messages": recent_messages,
        "modified_files": modified_files,
        "recent_commands": recent_commands,
        "tool_summary": tool_summary,
    }


# Pattern to match worktree paths: .worktrees/<branch-name>/
WORKTREE_PATTERN = re.compile(r"\.worktrees/[^/]+/")


def _collapse_worktree_path(path: str) -> str:
    """Remove .worktrees/<branch>/ from a path to consolidate file activity."""
    return WORKTREE_PATTERN.sub("", path)


def query_file_activity(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
    limit: int = 20,
    collapse_worktrees: bool = False,
) -> dict:
    """Query file activity (reads, edits, writes) with breakdown.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter
        limit: Maximum files to return
        collapse_worktrees: If True, consolidate .worktrees/<branch>/ paths

    Returns:
        File activity data with read/edit/write breakdown
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name IN ('Read', 'Edit', 'Write')", "file_path IS NOT NULL"],
    )

    rows = storage.execute_query(
        f"""
        SELECT
            file_path,
            tool_name,
            COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY file_path, tool_name
        ORDER BY count DESC
        """,
        params,
    )

    # Aggregate by file, optionally collapsing worktree paths
    file_stats: dict[str, dict] = {}
    for row in rows:
        path = row["file_path"]
        if collapse_worktrees:
            path = _collapse_worktree_path(path)

        if path not in file_stats:
            file_stats[path] = {"reads": 0, "edits": 0, "writes": 0, "total": 0}

        tool = row["tool_name"]
        count = row["count"]
        if tool == "Read":
            file_stats[path]["reads"] += count
        elif tool == "Edit":
            file_stats[path]["edits"] += count
        elif tool == "Write":
            file_stats[path]["writes"] += count
        file_stats[path]["total"] += count

    # Sort by total and limit
    sorted_files = sorted(file_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:limit]

    files = [
        {
            "file": path,
            "total": stats["total"],
            "reads": stats["reads"],
            "edits": stats["edits"],
            "writes": stats["writes"],
        }
        for path, stats in sorted_files
    ]

    return {
        "days": days,
        "collapse_worktrees": collapse_worktrees,
        "file_count": len(file_stats),
        "files": files,
    }


def query_languages(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
) -> dict:
    """Query language distribution from file extensions.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter

    Returns:
        Language distribution data
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name IN ('Read', 'Edit', 'Write')", "file_path IS NOT NULL"],
    )

    rows = storage.execute_query(
        f"""
        SELECT
            CASE
                WHEN file_path LIKE '%.rs' THEN 'Rust'
                WHEN file_path LIKE '%.py' THEN 'Python'
                WHEN file_path LIKE '%.ts' THEN 'TypeScript'
                WHEN file_path LIKE '%.tsx' THEN 'TypeScript'
                WHEN file_path LIKE '%.js' THEN 'JavaScript'
                WHEN file_path LIKE '%.jsx' THEN 'JavaScript'
                WHEN file_path LIKE '%.md' THEN 'Markdown'
                WHEN file_path LIKE '%.json' THEN 'JSON'
                WHEN file_path LIKE '%.toml' THEN 'TOML'
                WHEN file_path LIKE '%.yaml' THEN 'YAML'
                WHEN file_path LIKE '%.yml' THEN 'YAML'
                WHEN file_path LIKE '%.sh' THEN 'Shell'
                WHEN file_path LIKE '%.bash' THEN 'Shell'
                WHEN file_path LIKE '%.go' THEN 'Go'
                WHEN file_path LIKE '%.java' THEN 'Java'
                WHEN file_path LIKE '%.rb' THEN 'Ruby'
                WHEN file_path LIKE '%.c' THEN 'C'
                WHEN file_path LIKE '%.cpp' THEN 'C++'
                WHEN file_path LIKE '%.h' THEN 'C/C++ Header'
                WHEN file_path LIKE '%.hpp' THEN 'C++ Header'
                WHEN file_path LIKE '%.swift' THEN 'Swift'
                WHEN file_path LIKE '%.css' THEN 'CSS'
                WHEN file_path LIKE '%.html' THEN 'HTML'
                WHEN file_path LIKE '%.sql' THEN 'SQL'
                ELSE 'Other'
            END as language,
            COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY language
        ORDER BY count DESC
        """,
        params,
    )

    total = sum(row["count"] for row in rows)
    languages = [
        {
            "language": row["language"],
            "count": row["count"],
            "percent": round(row["count"] / total * 100, 1) if total > 0 else 0,
        }
        for row in rows
    ]

    return {
        "days": days,
        "total_operations": total,
        "languages": languages,
    }


def query_projects(
    storage: SQLiteStorage,
    days: int = 7,
) -> dict:
    """Query cross-project activity.

    Note: This function intentionally does not have a project filter parameter
    because it's designed to show activity *across* all projects.

    Args:
        storage: Storage instance
        days: Number of days to analyze

    Returns:
        Project activity data with event counts and session counts per project
    """
    cutoff = get_cutoff(days=days)

    rows = storage.execute_query(
        """
        SELECT
            project_path,
            COUNT(*) as events,
            COUNT(DISTINCT session_id) as sessions
        FROM events
        WHERE timestamp >= ?
          AND project_path IS NOT NULL
        GROUP BY project_path
        ORDER BY events DESC
        """,
        (cutoff,),
    )

    # Extract repo name from path
    def get_repo_name(path: str) -> str:
        # Try to extract meaningful name from path
        parts = path.rstrip("/").split("/")
        # Look for common patterns
        for i, part in enumerate(parts):
            if part in ("projects", "repos", "src", "Documents"):
                if i + 1 < len(parts):
                    return parts[i + 1]
        # Fallback to last component
        return parts[-1] if parts else path

    projects = [
        {
            "project": row["project_path"],
            "name": get_repo_name(row["project_path"]),
            "events": row["events"],
            "sessions": row["sessions"],
        }
        for row in rows
    ]

    return {
        "days": days,
        "project_count": len(projects),
        "projects": projects,
    }


def query_mcp_usage(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
) -> dict:
    """Query MCP server/tool usage breakdown.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter

    Returns:
        MCP usage data by server and tool
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
        extra_conditions=["tool_name LIKE 'mcp__%'"],
    )

    rows = storage.execute_query(
        f"""
        SELECT
            tool_name,
            COUNT(*) as count
        FROM events
        WHERE {where_clause}
        GROUP BY tool_name
        ORDER BY count DESC
        """,
        params,
    )

    # Group by server (extract from mcp__<server>__<tool>)
    servers: dict[str, dict] = {}
    total = 0

    for row in rows:
        tool_name = row["tool_name"]
        count = row["count"]
        total += count

        # Parse mcp__<server>__<tool>
        parts = tool_name.split("__")
        if len(parts) >= 3:
            server = parts[1]
            tool = "__".join(parts[2:])  # Handle tools with __ in name
        else:
            server = "unknown"
            tool = tool_name

        if server not in servers:
            servers[server] = {"total": 0, "tools": []}

        servers[server]["total"] += count
        servers[server]["tools"].append({"tool": tool, "count": count})

    # Sort servers by total and tools by count
    server_list = sorted(servers.items(), key=lambda x: x[1]["total"], reverse=True)
    result_servers = []
    for server_name, data in server_list:
        data["tools"].sort(key=lambda x: x["count"], reverse=True)
        result_servers.append(
            {
                "server": server_name,
                "total": data["total"],
                "tools": data["tools"],
            }
        )

    return {
        "days": days,
        "total_mcp_calls": total,
        "servers": result_servers,
    }


def query_agent_activity(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
) -> dict:
    """Query activity breakdown by Task subagent.

    RFC #41: Tracks agent activity from Task tool invocations,
    distinguishing work done by agents vs main session.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        project: Optional project path filter

    Returns:
        Dict with agent activity breakdown including:
        - Main session stats (agent_id IS NULL)
        - Per-agent stats (agent_id IS NOT NULL)
        - Token usage, event counts, tool usage per agent
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(
        cutoff=cutoff,
        project=project,
    )

    # Query aggregated stats per agent_id (NULL = main session)
    rows = storage.execute_query(
        f"""
        SELECT
            agent_id,
            COUNT(*) as event_count,
            SUM(CASE WHEN entry_type = 'tool_use' THEN 1 ELSE 0 END) as tool_use_count,
            SUM(COALESCE(input_tokens, 0)) as input_tokens,
            SUM(COALESCE(output_tokens, 0)) as output_tokens,
            SUM(COALESCE(cache_read_tokens, 0)) as cache_read_tokens,
            SUM(CASE WHEN is_sidechain = 1 THEN 1 ELSE 0 END) as sidechain_events,
            MIN(timestamp) as first_seen,
            MAX(timestamp) as last_seen
        FROM events
        WHERE {where_clause}
        GROUP BY agent_id
        ORDER BY input_tokens DESC
        """,
        params,
    )

    agents = []
    main_session_stats = None

    for row in rows:
        agent_data = {
            "agent_id": row["agent_id"],
            "event_count": row["event_count"],
            "tool_use_count": row["tool_use_count"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "sidechain_events": row["sidechain_events"],
            "first_seen": _format_timestamp(row["first_seen"]),
            "last_seen": _format_timestamp(row["last_seen"]),
        }

        if row["agent_id"] is None:
            main_session_stats = agent_data
        else:
            agents.append(agent_data)

    # Get top tools per agent (for agents with activity)
    agent_ids = [a["agent_id"] for a in agents]
    if agent_ids:
        placeholders = ",".join(["?"] * len(agent_ids))
        tool_rows = storage.execute_query(
            f"""
            SELECT
                agent_id,
                tool_name,
                COUNT(*) as count
            FROM events
            WHERE {where_clause}
              AND agent_id IN ({placeholders})
              AND tool_name IS NOT NULL
            GROUP BY agent_id, tool_name
            ORDER BY agent_id, count DESC
            """,
            params + agent_ids,
        )

        # Group top 5 tools per agent
        agent_tools: dict[str, list] = {}
        for row in tool_rows:
            aid = row["agent_id"]
            if aid not in agent_tools:
                agent_tools[aid] = []
            if len(agent_tools[aid]) < 5:
                agent_tools[aid].append({"tool": row["tool_name"], "count": row["count"]})

        # Attach tools to agents
        for agent in agents:
            agent["top_tools"] = agent_tools.get(agent["agent_id"], [])

    # Calculate totals
    total_agent_tokens = sum(a["input_tokens"] for a in agents)
    total_main_tokens = main_session_stats["input_tokens"] if main_session_stats else 0

    return {
        "days": days,
        "main_session": main_session_stats,
        "agents": agents,
        "summary": {
            "agent_count": len(agents),
            "total_agent_events": sum(a["event_count"] for a in agents),
            "total_agent_tokens": total_agent_tokens,
            "total_main_tokens": total_main_tokens,
            "agent_token_percentage": (
                round(total_agent_tokens / (total_agent_tokens + total_main_tokens) * 100, 1)
                if (total_agent_tokens + total_main_tokens) > 0
                else 0
            ),
        },
    }


def query_bus_events(
    storage: SQLiteStorage,
    days: int = 7,
    event_type: str | None = None,
    session_id: str | None = None,
    repo: str | None = None,
    limit: int = 100,
) -> dict:
    """Query event-bus events with optional filters.

    Returns raw events from the event-bus for cross-session insights.
    Events include gotcha_discovered, pattern_found, help_needed, etc.

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        event_type: Filter by event type (e.g., 'gotcha_discovered')
        session_id: Filter by session ID
        repo: Filter by repo name
        limit: Maximum events to return (default: 100)

    Returns:
        Dict with events list and type breakdown
    """
    cutoff = get_cutoff(days=days)

    # Build where clause
    where_parts = ["timestamp >= ?"]
    params: list = [cutoff]

    if event_type:
        where_parts.append("event_type = ?")
        params.append(event_type)
    if session_id:
        where_parts.append("session_id = ?")
        params.append(session_id)
    if repo:
        where_parts.append("repo = ?")
        params.append(repo)

    where_clause = " AND ".join(where_parts)
    params.append(limit)

    # Get events
    rows = storage.execute_query(
        f"""
        SELECT
            event_id,
            timestamp,
            event_type,
            channel,
            session_id,
            repo,
            payload
        FROM bus_events
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        tuple(params),
    )

    events = [
        {
            "event_id": row["event_id"],
            "timestamp": _format_timestamp(row["timestamp"]),
            "event_type": row["event_type"],
            "channel": row["channel"],
            "session_id": row["session_id"],
            "repo": row["repo"],
            "payload": row["payload"],
        }
        for row in rows
    ]

    # Get type breakdown
    type_rows = storage.execute_query(
        f"""
        SELECT event_type, COUNT(*) as count
        FROM bus_events
        WHERE {" AND ".join(where_parts[:-1]) if len(where_parts) > 1 else where_parts[0]}
        GROUP BY event_type
        ORDER BY count DESC
        """,
        tuple(params[:-1]),  # Exclude limit param
    )

    type_counts = {row["event_type"]: row["count"] for row in type_rows}

    return {
        "days": days,
        "event_count": len(events),
        "event_types": type_counts,
        "events": events,
    }


def query_error_details(
    storage: SQLiteStorage,
    days: int = 7,
    tool: str | None = None,
    limit: int = 50,
) -> dict:
    """Get detailed error information including tool parameters that caused failures.

    Joins tool_result errors with tool_use events to extract the parameters
    (pattern for Glob/Grep, command for Bash, file_path for file operations)
    that caused the failure.

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        tool: Optional filter by tool name (e.g., "Glob", "Bash")
        limit: Maximum errors to return per tool (default: 50)

    Returns:
        Dict with error details grouped by tool and parameter
    """
    cutoff = get_cutoff(days=days)

    # Build tool filter
    tool_filter = ""
    params: list = [cutoff]
    if tool:
        tool_filter = "AND e2.tool_name = ?"
        params.append(tool)

    # Query errors with tool parameters
    # Uses json_extract to get the relevant parameter based on tool type:
    # - Glob/Grep: pattern
    # - Bash: command (already extracted to column)
    # - Read/Edit/Write: file_path (already extracted to column)
    rows = storage.execute_query(
        f"""
        SELECT
            e2.tool_name,
            e2.command,
            e2.file_path,
            json_extract(e2.tool_input_json, '$.pattern') as pattern,
            json_extract(e2.tool_input_json, '$.path') as search_path,
            e1.project_path,
            COUNT(*) as error_count
        FROM events e1
        JOIN events e2 ON e1.tool_id = e2.tool_id AND e2.entry_type = 'tool_use'
        WHERE e1.timestamp >= ?
          AND e1.is_error = 1
          AND e1.entry_type = 'tool_result'
          {tool_filter}
        GROUP BY e2.tool_name, e2.command, e2.file_path, pattern, search_path, e1.project_path
        ORDER BY e2.tool_name, error_count DESC
        """,
        tuple(params),
    )

    # Organize by tool with the relevant parameter
    errors_by_tool: dict[str, list[dict]] = {}
    tool_totals: dict[str, int] = {}

    for row in rows:
        tool_name = row["tool_name"]
        if not tool_name:
            continue

        # Determine the key parameter based on tool type
        if tool_name in ("Glob", "Grep"):
            key_param = row["pattern"]
            param_type = "pattern"
        elif tool_name == "Bash":
            key_param = row["command"]
            param_type = "command"
        else:
            key_param = row["file_path"]
            param_type = "file_path"

        if tool_name not in errors_by_tool:
            errors_by_tool[tool_name] = []
            tool_totals[tool_name] = 0

        tool_totals[tool_name] += row["error_count"]

        # Only keep top N per tool
        if len(errors_by_tool[tool_name]) < limit:
            error_detail = {
                "param_type": param_type,
                "param_value": key_param,
                "error_count": row["error_count"],
                "project": row["project_path"],
            }
            # Add search_path for Glob/Grep if present
            if tool_name in ("Glob", "Grep") and row["search_path"]:
                error_detail["search_path"] = row["search_path"]

            errors_by_tool[tool_name].append(error_detail)

    return {
        "days": days,
        "tool_filter": tool,
        "errors_by_tool": errors_by_tool,
        "tool_totals": tool_totals,
        "total_errors": sum(tool_totals.values()),
    }


# Issue #69: Context efficiency queries


def get_compaction_events(
    storage: SQLiteStorage,
    days: int = 7,
    session_id: str | None = None,
    limit: int = 50,
    aggregate: bool = False,
) -> dict:
    """List compaction events where conversation history was truncated.

    Compaction events are summaries with "continued from a previous conversation"
    marker, indicating Claude Code compacted the context window.

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        session_id: Optional filter for specific session
        limit: Maximum events to return (default: 50)
        aggregate: If True, group by session with counts instead of individual events

    Returns:
        Dict with compaction events and their timestamps (or session aggregates if aggregate=True)
    """
    cutoff = get_cutoff(days=days)

    where_parts = ["timestamp >= ?", "entry_type = 'compaction'"]
    params: list = [cutoff]

    if session_id:
        where_parts.append("session_id = ?")
        params.append(session_id)

    where_clause = " AND ".join(where_parts)

    # First get total count
    count_row = storage.execute_query(
        f"SELECT COUNT(*) as total FROM events WHERE {where_clause}",
        tuple(params),
    )
    total_count = count_row[0]["total"] if count_row else 0

    # Issue #81: Aggregate mode groups by session
    if aggregate:
        query_params = list(params)
        limit_clause = ""
        if limit > 0:
            limit_clause = "LIMIT ?"
            query_params.append(limit)

        rows = storage.execute_query(
            f"""
            SELECT
                session_id,
                project_path,
                COUNT(*) as compaction_count,
                MIN(timestamp) as first_compaction,
                MAX(timestamp) as last_compaction,
                SUM(result_size_bytes) as total_summary_bytes
            FROM events
            WHERE {where_clause}
            GROUP BY session_id
            ORDER BY compaction_count DESC
            {limit_clause}
            """,
            tuple(query_params),
        )

        sessions = [
            {
                "session_id": row["session_id"],
                "project": row["project_path"],
                "compaction_count": row["compaction_count"],
                "first_compaction": _format_timestamp(row["first_compaction"]),
                "last_compaction": _format_timestamp(row["last_compaction"]),
                "total_summary_kb": round((row["total_summary_bytes"] or 0) / 1024, 1),
            }
            for row in rows
        ]

        # Count unique sessions
        session_count_row = storage.execute_query(
            f"SELECT COUNT(DISTINCT session_id) as count FROM events WHERE {where_clause}",
            tuple(params),
        )
        total_sessions = session_count_row[0]["count"] if session_count_row else 0

        return {
            "days": days,
            "session_id": session_id,
            "limit": limit,
            "aggregate": True,
            "total_compaction_count": total_count,
            "total_sessions_with_compactions": total_sessions,
            "session_count": len(sessions),
            "sessions": sessions,
        }

    # Non-aggregate mode: return individual compaction events
    query_params = list(params)
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        query_params.append(limit)

    rows = storage.execute_query(
        f"""
        SELECT
            timestamp,
            session_id,
            project_path,
            result_size_bytes,
            message_text
        FROM events
        WHERE {where_clause}
        ORDER BY timestamp DESC
        {limit_clause}
        """,
        tuple(query_params),
    )

    compactions = [
        {
            "timestamp": _format_timestamp(row["timestamp"]),
            "session_id": row["session_id"],
            "project": row["project_path"],
            "summary_size_bytes": row["result_size_bytes"],
            "summary_preview": (row["message_text"] or "")[:200],
        }
        for row in rows
    ]

    return {
        "days": days,
        "session_id": session_id,
        "limit": limit,
        "aggregate": False,
        "total_compaction_count": total_count,
        "compaction_count": len(compactions),
        "compactions": compactions,
    }


def get_pre_compaction_events(
    storage: SQLiteStorage,
    session_id: str,
    compaction_timestamp: str,
    limit: int = 50,
) -> dict:
    """Get events before a compaction to understand what was summarized.

    Shows the N events immediately before a compaction event occurred,
    revealing what context was compressed. Events are ordered by timestamp
    descending (most recent first) so the events closest to the compaction
    appear first.

    Args:
        storage: Storage instance
        session_id: Session containing the compaction
        compaction_timestamp: ISO timestamp of compaction event
        limit: Max events to return before compaction (default: 50)

    Returns:
        Dict with pre-compaction events, ordered by timestamp descending
    """
    compact_time = datetime.fromisoformat(compaction_timestamp)

    rows = storage.execute_query(
        """
        SELECT
            timestamp,
            entry_type,
            tool_name,
            command,
            file_path,
            is_error,
            result_size_bytes
        FROM events
        WHERE session_id = ?
          AND timestamp < ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (session_id, compact_time, limit),
    )

    events = [
        {
            "timestamp": _format_timestamp(row["timestamp"]),
            "type": row["entry_type"],
            "tool": row["tool_name"],
            "command": row["command"],
            "file": row["file_path"],
            "error": bool(row["is_error"]),
            "size_bytes": row["result_size_bytes"],
        }
        for row in rows
    ]

    return {
        "session_id": session_id,
        "compaction_timestamp": compaction_timestamp,
        "event_count": len(events),
        "events": events,
    }


def analyze_pre_compaction_patterns(
    storage: SQLiteStorage,
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
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        events_before: Events to analyze before each compaction (default: 50)
        limit: Max compactions to analyze (default: 20)

    Returns:
        Dict with aggregated patterns across analyzed compactions
    """
    cutoff = get_cutoff(days=days)

    # Get recent compactions
    compactions = storage.execute_query(
        """
        SELECT session_id, timestamp
        FROM events
        WHERE timestamp >= ?
          AND entry_type = 'compaction'
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (cutoff, limit),
    )

    if not compactions:
        return {
            "days": days,
            "compactions_analyzed": 0,
            "patterns": {},
            "recommendations": [],
        }

    # Analyze patterns across all compactions
    total_consecutive_reads = 0
    total_files_read_multiple = 0
    total_large_results = 0
    tool_counts: dict[str, int] = {}
    file_read_counts: dict[str, int] = {}
    large_results_by_tool: dict[str, int] = {}

    for compaction in compactions:
        session_id = compaction["session_id"]
        compact_time = compaction["timestamp"]

        # Get events before this compaction
        events = storage.execute_query(
            """
            SELECT
                entry_type,
                tool_name,
                file_path,
                result_size_bytes
            FROM events
            WHERE session_id = ?
              AND timestamp < ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (session_id, compact_time, events_before),
        )

        # Count consecutive reads (no edit between reads)
        consecutive_reads = 0
        max_consecutive_reads = 0
        for event in events:
            if event["tool_name"] == "Read":
                consecutive_reads += 1
                max_consecutive_reads = max(max_consecutive_reads, consecutive_reads)
            elif event["tool_name"] == "Edit":
                consecutive_reads = 0
        total_consecutive_reads += max_consecutive_reads

        # Count files read multiple times
        session_file_counts: dict[str, int] = {}
        for event in events:
            if event["tool_name"] == "Read" and event["file_path"]:
                session_file_counts[event["file_path"]] = (
                    session_file_counts.get(event["file_path"], 0) + 1
                )
        multi_read_files = sum(1 for c in session_file_counts.values() if c > 1)
        total_files_read_multiple += multi_read_files

        # Aggregate file reads across all compactions
        for f, c in session_file_counts.items():
            file_read_counts[f] = file_read_counts.get(f, 0) + c

        # Count large results (>10KB)
        for event in events:
            size = event["result_size_bytes"] or 0
            if size > 10240:
                total_large_results += 1
                tool = event["tool_name"] or "unknown"
                large_results_by_tool[tool] = large_results_by_tool.get(tool, 0) + 1

        # Tool distribution
        for event in events:
            if event["tool_name"]:
                tool_counts[event["tool_name"]] = tool_counts.get(event["tool_name"], 0) + 1

    # Calculate averages
    compactions_analyzed = len(compactions)
    avg_consecutive_reads = total_consecutive_reads / compactions_analyzed
    avg_files_read_multiple = total_files_read_multiple / compactions_analyzed
    avg_large_results = total_large_results / compactions_analyzed

    # Top files read multiple times
    top_reread_files = sorted(
        [(f, c) for f, c in file_read_counts.items() if c > 1],
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    # Tool distribution sorted by count
    tool_distribution = sorted(
        tool_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    # Generate recommendations based on findings
    recommendations = []
    if avg_consecutive_reads > 5:
        recommendations.append(
            f"High consecutive reads ({avg_consecutive_reads:.1f} avg) - "
            "consider reading fewer files or using grep to target specific content"
        )
    if avg_files_read_multiple > 2:
        recommendations.append(
            f"Files re-read frequently ({avg_files_read_multiple:.1f} avg) - "
            "consider keeping file content in context or summarizing key sections"
        )
    if avg_large_results > 3:
        recommendations.append(
            f"Many large tool results ({avg_large_results:.1f} avg) - "
            "use offset/limit parameters or grep to reduce result size"
        )

    # Check for Read-heavy tool distribution
    read_count = tool_counts.get("Read", 0)
    edit_count = tool_counts.get("Edit", 0)
    if edit_count == 0 and read_count > 10:
        # All reads, no edits - pure exploration that consumed context
        recommendations.append(
            f"Pure exploration pattern ({read_count} reads, 0 edits) - "
            "context consumed without productive editing"
        )
    elif read_count > 0 and edit_count > 0:
        read_edit_ratio = read_count / edit_count
        if read_edit_ratio > 5:
            recommendations.append(
                f"High read:edit ratio ({read_edit_ratio:.1f}:1) - "
                "may indicate over-exploration before action"
            )

    return {
        "days": days,
        "events_before": events_before,
        "compactions_analyzed": compactions_analyzed,
        "patterns": {
            "avg_consecutive_reads": round(avg_consecutive_reads, 1),
            "avg_files_read_multiple_times": round(avg_files_read_multiple, 1),
            "avg_large_results": round(avg_large_results, 1),
            "tool_distribution": [
                {"tool": tool, "count": count} for tool, count in tool_distribution
            ],
            "top_reread_files": [{"file": f, "read_count": c} for f, c in top_reread_files],
            "large_results_by_tool": [
                {"tool": tool, "count": count}
                for tool, count in sorted(
                    large_results_by_tool.items(), key=lambda x: x[1], reverse=True
                )
            ],
        },
        "recommendations": recommendations,
    }


def get_large_tool_results(
    storage: SQLiteStorage,
    days: int = 7,
    min_size_kb: int = 10,
    limit: int = 50,
) -> dict:
    """Find tool calls with large outputs (bloat detection).

    Identifies tool results consuming significant context space,
    indicating opportunities for pagination or output filtering.

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        min_size_kb: Minimum size in KB to report (default: 10)
        limit: Max results to return (default: 50)

    Returns:
        Dict with large tool results by tool and size
    """
    cutoff = get_cutoff(days=days)
    min_size_bytes = min_size_kb * 1024

    rows = storage.execute_query(
        """
        SELECT
            e1.timestamp,
            e1.session_id,
            e1.project_path,
            e2.tool_name,
            e2.command,
            e2.file_path,
            e1.result_size_bytes
        FROM events e1
        JOIN events e2 ON e1.tool_id = e2.tool_id AND e2.entry_type = 'tool_use'
        WHERE e1.timestamp >= ?
          AND e1.entry_type = 'tool_result'
          AND e1.result_size_bytes >= ?
        ORDER BY e1.result_size_bytes DESC
        LIMIT ?
        """,
        (cutoff, min_size_bytes, limit),
    )

    results = [
        {
            "timestamp": _format_timestamp(row["timestamp"]),
            "session_id": row["session_id"],
            "project": row["project_path"],
            "tool": row["tool_name"],
            "command": row["command"],
            "file": row["file_path"],
            "size_kb": round(row["result_size_bytes"] / 1024, 1),
        }
        for row in rows
    ]

    # Aggregate by tool
    tool_totals: dict[str, int] = {}
    for row in rows:
        tool = row["tool_name"]
        if tool:
            tool_totals[tool] = tool_totals.get(tool, 0) + row["result_size_bytes"]

    tool_breakdown = [
        {"tool": tool, "total_mb": round(size / 1024 / 1024, 2)}
        for tool, size in sorted(tool_totals.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "days": days,
        "min_size_kb": min_size_kb,
        "result_count": len(results),
        "tool_breakdown": tool_breakdown,
        "large_results": results,
    }


def get_session_efficiency(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
    limit: int = 50,
) -> dict:
    """Analyze session efficiency: burn rate, compactions, read patterns.

    Provides raw efficiency signals:
    - Token burn rate (tokens per event)
    - Compaction frequency (how often context fills up)
    - Read-heavy patterns (large tool results consuming context)
    - Assistant verbosity (output tokens per response)
    - Read-to-edit ratio (high ratio suggests inefficient exploration)
    - Files read multiple times (redundant reads)

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        project: Optional project filter
        limit: Maximum sessions to return (default: 50)

    Returns:
        Dict with efficiency metrics per session
    """
    cutoff = get_cutoff(days=days)
    where_clause, params = build_where_clause(cutoff=cutoff, project=project)

    # Get session-level efficiency metrics
    query_params = list(params)
    limit_clause = ""
    if limit > 0:
        limit_clause = "LIMIT ?"
        query_params.append(limit)

    rows = storage.execute_query(
        f"""
        SELECT
            session_id,
            project_path,
            COUNT(*) as total_events,
            SUM(CASE WHEN entry_type = 'compaction' THEN 1 ELSE 0 END) as compaction_count,
            SUM(COALESCE(input_tokens, 0)) as input_tokens,
            SUM(COALESCE(output_tokens, 0)) as output_tokens,
            SUM(COALESCE(result_size_bytes, 0)) as total_result_bytes,
            SUM(CASE WHEN entry_type = 'assistant' THEN 1 ELSE 0 END) as assistant_count,
            SUM(CASE WHEN entry_type = 'tool_result' AND result_size_bytes > 10240 THEN 1 ELSE 0 END) as large_result_count,
            SUM(CASE WHEN tool_name = 'Read' THEN 1 ELSE 0 END) as read_count,
            SUM(CASE WHEN tool_name = 'Edit' THEN 1 ELSE 0 END) as edit_count,
            MIN(timestamp) as first_seen,
            MAX(timestamp) as last_seen
        FROM events
        WHERE {where_clause}
        GROUP BY session_id
        HAVING COUNT(*) >= 10
        ORDER BY compaction_count DESC, input_tokens DESC
        {limit_clause}
        """,
        tuple(query_params),
    )

    # Get files read multiple times per session
    session_ids = [row["session_id"] for row in rows]
    files_read_multiple: dict[str, int] = {}
    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        multi_read_rows = storage.execute_query(
            f"""
            SELECT session_id, COUNT(*) as multi_read_files
            FROM (
                SELECT session_id, file_path, COUNT(*) as read_count
                FROM events
                WHERE session_id IN ({placeholders})
                  AND tool_name = 'Read'
                  AND file_path IS NOT NULL
                GROUP BY session_id, file_path
                HAVING COUNT(*) > 1
            )
            GROUP BY session_id
            """,
            tuple(session_ids),
        )
        files_read_multiple = {r["session_id"]: r["multi_read_files"] for r in multi_read_rows}

    sessions = []
    for row in rows:
        total_events = row["total_events"] or 1
        input_tokens = row["input_tokens"] or 0
        output_tokens = row["output_tokens"] or 0
        assistant_count = row["assistant_count"] or 1
        read_count = row["read_count"] or 0
        edit_count = row["edit_count"] or 1  # Avoid division by zero

        sessions.append(
            {
                "session_id": row["session_id"],
                "project": row["project_path"],
                "first_seen": _format_timestamp(row["first_seen"]),
                "last_seen": _format_timestamp(row["last_seen"]),
                "efficiency_signals": {
                    "compaction_count": row["compaction_count"],
                    "burn_rate_tokens_per_event": round(input_tokens / total_events, 1),
                    "avg_assistant_tokens": round(output_tokens / assistant_count, 1),
                    "total_result_mb": round(row["total_result_bytes"] / 1024 / 1024, 2),
                    "large_result_count": row["large_result_count"],
                    "has_compaction": row["compaction_count"] > 0,
                    "read_to_edit_ratio": round(read_count / edit_count, 2),
                    "files_read_multiple_times": files_read_multiple.get(row["session_id"], 0),
                },
                "totals": {
                    "events": total_events,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            }
        )

    return {
        "days": days,
        "project": project,
        "limit": limit,
        "session_count": len(sessions),
        "sessions": sessions,
    }

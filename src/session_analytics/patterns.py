"""Pattern detection and insight generation for session analytics."""

import json
import logging
import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from session_analytics.storage import Pattern, SQLiteStorage

logger = logging.getLogger("session-analytics")

# Default settings.json location
DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def compute_tool_frequency_patterns(
    storage: SQLiteStorage,
    days: int = 7,
) -> list[Pattern]:
    """Compute tool frequency patterns from events.

    Args:
        storage: Storage instance
        days: Number of days to analyze

    Returns:
        List of tool frequency patterns
    """
    cutoff = datetime.now() - timedelta(days=days)
    now = datetime.now()

    rows = storage.execute_query(
        """
        SELECT tool_name, COUNT(*) as count, MAX(timestamp) as last_seen
        FROM events
        WHERE timestamp >= ? AND tool_name IS NOT NULL
        GROUP BY tool_name
        ORDER BY count DESC
        """,
        (cutoff,),
    )

    patterns = []
    for row in rows:
        patterns.append(
            Pattern(
                id=None,
                pattern_type="tool_frequency",
                pattern_key=row["tool_name"],
                count=row["count"],
                last_seen=row["last_seen"],
                metadata={},
                computed_at=now,
            )
        )

    return patterns


def compute_command_patterns(
    storage: SQLiteStorage,
    days: int = 7,
) -> list[Pattern]:
    """Compute Bash command patterns from events.

    Args:
        storage: Storage instance
        days: Number of days to analyze

    Returns:
        List of command patterns
    """
    cutoff = datetime.now() - timedelta(days=days)
    now = datetime.now()

    rows = storage.execute_query(
        """
        SELECT command, COUNT(*) as count, MAX(timestamp) as last_seen
        FROM events
        WHERE timestamp >= ? AND tool_name = 'Bash' AND command IS NOT NULL
        GROUP BY command
        ORDER BY count DESC
        """,
        (cutoff,),
    )

    patterns = []
    for row in rows:
        patterns.append(
            Pattern(
                id=None,
                pattern_type="command_frequency",
                pattern_key=row["command"],
                count=row["count"],
                last_seen=row["last_seen"],
                metadata={},
                computed_at=now,
            )
        )

    return patterns


def compute_sequence_patterns(
    storage: SQLiteStorage,
    days: int = 7,
    sequence_length: int = 2,
    min_count: int = 3,
) -> list[Pattern]:
    """Compute tool sequence patterns (n-grams) from events.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        sequence_length: Length of sequences to detect
        min_count: Minimum occurrences to include

    Returns:
        List of sequence patterns
    """
    cutoff = datetime.now() - timedelta(days=days)
    now = datetime.now()

    # Get all tool events ordered by session and timestamp
    rows = storage.execute_query(
        """
        SELECT session_id, tool_name, timestamp
        FROM events
        WHERE timestamp >= ? AND tool_name IS NOT NULL
        ORDER BY session_id, timestamp
        """,
        (cutoff,),
    )

    # Group by session and extract sequences
    sequences: Counter = Counter()
    current_session = None
    session_tools: list[str] = []

    for row in rows:
        if row["session_id"] != current_session:
            # Process previous session
            if len(session_tools) >= sequence_length:
                for i in range(len(session_tools) - sequence_length + 1):
                    seq = tuple(session_tools[i : i + sequence_length])
                    sequences[seq] += 1

            current_session = row["session_id"]
            session_tools = []

        session_tools.append(row["tool_name"])

    # Process last session
    if len(session_tools) >= sequence_length:
        for i in range(len(session_tools) - sequence_length + 1):
            seq = tuple(session_tools[i : i + sequence_length])
            sequences[seq] += 1

    # Create patterns for sequences meeting min_count
    patterns = []
    for seq, count in sequences.most_common():
        if count < min_count:
            break
        patterns.append(
            Pattern(
                id=None,
                pattern_type="tool_sequence",
                pattern_key=" → ".join(seq),
                count=count,
                last_seen=now,
                metadata={"sequence": list(seq)},
                computed_at=now,
            )
        )

    return patterns


def sample_sequences(
    storage: SQLiteStorage,
    pattern: str,
    count: int = 5,
    context_events: int = 2,
    days: int = 7,
) -> dict:
    """Return random samples of a sequence pattern with surrounding context.

    Instead of just counting occurrences, this function returns actual examples
    of a pattern with context, enabling LLM interpretation of workflow patterns.

    Args:
        storage: Storage instance
        pattern: Sequence pattern (e.g., "Read → Edit" or "Read,Edit")
        count: Number of random samples to return (default: 5)
        context_events: Number of events before/after to include (default: 2)
        days: Number of days to analyze

    Returns:
        Dict with pattern info, total occurrences, and sampled instances
    """
    cutoff = datetime.now() - timedelta(days=days)

    # Validate pattern input
    if len(pattern) > 500:
        return {
            "pattern": pattern[:50] + "...",
            "error": "Pattern too long (max 500 characters)",
            "total_occurrences": 0,
            "samples": [],
        }

    # Parse pattern into tool list (support both "→" and "," separators)
    if " → " in pattern:
        target_tools = [t.strip() for t in pattern.split(" → ")]
    else:
        target_tools = [t.strip() for t in pattern.split(",")]

    # Validate individual tool names (alphanumeric + underscore only)
    for tool in target_tools:
        if not tool or not all(c.isalnum() or c == "_" for c in tool):
            return {
                "pattern": pattern,
                "error": f"Invalid tool name: '{tool}' (must be alphanumeric)",
                "total_occurrences": 0,
                "samples": [],
            }

    sequence_length = len(target_tools)
    if sequence_length < 2:
        return {
            "pattern": pattern,
            "error": "Pattern must contain at least 2 tools",
            "total_occurrences": 0,
            "samples": [],
        }

    # Get all tool events ordered by session and timestamp
    rows = storage.execute_query(
        """
        SELECT id, session_id, tool_name, timestamp, project_path, file_path, command
        FROM events
        WHERE timestamp >= ? AND tool_name IS NOT NULL
        ORDER BY session_id, timestamp
        """,
        (cutoff,),
    )

    # Group events by session and find pattern occurrences
    occurrences = []  # List of (session_id, start_index, events_slice)
    current_session = None
    session_events: list[dict] = []

    for row in rows:
        if row["session_id"] != current_session:
            # Process previous session to find pattern matches
            if len(session_events) >= sequence_length:
                for i in range(len(session_events) - sequence_length + 1):
                    tools = [session_events[j]["tool_name"] for j in range(i, i + sequence_length)]
                    if tools == target_tools:
                        # Calculate context boundaries
                        start_ctx = max(0, i - context_events)
                        end_ctx = min(len(session_events), i + sequence_length + context_events)
                        occurrences.append(
                            {
                                "session_id": current_session,
                                "match_start": i,
                                "context_start": start_ctx,
                                "events": session_events[start_ctx:end_ctx],
                                "match_offset": i
                                - start_ctx,  # Where in events slice the match starts
                            }
                        )

            current_session = row["session_id"]
            session_events = []

        session_events.append(
            {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "timestamp": row["timestamp"],
                "project_path": row["project_path"],
                "file_path": row["file_path"],
                "command": row["command"],
            }
        )

    # Process last session
    if len(session_events) >= sequence_length:
        for i in range(len(session_events) - sequence_length + 1):
            tools = [session_events[j]["tool_name"] for j in range(i, i + sequence_length)]
            if tools == target_tools:
                start_ctx = max(0, i - context_events)
                end_ctx = min(len(session_events), i + sequence_length + context_events)
                occurrences.append(
                    {
                        "session_id": current_session,
                        "match_start": i,
                        "context_start": start_ctx,
                        "events": session_events[start_ctx:end_ctx],
                        "match_offset": i - start_ctx,
                    }
                )

    total_occurrences = len(occurrences)

    # Random sample
    if total_occurrences <= count:
        samples = occurrences
    else:
        samples = random.sample(occurrences, count)

    # Format samples for output
    formatted_samples = []
    for sample in samples:
        events = sample["events"]
        match_start = sample["match_offset"]
        match_end = match_start + sequence_length

        formatted_events = []
        for idx, evt in enumerate(events):
            formatted_evt = {
                "tool": evt["tool_name"],
                "timestamp": evt["timestamp"].isoformat() if evt["timestamp"] else None,
                "is_match": match_start <= idx < match_end,
            }
            if evt["file_path"]:
                formatted_evt["file"] = evt["file_path"]
            if evt["command"]:
                formatted_evt["command"] = evt["command"]
            formatted_events.append(formatted_evt)

        # Get project from first event
        project = events[0]["project_path"] if events else None

        formatted_samples.append(
            {
                "session_id": sample["session_id"],
                "project": project,
                "timestamp": events[match_start]["timestamp"].isoformat()
                if events and events[match_start]["timestamp"]
                else None,
                "events": formatted_events,
            }
        )

    return {
        "pattern": pattern,
        "parsed_tools": target_tools,
        "total_occurrences": total_occurrences,
        "sample_count": len(formatted_samples),
        "context_events": context_events,
        "samples": formatted_samples,
    }


def analyze_failures(
    storage: SQLiteStorage,
    days: int = 7,
    rework_window_minutes: int = 10,
) -> dict:
    """Analyze failure patterns and recovery behavior.

    Identifies:
    - Tool errors (is_error=True in tool_result)
    - Rework patterns (same file edited multiple times quickly)
    - Error clustering by tool/command

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        rework_window_minutes: Time window for detecting rework (default: 10)

    Returns:
        Dict with failure analysis including error counts, rework patterns, recovery times
    """
    cutoff = datetime.now() - timedelta(days=days)

    # Get all error events
    error_rows = storage.execute_query(
        """
        SELECT
            id,
            timestamp,
            session_id,
            project_path,
            tool_id,
            tool_name
        FROM events
        WHERE timestamp >= ?
          AND is_error = 1
        ORDER BY timestamp
        """,
        (cutoff,),
    )

    # Count errors by session
    errors_by_session: dict[str, int] = {}
    total_errors = 0

    for row in error_rows:
        total_errors += 1
        session_id = row["session_id"]
        errors_by_session[session_id] = errors_by_session.get(session_id, 0) + 1

    # Get error counts by associated tool (from tool_use before tool_result)
    tool_error_counts = storage.execute_query(
        """
        SELECT
            e2.tool_name,
            COUNT(*) as error_count
        FROM events e1
        JOIN events e2 ON e1.tool_id = e2.tool_id AND e2.entry_type = 'tool_use'
        WHERE e1.timestamp >= ?
          AND e1.is_error = 1
          AND e1.entry_type = 'tool_result'
        GROUP BY e2.tool_name
        ORDER BY error_count DESC
        """,
        (cutoff,),
    )

    errors_by_tool = [
        {"tool": row["tool_name"], "errors": row["error_count"]}
        for row in tool_error_counts
        if row["tool_name"]
    ]

    # Detect rework patterns: same file edited multiple times in quick succession
    rework_window = timedelta(minutes=rework_window_minutes)

    file_edits = storage.execute_query(
        """
        SELECT
            timestamp,
            session_id,
            file_path
        FROM events
        WHERE timestamp >= ?
          AND tool_name = 'Edit'
          AND file_path IS NOT NULL
        ORDER BY session_id, file_path, timestamp
        """,
        (cutoff,),
    )

    rework_instances = []
    current_file = None
    current_session = None
    edits_in_window: list[datetime] = []

    for row in file_edits:
        file_path = row["file_path"]
        session_id = row["session_id"]
        timestamp = row["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        # Reset if different file or session
        if file_path != current_file or session_id != current_session:
            # Check if previous file had rework
            if len(edits_in_window) >= 3:
                rework_instances.append(
                    {
                        "file": current_file,
                        "session_id": current_session,
                        "edit_count": len(edits_in_window),
                        "duration_minutes": int(
                            (edits_in_window[-1] - edits_in_window[0]).total_seconds() / 60
                        ),
                    }
                )

            current_file = file_path
            current_session = session_id
            edits_in_window = [timestamp]
        else:
            # Same file/session - check if within window
            if edits_in_window and timestamp - edits_in_window[-1] <= rework_window:
                edits_in_window.append(timestamp)
            else:
                # Gap too large, check if we had rework
                if len(edits_in_window) >= 3:
                    rework_instances.append(
                        {
                            "file": current_file,
                            "session_id": current_session,
                            "edit_count": len(edits_in_window),
                            "duration_minutes": int(
                                (edits_in_window[-1] - edits_in_window[0]).total_seconds() / 60
                            ),
                        }
                    )
                edits_in_window = [timestamp]

    # Check final file
    if len(edits_in_window) >= 3:
        rework_instances.append(
            {
                "file": current_file,
                "session_id": current_session,
                "edit_count": len(edits_in_window),
                "duration_minutes": int(
                    (edits_in_window[-1] - edits_in_window[0]).total_seconds() / 60
                ),
            }
        )

    # Calculate summary stats
    sessions_with_errors = len(errors_by_session)
    avg_errors_per_session = total_errors / sessions_with_errors if sessions_with_errors > 0 else 0

    return {
        "days": days,
        "total_errors": total_errors,
        "sessions_with_errors": sessions_with_errors,
        "avg_errors_per_session": round(avg_errors_per_session, 2),
        "errors_by_tool": errors_by_tool[:10],
        "rework_patterns": {
            "instances_detected": len(rework_instances),
            "rework_window_minutes": rework_window_minutes,
            "examples": rework_instances[:10],
        },
    }


def load_allowed_commands(settings_path: Path = DEFAULT_SETTINGS_PATH) -> set[str]:
    """Load allowed commands from Claude Code settings.json.

    Args:
        settings_path: Path to settings.json

    Returns:
        Set of allowed command prefixes
    """
    if not settings_path.exists():
        return set()

    try:
        with open(settings_path) as f:
            settings = json.load(f)

        allowed = set()
        permissions = settings.get("permissions", {})

        # Look for allow patterns with Bash(command:*)
        for pattern in permissions.get("allow", []):
            if pattern.startswith("Bash(") and pattern.endswith(":*)"):
                cmd = pattern[5:-3]  # Extract command from "Bash(cmd:*)"
                allowed.add(cmd)

        return allowed
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load settings.json: {e}")
        return set()


def compute_permission_gaps(
    storage: SQLiteStorage,
    days: int = 7,
    threshold: int = 5,
    settings_path: Path = DEFAULT_SETTINGS_PATH,
) -> list[Pattern]:
    """Find commands that are frequently used but not in settings.json.

    Args:
        storage: Storage instance
        days: Number of days to analyze
        threshold: Minimum usage count to suggest adding
        settings_path: Path to settings.json

    Returns:
        List of permission gap patterns
    """
    cutoff = datetime.now() - timedelta(days=days)
    now = datetime.now()

    allowed_commands = load_allowed_commands(settings_path)

    rows = storage.execute_query(
        """
        SELECT command, COUNT(*) as count
        FROM events
        WHERE timestamp >= ? AND tool_name = 'Bash' AND command IS NOT NULL
        GROUP BY command
        HAVING COUNT(*) >= ?
        ORDER BY count DESC
        """,
        (cutoff, threshold),
    )

    patterns = []
    for row in rows:
        cmd = row["command"]
        if cmd not in allowed_commands:
            patterns.append(
                Pattern(
                    id=None,
                    pattern_type="permission_gap",
                    pattern_key=cmd,
                    count=row["count"],
                    last_seen=now,
                    metadata={"suggestion": f"Bash({cmd}:*)"},
                    computed_at=now,
                )
            )

    return patterns


def compute_all_patterns(
    storage: SQLiteStorage,
    days: int = 7,
) -> dict:
    """Compute all pattern types and store them.

    Args:
        storage: Storage instance
        days: Number of days to analyze

    Returns:
        Stats about computed patterns
    """
    # Clear existing patterns
    storage.clear_patterns()

    # Compute tool frequency
    tool_patterns = compute_tool_frequency_patterns(storage, days=days)
    for p in tool_patterns:
        storage.upsert_pattern(p)

    # Compute command frequency
    command_patterns = compute_command_patterns(storage, days=days)
    for p in command_patterns:
        storage.upsert_pattern(p)

    # Compute sequences
    sequence_patterns = compute_sequence_patterns(storage, days=days)
    for p in sequence_patterns:
        storage.upsert_pattern(p)

    # Compute permission gaps
    gap_patterns = compute_permission_gaps(storage, days=days)
    for p in gap_patterns:
        storage.upsert_pattern(p)

    return {
        "tool_frequency_patterns": len(tool_patterns),
        "command_patterns": len(command_patterns),
        "sequence_patterns": len(sequence_patterns),
        "permission_gap_patterns": len(gap_patterns),
        "total_patterns": len(tool_patterns)
        + len(command_patterns)
        + len(sequence_patterns)
        + len(gap_patterns),
    }


def get_insights(
    storage: SQLiteStorage,
    refresh: bool = False,
    days: int = 7,
    include_advanced: bool = True,
) -> dict:
    """Get pre-computed insights for /improve-workflow.

    Includes both traditional pattern analysis and advanced LLM-focused analytics
    from RFC #17 phases.

    Args:
        storage: Storage instance
        refresh: Force recomputation of patterns
        days: Number of days to analyze (only used if refresh=True)
        include_advanced: Include advanced analytics (trends, failures, classification)

    Returns:
        Insights organized by type, with optional advanced analytics
    """
    # Check if we need to refresh
    patterns = storage.get_patterns()
    if not patterns or refresh:
        compute_all_patterns(storage, days=days)
        patterns = storage.get_patterns()

    # Organize by type
    insights = {
        "tool_frequency": [],
        "command_frequency": [],
        "sequences": [],
        "permission_gaps": [],
    }

    for p in patterns:
        if p.pattern_type == "tool_frequency":
            insights["tool_frequency"].append({"tool": p.pattern_key, "count": p.count})
        elif p.pattern_type == "command_frequency":
            insights["command_frequency"].append({"command": p.pattern_key, "count": p.count})
        elif p.pattern_type == "tool_sequence":
            insights["sequences"].append({"sequence": p.pattern_key, "count": p.count})
        elif p.pattern_type == "permission_gap":
            insights["permission_gaps"].append(
                {
                    "command": p.pattern_key,
                    "count": p.count,
                    "suggestion": p.metadata.get("suggestion", ""),
                }
            )

    # Add summary stats
    insights["summary"] = {
        "total_tools": len(insights["tool_frequency"]),
        "total_commands": len(insights["command_frequency"]),
        "total_sequences": len(insights["sequences"]),
        "permission_gaps_found": len(insights["permission_gaps"]),
    }

    # Add advanced analytics from RFC #17 phases
    if include_advanced:
        # Trend analysis (Phase 7)
        try:
            trends = analyze_trends(storage, days=days, compare_to="previous")
            insights["trends"] = {
                "events_direction": trends["metrics"]["events"]["direction"],
                "events_change_pct": trends["metrics"]["events"]["change_pct"],
                "sessions_direction": trends["metrics"]["sessions"]["direction"],
                "sessions_change_pct": trends["metrics"]["sessions"]["change_pct"],
                "error_rate_direction": trends["metrics"]["error_rate"]["direction"],
                "significant_tool_changes": [
                    tc
                    for tc in trends.get("tool_changes", [])[:3]
                    if tc["direction"] != "unchanged"
                ],
            }
            insights["summary"]["has_trends"] = True
        except Exception as e:
            logger.warning("Failed to analyze trends in get_insights: %s", e, exc_info=True)
            insights["summary"]["has_trends"] = False

        # Failure analysis summary (Phase 4)
        try:
            failures = analyze_failures(storage, days=days)
            insights["failure_summary"] = {
                "total_errors": failures["total_errors"],
                "sessions_with_errors": failures["sessions_with_errors"],
                "rework_instances": failures["rework_patterns"]["instances_detected"],
                "top_error_tools": failures["errors_by_tool"][:3],
            }
            insights["summary"]["has_failure_analysis"] = True
        except Exception as e:
            logger.warning("Failed to analyze failures in get_insights: %s", e, exc_info=True)
            insights["summary"]["has_failure_analysis"] = False

        # Session classification summary (Phase 5) - import here to avoid circular
        from session_analytics.queries import classify_sessions

        try:
            classification = classify_sessions(storage, days=days)
            insights["session_types"] = classification.get("category_distribution", {})
            insights["summary"]["total_sessions_classified"] = classification.get(
                "session_count", 0
            )
            insights["summary"]["has_classification"] = True
        except Exception as e:
            logger.warning("Failed to classify sessions in get_insights: %s", e, exc_info=True)
            insights["summary"]["has_classification"] = False

    return insights


def get_session_signals(
    storage: SQLiteStorage,
    days: int = 7,
    min_events: int = 5,
    project: str | None = None,
) -> dict:
    """Get raw session signals for LLM interpretation.

    RFC #26 (revised per RFC #17 principle): Extracts observable session data
    without interpretation. Per RFC #17: "Don't over-distill - raw data with
    light structure beats heavily processed summaries. The LLM can handle context."

    The consuming LLM should interpret these signals to determine outcomes like
    success, abandonment, or frustration based on the full context.

    Args:
        storage: Storage instance
        days: Number of days to analyze (default: 7)
        min_events: Minimum events for a session to be included (default: 5)
        project: Optional project path filter

    Returns:
        Dict with raw session signals for LLM interpretation
    """
    cutoff = datetime.now() - timedelta(days=days)

    # Build optional project filter
    project_filter = ""
    params: list = [cutoff]
    if project:
        project_filter = "AND project_path LIKE ?"
        params.append(f"%{project}%")
    params.append(min_events)

    # Get session summaries with activity metrics
    sessions = storage.execute_query(
        f"""
        SELECT
            session_id,
            project_path,
            COUNT(*) as event_count,
            SUM(CASE WHEN is_error = 1 THEN 1 ELSE 0 END) as error_count,
            SUM(CASE WHEN tool_name = 'Edit' THEN 1 ELSE 0 END) as edit_count,
            SUM(CASE WHEN command = 'git' THEN 1 ELSE 0 END) as git_count,
            SUM(CASE WHEN skill_name IS NOT NULL THEN 1 ELSE 0 END) as skill_count,
            MIN(timestamp) as first_event,
            MAX(timestamp) as last_event
        FROM events
        WHERE timestamp >= ?
        {project_filter}
        GROUP BY session_id
        HAVING COUNT(*) >= ?
        """,
        tuple(params),
    )

    # Get commit counts per session from session_commits
    commit_counts = storage.execute_query(
        """
        SELECT session_id, COUNT(*) as commit_count
        FROM session_commits
        GROUP BY session_id
        """,
        (),
    )
    commits_by_session = {r["session_id"]: r["commit_count"] for r in commit_counts}

    # Detect rework patterns (file edited 4+ times in session)
    rework_sessions = set()
    file_edits = storage.execute_query(
        """
        SELECT session_id, file_path, COUNT(*) as edit_count
        FROM events
        WHERE timestamp >= ?
          AND tool_name = 'Edit'
          AND file_path IS NOT NULL
        GROUP BY session_id, file_path
        HAVING COUNT(*) >= 4
        """,
        (cutoff,),
    )
    for row in file_edits:
        rework_sessions.add(row["session_id"])

    # Check for PR-related activity
    pr_sessions = set()
    pr_events = storage.execute_query(
        """
        SELECT DISTINCT session_id
        FROM events
        WHERE timestamp >= ?
          AND (
            (command = 'gh' AND command_args LIKE 'pr %')
            OR skill_name LIKE '%pr%'
            OR skill_name LIKE '%commit%'
          )
        """,
        (cutoff,),
    )
    for row in pr_events:
        pr_sessions.add(row["session_id"])

    # Build raw signals for each session (no interpretation)
    signals = []
    for session in sessions:
        session_id = session["session_id"]
        event_count = session["event_count"]
        error_count = session["error_count"] or 0
        edit_count = session["edit_count"] or 0
        git_count = session["git_count"] or 0
        skill_count = session["skill_count"] or 0
        commit_count = commits_by_session.get(session_id, 0)

        # Calculate derived observables (still factual, not interpretive)
        error_rate = error_count / event_count if event_count > 0 else 0

        first_event = session["first_event"]
        last_event = session["last_event"]
        if isinstance(first_event, str):
            first_event = datetime.fromisoformat(first_event)
        if isinstance(last_event, str):
            last_event = datetime.fromisoformat(last_event)
        duration_minutes = (
            (last_event - first_event).total_seconds() / 60 if first_event and last_event else 0
        )

        signals.append(
            {
                "session_id": session_id,
                "project_path": session["project_path"],
                # Raw counts
                "event_count": event_count,
                "error_count": error_count,
                "edit_count": edit_count,
                "git_count": git_count,
                "skill_count": skill_count,
                "commit_count": commit_count,
                # Derived observables
                "error_rate": round(error_rate, 3),
                "duration_minutes": round(duration_minutes, 1),
                # Boolean flags (observable patterns)
                "has_rework": session_id in rework_sessions,
                "has_pr_activity": session_id in pr_sessions,
            }
        )

    return {
        "days": days,
        "sessions_analyzed": len(signals),
        "sessions": signals,
    }


def analyze_trends(
    storage: SQLiteStorage,
    days: int = 7,
    compare_to: str = "previous",
) -> dict:
    """Analyze trends by comparing current period to previous period.

    Compares metrics between two time periods to identify changes in usage patterns.

    Args:
        storage: Storage instance
        days: Length of current period in days (default: 7)
        compare_to: Comparison type: 'previous' (same length before current)
                    or 'same_last_month' (same days in previous month) (default: previous)

    Returns:
        Dict with trend analysis including percentage changes and direction
    """
    now = datetime.now()
    current_start = now - timedelta(days=days)

    if compare_to == "previous":
        previous_start = current_start - timedelta(days=days)
        previous_end = current_start
    else:
        # same_last_month - compare to same period last month
        previous_start = now - timedelta(days=30 + days)
        previous_end = now - timedelta(days=30)

    def get_period_metrics(start: datetime, end: datetime) -> dict:
        """Get metrics for a specific time period."""
        # Total events
        event_count = storage.execute_query(
            """
            SELECT COUNT(*) as count FROM events
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start, end),
        )
        total_events = event_count[0]["count"] if event_count else 0

        # Session count
        session_count = storage.execute_query(
            """
            SELECT COUNT(DISTINCT session_id) as count FROM events
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start, end),
        )
        sessions = session_count[0]["count"] if session_count else 0

        # Error count
        error_count = storage.execute_query(
            """
            SELECT COUNT(*) as count FROM events
            WHERE timestamp >= ? AND timestamp < ? AND is_error = 1
            """,
            (start, end),
        )
        errors = error_count[0]["count"] if error_count else 0

        # Tool usage
        tool_usage = storage.execute_query(
            """
            SELECT tool_name, COUNT(*) as count
            FROM events
            WHERE timestamp >= ? AND timestamp < ? AND tool_name IS NOT NULL
            GROUP BY tool_name
            ORDER BY count DESC
            LIMIT 10
            """,
            (start, end),
        )
        top_tools = {row["tool_name"]: row["count"] for row in tool_usage}

        # Token usage
        token_usage = storage.execute_query(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) as input_tokens,
                COALESCE(SUM(output_tokens), 0) as output_tokens
            FROM events
            WHERE timestamp >= ? AND timestamp < ?
            """,
            (start, end),
        )
        tokens = token_usage[0] if token_usage else {"input_tokens": 0, "output_tokens": 0}

        return {
            "total_events": total_events,
            "sessions": sessions,
            "errors": errors,
            "error_rate": errors / total_events if total_events > 0 else 0,
            "top_tools": top_tools,
            "input_tokens": tokens["input_tokens"] or 0,
            "output_tokens": tokens["output_tokens"] or 0,
        }

    def calculate_change(current: float, previous: float) -> dict:
        """Calculate percentage change and direction."""
        if previous == 0:
            if current == 0:
                pct_change = 0.0
                direction = "unchanged"
            else:
                pct_change = 100.0
                direction = "up"
        else:
            pct_change = ((current - previous) / previous) * 100
            if pct_change > 5:
                direction = "up"
            elif pct_change < -5:
                direction = "down"
            else:
                direction = "unchanged"

        return {
            "current": current,
            "previous": previous,
            "change_pct": round(pct_change, 1),
            "direction": direction,
        }

    current_metrics = get_period_metrics(current_start, now)
    previous_metrics = get_period_metrics(previous_start, previous_end)

    # Calculate tool-specific changes
    tool_changes = []
    all_tools = set(current_metrics["top_tools"].keys()) | set(previous_metrics["top_tools"].keys())
    for tool in all_tools:
        current_count = current_metrics["top_tools"].get(tool, 0)
        previous_count = previous_metrics["top_tools"].get(tool, 0)
        change = calculate_change(current_count, previous_count)
        if change["direction"] != "unchanged" or current_count > 0:
            tool_changes.append({"tool": tool, **change})

    # Sort by absolute change magnitude
    tool_changes.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    return {
        "days": days,
        "compare_to": compare_to,
        "current_period": {
            "start": current_start.isoformat(),
            "end": now.isoformat(),
        },
        "previous_period": {
            "start": previous_start.isoformat(),
            "end": previous_end.isoformat(),
        },
        "metrics": {
            "events": calculate_change(
                current_metrics["total_events"], previous_metrics["total_events"]
            ),
            "sessions": calculate_change(current_metrics["sessions"], previous_metrics["sessions"]),
            "errors": calculate_change(current_metrics["errors"], previous_metrics["errors"]),
            "error_rate": calculate_change(
                current_metrics["error_rate"], previous_metrics["error_rate"]
            ),
            "input_tokens": calculate_change(
                current_metrics["input_tokens"], previous_metrics["input_tokens"]
            ),
            "output_tokens": calculate_change(
                current_metrics["output_tokens"], previous_metrics["output_tokens"]
            ),
        },
        "tool_changes": tool_changes[:10],
    }

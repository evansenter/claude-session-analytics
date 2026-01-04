"""JSONL log ingestion for Claude Code session analytics."""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from session_analytics.queries import get_cutoff, normalize_datetime
from session_analytics.storage import Event, GitCommit, IngestionState, Session, SQLiteStorage

logger = logging.getLogger("session-analytics")

# Default location for Claude Code session logs
DEFAULT_LOGS_DIR = Path.home() / ".claude" / "projects"

# Maximum length for user message text to prevent DB bloat while preserving context
USER_MESSAGE_MAX_LENGTH = 2000


def find_log_files(
    logs_dir: Path = DEFAULT_LOGS_DIR,
    days: int = 7,
    project_filter: str | None = None,
) -> list[Path]:
    """Find JSONL log files within the specified time range.

    Args:
        logs_dir: Directory containing project subdirectories
        days: Only include files modified within this many days
        project_filter: Optional project path to filter (encoded form)

    Returns:
        List of JSONL file paths, sorted by modification time (newest first)
    """
    if not logs_dir.exists():
        logger.warning(f"Logs directory does not exist: {logs_dir}")
        return []

    cutoff = get_cutoff(days=days)
    files = []

    for project_dir in logs_dir.iterdir():
        if not project_dir.is_dir():
            continue

        # Apply project filter if specified
        if project_filter and project_filter not in project_dir.name:
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)
                if mtime >= cutoff:
                    files.append((jsonl_file, mtime))
            except OSError as e:
                logger.warning(f"Could not stat {jsonl_file}: {e}")

    # Sort by modification time, newest first
    files.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in files]


def extract_command_name(content: str | list) -> str | None:
    """Extract command name from isMeta user message content.

    User-defined commands (e.g., /status-report) are expanded as user messages
    with isMeta=true. The content starts with a markdown heading like "# Status Report".

    Returns:
        Normalized command name (e.g., "status-report") or None if not detected.
    """
    # Get the text content
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                break
            elif isinstance(item, str):
                text = item
                break

    if not text:
        return None

    # Look for markdown heading at the start: "# Command Name"
    match = re.match(r"^#\s+(.+?)(?:\n|$)", text.strip())
    if not match:
        return None

    # Normalize: "Status Report" -> "status-report", "I'm Lost" -> "im-lost"
    # Use regex to replace non-alphanumeric chars with hyphens, then clean up
    command_name = re.sub(r"[^a-z0-9]+", "-", match.group(1).strip().lower())
    command_name = command_name.strip("-")  # Remove leading/trailing hyphens

    # Filter out common non-command headings
    non_commands = {"context", "instructions", "usage", "example", "examples", "notes"}
    if command_name in non_commands:
        return None

    return command_name


def parse_tool_use(tool_use: dict) -> dict:
    """Extract normalized fields from a tool_use block.

    Returns dict with: tool_name, tool_id, tool_input_json, command, command_args,
    file_path, skill_name
    """
    result = {
        "tool_name": tool_use.get("name"),
        "tool_id": tool_use.get("id"),
        "tool_input_json": json.dumps(tool_use.get("input", {})),
        "command": None,
        "command_args": None,
        "file_path": None,
        "skill_name": None,
    }

    tool_input = tool_use.get("input", {})
    tool_name = result["tool_name"]

    # Extract Bash command info
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if cmd:
            parts = cmd.split(None, 1)
            result["command"] = parts[0] if parts else None
            result["command_args"] = parts[1] if len(parts) > 1 else None

    # Extract file path for file operations
    elif tool_name in ("Read", "Edit", "Write", "Glob", "Grep"):
        result["file_path"] = tool_input.get("file_path") or tool_input.get("path")

    # Extract skill name
    elif tool_name == "Skill":
        result["skill_name"] = tool_input.get("skill")

    # Note: MCP tools (mcp__*) don't need special extraction - full name is preserved

    return result


def parse_entry(raw: dict, project_path: str) -> list[Event]:
    """Parse a single JSONL entry into Event objects.

    An entry may produce multiple events (e.g., assistant with multiple tool_use blocks).

    Args:
        raw: Parsed JSON object from JSONL
        project_path: Encoded project path from directory name

    Returns:
        List of Event objects (may be empty for skipped entries)
    """
    entry_type = raw.get("type")

    # Skip certain entry types that don't contain useful analytics data
    if entry_type in ("file-history-snapshot", "queue-operation", "create"):
        return []

    # Skip thinking/text blocks that are nested content
    if entry_type in ("thinking", "text", "tool_use", "tool_result", "message"):
        return []

    uuid = raw.get("uuid")
    session_id = raw.get("sessionId")
    timestamp_str = raw.get("timestamp")

    # Skip entries without required fields
    if not uuid or not session_id or not timestamp_str:
        return []

    # Parse timestamp from Claude Code JSONL format:
    # - Input format: ISO 8601 with "Z" suffix (e.g., "2024-12-15T10:30:00.000Z")
    # - We replace "Z" with "+00:00" for Python's fromisoformat() compatibility
    # - We then strip timezone info to store as naive datetime in SQLite
    # - This ensures consistent ordering and comparison without timezone complexity
    try:
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        timestamp = timestamp.replace(tzinfo=None)
    except (ValueError, AttributeError):
        logger.debug(f"Could not parse timestamp: {timestamp_str}")
        return []

    # Extract common fields
    cwd = raw.get("cwd")
    git_branch = raw.get("gitBranch")

    # Extract token usage from assistant messages
    message = raw.get("message", {})
    usage = message.get("usage", {})
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    cache_read_tokens = usage.get("cache_read_input_tokens")
    cache_creation_tokens = usage.get("cache_creation_input_tokens")
    model = message.get("model")

    # RFC #41: Extract agent tracking fields
    agent_id = raw.get("agentId")  # Present only in agent-*.jsonl files
    is_sidechain = raw.get("isSidechain", False)  # True for agent/background work
    version = raw.get("version")  # Claude Code version

    events = []

    # Handle assistant entries with tool_use blocks
    # RFC #41: Always create assistant event with tokens, then tool_use events without tokens
    if entry_type == "assistant":
        content = message.get("content", [])
        tool_uses = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]

        # ALWAYS create assistant event with tokens (fixes token duplication)
        events.append(
            Event(
                id=None,
                uuid=uuid,
                timestamp=timestamp,
                session_id=session_id,
                project_path=project_path,
                entry_type="assistant",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_creation_tokens=cache_creation_tokens,
                model=model,
                git_branch=git_branch,
                cwd=cwd,
                # RFC #41: Agent tracking fields
                parent_uuid=None,  # Assistant events have no parent
                agent_id=agent_id,
                is_sidechain=is_sidechain,
                version=version,
            )
        )

        # Create tool_use events WITHOUT tokens, linked via parent_uuid
        for tool_use in tool_uses:
            parsed = parse_tool_use(tool_use)
            events.append(
                Event(
                    id=None,
                    uuid=f"{uuid}:{parsed['tool_id']}",  # Unique per tool_use
                    timestamp=timestamp,
                    session_id=session_id,
                    project_path=project_path,
                    entry_type="tool_use",
                    tool_name=parsed["tool_name"],
                    tool_input_json=parsed["tool_input_json"],
                    tool_id=parsed["tool_id"],
                    is_error=False,
                    command=parsed["command"],
                    command_args=parsed["command_args"],
                    file_path=parsed["file_path"],
                    skill_name=parsed["skill_name"],
                    # RFC #41: NO tokens on tool_use - they're on the parent assistant
                    input_tokens=None,
                    output_tokens=None,
                    cache_read_tokens=None,
                    cache_creation_tokens=None,
                    model=model,
                    git_branch=git_branch,
                    cwd=cwd,
                    # RFC #41: Link to parent assistant event
                    parent_uuid=uuid,
                    agent_id=agent_id,
                    is_sidechain=is_sidechain,
                    version=version,
                )
            )

    # Handle user entries (may contain tool_result)
    elif entry_type == "user":
        content = message.get("content", "")

        # Extract user message text for user journey tracking
        user_message_text = None
        if isinstance(content, str):
            user_message_text = content[:USER_MESSAGE_MAX_LENGTH] if content else None
        elif isinstance(content, list):
            # Extract text from text blocks in the content list
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            if text_parts:
                user_message_text = " ".join(text_parts)[:USER_MESSAGE_MAX_LENGTH]

        # Extract command name from isMeta user messages (slash command expansions)
        # e.g., /status-report expands to a user message starting with "# Status Report"
        is_meta = raw.get("isMeta", False)
        command_name = extract_command_name(content) if is_meta else None

        # Check if content is a list with tool_result blocks
        if isinstance(content, list):
            tool_results = [
                c for c in content if isinstance(c, dict) and c.get("type") == "tool_result"
            ]
            if tool_results:
                for tr in tool_results:
                    # Check for error
                    is_error = tr.get("is_error", False)
                    events.append(
                        Event(
                            id=None,
                            uuid=f"{uuid}:{tr.get('tool_use_id', 'result')}",
                            timestamp=timestamp,
                            session_id=session_id,
                            project_path=project_path,
                            entry_type="tool_result",
                            tool_id=tr.get("tool_use_id"),
                            is_error=is_error,
                            git_branch=git_branch,
                            cwd=cwd,
                            # RFC #41: Agent tracking fields
                            agent_id=agent_id,
                            is_sidechain=is_sidechain,
                            version=version,
                        )
                    )
            else:
                # User message with other content types
                events.append(
                    Event(
                        id=None,
                        uuid=uuid,
                        timestamp=timestamp,
                        session_id=session_id,
                        project_path=project_path,
                        entry_type="command" if command_name else "user",
                        skill_name=command_name,  # Reuse skill_name for command tracking
                        user_message_text=user_message_text,
                        git_branch=git_branch,
                        cwd=cwd,
                        # RFC #41: Agent tracking fields
                        agent_id=agent_id,
                        is_sidechain=is_sidechain,
                        version=version,
                    )
                )
        else:
            # Plain text user message
            events.append(
                Event(
                    id=None,
                    uuid=uuid,
                    timestamp=timestamp,
                    session_id=session_id,
                    project_path=project_path,
                    entry_type="command" if command_name else "user",
                    skill_name=command_name,  # Reuse skill_name for command tracking
                    user_message_text=user_message_text,
                    git_branch=git_branch,
                    cwd=cwd,
                    # RFC #41: Agent tracking fields
                    agent_id=agent_id,
                    is_sidechain=is_sidechain,
                    version=version,
                )
            )

    # Handle summary entries
    elif entry_type == "summary":
        events.append(
            Event(
                id=None,
                uuid=uuid if uuid else f"summary:{raw.get('leafUuid', 'unknown')}",
                timestamp=timestamp if timestamp else datetime.now(),
                session_id=session_id if session_id else "unknown",
                project_path=project_path,
                entry_type="summary",
                # RFC #41: Agent tracking fields
                agent_id=agent_id,
                is_sidechain=is_sidechain,
                version=version,
            )
        )

    return events


def ingest_file(
    file_path: Path,
    storage: SQLiteStorage,
    force: bool = False,
) -> dict:
    """Ingest a single JSONL file.

    Uses incremental ingestion - only processes new entries if file has changed.

    Args:
        file_path: Path to JSONL file
        storage: Storage instance
        force: Force re-ingestion even if file hasn't changed

    Returns:
        Stats dict with entries_processed, events_added, skipped
    """
    file_str = str(file_path)
    stat = file_path.stat()
    file_size = stat.st_size
    file_mtime = datetime.fromtimestamp(stat.st_mtime)

    # Check if we've already processed this file
    state = storage.get_ingestion_state(file_str)
    if state and not force:
        # Skip if file hasn't changed
        if state.file_size == file_size and state.last_modified >= file_mtime:
            return {"entries_processed": 0, "events_added": 0, "skipped": True}

    # Extract project path from directory name
    project_path = file_path.parent.name

    # Parse and collect events
    events = []
    entries_processed = 0
    errors = 0

    with open(file_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                raw = json.loads(line)
                parsed_events = parse_entry(raw, project_path)
                events.extend(parsed_events)
                entries_processed += 1
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse error in {file_path}:{line_num}: {e}")
                errors += 1
            except Exception as e:
                logger.warning(f"Error processing {file_path}:{line_num}: {e}")
                errors += 1

    # Batch insert events
    events_added = storage.add_events_batch(events) if events else 0

    # Update ingestion state
    storage.update_ingestion_state(
        IngestionState(
            file_path=file_str,
            file_size=file_size,
            last_modified=file_mtime,
            entries_processed=entries_processed,
            last_processed=datetime.now(),
        )
    )

    return {
        "entries_processed": entries_processed,
        "events_added": events_added,
        "skipped": False,
        "errors": errors,
    }


def update_session_stats(storage: SQLiteStorage) -> int:
    """Update session statistics from ingested events.

    Returns number of sessions updated.
    """
    # Query distinct sessions from events
    with storage._connect() as conn:
        rows = conn.execute("""
            SELECT
                session_id,
                project_path,
                MIN(timestamp) as first_seen,
                MAX(timestamp) as last_seen,
                COUNT(*) as entry_count,
                SUM(CASE WHEN tool_name IS NOT NULL THEN 1 ELSE 0 END) as tool_use_count,
                SUM(COALESCE(input_tokens, 0)) as total_input_tokens,
                SUM(COALESCE(output_tokens, 0)) as total_output_tokens,
                (SELECT git_branch FROM events e2
                 WHERE e2.session_id = events.session_id
                 ORDER BY timestamp DESC LIMIT 1) as primary_branch
            FROM events
            GROUP BY session_id
        """).fetchall()

        count = 0
        for row in rows:
            storage.upsert_session(
                Session(
                    id=row["session_id"],
                    project_path=row["project_path"],
                    first_seen=row["first_seen"],
                    last_seen=row["last_seen"],
                    entry_count=row["entry_count"],
                    tool_use_count=row["tool_use_count"],
                    total_input_tokens=row["total_input_tokens"],
                    total_output_tokens=row["total_output_tokens"],
                    primary_branch=row["primary_branch"],
                )
            )
            count += 1

    return count


def ingest_logs(
    storage: SQLiteStorage,
    days: int = 7,
    project: str | None = None,
    force: bool = False,
) -> dict:
    """Ingest all JSONL log files.

    Args:
        storage: Storage instance
        days: Number of days to look back
        project: Optional project filter
        force: Force re-ingestion

    Returns:
        Stats dict with totals
    """
    files = find_log_files(days=days, project_filter=project)

    total_entries = 0
    total_events = 0
    files_processed = 0
    files_skipped = 0
    total_errors = 0

    for file_path in files:
        try:
            result = ingest_file(file_path, storage, force=force)
            if result["skipped"]:
                files_skipped += 1
            else:
                files_processed += 1
                total_entries += result["entries_processed"]
                total_events += result["events_added"]
                total_errors += result.get("errors", 0)
        except Exception as e:
            logger.error(f"Failed to ingest {file_path}: {e}")
            total_errors += 1

    # Update session statistics
    sessions_updated = update_session_stats(storage)

    return {
        "files_found": len(files),
        "files_processed": files_processed,
        "files_skipped": files_skipped,
        "entries_processed": total_entries,
        "events_added": total_events,
        "sessions_updated": sessions_updated,
        "errors": total_errors,
    }


def ingest_git_history(
    storage: SQLiteStorage,
    repo_path: Path | str | None = None,
    days: int = 7,
    project_path: str | None = None,
) -> dict:
    """Ingest git commit history from a repository.

    Parses git log output and stores commits in the database for correlation
    with session activity.

    Args:
        storage: Storage instance
        repo_path: Path to git repository (default: current directory)
        days: Number of days of history to ingest (default: 7)
        project_path: Optional project path to associate commits with

    Returns:
        Dict with ingestion stats
    """
    import subprocess

    if repo_path is None:
        repo_path = Path.cwd()
    else:
        repo_path = Path(repo_path)

    if not (repo_path / ".git").is_dir():
        return {
            "error": f"Not a git repository: {repo_path}",
            "commits_added": 0,
        }

    # Get commits from the last N days
    since_date = get_cutoff(days=days).strftime("%Y-%m-%d")

    try:
        # Git log format: hash|author|date|subject
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={since_date}",
                "--format=%H|%an|%aI|%s",
                "--all",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return {
                "error": f"git log failed: {result.stderr}",
                "commits_added": 0,
            }

        commits = []
        skipped_malformed = 0
        skipped_date_parse = 0

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            parts = line.split("|", 3)
            if len(parts) < 4:
                skipped_malformed += 1
                logger.debug("Skipping malformed git log line: %s", line[:100])
                continue

            sha, author, date_str, message = parts

            try:
                timestamp = datetime.fromisoformat(date_str)
            except ValueError:
                skipped_date_parse += 1
                logger.debug("Failed to parse date '%s' for commit %s", date_str, sha[:8])
                continue

            commits.append(
                GitCommit(
                    sha=sha,
                    message=f"[{author}] {message[:480]}",  # Include author in message
                    timestamp=timestamp,
                    project_path=project_path or str(repo_path),
                    session_id=None,  # Will be correlated later
                )
            )

        # Store commits
        added = storage.add_git_commits_batch(commits)
        total_lines = len(commits) + skipped_malformed + skipped_date_parse

        return {
            "repo_path": str(repo_path),
            "days": days,
            "commits_found": total_lines,
            "commits_parsed": len(commits),
            "commits_added": added,
            "skipped_malformed": skipped_malformed,
            "skipped_date_parse": skipped_date_parse,
        }

    except subprocess.TimeoutExpired:
        return {
            "error": "git log timed out",
            "commits_added": 0,
        }
    except FileNotFoundError:
        return {
            "error": "git command not found",
            "commits_added": 0,
        }


def correlate_git_with_sessions(
    storage: SQLiteStorage,
    days: int = 7,
) -> dict:
    """Correlate git commits with session activity.

    Associates commits with sessions based on timing - if a commit was made
    during an active session, it's likely related to that session's work.

    RFC #26: Also populates session_commits junction table with timing metadata:
    - time_to_commit_seconds: Time from session start to commit
    - is_first_commit: Whether this was the first commit in the session

    Args:
        storage: Storage instance
        days: Number of days to correlate (default: 7)

    Returns:
        Dict with correlation stats
    """
    cutoff = get_cutoff(days=days)

    # Get session time ranges
    sessions = storage.execute_query(
        """
        SELECT session_id, project_path,
               MIN(timestamp) as start_time,
               MAX(timestamp) as end_time
        FROM events
        WHERE timestamp >= ?
        GROUP BY session_id
        """,
        (cutoff,),
    )

    # Build session lookup by time range
    session_ranges = []
    for s in sessions:
        start = s["start_time"]
        end = s["end_time"]
        if isinstance(start, str):
            start = datetime.fromisoformat(start)
        if isinstance(end, str):
            end = datetime.fromisoformat(end)
        # Normalize to naive datetime for consistent comparison with git commits
        start = normalize_datetime(start)
        end = normalize_datetime(end)
        session_ranges.append(
            {
                "session_id": s["session_id"],
                "project_path": s["project_path"],
                "start": start,
                "end": end,
            }
        )

    # Get commits and filter to uncorrelated ones
    all_commits = storage.get_git_commits(start=cutoff)
    commits = [c for c in all_commits if c.session_id is None]

    # Buffer of 5 minutes before session start and after session end
    # Commits just before starting a session are often related preparatory work
    buffer = timedelta(minutes=5)

    # Collect correlations for batch update: (session_id, sha)
    correlations: list[tuple[str, str]] = []
    # Collect session_commits data: (session_id, sha, time_to_commit_seconds, is_first_commit)
    session_commit_links: list[tuple[str, str, int | None, bool]] = []
    # Track first commit per session for is_first_commit calculation
    session_first_commits: dict[str, tuple[str, datetime]] = {}  # session_id -> (sha, time)

    for commit in commits:
        commit_time = commit.timestamp
        if isinstance(commit_time, str):
            commit_time = datetime.fromisoformat(commit_time)
        # Normalize to naive datetime for consistent comparison with session times
        commit_time = normalize_datetime(commit_time)

        # Find matching session (commit within session window ± 5 min buffer)
        for sr in session_ranges:
            if (sr["start"] - buffer) <= commit_time <= (sr["end"] + buffer):
                session_id = sr["session_id"]
                correlations.append((session_id, commit.sha))

                # Calculate time to commit (seconds from session start)
                time_to_commit = int((commit_time - sr["start"]).total_seconds())
                # Clamp negative values (commits before session start) to 0
                time_to_commit = max(0, time_to_commit)

                # Track earliest commit per session for is_first_commit
                if session_id not in session_first_commits:
                    session_first_commits[session_id] = (commit.sha, commit_time)
                elif commit_time < session_first_commits[session_id][1]:
                    session_first_commits[session_id] = (commit.sha, commit_time)

                session_commit_links.append((session_id, commit.sha, time_to_commit, False))
                break

    # Mark is_first_commit for each session's earliest commit
    session_commit_links_final = []
    for session_id, sha, time_to_commit, _ in session_commit_links:
        is_first = session_first_commits.get(session_id, (None,))[0] == sha
        session_commit_links_final.append((session_id, sha, time_to_commit, is_first))

    # Batch update all correlations
    correlated_count = 0
    correlation_errors = 0
    session_commits_added = 0
    session_commits_errors = 0

    if correlations:
        try:
            storage.executemany(
                """
                UPDATE git_commits
                SET session_id = ?
                WHERE sha = ?
                """,
                correlations,
            )
            correlated_count = len(correlations)
        except Exception as e:
            logger.error(
                "Failed to batch correlate %d commits: %s",
                len(correlations),
                e,
            )
            correlation_errors = len(correlations)

    # RFC #26: Populate session_commits junction table
    if session_commit_links_final:
        try:
            session_commits_added = storage.add_session_commits_batch(session_commit_links_final)
        except Exception as e:
            logger.error(
                "Failed to add %d session_commits: %s",
                len(session_commit_links_final),
                e,
            )
            session_commits_errors = len(session_commit_links_final)

    return {
        "days": days,
        "sessions_analyzed": len(session_ranges),
        "commits_checked": len(commits),
        "commits_correlated": correlated_count,
        "session_commits_added": session_commits_added,
        "correlation_errors": correlation_errors,
        "session_commits_errors": session_commits_errors,
    }

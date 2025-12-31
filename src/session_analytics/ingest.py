"""JSONL log ingestion for Claude Code session analytics."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from session_analytics.storage import Event, IngestionState, Session, SQLiteStorage

logger = logging.getLogger("session-analytics")

# Default location for Claude Code session logs
DEFAULT_LOGS_DIR = Path.home() / ".claude" / "projects"


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

    cutoff = datetime.now() - timedelta(days=days)
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

    # Handle MCP tools (e.g., mcp__event-bus__register_session)
    elif tool_name and tool_name.startswith("mcp__"):
        # Keep the full name for MCP tools
        pass

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

    try:
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        # Convert to naive datetime (remove timezone for SQLite compatibility)
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

    events = []

    # Handle assistant entries with tool_use blocks
    if entry_type == "assistant":
        content = message.get("content", [])
        tool_uses = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]

        if tool_uses:
            # Create an event for each tool_use
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
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read_tokens,
                        cache_creation_tokens=cache_creation_tokens,
                        model=model,
                        git_branch=git_branch,
                        cwd=cwd,
                    )
                )
        else:
            # Assistant message without tools
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
                )
            )

    # Handle user entries (may contain tool_result)
    elif entry_type == "user":
        content = message.get("content", "")

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
                        entry_type="user",
                        git_branch=git_branch,
                        cwd=cwd,
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
                    entry_type="user",
                    git_branch=git_branch,
                    cwd=cwd,
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

"""Command-line interface for session analytics."""

import argparse
import json

from session_analytics.ingest import ingest_logs
from session_analytics.patterns import compute_permission_gaps, compute_sequence_patterns
from session_analytics.patterns import get_insights as do_get_insights
from session_analytics.queries import (
    query_commands,
    query_sessions,
    query_tokens,
    query_tool_frequency,
)
from session_analytics.storage import SQLiteStorage


def format_output(data: dict, json_output: bool = False) -> str:
    """Format output as JSON or human-readable."""
    if json_output:
        return json.dumps(data, indent=2, default=str)

    # Human-readable formatting based on data type
    lines = []

    if "total_tool_calls" in data:
        lines.append(f"Total tool calls: {data['total_tool_calls']}")
        lines.append("")
        lines.append("Tool frequency:")
        for tool in data.get("tools", [])[:20]:
            lines.append(f"  {tool['tool']}: {tool['count']}")

    elif "total_commands" in data:
        lines.append(f"Total commands: {data['total_commands']}")
        lines.append("")
        lines.append("Command frequency:")
        for cmd in data.get("commands", [])[:20]:
            lines.append(f"  {cmd['command']}: {cmd['count']}")

    elif "session_count" in data and "total_entries" in data:
        # Session query result
        lines.append(f"Sessions: {data['session_count']}")
        lines.append(f"Total entries: {data['total_entries']}")
        lines.append(
            f"Total tokens: {data.get('total_input_tokens', 0) + data.get('total_output_tokens', 0)}"
        )

    elif "breakdown" in data:
        lines.append(f"Token usage by {data.get('group_by', 'unknown')}:")
        lines.append(f"Total input: {data['total_input_tokens']}")
        lines.append(f"Total output: {data['total_output_tokens']}")
        lines.append("")
        for item in data["breakdown"][:20]:
            key = item.get("day") or item.get("session_id") or item.get("model")
            lines.append(f"  {key}: {item['input_tokens']} in / {item['output_tokens']} out")

    elif "summary" in data:
        # get_insights output (has both summary and other keys)
        lines.append("Insights summary:")
        lines.append(f"  Tools: {data['summary']['total_tools']}")
        lines.append(f"  Commands: {data['summary']['total_commands']}")
        lines.append(f"  Sequences: {data['summary']['total_sequences']}")
        lines.append(f"  Permission gaps: {data['summary']['permission_gaps_found']}")

    elif "sequences" in data:
        lines.append("Common tool sequences:")
        for seq in data.get("sequences", [])[:20]:
            lines.append(f"  {seq['pattern']}: {seq['count']}")

    elif "gaps" in data:
        lines.append("Permission gaps (consider adding to settings.json):")
        for gap in data.get("gaps", [])[:20]:
            lines.append(f"  {gap['command']}: {gap['count']} uses -> {gap['suggestion']}")

    elif "files_found" in data:
        lines.append(f"Files found: {data['files_found']}")
        lines.append(f"Files processed: {data['files_processed']}")
        lines.append(f"Events added: {data['events_added']}")
        lines.append(f"Sessions updated: {data.get('sessions_updated', 0)}")

    elif "event_count" in data:
        lines.append(f"Database: {data.get('db_path', 'unknown')}")
        lines.append(f"Size: {data.get('db_size_bytes', 0) / 1024:.1f} KB")
        lines.append(f"Events: {data['event_count']}")
        lines.append(f"Sessions: {data['session_count']}")
        lines.append(f"Patterns: {data.get('pattern_count', 0)}")
        if data.get("earliest_event"):
            lines.append(
                f"Date range: {data['earliest_event'][:10]} to {data['latest_event'][:10]}"
            )

    else:
        return json.dumps(data, indent=2, default=str)

    return "\n".join(lines)


def cmd_status(args):
    """Show database status."""
    storage = SQLiteStorage()
    stats = storage.get_db_stats()
    last_ingest = storage.get_last_ingestion_time()

    result = {
        "last_ingestion": last_ingest.isoformat() if last_ingest else None,
        **stats,
    }
    print(format_output(result, args.json))


def cmd_ingest(args):
    """Ingest log files."""
    storage = SQLiteStorage()
    result = ingest_logs(
        storage,
        days=args.days,
        project=args.project,
        force=args.force,
    )
    print(format_output(result, args.json))


def cmd_frequency(args):
    """Show tool frequency."""
    storage = SQLiteStorage()
    result = query_tool_frequency(storage, days=args.days, project=args.project)
    print(format_output(result, args.json))


def cmd_commands(args):
    """Show command frequency."""
    storage = SQLiteStorage()
    result = query_commands(storage, days=args.days, project=args.project, prefix=args.prefix)
    print(format_output(result, args.json))


def cmd_sessions(args):
    """Show session info."""
    storage = SQLiteStorage()
    result = query_sessions(storage, days=args.days, project=args.project)
    print(format_output(result, args.json))


def cmd_tokens(args):
    """Show token usage."""
    storage = SQLiteStorage()
    result = query_tokens(storage, days=args.days, project=args.project, by=args.by)
    print(format_output(result, args.json))


def cmd_sequences(args):
    """Show tool sequences."""
    storage = SQLiteStorage()
    patterns = compute_sequence_patterns(
        storage, days=args.days, sequence_length=args.length, min_count=args.min_count
    )
    result = {
        "days": args.days,
        "sequences": [{"pattern": p.pattern_key, "count": p.count} for p in patterns],
    }
    print(format_output(result, args.json))


def cmd_permissions(args):
    """Show permission gaps."""
    storage = SQLiteStorage()
    patterns = compute_permission_gaps(storage, days=args.days, threshold=args.threshold)
    result = {
        "days": args.days,
        "gaps": [
            {
                "command": p.pattern_key,
                "count": p.count,
                "suggestion": p.metadata.get("suggestion", ""),
            }
            for p in patterns
        ],
    }
    print(format_output(result, args.json))


def cmd_insights(args):
    """Show insights for /improve-workflow."""
    storage = SQLiteStorage()
    result = do_get_insights(storage, refresh=args.refresh, days=args.days)
    print(format_output(result, args.json))


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Claude Session Analytics CLI",
        prog="session-analytics-cli",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    sub = subparsers.add_parser("status", help="Show database status")
    sub.set_defaults(func=cmd_status)

    # ingest
    sub = subparsers.add_parser("ingest", help="Ingest log files")
    sub.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.add_argument("--force", action="store_true", help="Force re-ingestion")
    sub.set_defaults(func=cmd_ingest)

    # frequency
    sub = subparsers.add_parser("frequency", help="Show tool frequency")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_frequency)

    # commands
    sub = subparsers.add_parser("commands", help="Show command frequency")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.add_argument("--prefix", help="Command prefix filter (e.g., 'git')")
    sub.set_defaults(func=cmd_commands)

    # sessions
    sub = subparsers.add_parser("sessions", help="Show session info")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_sessions)

    # tokens
    sub = subparsers.add_parser("tokens", help="Show token usage")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.add_argument("--by", choices=["day", "session", "model"], default="day", help="Group by")
    sub.set_defaults(func=cmd_tokens)

    # sequences
    sub = subparsers.add_parser("sequences", help="Show tool sequences")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--min-count", type=int, default=3, help="Minimum occurrences")
    sub.add_argument("--length", type=int, default=2, help="Sequence length")
    sub.set_defaults(func=cmd_sequences)

    # permissions
    sub = subparsers.add_parser("permissions", help="Show permission gaps")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--threshold", type=int, default=5, help="Minimum usage count")
    sub.set_defaults(func=cmd_permissions)

    # insights
    sub = subparsers.add_parser("insights", help="Show insights for /improve-workflow")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--refresh", action="store_true", help="Force refresh patterns")
    sub.set_defaults(func=cmd_insights)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

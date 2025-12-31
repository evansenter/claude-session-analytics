"""Command-line interface for session analytics."""

import argparse
import json

from session_analytics.ingest import (
    correlate_git_with_sessions as do_correlate_git,
)
from session_analytics.ingest import (
    ingest_git_history as do_ingest_git,
)
from session_analytics.ingest import (
    ingest_logs,
)
from session_analytics.patterns import (
    analyze_failures as do_analyze_failures,
)
from session_analytics.patterns import analyze_trends as do_analyze_trends
from session_analytics.patterns import (
    compute_permission_gaps,
    compute_sequence_patterns,
)
from session_analytics.patterns import get_insights as do_get_insights
from session_analytics.patterns import (
    sample_sequences as do_sample_sequences,
)
from session_analytics.queries import (
    classify_sessions as do_classify_sessions,
)
from session_analytics.queries import (
    detect_parallel_sessions,
    find_related_sessions,
    get_user_journey,
    query_commands,
    query_sessions,
    query_tokens,
    query_tool_frequency,
)
from session_analytics.queries import (
    get_handoff_context as do_get_handoff_context,
)
from session_analytics.storage import SQLiteStorage

# Formatter registry: list of (predicate, formatter) tuples
# Each predicate checks if this formatter can handle the data
# Order matters - first match wins
_FORMATTERS: list[tuple[callable, callable]] = []


def _register_formatter(predicate: callable):
    """Decorator to register a formatter with its predicate."""

    def decorator(formatter: callable):
        _FORMATTERS.append((predicate, formatter))
        return formatter

    return decorator


@_register_formatter(lambda d: "total_tool_calls" in d)
def _format_tool_frequency(data: dict) -> list[str]:
    lines = [f"Total tool calls: {data['total_tool_calls']}", "", "Tool frequency:"]
    for tool in data.get("tools", [])[:20]:
        lines.append(f"  {tool['tool']}: {tool['count']}")
    return lines


@_register_formatter(lambda d: "total_commands" in d)
def _format_commands(data: dict) -> list[str]:
    lines = [f"Total commands: {data['total_commands']}", "", "Command frequency:"]
    for cmd in data.get("commands", [])[:20]:
        lines.append(f"  {cmd['command']}: {cmd['count']}")
    return lines


@_register_formatter(lambda d: "session_count" in d and "total_entries" in d)
def _format_sessions(data: dict) -> list[str]:
    total_tokens = data.get("total_input_tokens", 0) + data.get("total_output_tokens", 0)
    return [
        f"Sessions: {data['session_count']}",
        f"Total entries: {data['total_entries']}",
        f"Total tokens: {total_tokens}",
    ]


@_register_formatter(lambda d: "breakdown" in d)
def _format_tokens(data: dict) -> list[str]:
    lines = [
        f"Token usage by {data.get('group_by', 'unknown')}:",
        f"Total input: {data['total_input_tokens']}",
        f"Total output: {data['total_output_tokens']}",
        "",
    ]
    for item in data["breakdown"][:20]:
        key = item.get("day") or item.get("session_id") or item.get("model")
        lines.append(f"  {key}: {item['input_tokens']} in / {item['output_tokens']} out")
    return lines


@_register_formatter(lambda d: "summary" in d)
def _format_insights(data: dict) -> list[str]:
    return [
        "Insights summary:",
        f"  Tools: {data['summary']['total_tools']}",
        f"  Commands: {data['summary']['total_commands']}",
        f"  Sequences: {data['summary']['total_sequences']}",
        f"  Permission gaps: {data['summary']['permission_gaps_found']}",
    ]


@_register_formatter(lambda d: "sequences" in d)
def _format_sequences(data: dict) -> list[str]:
    lines = ["Common tool sequences:"]
    for seq in data.get("sequences", [])[:20]:
        lines.append(f"  {seq['pattern']}: {seq['count']}")
    return lines


@_register_formatter(lambda d: "gaps" in d)
def _format_gaps(data: dict) -> list[str]:
    lines = ["Permission gaps (consider adding to settings.json):"]
    for gap in data.get("gaps", [])[:20]:
        lines.append(f"  {gap['command']}: {gap['count']} uses -> {gap['suggestion']}")
    return lines


@_register_formatter(lambda d: "samples" in d and "parsed_tools" in d)
def _format_sample_sequences(data: dict) -> list[str]:
    lines = [
        f"Pattern: {data['pattern']}",
        f"Total occurrences: {data['total_occurrences']}",
        f"Samples shown: {data['sample_count']}",
        "",
    ]
    for i, sample in enumerate(data.get("samples", [])[:10], 1):
        lines.append(f"Sample {i} ({sample.get('project', 'unknown')}):")
        for evt in sample.get("events", []):
            marker = "→ " if evt.get("is_match") else "  "
            details = []
            if evt.get("file"):
                details.append(evt["file"])
            if evt.get("command"):
                details.append(evt["command"])
            detail_str = f" ({', '.join(details)})" if details else ""
            lines.append(f"  {marker}{evt['tool']}{detail_str}")
        lines.append("")
    return lines


@_register_formatter(lambda d: "journey" in d and "message_count" in d)
def _format_user_journey(data: dict) -> list[str]:
    lines = [
        f"User Journey (last {data['hours']} hours)",
        f"Messages: {data['message_count']}",
    ]
    if data.get("projects_visited"):
        lines.append(f"Projects: {len(data['projects_visited'])}")
        lines.append(f"Project switches: {data.get('project_switches', 0)}")
    lines.append("")

    for event in data.get("journey", [])[:20]:
        ts = event.get("timestamp", "")[:16] if event.get("timestamp") else "unknown"
        msg = event.get("message", "")[:60]
        project = event.get("project", "")
        if project:
            lines.append(f"  [{ts}] ({project}) {msg}")
        else:
            lines.append(f"  [{ts}] {msg}")
    if len(data.get("journey", [])) > 20:
        lines.append(f"  ... and {len(data['journey']) - 20} more")
    return lines


@_register_formatter(lambda d: "parallel_periods" in d and "parallel_period_count" in d)
def _format_parallel_sessions(data: dict) -> list[str]:
    lines = [
        f"Parallel Sessions (last {data['hours']} hours)",
        f"Total sessions: {data['total_sessions']}",
        f"Parallel periods: {data['parallel_period_count']}",
        "",
    ]
    for period in data.get("parallel_periods", [])[:10]:
        sessions = period.get("sessions", [])
        session_info = " & ".join(f"{s.get('project', 'unknown')}" for s in sessions)
        lines.append(f"  {period['duration_minutes']}min: {session_info}")
    return lines


@_register_formatter(lambda d: "related_sessions" in d and "method" in d)
def _format_related_sessions(data: dict) -> list[str]:
    lines = [
        f"Related Sessions (method: {data['method']})",
        f"Session: {data['session_id']}",
        f"Related: {data['related_count']}",
        "",
    ]
    for rel in data.get("related_sessions", [])[:10]:
        details = []
        if rel.get("shared_files"):
            details.append(f"{rel['shared_files']} files")
        if rel.get("shared_commands"):
            details.append(f"{rel['shared_commands']} cmds")
        if rel.get("event_count"):
            details.append(f"{rel['event_count']} events")
        detail_str = f" ({', '.join(details)})" if details else ""
        lines.append(f"  {rel['session_id'][:16]} - {rel.get('project', 'unknown')}{detail_str}")
    return lines


@_register_formatter(lambda d: "files_found" in d)
def _format_ingest(data: dict) -> list[str]:
    return [
        f"Files found: {data['files_found']}",
        f"Files processed: {data['files_processed']}",
        f"Events added: {data['events_added']}",
        f"Sessions updated: {data.get('sessions_updated', 0)}",
    ]


@_register_formatter(lambda d: "event_count" in d)
def _format_status(data: dict) -> list[str]:
    lines = [
        f"Database: {data.get('db_path', 'unknown')}",
        f"Size: {data.get('db_size_bytes', 0) / 1024:.1f} KB",
        f"Events: {data['event_count']}",
        f"Sessions: {data['session_count']}",
        f"Patterns: {data.get('pattern_count', 0)}",
    ]
    if data.get("earliest_event"):
        lines.append(f"Date range: {data['earliest_event'][:10]} to {data['latest_event'][:10]}")
    return lines


@_register_formatter(lambda d: "total_errors" in d and "rework_patterns" in d)
def _format_failures(data: dict) -> list[str]:
    lines = [
        f"Failure Analysis (last {data['days']} days)",
        f"Total errors: {data['total_errors']}",
        f"Sessions with errors: {data['sessions_with_errors']}",
        f"Avg errors/session: {data['avg_errors_per_session']}",
        "",
    ]
    if data.get("errors_by_tool"):
        lines.append("Errors by tool:")
        for item in data["errors_by_tool"][:5]:
            lines.append(f"  {item['tool']}: {item['errors']}")
        lines.append("")

    rework = data.get("rework_patterns", {})
    if rework.get("instances_detected", 0) > 0:
        lines.append(f"Rework patterns: {rework['instances_detected']} instances")
        for ex in rework.get("examples", [])[:3]:
            lines.append(f"  {ex['file']}: {ex['edit_count']} edits in {ex['duration_minutes']}min")
    return lines


@_register_formatter(lambda d: "category_distribution" in d and "sessions" in d)
def _format_classify_sessions(data: dict) -> list[str]:
    lines = [
        f"Session Classification (last {data['days']} days)",
        f"Sessions analyzed: {data['session_count']}",
        "",
        "Category distribution:",
    ]
    for cat, count in data.get("category_distribution", {}).items():
        if count > 0:
            lines.append(f"  {cat}: {count}")
    lines.append("")

    lines.append("Recent sessions:")
    for sess in data.get("sessions", [])[:10]:
        lines.append(f"  {sess['session_id'][:16]} - {sess['category']} ({sess['confidence']:.0%})")
    return lines


@_register_formatter(lambda d: "recent_messages" in d and "modified_files" in d)
def _format_handoff_context(data: dict) -> list[str]:
    if "error" in data:
        return [f"Error: {data['error']}"]

    lines = [
        f"Handoff context for session {data.get('session_id', 'unknown')[:16]}...",
        f"Project: {data.get('project', 'unknown')}",
        f"Duration: {data.get('duration_minutes', 0)} minutes ({data.get('total_events', 0)} events)",
        "",
    ]

    if data.get("recent_messages"):
        lines.append("Recent messages:")
        for msg in data["recent_messages"][:5]:
            text = msg.get("message", "")[:80] if msg.get("message") else "(empty)"
            lines.append(f"  - {text}...")
        lines.append("")

    if data.get("modified_files"):
        lines.append("Modified files:")
        for f in data["modified_files"][:5]:
            lines.append(f"  {f['file']} ({f['touches']} edits)")
        lines.append("")

    if data.get("recent_commands"):
        lines.append("Commands run:")
        for c in data["recent_commands"][:5]:
            lines.append(f"  {c['command']}: {c['count']}x")
        lines.append("")

    if data.get("tool_summary"):
        lines.append("Tool usage:")
        for t in data["tool_summary"][:5]:
            lines.append(f"  {t['tool']}: {t['count']}")

    return lines


@_register_formatter(lambda d: "metrics" in d and "tool_changes" in d)
def _format_trends(data: dict) -> list[str]:
    def format_metric(name: str, metric: dict) -> str:
        arrow = {"up": "↑", "down": "↓", "unchanged": "→"}[metric["direction"]]
        return f"  {name}: {metric['current']} {arrow} ({metric['change_pct']:+.1f}%)"

    lines = [
        f"Trend Analysis (last {data['days']} days vs {data['compare_to']})",
        "",
        "Metrics:",
    ]

    metrics = data.get("metrics", {})
    for name, metric in metrics.items():
        if isinstance(metric, dict) and "direction" in metric:
            lines.append(format_metric(name, metric))

    lines.append("")
    lines.append("Tool changes:")
    for tc in data.get("tool_changes", [])[:5]:
        arrow = {"up": "↑", "down": "↓", "unchanged": "→"}[tc["direction"]]
        lines.append(f"  {tc['tool']}: {tc['current']} {arrow} ({tc['change_pct']:+.1f}%)")

    return lines


def format_output(data: dict, json_output: bool = False) -> str:
    """Format output as JSON or human-readable."""
    if json_output:
        return json.dumps(data, indent=2, default=str)

    # Find matching formatter from registry
    for predicate, formatter in _FORMATTERS:
        if predicate(data):
            return "\n".join(formatter(data))

    # Fallback to JSON if no formatter matches
    return json.dumps(data, indent=2, default=str)


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
    result = do_get_insights(
        storage,
        refresh=args.refresh,
        days=args.days,
        include_advanced=not args.basic,
    )
    print(format_output(result, args.json))


def cmd_sample_sequences(args):
    """Show sampled sequence instances."""
    storage = SQLiteStorage()
    result = do_sample_sequences(
        storage,
        pattern=args.pattern,
        count=args.count,
        context_events=args.context,
        days=args.days,
    )
    print(format_output(result, args.json))


def cmd_journey(args):
    """Show user journey across sessions."""
    storage = SQLiteStorage()
    result = get_user_journey(
        storage,
        hours=args.hours,
        include_projects=not args.no_projects,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_parallel(args):
    """Show parallel session detection."""
    storage = SQLiteStorage()
    result = detect_parallel_sessions(
        storage,
        hours=args.hours,
        min_overlap_minutes=args.min_overlap,
    )
    print(format_output(result, args.json))


def cmd_related(args):
    """Show related sessions."""
    storage = SQLiteStorage()
    result = find_related_sessions(
        storage,
        session_id=args.session_id,
        method=args.method,
        days=args.days,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_failures(args):
    """Show failure analysis."""
    storage = SQLiteStorage()
    result = do_analyze_failures(
        storage,
        days=args.days,
        rework_window_minutes=args.rework_window,
    )
    print(format_output(result, args.json))


def cmd_classify(args):
    """Show session classifications."""
    storage = SQLiteStorage()
    result = do_classify_sessions(
        storage,
        days=args.days,
        project=args.project,
    )
    print(format_output(result, args.json))


def cmd_handoff(args):
    """Show handoff context for a session."""
    storage = SQLiteStorage()
    result = do_get_handoff_context(
        storage,
        session_id=args.session_id,
        hours=args.hours,
        message_limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_trends(args):
    """Show trend analysis."""
    storage = SQLiteStorage()
    result = do_analyze_trends(
        storage,
        days=args.days,
        compare_to=args.compare_to,
    )
    print(format_output(result, args.json))


def cmd_git_ingest(args):
    """Ingest git history."""
    storage = SQLiteStorage()
    result = do_ingest_git(
        storage,
        repo_path=args.repo_path,
        days=args.days,
        project_path=args.project,
    )
    print(format_output(result, args.json))


def cmd_git_correlate(args):
    """Correlate git commits with sessions."""
    storage = SQLiteStorage()
    result = do_correlate_git(
        storage,
        days=args.days,
    )
    print(format_output(result, args.json))


def main():
    """CLI entry point."""
    epilog = """
Examples:
  session-analytics-cli status              # Database stats
  session-analytics-cli frequency --days 30 # Tool usage last 30 days
  session-analytics-cli commands --prefix git  # Git commands only
  session-analytics-cli tokens --by model   # Token usage by model
  session-analytics-cli permissions         # Commands needing settings.json

All commands support --json for machine-readable output.
Data location: ~/.claude/contrib/analytics/data.db
"""
    parser = argparse.ArgumentParser(
        description="Claude Session Analytics CLI - Analyze your Claude Code usage patterns",
        prog="session-analytics-cli",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    sub.add_argument(
        "--basic", action="store_true", help="Exclude advanced analytics (trends, failures, etc.)"
    )
    sub.set_defaults(func=cmd_insights)

    # sample-sequences
    sub = subparsers.add_parser(
        "sample-sequences", help="Show sampled instances of a sequence pattern"
    )
    sub.add_argument("pattern", help="Pattern to sample (e.g., 'Read → Edit' or 'Read,Edit')")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--count", type=int, default=5, help="Number of samples (default: 5)")
    sub.add_argument(
        "--context", type=int, default=2, help="Context events before/after (default: 2)"
    )
    sub.set_defaults(func=cmd_sample_sequences)

    # journey
    sub = subparsers.add_parser("journey", help="Show user journey across sessions")
    sub.add_argument("--hours", type=int, default=24, help="Hours to look back (default: 24)")
    sub.add_argument("--limit", type=int, default=100, help="Max messages (default: 100)")
    sub.add_argument("--no-projects", action="store_true", help="Exclude project info")
    sub.set_defaults(func=cmd_journey)

    # parallel
    sub = subparsers.add_parser("parallel", help="Detect parallel sessions")
    sub.add_argument("--hours", type=int, default=24, help="Hours to look back (default: 24)")
    sub.add_argument("--min-overlap", type=int, default=5, help="Min overlap minutes (default: 5)")
    sub.set_defaults(func=cmd_parallel)

    # related
    sub = subparsers.add_parser("related", help="Find related sessions")
    sub.add_argument("session_id", help="Session ID to find related sessions for")
    sub.add_argument(
        "--method",
        choices=["files", "commands", "temporal"],
        default="files",
        help="Relation method (default: files)",
    )
    sub.add_argument("--days", type=int, default=7, help="Days to search (default: 7)")
    sub.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    sub.set_defaults(func=cmd_related)

    # failures
    sub = subparsers.add_parser("failures", help="Analyze failure patterns and rework")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument(
        "--rework-window", type=int, default=10, help="Rework window in minutes (default: 10)"
    )
    sub.set_defaults(func=cmd_failures)

    # classify
    sub = subparsers.add_parser("classify", help="Classify sessions by activity type")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project filter")
    sub.set_defaults(func=cmd_classify)

    # handoff
    sub = subparsers.add_parser("handoff", help="Get handoff context for a session")
    sub.add_argument("--session-id", help="Specific session ID (default: most recent)")
    sub.add_argument("--hours", type=int, default=4, help="Hours to look back (default: 4)")
    sub.add_argument("--limit", type=int, default=10, help="Max messages (default: 10)")
    sub.set_defaults(func=cmd_handoff)

    # trends
    sub = subparsers.add_parser("trends", help="Analyze trends over time")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument(
        "--compare-to",
        choices=["previous", "same_last_month"],
        default="previous",
        help="Comparison period (default: previous)",
    )
    sub.set_defaults(func=cmd_trends)

    # git-ingest
    sub = subparsers.add_parser("git-ingest", help="Ingest git commit history")
    sub.add_argument("--repo-path", help="Path to git repository (default: current dir)")
    sub.add_argument("--days", type=int, default=7, help="Days of history (default: 7)")
    sub.add_argument("--project", help="Project path to associate commits with")
    sub.set_defaults(func=cmd_git_ingest)

    # git-correlate
    sub = subparsers.add_parser("git-correlate", help="Correlate commits with sessions")
    sub.add_argument("--days", type=int, default=7, help="Days to correlate (default: 7)")
    sub.set_defaults(func=cmd_git_correlate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

"""Command-line interface for session analytics."""

import argparse
import json
import sqlite3
import statistics
import time

from session_analytics.ingest import (
    correlate_git_with_sessions,
    ingest_git_history,
    ingest_git_history_all_projects,
    ingest_logs,
)
from session_analytics.patterns import (
    analyze_failures,
    analyze_trends,
    compute_permission_gaps,
    compute_sequence_patterns,
    get_insights,
    get_session_signals,
    sample_sequences,
)
from session_analytics.queries import (
    analyze_pre_compaction_patterns,
    classify_sessions,
    detect_parallel_sessions,
    find_related_sessions,
    get_compaction_events,
    get_handoff_context,
    get_large_tool_results,
    get_pre_compaction_events,
    get_session_efficiency,
    get_user_journey,
    query_agent_activity,
    query_bus_events,
    query_commands,
    query_error_details,
    query_file_activity,
    query_languages,
    query_mcp_usage,
    query_projects,
    query_sessions,
    query_tokens,
    query_tool_frequency,
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
    lines = [
        "Which tools you use most (Read, Edit, Bash, etc.)",
        "",
        f"Total tool calls: {data['total_tool_calls']}",
        "",
        "Tool frequency:",
    ]
    for tool in data.get("tools", []):
        lines.append(f"  {tool['tool']}: {tool['count']}")
        # Show breakdown if present (for Skill, Task, Bash)
        for item in tool.get("breakdown", []):
            lines.append(f"    └ {item['name']}: {item['count']}")
    return lines


@_register_formatter(lambda d: "total_commands" in d)
def _format_commands(data: dict) -> list[str]:
    lines = [
        "Bash commands by frequency (gh, git, cargo, etc.)",
        "",
        f"Total commands: {data['total_commands']}",
        "",
        "Command frequency:",
    ]
    for cmd in data.get("commands", []):
        lines.append(f"  {cmd['command']}: {cmd['count']}")
    return lines


@_register_formatter(lambda d: "session_count" in d and "total_entries" in d)
def _format_sessions(data: dict) -> list[str]:
    input_tokens = data.get("total_input_tokens", 0)
    output_tokens = data.get("total_output_tokens", 0)
    total_tokens = input_tokens + output_tokens
    return [
        "Summary of Claude Code sessions and token usage",
        "",
        f"Sessions: {data['session_count']}",
        f"Total entries: {data['total_entries']}",
        f"Tokens: {input_tokens:,} in / {output_tokens:,} out ({total_tokens:,} total)",
    ]


@_register_formatter(lambda d: "breakdown" in d)
def _format_tokens(data: dict) -> list[str]:
    lines = [
        f"Token consumption grouped by {data.get('group_by', 'unknown')}",
        "",
        f"Total input: {data['total_input_tokens']:,}",
        f"Total output: {data['total_output_tokens']:,}",
        "",
    ]
    for item in data["breakdown"]:
        key = item.get("day") or item.get("session_id") or item.get("model")
        lines.append(f"  {key}: {item['input_tokens']} in / {item['output_tokens']} out")
    return lines


@_register_formatter(lambda d: "summary" in d and "total_tools" in d.get("summary", {}))
def _format_insights(data: dict) -> list[str]:
    return [
        "Pre-computed patterns for /improve-workflow",
        "",
        f"Tools tracked: {data['summary']['total_tools']}",
        f"Commands tracked: {data['summary']['total_commands']}",
        f"Sequences found: {data['summary']['total_sequences']}",
        f"Permission gaps: {data['summary']['permission_gaps_found']}",
    ]


@_register_formatter(lambda d: "sequences" in d)
def _format_sequences(data: dict) -> list[str]:
    if data.get("expanded"):
        desc = "Detailed sequences (Bash→commands, Skill→skills, Task→agents)"
    else:
        desc = "Tool chains showing workflow patterns (Read → Edit, etc.)"

    total = data.get("total_patterns", len(data.get("sequences", [])))
    shown = len(data.get("sequences", []))

    lines = [desc, ""]

    # Show truncation info if results are limited
    if total > shown:
        lines.append(f"Showing {shown} of {total} total patterns")
        lines.append("")

    lines.append("Sequences:")
    for seq in data.get("sequences", []):
        lines.append(f"  {seq['pattern']}: {seq['count']}")
    return lines


@_register_formatter(lambda d: "gaps" in d)
def _format_gaps(data: dict) -> list[str]:
    lines = [
        "Commands used frequently that could be auto-approved in settings.json",
        "",
        "Permission gaps:",
    ]
    for gap in data.get("gaps", []):
        lines.append(f"  {gap['command']}: {gap['count']} uses -> {gap['suggestion']}")
    return lines


@_register_formatter(lambda d: "files" in d and "file_count" in d)
def _format_file_activity(data: dict) -> list[str]:
    collapsed = " (worktrees collapsed)" if data.get("collapse_worktrees") else ""
    lines = [
        f"Files with most activity (reads, edits, writes){collapsed}",
        "",
        f"Files touched: {data['file_count']}",
        "",
    ]
    for f in data.get("files", []):
        lines.append(f"  {f['file']}")
        lines.append(
            f"    total: {f['total']}  read: {f['reads']}  edit: {f['edits']}  write: {f['writes']}"
        )
    return lines


@_register_formatter(lambda d: "languages" in d and "total_operations" in d)
def _format_languages(data: dict) -> list[str]:
    lines = [
        "Language distribution from file extensions",
        "",
        f"Total file operations: {data['total_operations']:,}",
        "",
        f"{'LANGUAGE':<20} {'COUNT':>8} {'%':>6}",
    ]
    for lang in data.get("languages", []):
        lines.append(f"{lang['language']:<20} {lang['count']:>8} {lang['percent']:>5.1f}%")
    return lines


@_register_formatter(lambda d: "projects" in d and "project_count" in d)
def _format_projects(data: dict) -> list[str]:
    lines = [
        "Activity across projects",
        "",
        f"Projects: {data['project_count']}",
        "",
        f"{'PROJECT':<30} {'EVENTS':>8} {'SESSIONS':>8}",
    ]
    for proj in data.get("projects", []):
        lines.append(f"{proj['name']:<30} {proj['events']:>8} {proj['sessions']:>8}")
    return lines


@_register_formatter(lambda d: "servers" in d and "total_mcp_calls" in d)
def _format_mcp_usage(data: dict) -> list[str]:
    lines = [
        "MCP server and tool usage",
        "",
        f"Total MCP calls: {data['total_mcp_calls']:,}",
        "",
    ]
    for server in data.get("servers", []):
        lines.append(f"{server['server']}: {server['total']} calls")
        for tool in server.get("tools", [])[:5]:
            lines.append(f"  └ {tool['tool']}: {tool['count']}")
        if len(server.get("tools", [])) > 5:
            lines.append(f"  └ ... and {len(server['tools']) - 5} more")
    return lines


@_register_formatter(lambda d: "agents" in d and "main_session" in d)
def _format_agent_activity(data: dict) -> list[str]:
    """Format agent activity breakdown.

    RFC #41: Shows activity by Task subagent vs main session.
    """
    summary = data.get("summary", {})
    lines = [
        "Agent activity breakdown (Task subagent vs main session)",
        "",
        f"Agents: {summary.get('agent_count', 0)}",
        f"Agent tokens: {summary.get('total_agent_tokens', 0):,} ({summary.get('agent_token_percentage', 0)}%)",
        f"Main tokens: {summary.get('total_main_tokens', 0):,}",
        "",
    ]

    # Main session stats
    main = data.get("main_session")
    if main:
        lines.append("Main Session:")
        lines.append(f"  Events: {main['event_count']:,}")
        lines.append(f"  Tokens: {main['input_tokens']:,} in / {main['output_tokens']:,} out")
        lines.append("")

    # Per-agent stats
    for agent in data.get("agents", []):
        lines.append(f"Agent {agent['agent_id']}:")
        lines.append(f"  Events: {agent['event_count']:,} ({agent['tool_use_count']:,} tool uses)")
        lines.append(f"  Tokens: {agent['input_tokens']:,} in / {agent['output_tokens']:,} out")
        if agent.get("top_tools"):
            tools_str = ", ".join(f"{t['tool']}:{t['count']}" for t in agent["top_tools"][:3])
            lines.append(f"  Top tools: {tools_str}")
        lines.append("")

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
    entry_types = data.get("entry_types", ["user", "assistant"])
    lines = [
        f"Session Messages (last {data['hours']} hours)",
        f"Messages: {data['message_count']}",
        f"Types: {', '.join(entry_types)}",
    ]
    if data.get("projects_visited"):
        lines.append(f"Projects: {len(data['projects_visited'])}")
        lines.append(f"Project switches: {data.get('project_switches', 0)}")
    lines.append("")

    for event in data.get("journey", []):
        ts = event.get("timestamp", "")[:16] if event.get("timestamp") else "unknown"
        msg = event.get("message", "") if event.get("message") else ""
        msg_type = event.get("type", "user")
        project = event.get("project", "")
        type_prefix = f"[{msg_type[0].upper()}]"  # [U], [A], [T], [S]
        if project:
            lines.append(f"  [{ts}] {type_prefix} ({project}) {msg}")
        else:
            lines.append(f"  [{ts}] {type_prefix} {msg}")
    return lines


@_register_formatter(lambda d: "query" in d and "messages" in d and "count" in d)
def _format_search_results(data: dict) -> list[str]:
    entry_types = data.get("entry_types")
    lines = [
        f"Search: {data['query']}",
        f"Results: {data['count']}",
    ]
    if entry_types:
        lines.append(f"Types: {', '.join(entry_types)}")
    lines.append("")
    for msg in data.get("messages", []):
        ts = msg.get("timestamp", "")[:16] if msg.get("timestamp") else "unknown"
        text = msg.get("message", "") if msg.get("message") else ""
        msg_type = msg.get("type", "user")
        project = msg.get("project", "")
        type_prefix = f"[{msg_type[0].upper()}]"  # [U], [A], [T], [S]
        if project:
            lines.append(f"  [{ts}] {type_prefix} ({project}) {text}")
        else:
            lines.append(f"  [{ts}] {type_prefix} {text}")
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


@_register_formatter(lambda d: "event_count" in d and "db_path" in d)
def _format_status(data: dict) -> list[str]:
    lines = [
        "Analytics database status and ingestion info",
        "",
        f"Database: {data.get('db_path', 'unknown')}",
        f"Size: {data.get('db_size_bytes', 0) / 1024:.1f} KB",
        f"Events: {data['event_count']:,}",
        f"Sessions: {data['session_count']:,}",
        f"Patterns: {data.get('pattern_count', 0):,}",
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


@_register_formatter(lambda d: "errors_by_tool" in d and "tool_totals" in d)
def _format_error_details(data: dict) -> list[str]:
    lines = [
        f"Error Details (last {data['days']} days)",
        f"Total errors: {data['total_errors']}",
    ]
    if data.get("tool_filter"):
        lines.append(f"Filter: {data['tool_filter']}")
    lines.append("")

    errors_by_tool = data.get("errors_by_tool", {})
    tool_totals = data.get("tool_totals", {})

    if not errors_by_tool:
        lines.append("No errors found.")
        return lines

    for tool_name in sorted(errors_by_tool.keys(), key=lambda t: -tool_totals.get(t, 0)):
        total = tool_totals.get(tool_name, 0)
        lines.append(f"{tool_name} ({total} errors):")
        for err in errors_by_tool[tool_name][:10]:
            param = err.get("param_value") or "(unknown)"
            count = err.get("error_count", 0)
            suffix = ""
            if err.get("search_path"):
                suffix = f" in {err['search_path']}"
            elif err.get("project"):
                # Extract repo name from project path
                proj = err["project"]
                if proj:
                    proj = proj.split("-")[-1] if "-" in proj else proj
                    suffix = f" ({proj})"
            lines.append(f"  {param!r}: {count} errors{suffix}")
        lines.append("")

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


@_register_formatter(
    lambda d: "sessions_analyzed" in d
    and "sessions" in d
    and (len(d.get("sessions", [])) == 0 or "error_count" in d.get("sessions", [{}])[0])
)
def _format_signals(data: dict) -> list[str]:
    """Format raw session signals for display."""
    lines = [
        "Session metrics: events, duration, errors, rework, and PR activity",
        "",
        f"Sessions analyzed: {data['sessions_analyzed']} (last {data['days']} days)",
        "",
    ]
    for sess in data.get("sessions", []):
        commit_info = f", {sess['commit_count']} commits" if sess.get("commit_count") else ""
        error_info = f", {sess['error_rate']:.0%} errors" if sess.get("error_rate", 0) > 0 else ""
        rework = " [rework]" if sess.get("has_rework") else ""
        pr = " [PR]" if sess.get("has_pr_activity") else ""
        lines.append(
            f"  {sess['session_id']} - {sess['event_count']} events, "
            f"{sess['duration_minutes']:.0f}m{commit_info}{error_info}{rework}{pr}"
        )
    return lines


@_register_formatter(lambda d: "commits" in d and "total_commits" in d)
def _format_session_commits(data: dict) -> list[str]:
    lines = [
        f"Session Commits (last {data['days']} days)",
        f"Total commits: {data['total_commits']}",
        "",
    ]
    if data.get("session_id"):
        lines.insert(1, f"Session: {data['session_id']}")

    for commit in data.get("commits", []):
        sha = commit.get("sha", "")
        time_to = commit.get("time_to_commit_seconds", 0)
        first = " (first)" if commit.get("is_first_commit") else ""
        session = commit.get("session_id", "") if not data.get("session_id") else ""
        if session:
            lines.append(f"  {sha} - {time_to}s{first} [{session}]")
        else:
            lines.append(f"  {sha} - {time_to}s{first}")
    return lines


@_register_formatter(lambda d: "benchmarks" in d and "total_tools" in d)
def _format_benchmark(data: dict) -> list[str]:
    """Format benchmark results as a table."""
    lines = [
        f"Benchmark Results ({data['iterations']} iterations per tool)",
        f"Total tools: {data['total_tools']}",
        f"Slow tools (>5s): {data['slow_tools']}",
        "",
        f"{'TOOL':<35} {'MEDIAN':>10} {'P95':>10} {'P99':>10} {'STATUS':>10}",
        "-" * 77,
    ]

    for bench in data["benchmarks"]:
        if bench.get("error"):
            err_msg = bench["error"][:25] if len(bench.get("error", "")) > 25 else bench["error"]
            lines.append(f"{bench['tool']:<35} {'ERROR':<10} {'---':>10} {'---':>10} {err_msg}")
            continue

        median = bench["median"]
        p95 = bench["p95"]
        p99 = bench["p99"]

        status = "SLOW" if median > 5.0 else "OK"

        lines.append(f"{bench['tool']:<35} {median:>9.3f}s {p95:>9.3f}s {p99:>9.3f}s {status:>10}")

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


# Issue #69: Compaction and efficiency formatters


@_register_formatter(lambda d: "compaction_count" in d and "compactions" in d)
def _format_compactions(data: dict) -> list[str]:
    # Count unique sessions
    unique_sessions = len({c["session_id"] for c in data.get("compactions", [])})
    total_count = data.get("total_compaction_count", data["compaction_count"])
    shown_count = data["compaction_count"]

    lines = [
        f"Compaction events (context resets) - last {data.get('days', 7)} days",
        "",
    ]

    # Show truncation info if results are limited
    if total_count > shown_count:
        lines.append(f"Showing {shown_count} of {total_count} total compactions")
    else:
        lines.append(f"Total compactions: {shown_count}")
    lines.append(f"Sessions affected: {unique_sessions}")
    lines.append("")

    if data.get("compactions"):
        lines.append("Recent compactions:")
        for c in data["compactions"][:10]:
            lines.append(f"  {c['timestamp']} - session {c['session_id'][:8]}...")
    return lines


# Issue #81: Aggregate compactions formatter
@_register_formatter(
    lambda d: d.get("aggregate") is True and "sessions" in d and "total_compaction_count" in d
)
def _format_compactions_aggregate(data: dict) -> list[str]:
    total_compactions = data.get("total_compaction_count", 0)
    total_sessions = data.get("total_sessions_with_compactions", 0)
    shown_sessions = data.get("session_count", 0)

    lines = [
        f"Compaction summary by session - last {data.get('days', 7)} days",
        "",
        f"Total compactions: {total_compactions}",
        f"Sessions with compactions: {total_sessions}",
    ]

    if total_sessions > shown_sessions:
        lines.append(f"Showing {shown_sessions} of {total_sessions} sessions")
    lines.append("")

    if data.get("sessions"):
        lines.append("Sessions ranked by compaction count:")
        for s in data["sessions"][:15]:
            lines.append(
                f"  {s['session_id'][:8]}... - {s['compaction_count']} compactions "
                f"({s['total_summary_kb']:.0f}KB summaries)"
            )
    return lines


# Issue #81: Pre-compaction patterns formatter
@_register_formatter(lambda d: "compactions_analyzed" in d and "patterns" in d)
def _format_pre_compaction_patterns(data: dict) -> list[str]:
    lines = [
        f"Pre-compaction pattern analysis - last {data.get('days', 7)} days",
        "",
        f"Compactions analyzed: {data.get('compactions_analyzed', 0)}",
        f"Events analyzed before each: {data.get('events_before', 50)}",
        "",
    ]

    patterns = data.get("patterns", {})
    if patterns:
        lines.append("Detected patterns:")
        lines.append(f"  Avg consecutive reads: {patterns.get('avg_consecutive_reads', 0):.1f}")
        lines.append(f"  Avg files re-read: {patterns.get('avg_files_read_multiple_times', 0):.1f}")
        lines.append(f"  Avg large results (>10KB): {patterns.get('avg_large_results', 0):.1f}")
        lines.append("")

        if patterns.get("tool_distribution"):
            lines.append("Tool distribution before compactions:")
            for t in patterns["tool_distribution"][:5]:
                lines.append(f"  {t['tool']}: {t['count']}")
            lines.append("")

        if patterns.get("top_reread_files"):
            lines.append("Most frequently re-read files:")
            for f in patterns["top_reread_files"][:5]:
                lines.append(f"  {f['file']}: {f['read_count']}x")
            lines.append("")

    recommendations = data.get("recommendations", [])
    if recommendations:
        lines.append("Recommendations:")
        for r in recommendations:
            lines.append(f"  - {r}")
    elif data.get("compactions_analyzed", 0) > 0:
        lines.append("No antipatterns detected - context efficiency looks healthy.")

    return lines


@_register_formatter(lambda d: "compaction_timestamp" in d and "events" in d and "event_count" in d)
def _format_pre_compaction(data: dict) -> list[str]:
    lines = [
        f"Events before compaction at {data['compaction_timestamp']}",
        f"Session: {data['session_id']}",
        "",
        f"Events found: {data['event_count']}",
        "",
    ]
    if data.get("events"):
        lines.append("Events (most recent first):")
        for e in data["events"]:
            tool = e.get("tool") or e.get("type", "unknown")
            size_info = ""
            if e.get("size_bytes"):
                size_kb = e["size_bytes"] / 1024
                size_info = f" ({size_kb:.1f}KB)"
            identifier = e.get("file") or e.get("command") or ""
            if identifier:
                identifier = f" - {identifier[:40]}"
            error_mark = " [ERR]" if e.get("error") else ""
            lines.append(f"  {e['timestamp']} {tool}{size_info}{identifier}{error_mark}")
    return lines


@_register_formatter(lambda d: "large_results" in d and "result_count" in d)
def _format_large_results(data: dict) -> list[str]:
    # Calculate total from tool_breakdown
    total_mb = sum(t.get("total_mb", 0) for t in data.get("tool_breakdown", []))
    lines = [
        f"Large tool results (>= {data.get('min_size_kb', 10)}KB) - last {data.get('days', 7)} days",
        "",
        f"Total large results: {data['result_count']}",
        f"Total size: {total_mb:.2f}MB",
        "",
    ]
    if data.get("large_results"):
        lines.append("Top results by size:")
        for r in data["large_results"][:10]:
            identifier = r.get("file") or r.get("command") or "N/A"
            lines.append(f"  {r['tool']}: {r['size_kb']:.1f}KB - {identifier[:50]}")
    return lines


@_register_formatter(
    lambda d: "sessions" in d
    and "session_count" in d
    and any("efficiency_signals" in s for s in d.get("sessions", []))
)
def _format_efficiency(data: dict) -> list[str]:
    lines = [
        f"Session efficiency - last {data.get('days', 7)} days",
        "",
        f"Sessions analyzed: {data.get('session_count', 0)}",
        "",
        "Sessions by context usage:",
    ]
    for s in data.get("sessions", [])[:10]:
        signals = s.get("efficiency_signals", {})
        total_mb = signals.get("total_result_mb", 0)
        compactions = signals.get("compaction_count", 0)
        burn_rate = signals.get("burn_rate_tokens_per_event", 0)
        read_edit = signals.get("read_to_edit_ratio", 0)
        multi_read = signals.get("files_read_multiple_times", 0)
        lines.append(
            f"  {s['session_id'][:8]}...: {total_mb:.2f}MB, {compactions} compactions, "
            f"{burn_rate:.0f} tok/ev, R/E:{read_edit:.1f}, multi-read:{multi_read}"
        )
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
    expand = not getattr(args, "no_expand", False)
    result = query_tool_frequency(storage, days=args.days, project=args.project, expand=expand)
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
    sequence_patterns = compute_sequence_patterns(
        storage,
        days=args.days,
        sequence_length=args.length,
        min_count=args.min_count,
        expand=args.expand,
    )
    # Apply limit to match MCP behavior
    limit = getattr(args, "limit", 50)
    limited_patterns = sequence_patterns[:limit] if limit > 0 else sequence_patterns
    result = {
        "days": args.days,
        "expanded": args.expand,
        "limit": limit,
        "total_patterns": len(sequence_patterns),
        "sequences": [{"pattern": p.pattern_key, "count": p.count} for p in limited_patterns],
    }
    print(format_output(result, args.json))


def cmd_permissions(args):
    """Show permission gaps."""
    storage = SQLiteStorage()
    patterns = compute_permission_gaps(storage, days=args.days, threshold=args.min_count)
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


def cmd_file_activity(args):
    """Show file activity."""
    storage = SQLiteStorage()
    result = query_file_activity(
        storage,
        days=args.days,
        project=args.project,
        limit=args.limit,
        collapse_worktrees=args.collapse_worktrees,
    )
    print(format_output(result, args.json))


def cmd_languages(args):
    """Show language distribution."""
    storage = SQLiteStorage()
    result = query_languages(storage, days=args.days, project=args.project)
    print(format_output(result, args.json))


def cmd_projects(args):
    """Show project activity."""
    storage = SQLiteStorage()
    result = query_projects(storage, days=args.days)
    print(format_output(result, args.json))


def cmd_mcp_usage(args):
    """Show MCP server/tool usage."""
    storage = SQLiteStorage()
    result = query_mcp_usage(storage, days=args.days, project=args.project)
    print(format_output(result, args.json))


def cmd_agents(args):
    """Show agent activity breakdown.

    RFC #41: Shows activity by Task subagent vs main session.
    """
    storage = SQLiteStorage()
    result = query_agent_activity(storage, days=args.days, project=args.project)
    print(format_output(result, args.json))


def cmd_bus_events(args):
    """Show event-bus events for cross-session insights.

    RFC #54: Shows events from event-bus (gotchas, patterns, help, etc.).
    """
    from session_analytics.bus_ingest import ingest_bus_events

    storage = SQLiteStorage()
    # Ingest latest events before querying
    ingest_bus_events(storage, days=args.days)
    result = query_bus_events(
        storage,
        days=args.days,
        event_type=args.event_type,
        repo=args.repo,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_insights(args):
    """Show insights for /improve-workflow."""
    storage = SQLiteStorage()
    result = get_insights(
        storage,
        refresh=args.refresh,
        days=args.days,
        include_advanced=not args.basic,
    )
    print(format_output(result, args.json))


def cmd_sample_sequences(args):
    """Show sampled sequence instances."""
    storage = SQLiteStorage()
    result = sample_sequences(
        storage,
        pattern=args.pattern,
        count=args.limit,
        context_events=args.context,
        days=args.days,
        expand=args.expand,
    )
    print(format_output(result, args.json))


def cmd_journey(args):
    """Show messages across sessions."""
    storage = SQLiteStorage()
    hours = int(args.days * 24)
    entry_types = getattr(args, "entry_types", None)
    if entry_types:
        entry_types = [t.strip() for t in entry_types.split(",")]
    max_length = getattr(args, "max_length", 500)
    result = get_user_journey(
        storage,
        hours=hours,
        include_projects=not args.no_projects,
        session_id=getattr(args, "session_id", None),
        limit=args.limit,
        entry_types=entry_types,
        max_message_length=max_length,
    )
    print(format_output(result, args.json))


def cmd_search(args):
    """Search messages using full-text search."""
    storage = SQLiteStorage()
    project = getattr(args, "project", None)
    entry_types = getattr(args, "entry_types", None)
    if entry_types:
        entry_types = [t.strip() for t in entry_types.split(",")]
    try:
        results = storage.search_messages(
            args.query, limit=args.limit, project=project, entry_types=entry_types
        )
    except sqlite3.OperationalError as e:
        # Catch FTS5-related errors (syntax, unterminated strings, etc.)
        output = {
            "status": "error",
            "query": args.query,
            "error": f"Invalid FTS5 query syntax: {e}",
        }
        print(format_output(output, args.json))
        return
    output = {
        "query": args.query,
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
    print(format_output(output, args.json))


def cmd_parallel(args):
    """Show parallel session detection."""
    storage = SQLiteStorage()
    hours = int(args.days * 24)
    result = detect_parallel_sessions(
        storage,
        hours=hours,
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
    result = analyze_failures(
        storage,
        days=args.days,
        rework_window_minutes=args.rework_window,
    )
    print(format_output(result, args.json))


def cmd_error_details(args):
    """Show detailed error information with tool parameters."""
    storage = SQLiteStorage()
    result = query_error_details(
        storage,
        days=args.days,
        tool=args.tool,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_classify(args):
    """Show session classifications."""
    storage = SQLiteStorage()
    result = classify_sessions(
        storage,
        days=args.days,
        project=args.project,
    )
    print(format_output(result, args.json))


def cmd_handoff(args):
    """Show handoff context for a session."""
    storage = SQLiteStorage()
    hours = int(args.days * 24)
    result = get_handoff_context(
        storage,
        session_id=args.session_id,
        hours=hours,
        message_limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_trends(args):
    """Show trend analysis."""
    storage = SQLiteStorage()
    result = analyze_trends(
        storage,
        days=args.days,
        compare_to=args.compare_to,
    )
    print(format_output(result, args.json))


def cmd_git_ingest(args):
    """Ingest git history."""
    storage = SQLiteStorage()
    result = ingest_git_history(
        storage,
        repo_path=args.repo_path,
        days=args.days,
        project_path=args.project,
    )
    print(format_output(result, args.json))


def cmd_git_correlate(args):
    """Correlate git commits with sessions."""
    storage = SQLiteStorage()
    result = correlate_git_with_sessions(
        storage,
        days=args.days,
    )
    print(format_output(result, args.json))


def cmd_git_ingest_all(args):
    """Ingest git history from all known projects."""
    storage = SQLiteStorage()
    result = ingest_git_history_all_projects(
        storage,
        days=args.days,
    )
    print(format_output(result, args.json))


def cmd_signals(args):
    """Show raw session signals for LLM interpretation (RFC #26, revised per RFC #17)."""
    storage = SQLiteStorage()
    result = get_session_signals(
        storage,
        days=args.days,
        min_count=args.min_count,
        project=args.project,
    )
    print(format_output(result, args.json))


def cmd_session_commits(args):
    """Show session-commit associations (RFC #26)."""
    storage = SQLiteStorage()
    commits = storage.get_session_commits(args.session_id) if args.session_id else []

    # If no session_id, get all session commits from recent days
    if not args.session_id:
        project_filter = ""
        params = [f"-{args.days} days"]
        if args.project:
            project_filter = "AND s.project_path LIKE ?"
            params.append(f"%{args.project}%")

        rows = storage.execute_query(
            f"""
            SELECT sc.session_id, sc.commit_sha, sc.time_to_commit_seconds,
                   sc.is_first_commit
            FROM session_commits sc
            JOIN sessions s ON s.id = sc.session_id
            WHERE s.first_seen >= datetime('now', ?)
            {project_filter}
            ORDER BY s.first_seen DESC
            """,
            tuple(params),
        )
        commits = [
            {
                "session_id": r["session_id"],
                "sha": r["commit_sha"],
                "time_to_commit_seconds": r["time_to_commit_seconds"],
                "is_first_commit": bool(r["is_first_commit"]),
            }
            for r in rows
        ]

    result = {
        "days": args.days,
        "session_id": args.session_id,
        "project": getattr(args, "project", None),
        "total_commits": len(commits),
        "commits": commits,
    }
    print(format_output(result, args.json))


# Issue #69: Compaction and efficiency commands


def cmd_compactions(args):
    """Show compaction events (context resets)."""
    storage = SQLiteStorage()
    result = get_compaction_events(
        storage,
        days=args.days,
        session_id=getattr(args, "session_id", None),
        limit=getattr(args, "limit", 50),
        aggregate=getattr(args, "aggregate", False),
    )
    print(format_output(result, args.json))


def cmd_pre_compaction(args):
    """Show events before a compaction event."""
    storage = SQLiteStorage()
    result = get_pre_compaction_events(
        storage,
        session_id=args.session_id,
        compaction_timestamp=args.timestamp,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_pre_compaction_patterns(args):
    """Analyze patterns in events leading up to compactions."""
    storage = SQLiteStorage()
    result = analyze_pre_compaction_patterns(
        storage,
        days=args.days,
        events_before=args.events_before,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_large_results(args):
    """Show large tool results that consume context space."""
    storage = SQLiteStorage()
    result = get_large_tool_results(
        storage,
        days=args.days,
        min_size_kb=args.min_size,
        limit=args.limit,
    )
    print(format_output(result, args.json))


def cmd_efficiency(args):
    """Show session context efficiency metrics."""
    storage = SQLiteStorage()
    result = get_session_efficiency(
        storage,
        days=args.days,
        project=getattr(args, "project", None),
        limit=getattr(args, "limit", 50),
    )
    print(format_output(result, args.json))


def _benchmark_tool(tool_name: str, tool_func: callable, iterations: int = 3) -> dict:
    """Benchmark a single MCP tool with multiple iterations.

    Returns dict with tool name, median/p95/p99 times in seconds, or error.
    """
    times = []
    error = None

    for _ in range(iterations):
        try:
            start = time.perf_counter()
            tool_func()
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        except Exception as e:
            error = str(e)
            break

    if not times:
        return {
            "tool": tool_name,
            "error": error,
            "median": None,
            "p95": None,
            "p99": None,
        }

    times.sort()
    n = len(times)
    return {
        "tool": tool_name,
        "median": statistics.median(times),
        "p95": times[min(n - 1, int(n * 0.95))],
        "p99": times[min(n - 1, int(n * 0.99))],
        "error": None,
    }


def cmd_benchmark(args):
    """Benchmark all MCP tools against real database.

    Issue #63: Measures response times for all MCP tools to identify
    slow queries and establish performance baselines.

    Note: When adding new MCP tools, add them to the tool_functions dict below.
    """
    from session_analytics.patterns import (
        analyze_failures as patterns_analyze_failures,
    )
    from session_analytics.patterns import (
        analyze_trends as patterns_analyze_trends,
    )
    from session_analytics.patterns import (
        compute_permission_gaps as patterns_compute_permission_gaps,
    )
    from session_analytics.patterns import (
        compute_sequence_patterns as patterns_compute_sequence_patterns,
    )
    from session_analytics.patterns import (
        get_insights as patterns_get_insights,
    )
    from session_analytics.patterns import (
        get_session_signals as patterns_get_session_signals,
    )
    from session_analytics.patterns import (
        sample_sequences as patterns_sample_sequences,
    )
    from session_analytics.queries import (
        analyze_pre_compaction_patterns as queries_analyze_pre_compaction_patterns,
    )
    from session_analytics.queries import (
        classify_sessions as queries_classify_sessions,
    )
    from session_analytics.queries import (
        detect_parallel_sessions as queries_detect_parallel_sessions,
    )
    from session_analytics.queries import (
        get_compaction_events as queries_get_compaction_events,
    )
    from session_analytics.queries import (
        get_handoff_context as queries_get_handoff_context,
    )
    from session_analytics.queries import (
        get_large_tool_results as queries_get_large_tool_results,
    )
    from session_analytics.queries import (
        get_session_efficiency as queries_get_session_efficiency,
    )
    from session_analytics.queries import (
        get_user_journey as queries_get_user_journey,
    )
    from session_analytics.queries import (
        query_agent_activity as queries_query_agent_activity,
    )
    from session_analytics.queries import (
        query_bus_events as queries_query_bus_events,
    )
    from session_analytics.queries import (
        query_commands as queries_query_commands,
    )
    from session_analytics.queries import (
        query_error_details as queries_query_error_details,
    )
    from session_analytics.queries import (
        query_file_activity as queries_query_file_activity,
    )
    from session_analytics.queries import (
        query_languages as queries_query_languages,
    )
    from session_analytics.queries import (
        query_mcp_usage as queries_query_mcp_usage,
    )
    from session_analytics.queries import (
        query_projects as queries_query_projects,
    )
    from session_analytics.queries import (
        query_sessions as queries_query_sessions,
    )
    from session_analytics.queries import (
        query_timeline as queries_query_timeline,
    )
    from session_analytics.queries import (
        query_tokens as queries_query_tokens,
    )
    from session_analytics.queries import (
        query_tool_frequency as queries_query_tool_frequency,
    )

    storage = SQLiteStorage()
    iterations = args.iterations

    # Define all MCP tools with their default parameters
    # These call the underlying query functions directly (not the MCP wrappers)
    # Skip mutating tools (ingest_*) and tools requiring specific IDs
    tool_functions = {
        "get_status": lambda: storage.get_db_stats(),
        "get_tool_frequency": lambda: queries_query_tool_frequency(storage, days=7),
        "get_session_events": lambda: queries_query_timeline(storage, limit=10),
        "get_command_frequency": lambda: queries_query_commands(storage, days=7),
        "list_sessions": lambda: queries_query_sessions(storage, days=7),
        "get_token_usage": lambda: queries_query_tokens(storage, days=7),
        "get_tool_sequences": lambda: patterns_compute_sequence_patterns(storage, days=7),
        "sample_sequences": lambda: patterns_sample_sequences(
            storage, pattern="Read → Edit", count=2
        ),
        "get_permission_gaps": lambda: patterns_compute_permission_gaps(storage, days=7),
        "get_session_messages": lambda: queries_get_user_journey(
            storage, hours=24, entry_types=["user", "assistant"]
        ),
        "get_session_messages_all": lambda: queries_get_user_journey(
            storage, hours=24, entry_types=["user", "assistant", "tool_result"]
        ),
        "search_messages": lambda: storage.search_messages("test", limit=10),
        "search_messages_filtered": lambda: storage.search_messages(
            "test", limit=10, entry_types=["user", "assistant"]
        ),
        "detect_parallel_sessions": lambda: queries_detect_parallel_sessions(storage, hours=24),
        "get_insights": lambda: patterns_get_insights(storage, refresh=False, days=7),
        "analyze_failures": lambda: patterns_analyze_failures(storage, days=7),
        "get_error_details": lambda: queries_query_error_details(storage, days=7, limit=10),
        "classify_sessions": lambda: queries_classify_sessions(storage, days=7),
        "get_handoff_context": lambda: queries_get_handoff_context(storage, hours=4),
        "analyze_trends": lambda: patterns_analyze_trends(storage, days=7),
        "get_session_signals": lambda: patterns_get_session_signals(storage, days=7),
        "get_session_commits": lambda: storage.get_session_commits(None),
        "get_file_activity": lambda: queries_query_file_activity(storage, days=7),
        "get_languages": lambda: queries_query_languages(storage, days=7),
        "get_projects": lambda: queries_query_projects(storage, days=7),
        "get_mcp_usage": lambda: queries_query_mcp_usage(storage, days=7),
        "get_agent_activity": lambda: queries_query_agent_activity(storage, days=7),
        "get_bus_events": lambda: queries_query_bus_events(storage, days=7, limit=10),
        # Issue #69: Compaction and efficiency tools
        "get_compaction_events": lambda: queries_get_compaction_events(storage, days=7),
        "get_compaction_events_agg": lambda: queries_get_compaction_events(
            storage, days=7, aggregate=True
        ),
        "get_large_tool_results": lambda: queries_get_large_tool_results(
            storage, days=7, min_size_kb=10, limit=10
        ),
        "get_session_efficiency": lambda: queries_get_session_efficiency(storage, days=7),
        # Issue #81: Pre-compaction pattern analysis
        "analyze_pre_compaction_patterns": lambda: queries_analyze_pre_compaction_patterns(
            storage, days=7
        ),
    }

    # Skipped tools (require specific data or modify DB):
    # - ingest_logs, ingest_git_history, ingest_git_history_all_projects
    # - correlate_git_with_sessions, ingest_bus_events
    # - find_related_sessions (requires valid session_id)

    benchmarks = []
    for tool_name, tool_func in tool_functions.items():
        print(f"Benchmarking {tool_name}...", end=" ", flush=True)
        result = _benchmark_tool(tool_name, tool_func, iterations=iterations)
        benchmarks.append(result)
        status = "ERROR" if result.get("error") else f"{result['median']:.3f}s"
        print(status)

    # Sort by median time (slowest first), errors at bottom
    benchmarks.sort(key=lambda x: (x["median"] is None, -(x["median"] or 0)))

    slow_count = sum(1 for b in benchmarks if b.get("median") and b["median"] > 5.0)

    output = {
        "total_tools": len(benchmarks),
        "iterations": iterations,
        "slow_tools": slow_count,
        "benchmarks": benchmarks,
    }

    print()  # Blank line before results table
    print(format_output(output, args.json))


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
    sub.add_argument(
        "--no-expand",
        action="store_true",
        help="Disable breakdown for Skill, Task, and Bash",
    )
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
    sub.add_argument(
        "--expand",
        action="store_true",
        help="Expand Bash→commands, Skill→skills, Task→agents",
    )
    sub.add_argument("--limit", type=int, default=50, help="Max patterns to return (default: 50)")
    sub.set_defaults(func=cmd_sequences)

    # permissions
    sub = subparsers.add_parser("permissions", help="Show permission gaps")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--min-count", type=int, default=5, help="Minimum usage count (default: 5)")
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
    sub.add_argument("--limit", type=int, default=5, help="Number of samples (default: 5)")
    sub.add_argument(
        "--context", type=int, default=2, help="Context events before/after (default: 2)"
    )
    sub.add_argument(
        "--expand",
        action="store_true",
        help="Match expanded tool names (Bash→command, Skill→skill_name, Task→subagent_type)",
    )
    sub.set_defaults(func=cmd_sample_sequences)

    # journey (maps to get_session_messages MCP tool)
    sub = subparsers.add_parser("journey", help="Show messages across sessions")
    sub.add_argument(
        "--days", type=float, default=1, help="Days to look back (default: 1, supports 0.5 for 12h)"
    )
    sub.add_argument("--limit", type=int, default=100, help="Max messages (default: 100)")
    sub.add_argument("--no-projects", action="store_true", help="Exclude project info")
    sub.add_argument("--session-id", help="Filter to specific session ID")
    sub.add_argument(
        "--entry-types",
        help="Entry types to include, comma-separated (default: user,assistant)",
    )
    sub.add_argument(
        "--max-length", type=int, default=500, help="Max message length (default: 500, 0=no limit)"
    )
    sub.set_defaults(func=cmd_journey)

    # search
    sub = subparsers.add_parser("search", help="Search messages (FTS)")
    sub.add_argument("query", help="FTS5 query (e.g., 'auth', '\"fix bug\"', 'skip OR defer')")
    sub.add_argument("--limit", type=int, default=50, help="Max results (default: 50)")
    sub.add_argument("--project", help="Project path filter")
    sub.add_argument("--entry-types", help="Entry types to search, comma-separated (default: all)")
    sub.set_defaults(func=cmd_search)

    # parallel
    sub = subparsers.add_parser("parallel", help="Detect parallel sessions")
    sub.add_argument(
        "--days", type=float, default=1, help="Days to look back (default: 1, supports 0.5 for 12h)"
    )
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

    # error-details
    sub = subparsers.add_parser("error-details", help="Show error details with tool parameters")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--tool", help="Filter by tool name (e.g., Glob, Bash, Edit)")
    sub.add_argument("--limit", type=int, default=50, help="Max errors per tool (default: 50)")
    sub.set_defaults(func=cmd_error_details)

    # classify
    sub = subparsers.add_parser("classify", help="Classify sessions by activity type")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project filter")
    sub.set_defaults(func=cmd_classify)

    # handoff
    sub = subparsers.add_parser("handoff", help="Get handoff context for a session")
    sub.add_argument("--session-id", help="Specific session ID (default: most recent)")
    sub.add_argument(
        "--days", type=float, default=0.17, help="Days to look back (default: 0.17 = ~4 hours)"
    )
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

    # git-ingest-all
    sub = subparsers.add_parser("git-ingest-all", help="Ingest git history from all known projects")
    sub.add_argument("--days", type=int, default=7, help="Days of history (default: 7)")
    sub.set_defaults(func=cmd_git_ingest_all)

    # signals (RFC #26, revised per RFC #17 - raw data, no interpretation)
    sub = subparsers.add_parser("signals", help="Show raw session signals for LLM interpretation")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--min-count", type=int, default=1, help="Min events per session (default: 1)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_signals)

    # session-commits (RFC #26)
    sub = subparsers.add_parser("session-commits", help="Show session-commit associations")
    sub.add_argument("--session-id", help="Specific session ID (default: all recent)")
    sub.add_argument("--days", type=int, default=7, help="Days to look back (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_session_commits)

    # file-activity
    sub = subparsers.add_parser("file-activity", help="Show file read/write activity")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.add_argument("--limit", type=int, default=20, help="Max files to show (default: 20)")
    sub.add_argument(
        "--collapse-worktrees",
        action="store_true",
        help="Consolidate .worktrees/<branch>/ paths",
    )
    sub.set_defaults(func=cmd_file_activity)

    # languages
    sub = subparsers.add_parser("languages", help="Show language breakdown by file operations")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_languages)

    # projects
    sub = subparsers.add_parser("projects", help="Show activity by project")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.set_defaults(func=cmd_projects)

    # mcp-usage
    sub = subparsers.add_parser("mcp-usage", help="Show MCP server/tool usage")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_mcp_usage)

    # agents (RFC #41)
    sub = subparsers.add_parser("agents", help="Show Task subagent activity breakdown")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.set_defaults(func=cmd_agents)

    # bus-events (RFC #54)
    sub = subparsers.add_parser(
        "bus-events", help="Show event-bus events (gotchas, patterns, etc.)"
    )
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--event-type", help="Filter by event type (e.g., 'gotcha_discovered')")
    sub.add_argument("--repo", help="Filter by repo name")
    sub.add_argument("--limit", type=int, default=100, help="Max events to return (default: 100)")
    sub.set_defaults(func=cmd_bus_events)

    # Issue #69: Compaction and efficiency commands

    # compactions
    sub = subparsers.add_parser("compactions", help="Show compaction events (context resets)")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--session-id", help="Filter to specific session ID")
    sub.add_argument("--limit", type=int, default=50, help="Max events to return (default: 50)")
    sub.add_argument(
        "--aggregate", action="store_true", help="Group by session instead of individual events"
    )
    sub.set_defaults(func=cmd_compactions)

    # pre-compaction
    sub = subparsers.add_parser("pre-compaction", help="Show events before a compaction event")
    sub.add_argument("session_id", help="Session ID to analyze")
    sub.add_argument("timestamp", help="ISO timestamp of the compaction event")
    sub.add_argument("--limit", type=int, default=50, help="Max events to return (default: 50)")
    sub.set_defaults(func=cmd_pre_compaction)

    # pre-compaction-patterns (Issue #81)
    sub = subparsers.add_parser(
        "pre-compaction-patterns", help="Analyze patterns in events before compactions"
    )
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument(
        "--events-before",
        type=int,
        default=50,
        help="Events to analyze before each compaction (default: 50)",
    )
    sub.add_argument(
        "--limit", type=int, default=20, help="Max compactions to analyze (default: 20)"
    )
    sub.set_defaults(func=cmd_pre_compaction_patterns)

    # large-results
    sub = subparsers.add_parser(
        "large-results", help="Show large tool results consuming context space"
    )
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--min-size", type=int, default=10, help="Minimum size in KB (default: 10)")
    sub.add_argument("--limit", type=int, default=50, help="Max results to return (default: 50)")
    sub.set_defaults(func=cmd_large_results)

    # efficiency
    sub = subparsers.add_parser("efficiency", help="Show session context efficiency metrics")
    sub.add_argument("--days", type=int, default=7, help="Days to analyze (default: 7)")
    sub.add_argument("--project", help="Project path filter")
    sub.add_argument("--limit", type=int, default=50, help="Max sessions to return (default: 50)")
    sub.set_defaults(func=cmd_efficiency)

    # benchmark (Issue #63)
    sub = subparsers.add_parser("benchmark", help="Benchmark all MCP tool response times")
    sub.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Iterations per tool (default: 3; use 10+ for meaningful p95/p99)",
    )
    sub.set_defaults(func=cmd_benchmark)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

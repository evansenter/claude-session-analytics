# CLAUDE.md

Queryable analytics for Claude Code session logs, exposed as an MCP server.

## Project Overview

This MCP server replaces the bash script `~/.claude/contrib/parse-session-logs.sh` with a persistent, queryable analytics layer. It parses JSONL session logs from `~/.claude/projects/` and provides:

- **User-centric timeline**: Events across conversations, organized by timestamp
- **Rich querying**: Tool frequency, command breakdown, sequences, permission gaps
- **Persistent storage**: SQLite at `~/.claude/contrib/analytics/data.db`
- **Auto-refresh**: Queries automatically refresh stale data (>5 min old)
- **CLI access**: Full CLI for shell scripts and hooks

## Architecture

Follows the `claude-event-bus` pattern:
- FastMCP for MCP server implementation
- SQLite for persistence
- LaunchAgent for always-on availability
- CLI wrapper for shell access

## Commands

```bash
make check      # Run fmt, lint, test
make install    # Install LaunchAgent + CLI
make uninstall  # Remove LaunchAgent + CLI
make dev        # Run in dev mode with auto-reload
```

## Key Files

- `src/session_analytics/server.py` - MCP tools + entry point
- `src/session_analytics/storage.py` - SQLite backend
- `src/session_analytics/ingest.py` - JSONL parsing
- `src/session_analytics/queries.py` - Query implementations
- `src/session_analytics/patterns.py` - Pattern detection

## MCP Tools

| Tool | Purpose |
|------|---------|
| `ingest_logs` | Refresh data from JSONL files |
| `query_timeline` | Events in time window |
| `query_tool_frequency` | Tool usage counts |
| `query_commands` | Bash command breakdown |
| `query_sequences` | Common tool patterns |
| `query_permission_gaps` | Commands needing settings.json |
| `query_sessions` | Session metadata |
| `query_tokens` | Token usage analysis |
| `get_insights` | Pre-computed patterns for /improve-workflow |
| `get_status` | Ingestion status + DB stats |

## Reference

Full implementation plan: `~/.claude/plans/precious-crunching-crescent.md`

Reference implementation: `~/Documents/projects/claude-event-bus/`

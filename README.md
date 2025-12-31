# Claude Session Analytics

Queryable analytics for Claude Code session logs, exposed as an MCP server.

## Overview

This MCP server replaces the bash script `~/.claude/contrib/parse-session-logs.sh` with a persistent, queryable analytics layer. It parses JSONL session logs from `~/.claude/projects/` and provides:

- **User-centric timeline**: Events across conversations, organized by timestamp
- **Rich querying**: Tool frequency, command breakdown, sequences, permission gaps
- **Persistent storage**: SQLite at `~/.claude/contrib/analytics/data.db`
- **Auto-refresh**: Queries automatically refresh stale data (>5 min old)

## Installation

```bash
make install    # Install LaunchAgent + CLI
make uninstall  # Remove LaunchAgent + CLI
```

## Development

```bash
make check      # Run fmt, lint, test
make dev        # Run in dev mode with auto-reload
```

## MCP Tools

| Tool | Purpose |
|------|---------|
| `ingest_logs` | Refresh data from JSONL files |
| `query_tool_frequency` | Tool usage counts |
| `get_status` | Ingestion status + DB stats |

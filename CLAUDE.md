# CLAUDE.md

Queryable analytics for Claude Code session logs, exposed as an MCP server and CLI.

## Project Overview

This MCP server replaces the bash script `~/.claude/contrib/parse-session-logs.sh` with a persistent, queryable analytics layer. It parses JSONL session logs from `~/.claude/projects/` and provides:

- **Tool frequency analysis**: Which tools you use most (Read, Edit, Bash, etc.)
- **Command breakdown**: Bash command patterns (git, make, cargo, etc.)
- **Workflow sequences**: Common tool chains like Read → Edit → Bash
- **Permission gap detection**: Commands that should be added to settings.json
- **Token usage tracking**: Usage by day, session, or model
- **Session timeline**: Events across conversations, organized by timestamp

## Architecture

```
~/.claude/projects/**/*.jsonl  →  SQLite DB  →  MCP Server / CLI
                                     ↓
                           ~/.claude/contrib/analytics/data.db
```

Key components:
- **FastMCP** for MCP server implementation
- **SQLite** for persistent storage with incremental ingestion
- **Auto-refresh** queries automatically refresh stale data (>5 min old)
- **LaunchAgent** for always-on availability (macOS)

## Commands

```bash
make check      # Run fmt, lint, test (84 tests)
make install    # Install LaunchAgent + CLI
make uninstall  # Remove LaunchAgent + CLI
make dev        # Run in dev mode with auto-reload
```

## Key Files

| File | Purpose |
|------|---------|
| `src/session_analytics/server.py` | MCP tools + HTTP server entry point |
| `src/session_analytics/cli.py` | CLI commands (status, ingest, frequency, etc.) |
| `src/session_analytics/storage.py` | SQLite backend with datetime handling |
| `src/session_analytics/ingest.py` | JSONL parsing with incremental updates |
| `src/session_analytics/queries.py` | Query implementations (timeline, tokens, etc.) |
| `src/session_analytics/patterns.py` | Pattern detection (sequences, permission gaps) |

## MCP Tools

| Tool | Purpose |
|------|---------|
| `get_status` | Database stats and last ingestion time |
| `ingest_logs` | Refresh data from JSONL files |
| `query_tool_frequency` | Tool usage counts (Read, Edit, Bash, etc.) |
| `query_timeline` | Events in time window with filtering |
| `query_commands` | Bash command breakdown with prefix filter |
| `query_sessions` | Session metadata and token totals |
| `query_tokens` | Token usage by day, session, or model |
| `query_sequences` | Common tool patterns (n-grams) |
| `query_permission_gaps` | Commands needing settings.json entries |
| `get_insights` | Pre-computed patterns for /improve-workflow |

## CLI Commands

All commands support `--json` for machine-readable output:

```bash
session-analytics-cli status              # DB stats
session-analytics-cli ingest --days 30    # Refresh data
session-analytics-cli frequency           # Tool usage
session-analytics-cli commands --prefix git  # Command breakdown
session-analytics-cli sessions            # Session info
session-analytics-cli tokens --by model   # Token usage
session-analytics-cli sequences           # Tool chains
session-analytics-cli permissions         # Permission gaps
session-analytics-cli insights            # For /improve-workflow
```

## Integration

### With /improve-workflow

The `get_insights` tool (or `session-analytics-cli insights`) provides pre-computed patterns:
- Tool frequency for identifying high-value automations
- Command frequency for settings.json additions
- Tool sequences for workflow optimization
- Permission gaps with ready-to-use suggestions

### With session-start hook

Can be used to auto-ingest on session start:
```bash
session-analytics-cli ingest --days 1 --json 2>/dev/null || true
```

## Data Model

**Events table**: Individual tool uses with timestamps, tokens, commands
**Sessions table**: Aggregated session metadata
**Patterns table**: Pre-computed patterns for fast querying
**Ingested files table**: Tracks file mtime/size for incremental updates

## Reference

Full implementation plan: `~/.claude/plans/precious-crunching-crescent.md`

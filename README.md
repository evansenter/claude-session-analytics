# Claude Session Analytics

MCP server and CLI for queryable analytics on Claude Code session logs.

**Related**: [claude-event-bus](https://github.com/evansenter/claude-event-bus) shares design patterns with this project.

## What It Does

Parses your Claude Code session logs (`~/.claude/projects/**/*.jsonl`) and provides:

- **Tool frequency** - Which tools you use most (Read, Edit, Bash, Grep, etc.)
- **Command breakdown** - Bash command patterns (git, make, cargo, npm, etc.)
- **Workflow sequences** - Common tool chains like `Read → Edit → Bash`
- **Permission gaps** - Commands that should be added to settings.json
- **Token usage** - Usage breakdown by day, session, or model
- **Session timeline** - Events across conversations with filtering
- **Cross-session insights** - Gotchas, patterns, and learnings from [event-bus](https://github.com/evansenter/claude-event-bus)

Data is stored persistently in SQLite and auto-refreshes when stale (>5 min old).

## Installation

```bash
make install
```

This will:
1. Create a virtual environment and install dependencies
2. Set up a LaunchAgent for auto-start (macOS)
3. Add the MCP server to Claude Code
4. Install the CLI to your path

## CLI Usage

```bash
# Status & Ingestion
session-analytics-cli status              # Database stats
session-analytics-cli ingest --days 7     # Refresh data from logs

# Core Analytics
session-analytics-cli frequency           # Tool usage (--no-expand to hide breakdowns)
session-analytics-cli commands            # Bash command breakdown (--prefix git)
session-analytics-cli sessions            # Session metadata and tokens
session-analytics-cli tokens --by day     # Token usage (day/session/model)

# Workflow Analysis
session-analytics-cli sequences           # Tool chains (--expand for command-level)
session-analytics-cli permissions         # Commands needing settings.json
session-analytics-cli insights            # Pre-computed patterns for /improve-workflow

# File & Project Activity
session-analytics-cli file-activity       # File reads/edits/writes
session-analytics-cli languages           # Language distribution
session-analytics-cli projects            # Activity by project
session-analytics-cli mcp-usage           # MCP server/tool usage

# Agent Activity
session-analytics-cli agents              # Task subagent activity vs main session

# Session Analysis
session-analytics-cli signals             # Raw session metrics for LLM interpretation
session-analytics-cli classify            # Categorize sessions (debug/dev/research)
session-analytics-cli failures            # Error patterns and rework detection
session-analytics-cli trends              # Compare usage across time periods
session-analytics-cli handoff             # Context summary for session handoff

# User Messages
session-analytics-cli journey             # User messages across sessions
session-analytics-cli search <query>      # Full-text search on messages

# Session Relationships
session-analytics-cli parallel            # Find simultaneously active sessions
session-analytics-cli related <id>        # Find sessions with similar patterns

# Git Integration
session-analytics-cli git-ingest          # Import git commit history
session-analytics-cli git-correlate       # Link commits to sessions
session-analytics-cli session-commits     # Show commits per session

# Event-Bus Integration
session-analytics-cli bus-events          # Query cross-session events (gotchas, patterns)

# Pattern Inspection
session-analytics-cli sample-sequences    # Sample instances of a pattern with context
```

All commands support:
- `--json` for machine-readable output
- `--days N` to specify time range (default: 7)
- `--project PATH` to filter by project

## MCP Tools

30 tools available when running as an MCP server:

| Category | Tools |
|----------|-------|
| **Status** | `get_status`, `ingest_logs` |
| **Analytics** | `get_tool_frequency`, `get_command_frequency`, `get_session_events`, `list_sessions`, `get_token_usage` |
| **Patterns** | `get_tool_sequences`, `sample_sequences`, `get_permission_gaps`, `get_insights` |
| **Files** | `get_file_activity`, `get_languages`, `get_projects`, `get_mcp_usage` |
| **Agents** | `get_agent_activity` |
| **Sessions** | `get_session_signals`, `classify_sessions`, `analyze_failures`, `analyze_trends`, `get_handoff_context` |
| **Messages** | `get_session_messages`, `search_messages` |
| **Relationships** | `detect_parallel_sessions`, `find_related_sessions` |
| **Git** | `ingest_git_history`, `correlate_git_with_sessions`, `get_session_commits` |
| **Event-Bus** | `ingest_bus_events`, `get_bus_events` |

For detailed usage, read the MCP resource `session-analytics://guide` or see [guide.md](src/session_analytics/guide.md).

## Development

```bash
# Install dev dependencies
make dev

# Run in dev mode with auto-reload
./scripts/dev.sh

# Run checks (format, lint, test)
make check

# Run tests only
.venv/bin/pytest tests/ -v
```

## Data Location

- **Database**: `~/.claude/contrib/analytics/data.db`
- **Logs parsed from**: `~/.claude/projects/**/*.jsonl`
- **Event-bus source**: `~/.claude/contrib/event-bus/data.db` (if [claude-event-bus](https://github.com/evansenter/claude-event-bus) is installed)

## How It Works

1. **Ingestion**: Parses JSONL session logs incrementally (tracks file mtime/size)
2. **Storage**: SQLite database with events, sessions, and patterns tables
3. **Auto-refresh**: Queries detect stale data (>5 min) and trigger re-ingestion
4. **Patterns**: Pre-computes tool sequences and permission gaps for fast queries

## Architecture

Key patterns used in the codebase:

- **Public Storage API**: Use `storage.execute_query()` for reads, `execute_write()` for writes
- **Query Helpers**: `build_where_clause()` reduces duplication across query functions
- **Formatter Registry**: CLI uses `@_register_formatter(predicate)` for extensible output formatting
- **Schema Migrations**: `@migration(version, name)` decorator for future DB schema changes

See `CLAUDE.md` for more details on contributing.

## Related

- [claude-event-bus](https://github.com/evansenter/claude-event-bus) - Cross-session communication for Claude Code

## Uninstall

```bash
make uninstall
```

## License

MIT

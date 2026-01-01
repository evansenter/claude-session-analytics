# Claude Session Analytics

MCP server and CLI for queryable analytics on Claude Code session logs.

## What It Does

Parses your Claude Code session logs (`~/.claude/projects/**/*.jsonl`) and provides:

- **Tool frequency** - Which tools you use most (Read, Edit, Bash, Grep, etc.)
- **Command breakdown** - Bash command patterns (git, make, cargo, npm, etc.)
- **Workflow sequences** - Common tool chains like `Read → Edit → Bash`
- **Permission gaps** - Commands that should be added to settings.json
- **Token usage** - Usage breakdown by day, session, or model
- **Session timeline** - Events across conversations with filtering

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

# Pattern Inspection
session-analytics-cli sample-sequences    # Sample instances of a pattern with context
```

All commands support:
- `--json` for machine-readable output
- `--days N` to specify time range (default: 7)
- `--project PATH` to filter by project

## MCP Tools

When running as an MCP server, these tools are available:

### Status & Ingestion

| Tool | Description |
|------|-------------|
| `get_status` | Database stats and last ingestion time |
| `ingest_logs` | Refresh data from JSONL files |

### Core Analytics

| Tool | Description |
|------|-------------|
| `get_tool_frequency` | Tool usage counts with optional breakdown |
| `get_session_events` | Events in time window with filtering |
| `get_command_frequency` | Bash command breakdown |
| `list_sessions` | Session metadata and totals |
| `get_token_usage` | Token usage by day/session/model |

### Workflow Analysis

| Tool | Description |
|------|-------------|
| `get_tool_sequences` | Common tool patterns (n-grams) |
| `sample_sequences` | Sample instances of a pattern with context |
| `get_permission_gaps` | Commands needing settings.json |
| `get_insights` | Pre-computed patterns for /improve-workflow |

### File & Project Activity

| Tool | Description |
|------|-------------|
| `get_file_activity` | File reads/edits/writes breakdown |
| `get_languages` | Language distribution from file extensions |
| `get_projects` | Activity breakdown by project |
| `get_mcp_usage` | MCP server and tool usage |

### Session Analysis

| Tool | Description |
|------|-------------|
| `get_session_signals` | Raw session metrics for LLM interpretation |
| `classify_sessions` | Categorize sessions (debugging, dev, research) |
| `analyze_failures` | Error patterns and rework detection |
| `analyze_trends` | Compare usage across time periods |
| `get_handoff_context` | Context summary for session handoff |

### User Messages

| Tool | Description |
|------|-------------|
| `get_session_messages` | User messages across sessions |
| `search_messages` | Full-text search on user messages (FTS5) |

### Session Relationships

| Tool | Description |
|------|-------------|
| `detect_parallel_sessions` | Find simultaneously active sessions |
| `find_related_sessions` | Find sessions with similar patterns |

### Git Integration

| Tool | Description |
|------|-------------|
| `ingest_git_history` | Import git commit history |
| `correlate_git_with_sessions` | Link commits to sessions by timing |
| `get_session_commits` | Get commits associated with a session |

### Example: get_tool_frequency

```json
{
  "days": 7,
  "total_tool_calls": 1523,
  "tools": [
    {"tool": "Read", "count": 423},
    {"tool": "Bash", "count": 312, "breakdown": [{"name": "git", "count": 145}, {"name": "make", "count": 89}]},
    {"tool": "Edit", "count": 289},
    {"tool": "Grep", "count": 156}
  ]
}
```

### Example: get_permission_gaps

```json
{
  "gaps": [
    {"command": "npm", "count": 47, "suggestion": "Bash(npm:*)"},
    {"command": "docker", "count": 23, "suggestion": "Bash(docker:*)"}
  ]
}
```

### Example: get_tool_sequences

```json
{
  "sequences": [
    {"pattern": "Read → Edit", "count": 156},
    {"pattern": "Grep → Read", "count": 89},
    {"pattern": "Edit → Bash", "count": 67}
  ]
}
```

### Example: get_session_signals

```json
{
  "sessions": [
    {
      "session_id": "abc123",
      "event_count": 45,
      "error_rate": 0.04,
      "commit_count": 2,
      "has_rework": false,
      "has_pr_activity": true
    }
  ]
}
```

## Integration with /improve-workflow

The `get_insights` tool returns pre-computed patterns optimized for the `/improve-workflow` command:

```bash
session-analytics-cli insights --refresh
```

Returns:
- Tool frequency for identifying high-value automations
- Command frequency for settings.json additions
- Tool sequences for workflow optimization
- Permission gaps with ready-to-use `Bash(cmd:*)` suggestions

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

## Uninstall

```bash
make uninstall
```

## License

MIT

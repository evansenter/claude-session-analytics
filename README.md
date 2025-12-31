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
# Database status and stats
session-analytics-cli status

# Ingest/refresh log data
session-analytics-cli ingest --days 7

# Tool frequency (which tools you use most)
session-analytics-cli frequency --days 30

# Bash command breakdown
session-analytics-cli commands
session-analytics-cli commands --prefix git    # Just git commands

# Session info and token totals
session-analytics-cli sessions

# Token usage analysis
session-analytics-cli tokens --by day
session-analytics-cli tokens --by session
session-analytics-cli tokens --by model

# Common tool sequences (workflow patterns)
session-analytics-cli sequences --min-count 5 --length 3

# Permission gaps (commands that need settings.json)
session-analytics-cli permissions --threshold 10

# Full insights for /improve-workflow
session-analytics-cli insights --refresh
```

All commands support:
- `--json` for machine-readable output
- `--days N` to specify time range (default: 7)
- `--project PATH` to filter by project

## MCP Tools

When running as an MCP server, these tools are available:

| Tool | Description |
|------|-------------|
| `get_status` | Database stats and last ingestion time |
| `ingest_logs` | Refresh data from JSONL files |
| `query_tool_frequency` | Tool usage counts |
| `query_timeline` | Events in time window with filtering |
| `query_commands` | Bash command breakdown |
| `query_sessions` | Session metadata and totals |
| `query_tokens` | Token usage by day/session/model |
| `query_sequences` | Common tool patterns (n-grams) |
| `query_permission_gaps` | Commands needing settings.json |
| `get_insights` | Pre-computed patterns for /improve-workflow |

### Example: query_tool_frequency

```json
{
  "days": 7,
  "total_tool_calls": 1523,
  "tools": [
    {"tool": "Read", "count": 423},
    {"tool": "Bash", "count": 312},
    {"tool": "Edit", "count": 289},
    {"tool": "Grep", "count": 156}
  ]
}
```

### Example: query_permission_gaps

```json
{
  "gaps": [
    {
      "command": "npm",
      "count": 47,
      "suggestion": "Bash(npm:*)"
    },
    {
      "command": "docker",
      "count": 23,
      "suggestion": "Bash(docker:*)"
    }
  ]
}
```

### Example: query_sequences

```json
{
  "sequences": [
    {"pattern": "Read → Edit", "count": 156},
    {"pattern": "Grep → Read", "count": 89},
    {"pattern": "Edit → Bash", "count": 67},
    {"pattern": "Read → Edit → Bash", "count": 45}
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

## Uninstall

```bash
make uninstall
```

## License

MIT

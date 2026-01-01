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

---

## ⚠️ DATABASE PROTECTION - READ THIS ⚠️

**The database at `~/.claude/contrib/analytics/data.db` contains irreplaceable historical data.**

### NEVER do any of the following:
- Add code that deletes the database file (`os.remove()`, `unlink()`, `rm`)
- Add `DROP TABLE` statements for `events`, `sessions`, `ingested_files`, or `git_commits`
- Add `DELETE FROM` for user data tables (only `patterns` table can be cleared - it's re-computed)
- Add any "reset" or "clear all" functionality that destroys historical data

### Safe operations:
- `DELETE FROM patterns` - OK, patterns are re-computed derived data
- `make uninstall` - OK, preserves database (only removes LaunchAgent + MCP config)
- `make reinstall` - OK, just reinstalls Python package

### If you need to test destructive operations:
Use a temporary database in tests (all tests already do this via `tmpdir`).

---

## Commands

```bash
make check      # Run fmt, lint, test
make install    # Install LaunchAgent + CLI + MCP config
make uninstall  # Remove LaunchAgent + CLI
make restart    # Restart LaunchAgent to pick up code changes
make reinstall  # pip install -e . + restart (for pyproject.toml changes)
make dev        # Run in dev mode with auto-reload
```

### When to restart

The install is editable (`pip install -e .`), so Python code changes are picked up automatically by the CLI. The MCP server (LaunchAgent) needs a restart to see changes.

| Change type | Action needed |
|-------------|---------------|
| MCP tools (`server.py`) | `make restart` |
| Query/pattern logic (`queries.py`, `patterns.py`) | `make restart` |
| Storage/migrations (`storage.py`) | `make restart` |
| CLI only (`cli.py`) | None - CLI runs fresh each time |
| `pyproject.toml` (entry points, deps) | `make reinstall` |
| Tests | None - pytest runs fresh |
| Documentation (`guide.md`, `CLAUDE.md`) | None |

## Key Files

| File | Purpose |
|------|---------|
| `src/session_analytics/server.py` | MCP tools + HTTP server entry point |
| `src/session_analytics/cli.py` | CLI with formatter registry for output |
| `src/session_analytics/storage.py` | SQLite backend with migration support |
| `src/session_analytics/ingest.py` | JSONL parsing with incremental updates |
| `src/session_analytics/queries.py` | Query implementations with `build_where_clause()` helper |
| `src/session_analytics/patterns.py` | Pattern detection (sequences, permission gaps) |

## Architecture Patterns

- **Public API**: Use `storage.execute_query()` / `execute_write()` for raw SQL; avoid `_connect()`
- **Formatter Registry**: CLI uses `@_register_formatter(predicate)` decorator pattern
- **Schema Migrations**: Use `@migration(version, name)` decorator in storage.py for DB changes
- **Module Imports**: server.py uses `from session_analytics import queries, patterns, ingest`
- **CLI/MCP Parity**: Always expose new query functions on both CLI and MCP. Add MCP tool in `server.py`, CLI command in `cli.py`, document in both `guide.md` and this file

## MCP API Naming Conventions

Standard conventions shared with claude-event-bus. See event-bus CLAUDE.md for the canonical reference.

### Tool Names

| Prefix | When to use | Example |
|--------|-------------|---------|
| `list_*` | Enumerate items (no complex filtering) | `list_sessions()` |
| `get_*` | Retrieve data with parameters/filters | `get_events(...)` |
| `search_*` | Full-text/fuzzy search | `search_messages(...)` |
| `analyze_*` | Compute derived insights | `analyze_trends(...)` |
| `ingest_*` | Load/import data | `ingest_logs(...)` |

### Argument Names

| Concept | Standard Name | Notes |
|---------|---------------|-------|
| Session identifier | `session_id` | Not `session` or `sid` |
| Max results | `limit` | Not `count` or `max` |
| Time window | `days` | Use fractional for hours: `days=0.5` = 12h |
| Project filter | `project` | Not `project_path` |
| Minimum threshold | `min_count` | Not `threshold` or `min_events` |

## Design Philosophy

**"Don't over-distill"** (RFC #17): Raw data with light structure beats heavily processed summaries. The LLM can handle context.

This means:
- **Surface raw signals, not interpretations**: Return event counts, error rates, and timing data - not pre-computed labels like "success" or "frustrated"
- **Let the LLM interpret**: The consuming LLM has context we don't (user intent, conversation history). It should decide what patterns mean
- **Avoid premature classification**: Don't try to outsmart the LLM by pre-digesting data. Structured raw data is more useful than simplified conclusions

Example - instead of:
```python
# BAD: Pre-computed interpretation
{"outcome": "frustrated", "confidence": 0.75}
```

Do this:
```python
# GOOD: Raw signals for LLM interpretation
{"error_count": 5, "error_rate": 0.25, "has_rework": True, "commit_count": 0}
```

## MCP Tools

| Tool | Purpose |
|------|---------|
| `get_status` | Database stats and last ingestion time |
| `ingest_logs` | Refresh data from JSONL files |
| `get_tool_frequency` | Tool usage counts (Read, Edit, Bash, etc.) |
| `get_session_events` | Events in time window (supports `session_id` filter) |
| `get_command_frequency` | Bash command breakdown with prefix filter |
| `list_sessions` | Session metadata and token totals (lists all session IDs) |
| `get_token_usage` | Token usage by day, session, or model |
| `get_tool_sequences` | Common tool patterns (n-grams, `length` param for n-gram size) |
| `get_permission_gaps` | Commands needing settings.json entries |
| `get_insights` | Pre-computed patterns for /improve-workflow |
| `get_session_messages` | User messages across sessions (supports `session_id` filter) |
| `search_messages` | Full-text search on user messages (FTS5) |
| `get_session_signals` | Raw session metrics for LLM interpretation (RFC #26) |
| `get_session_commits` | Session-commit mappings with timing (RFC #26) |
| `get_file_activity` | File reads/edits/writes with breakdown |
| `get_languages` | Language distribution from file extensions |
| `get_projects` | Activity across all projects |
| `get_mcp_usage` | MCP server and tool usage breakdown |

### Session Discovery and Drill-In Flow

1. **Discover sessions**: `list_sessions()` returns all session IDs with basic metadata
2. **Get signals**: `get_session_signals()` returns raw metrics (error_rate, commit_count, etc.)
3. **Drill into session**:
   - `get_session_events(session_id=<id>)` - get full event trace
   - `get_session_messages(session_id=<id>)` - get all user messages
   - `get_session_commits(session_id=<id>)` - get commit associations

> **Maintainer note**: This discovery flow is also documented in `src/session_analytics/guide.md`
> (exposed as MCP resource `session-analytics://guide`). Keep both in sync when updating API docs.

## CLI Commands

All commands support `--json` for machine-readable output:

```bash
session-analytics-cli status              # DB stats
session-analytics-cli ingest --days 30    # Refresh data
session-analytics-cli frequency           # Tool usage (--no-expand to hide breakdowns)
session-analytics-cli commands --prefix git  # Command breakdown
session-analytics-cli sessions            # Session info
session-analytics-cli tokens --by model   # Token usage
session-analytics-cli sequences           # Tool chains (--expand for command-level)
session-analytics-cli permissions         # Permission gaps
session-analytics-cli insights            # For /improve-workflow
session-analytics-cli journey             # User messages across sessions
session-analytics-cli search <query>      # Full-text search on messages
session-analytics-cli signals             # Raw session signals (RFC #26)
session-analytics-cli session-commits     # Session-commit associations (RFC #26)
session-analytics-cli file-activity       # File reads/edits/writes
session-analytics-cli languages           # Language distribution
session-analytics-cli projects            # Cross-project activity
session-analytics-cli mcp-usage           # MCP server/tool usage
```

### Expand Flags

The `--expand` flag shows detailed breakdowns for aggregated tools:

| Command | Default | Flag | Effect |
|---------|---------|------|--------|
| `frequency` | Expanded | `--no-expand` | Show Bash/Skill/Task breakdowns (commands, skills, agents) |
| `sequences` | Tool-level | `--expand` | Expand to command/skill/agent level sequences |

**Why different defaults?**
- `frequency` answers "what am I using?" - breakdowns are useful by default
- `sequences` answers "what's my workflow?" - tool-level patterns are clearer by default, command-level is for drilling in

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

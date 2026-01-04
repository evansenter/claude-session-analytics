# CLAUDE.md

Queryable analytics for Claude Code session logs, exposed as an MCP server and CLI.

**API Reference**: Run `session-analytics-cli --help` or read `src/session_analytics/guide.md` (served as `session-analytics://guide` MCP resource).

**Related**: [claude-event-bus](https://github.com/evansenter/claude-event-bus) shares design patterns with this project.

---

## ⚠️ DATABASE PROTECTION

**The database at `~/.claude/contrib/analytics/data.db` contains irreplaceable historical data.**

### NEVER:
- Delete the database file (`os.remove()`, `unlink()`, `rm`)
- `DROP TABLE` on `events`, `sessions`, `ingested_files`, or `git_commits`
- `DELETE FROM` user data tables (only `patterns` is safe - it's re-computed)
- Add "reset" or "clear all" functionality

### Before schema changes:
```bash
cp ~/.claude/contrib/analytics/data.db ~/.claude/contrib/analytics/data.db.backup-$(date +%Y%m%d-%H%M%S)
```

---

## Design Philosophy

This API is consumed by LLMs. Every endpoint should be designed with that in mind.

### Principle 1: Don't Over-Distill

Raw data with light structure beats heavily processed summaries. The LLM can handle context.

```python
# BAD: Pre-computed interpretation
{"outcome": "frustrated", "confidence": 0.75}

# GOOD: Raw signals for LLM interpretation
{"error_count": 5, "error_rate": 0.25, "has_rework": True, "commit_count": 0}
```

### Principle 2: Aggregate → Drill-Down

Every aggregate endpoint needs a path to actionable detail. If an LLM sees "821 Bash errors", it should be able to discover WHICH commands failed.

**The test**: Can an LLM go from high-level insight to actionable fix using only MCP calls?

See RFC #49 for current drill-down gaps and solutions.

### Principle 3: Self-Play Testing

Before merging new API endpoints, test them as an LLM would:

1. Start from a high-level question ("What's causing errors?")
2. Use only MCP tools (no direct DB access)
3. Attempt to reach an actionable conclusion
4. If blocked, the API is incomplete

---

## Quick Reference

### Commands

```bash
make check      # Run fmt, lint, test
make install    # Install LaunchAgent + CLI + MCP config
make restart    # Restart LaunchAgent to pick up code changes
make reinstall  # pip install -e . + restart (for pyproject.toml changes)
```

### When to Restart

| Change | Action |
|--------|--------|
| `server.py`, `queries.py`, `patterns.py`, `storage.py` | `make restart` |
| `cli.py` only | None (CLI runs fresh) |
| `pyproject.toml` | `make reinstall` |

### Key Files

| File | Purpose |
|------|---------|
| `server.py` | MCP tools + entry point |
| `cli.py` | CLI with formatter registry |
| `storage.py` | SQLite + migrations |
| `ingest.py` | JSONL parsing |
| `queries.py` | Query implementations |
| `patterns.py` | Sequence/permission gap detection |
| `guide.md` | API reference (MCP resource) |

---

## Adding New Endpoints

Checklist for adding a new query:

1. **Query function** in `queries.py`
   - Use `build_where_clause()` helper for filters
   - Return structured dict, not raw tuples

2. **MCP tool** in `server.py`
   - Follow naming: `get_*`, `list_*`, `search_*`, `analyze_*`
   - Standard args: `days`, `limit`, `session_id`, `project`

3. **CLI command** in `cli.py`
   - Add formatter with `@_register_formatter(predicate)`
   - Support `--json` flag

4. **Documentation** in `guide.md`
   - Add to appropriate section
   - Include example usage

5. **Self-play test**
   - Can you reach actionable info using only MCP?
   - If aggregate, what's the drill-down path?

6. **Run `make check`**

---

## Architecture

```
~/.claude/projects/**/*.jsonl  →  SQLite DB  →  MCP Server / CLI
                                     ↓
                           ~/.claude/contrib/analytics/data.db
```

### Key Patterns

- **Storage API**: Use `storage.execute_query()` / `execute_write()`; avoid `_connect()`
- **Migrations**: Use `@migration(version, name)` decorator in storage.py
- **Formatters**: CLI uses `@_register_formatter(predicate)` - first match wins
- **CLI/MCP Parity**: Every query should be accessible from both

### Naming Conventions

| Prefix | Use | Example |
|--------|-----|---------|
| `list_*` | Enumerate (no complex filtering) | `list_sessions()` |
| `get_*` | Retrieve with filters | `get_session_events()` |
| `search_*` | Full-text search | `search_messages()` |
| `analyze_*` | Compute insights | `analyze_failures()` |
| `ingest_*` | Load/import data | `ingest_logs()` |

| Arg | Standard Name |
|-----|---------------|
| Session ID | `session_id` |
| Max results | `limit` |
| Time window | `days` (fractional OK: `0.5` = 12h) |
| Project filter | `project` |

---

## Data Model

| Table | Purpose |
|-------|---------|
| `events` | Individual tool uses with timestamps, tokens, commands |
| `sessions` | Aggregated session metadata |
| `patterns` | Pre-computed patterns (safe to delete - re-computed) |
| `ingested_files` | Tracks file mtime/size for incremental updates |
| `git_commits` | Commit history for session correlation |

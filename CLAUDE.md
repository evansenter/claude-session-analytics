# CLAUDE.md

Queryable analytics for Claude Code session logs, exposed as an MCP server and CLI.

**API Reference**: `session-analytics-cli --help` or `src/session_analytics/guide.md` (MCP resource: `session-analytics://guide`).

**Schema Design**: See [docs/SCHEMA.md](docs/SCHEMA.md) for database tables, indexes, and migration history.

---

## ⚠️ DATABASE PROTECTION

**The database at `~/.claude/contrib/analytics/data.db` contains irreplaceable historical data.**

### NEVER:
- Delete the database file
- `DROP TABLE` or `DELETE FROM` user data tables (only `patterns` is safe - it's re-computed)
- Add "reset" or "clear all" functionality

### Before schema changes:
```bash
cp ~/.claude/contrib/analytics/data.db ~/.claude/contrib/analytics/data.db.backup-$(date +%Y%m%d-%H%M%S)
```

---

## Design Philosophy

This API is consumed by LLMs. Design with that in mind:

1. **Don't over-distill** - Raw signals (`error_count: 5, has_rework: true`) beat pre-computed interpretations (`outcome: "frustrated"`)

2. **Aggregate → drill-down** - If an endpoint shows "821 Bash errors", there must be a path to discover WHICH commands failed

3. **Self-play test** - Before merging, try reaching an actionable conclusion using only MCP tools. If blocked, the API is incomplete

---

## Commands

```bash
make check      # fmt, lint, test
make install    # LaunchAgent + CLI + MCP config
make restart    # Restart LaunchAgent for code changes
make reinstall  # pip install -e . + restart (for pyproject.toml)
```

### When to Restart

| Change | Action |
|--------|--------|
| `server.py`, `queries.py`, `patterns.py`, `storage.py` | `make restart` |
| `cli.py` only | None (CLI runs fresh) |
| `pyproject.toml` | `make reinstall` |

---

## Key Patterns

- **Storage API**: Use `storage.execute_query()` / `execute_write()`; avoid `_connect()`
- **Migrations**: `@migration(version, name)` decorator in storage.py
- **CLI/MCP parity**: Every query accessible from both interfaces

---

## Adding Endpoints

1. Query function in `queries.py` (use `build_where_clause()` helper)
2. MCP tool in `server.py` (naming: `get_*`, `list_*`, `search_*`, `analyze_*`)
3. CLI command in `cli.py` (formatter via `@_register_formatter`)
4. **Add to benchmark**: Update `cmd_benchmark()` in `cli.py` to include the new tool
5. Documentation in `guide.md`
6. Self-play test: can you reach actionable info using only MCP?
7. Run `make check`

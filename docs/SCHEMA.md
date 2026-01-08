# Database Schema Design

This document describes the SQLite database schema for session-analytics.

**Location**: `~/.claude/contrib/analytics/data.db`

---

## Design Principles

1. **Don't over-distill** - Store raw signals (error counts, timestamps, parameters) rather than pre-computed interpretations. The consuming LLM handles context.

2. **Aggregate → drill-down** - Every aggregate must be traceable to specifics. If "821 Bash errors" appears, the schema must support finding which commands failed.

3. **Denormalize for common queries** - Extract frequently-filtered fields (command, file_path, skill_name) into columns rather than requiring JSON parsing.

---

## Tables Overview

| Table | Purpose | Rows (typical) |
|-------|---------|----------------|
| `events` | All tool calls, messages, and summaries from JSONL logs | 100K+ |
| `sessions` | Aggregated session metadata | 1K+ |
| `ingestion_state` | Tracks which JSONL files have been processed | ~100 |
| `patterns` | Pre-computed patterns (re-computable, safe to drop) | ~1K |
| `git_commits` | Git history for correlation | ~5K |
| `session_commits` | Junction table linking sessions to commits | ~3K |
| `bus_events` | Cross-session events from event-bus | ~2K |
| `events_fts` | FTS5 virtual table for user message search | N/A |

---

## Core Tables

### events

The primary table storing all parsed JSONL entries.

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    session_id TEXT NOT NULL,
    project_path TEXT,
    entry_type TEXT,           -- 'user', 'assistant', 'summary', 'tool_use', 'tool_result'

    -- Tool-specific (null if not a tool call)
    tool_name TEXT,
    tool_input_json TEXT,      -- Full JSON for drill-down
    tool_id TEXT,              -- Correlates tool_use with tool_result
    is_error INTEGER DEFAULT 0,

    -- Denormalized for common filters
    command TEXT,              -- Bash: first word (e.g., "git")
    command_args TEXT,         -- Bash: remaining args
    file_path TEXT,            -- Read/Edit/Write target
    skill_name TEXT,           -- Skill invocation name

    -- Token tracking (only on assistant events to avoid duplication)
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    model TEXT,

    -- Context
    git_branch TEXT,
    cwd TEXT,

    -- User journey (RFC #17)
    user_message_text TEXT,    -- For FTS search
    exit_code INTEGER,         -- Reserved for future extraction

    -- Agent tracking (RFC #41)
    parent_uuid TEXT,          -- Links tool_use to parent assistant event
    agent_id TEXT,             -- Task subagent ID from agent-*.jsonl
    is_sidechain INTEGER DEFAULT 0,
    version TEXT               -- Claude Code version
)
```

**Key patterns**:
- `entry_type='tool_use'` + `entry_type='tool_result'` are correlated by `tool_id`
- Token columns only populated on `entry_type='assistant'` to avoid double-counting
- `user_message_text` enables FTS via `events_fts` virtual table
- `tool_input_json` preserves full parameters for drill-down queries

### sessions

Aggregated metadata per session.

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,       -- UUID from session file
    project_path TEXT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    entry_count INTEGER DEFAULT 0,
    tool_use_count INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    primary_branch TEXT,
    slug TEXT,                 -- Human-readable session name
    context_switch_count INTEGER DEFAULT 0  -- RFC #26
)
```

### git_commits

Git history for session correlation.

```sql
CREATE TABLE git_commits (
    sha TEXT PRIMARY KEY,
    timestamp TIMESTAMP,
    message TEXT,
    session_id TEXT,           -- Inferred from timestamp proximity
    project_path TEXT
)
```

### session_commits

Junction table for time-to-commit analysis.

```sql
CREATE TABLE session_commits (
    session_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    time_to_commit_seconds INTEGER,
    is_first_commit INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, commit_sha)
)
```

### bus_events

Events from the event-bus for cross-session insights.

```sql
CREATE TABLE bus_events (
    id INTEGER PRIMARY KEY,
    event_id INTEGER UNIQUE NOT NULL,  -- Original ID from event-bus
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,          -- 'gotcha_discovered', 'pattern_found', etc.
    channel TEXT,
    session_id TEXT,
    repo TEXT,                         -- Extracted from channel
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

---

## Indexes

Performance-critical indexes and their purpose:

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_events_timestamp` | `timestamp` | Time-range queries (days parameter) |
| `idx_events_session` | `session_id` | Session-specific event lookup |
| `idx_events_tool` | `tool_name` | Tool frequency analysis |
| `idx_events_project` | `project_path` | Project filtering |
| `idx_events_tool_id` | `tool_id` | Self-join for tool_use ↔ tool_result correlation |
| `idx_events_parent_uuid` | `parent_uuid` | Token deduplication queries |
| `idx_events_agent_id` | `agent_id` | Agent activity breakdown |
| `idx_events_has_user_message` | Partial on `id` | FTS join optimization |

**Performance note**: The `idx_events_tool_id` index is critical for `query_error_details()` which self-joins events to correlate errors with their input parameters. Without it, queries take ~25s on 160K rows; with it, ~0.3s.

---

## Full-Text Search

User messages are indexed via FTS5:

```sql
CREATE VIRTUAL TABLE events_fts USING fts5(
    user_message_text,
    content='events',
    content_rowid='id'
)
```

Sync triggers maintain index consistency:
- `events_fts_insert`: Populates FTS on new events
- `events_fts_delete`: Removes from FTS on delete
- `events_fts_update`: Handles message text changes

---

## Migration History

| Version | Name | Changes |
|---------|------|---------|
| 1 | Initial | Core tables: events, sessions, ingestion_state, patterns |
| 2 | add_rfc17_phase1_columns | user_message_text, exit_code, git_commits table |
| 3 | add_user_message_fts | FTS5 virtual table and sync triggers |
| 4 | add_session_enrichment | session_commits junction, context_switch_count |
| 5 | add_agent_tracking | parent_uuid, agent_id, is_sidechain, version |
| 6 | add_event_bus_integration | bus_events table |
| 7 | add_tool_id_index | Performance index for self-joins |

---

## Schema Evolution

When adding schema changes:

1. Add migration function with `@migration(N, "name")` decorator
2. Update `SCHEMA_VERSION = N` constant
3. Add to `_init_db()` for fresh installs
4. Use `IF NOT EXISTS` for idempotency
5. Test with both fresh DB and migration path

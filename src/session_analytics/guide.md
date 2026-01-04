# Session Analytics Usage Guide

> **Tip:** Read this guide via the MCP resource `session-analytics://guide` for usage patterns and best practices.

## What is this?

Session Analytics provides queryable analytics on Claude Code session logs. It parses
the JSONL files from `~/.claude/projects/` and stores them in SQLite for fast querying.
Use it to understand your Claude Code usage patterns, find workflow improvements, and
identify permission gaps.

## Available Tools

### Status & Ingestion

| Tool | Purpose |
|------|---------|
| `get_status()` | Database stats, last ingestion time |
| `ingest_logs(days?, project?, force?)` | Refresh data from JSONL files |

### Core Queries

| Tool | Purpose |
|------|---------|
| `get_tool_frequency(days?, project?)` | Tool usage counts (Read, Edit, Bash, etc.) |
| `get_command_frequency(days?, prefix?, project?)` | Bash command breakdown |
| `list_sessions(days?, project?)` | Session metadata and token totals |
| `get_token_usage(days?, by?, project?)` | Token usage by day, session, or model |
| `get_session_events(days?, tool?, session_id?)` | Recent events with filtering |
| `get_file_activity(days?, project?, limit?, collapse_worktrees?)` | File reads/edits/writes breakdown |
| `get_languages(days?, project?)` | Language distribution from file extensions |
| `get_projects(days?)` | Activity across all projects |
| `get_mcp_usage(days?, project?)` | MCP server and tool usage |

### Pattern Analysis

| Tool | Purpose |
|------|---------|
| `get_tool_sequences(days?, min_count?, length?)` | Common tool chains (e.g., Read → Edit → Bash) |
| `sample_sequences(pattern, limit?, context_events?)` | Random samples of a pattern with surrounding context |
| `get_permission_gaps(days?, min_count?)` | Commands not covered by settings.json (supports glob patterns) |
| `get_insights(days?, refresh?)` | Pre-computed patterns for /improve-workflow |

### Failure Analysis

| Tool | Purpose |
|------|---------|
| `analyze_failures(days?, project?)` | Failure patterns with drill-down to specific commands |

Returns:
- `errors_by_tool`: Count of errors per tool
- `error_examples`: Top failing commands (Bash) or files (Edit/Read/Write) for drill-down
- `rework_patterns`: Files edited 3+ times within 10 minutes

### Session Classification

| Tool | Purpose |
|------|---------|
| `classify_sessions(days?, project?)` | Categorize sessions with explanation of why |

Each session includes `classification_factors` explaining WHY it was categorized:
- `trigger`: The threshold that was exceeded (e.g., "error_rate > 15%")
- Relevant metrics (error_rate, edit_rate, etc.)

### Trend Analysis

| Tool | Purpose |
|------|---------|
| `analyze_trends(days?, compare_to?)` | Token/event trends with growth rates |

### User Messages

| Tool | Purpose |
|------|---------|
| `get_session_messages(days?, project?, session_id?)` | User messages across sessions chronologically |
| `search_messages(query, limit?)` | Full-text search on user messages (FTS5) |

### Session Relationships

| Tool | Purpose |
|------|---------|
| `detect_parallel_sessions(days?, min_overlap_minutes?)` | Find simultaneously active sessions |
| `find_related_sessions(session_id)` | Find sessions with similar patterns |

### Git Integration

| Tool | Purpose |
|------|---------|
| `ingest_git_history(days?, repo_path?)` | Parse and store git commits |
| `correlate_git_with_sessions(days?)` | Link commits to sessions by timing |
| `get_session_commits(session_id?)` | Get commits associated with a session |

### Session Signals

| Tool | Purpose |
|------|---------|
| `get_session_signals(days?, min_count?)` | Raw session metrics for LLM interpretation |
| `get_handoff_context(session_id?, days?, limit?)` | Recent activity summary for session continuity |

### Agent Activity

| Tool | Purpose |
|------|---------|
| `get_agent_activity(days?, project?)` | Task subagent activity vs main session (RFC #41) |

## Quick Start

### 1. Check status
```
get_status()
→ {last_ingestion: "2025-01-15T10:30:00", event_count: 5432, db_size_mb: 2.1}
```

### 2. Ingest recent logs
```
ingest_logs(days=7)
→ {files_processed: 12, entries_added: 847, entries_skipped: 23}
```
Data auto-refreshes when queries detect stale data (>5 min old).

### 3. Query your usage
```
get_tool_frequency(days=30)
→ {tools: [{name: "Read", count: 1234}, {name: "Edit", count: 567}, ...]}
```

## Suggested Workflows

These are common patterns for using the analytics API. They're suggestions, not requirements—
use the APIs however best fits your needs.

### Workflow: Broad to Narrow

```
┌─────────────────────────────────────────────────────────────────┐
│                     BROAD OVERVIEW                               │
├─────────────────────────────────────────────────────────────────┤
│  get_status()           → Is data fresh? How many events?       │
│  get_tool_frequency()   → What tools are used most?             │
│  get_command_frequency()→ What commands are common?             │
├─────────────────────────────────────────────────────────────────┤
│                     DISCOVER PATTERNS                            │
├─────────────────────────────────────────────────────────────────┤
│  list_sessions()        → What sessions exist?                  │
│  get_session_signals()  → Which sessions look interesting?      │
│  classify_sessions()    → What type of work (debug, dev, etc)?  │
├─────────────────────────────────────────────────────────────────┤
│                     DRILL INTO SPECIFICS                         │
├─────────────────────────────────────────────────────────────────┤
│  get_session_events(session_id=X)   → Full event trace          │
│  get_session_messages(session_id=X) → User intent               │
│  get_session_commits(session_id=X)  → Work products             │
│  search_messages("query")           → Find specific topics      │
└─────────────────────────────────────────────────────────────────┘
```

### Workflow: Question-Based

| Question | Tools to Use |
|----------|-------------|
| "What have I been working on?" | `list_sessions()` → `get_session_messages()` |
| "Why did session X struggle?" | `get_session_signals(session_id=X)` → `get_session_events(session_id=X)` |
| "What workflows can I automate?" | `get_tool_sequences()` → `get_permission_gaps()` |
| "How has my usage changed?" | `analyze_trends()` |
| "What did I do with feature X?" | `search_messages("feature X")` |

### Workflow: Improvement-Focused

```
get_permission_gaps() → "Add these commands to settings.json"
get_tool_sequences()  → "These patterns could be automated"
analyze_failures()    → "These commands tend to fail"
analyze_trends()      → "Usage is increasing/decreasing"
```

## Reference

### Session Categories

`classify_sessions()` returns one of these categories, with `classification_factors` explaining why:

| Category | Criteria | Trigger Example |
|----------|----------|-----------------|
| **debugging** | High error rate (>15%) or 5+ errors | `"error_rate > 15%"` |
| **development** | Heavy editing (>30% edits or 3+ writes) | `"edit_rate > 30%"` |
| **maintenance** | Git/build focus without much editing | `"git_build_rate > 30%"` |
| **research** | Mostly reading/searching codebase | `"read_search_rate > 50%"` |
| **mixed** | No dominant pattern | `"no_dominant_pattern"` |

### Permission Gaps

`get_permission_gaps()` returns commands to add to `~/.claude/settings.json`:

```
get_permission_gaps(min_count=5)
→ [{command: "npm", count: 23, suggestion: "Bash(npm:*)"}]
```

Add suggestions to `permissions.allow` in your settings.

**Note:** Supports glob pattern matching. Patterns like `Bash(make*)` will correctly
match commands `make`, `make-test`, etc. using fnmatch.

### Git Integration

Git correlation requires two steps:

```
ingest_git_history(days=30)   # Parse commits from repo
correlate_git_with_sessions() # Link to sessions by timing
get_session_commits(session_id="abc")  # View results
```

## Tips

- **Auto-refresh**: Queries auto-ingest when data is stale (>5 min). Use `get_status()` to check.
- **Project filter**: Most queries accept `project` - uses LIKE matching, partial names work.
- **Day filters**: `days=7` for recent trends, `days=30` for patterns.
- **Permission gaps**: Compare against `~/.claude/settings.json`. Higher `min_count` = less noise.
- **Sequences**: `length=3` finds complex patterns but needs more data.
- **CLI parity**: `session-analytics-cli` mirrors all MCP tools for terminal use.

## Data

| Item | Path |
|------|------|
| Database | `~/.claude/contrib/analytics/data.db` |
| Source logs | `~/.claude/projects/**/*.jsonl` |

Ingestion is incremental - only changed files are re-parsed.

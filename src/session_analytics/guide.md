# Session Analytics Usage Guide

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
| `query_tool_frequency(days?, project?)` | Tool usage counts (Read, Edit, Bash, etc.) |
| `query_commands(days?, prefix?, project?)` | Bash command breakdown |
| `query_sessions(days?, project?)` | Session metadata and token totals |
| `query_tokens(days?, by?, project?)` | Token usage by day, session, or model |
| `query_timeline(hours?, tool?, session_id?)` | Recent events with filtering |

### Pattern Analysis

| Tool | Purpose |
|------|---------|
| `query_sequences(days?, min_count?, length?)` | Common tool chains (e.g., Read → Edit → Bash) |
| `query_permission_gaps(days?, threshold?)` | Commands that should be in settings.json |
| `get_insights(days?, refresh?)` | Pre-computed patterns for /improve-workflow |

### Failure Analysis

| Tool | Purpose |
|------|---------|
| `query_failure_correlation(days?, project?)` | Correlate tool failures with commands |
| `query_common_failures(days?, min_count?)` | Aggregate failure patterns |

### Session Classification

| Tool | Purpose |
|------|---------|
| `classify_sessions(days?, project?)` | Categorize sessions (debugging, development, research, maintenance) |
| `query_session_progression(session_id)` | Track session stage transitions |

### Trend Analysis

| Tool | Purpose |
|------|---------|
| `analyze_trends(days?, compare_to?)` | Token/event trends with growth rates |
| `compare_periods(days?, metric?)` | Period-over-period comparisons |

### User Workflow

| Tool | Purpose |
|------|---------|
| `get_user_journey(days?, project?)` | Session summaries with tool chains |
| `find_related_sessions(session_id)` | Find sessions with similar patterns |

### Git Integration

| Tool | Purpose |
|------|---------|
| `ingest_git_history(days?, repo_path?)` | Parse and store git commits |
| `correlate_git_with_sessions(days?)` | Link commits to sessions by timing |
| `query_session_commits(session_id)` | Get commits associated with a session |

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
query_tool_frequency(days=30)
→ {tools: [{name: "Read", count: 1234}, {name: "Edit", count: 567}, ...]}
```

## Session Discovery and Drill-In

A common workflow is discovering sessions, getting signals about them, then drilling into interesting ones:

### 1. Discover sessions
```
query_sessions(days=7)
→ {sessions: [{id: "abc123", project: "my-repo", event_count: 50}, ...]}
```

### 2. Get signals for sessions
```
get_session_signals(days=7)
→ {sessions: [
    {session_id: "abc123", error_rate: 0.04, commit_count: 2, has_rework: false, ...},
    {session_id: "def456", error_rate: 0.25, commit_count: 0, has_rework: true, ...}
  ]}
```

The LLM interprets these raw signals - high error rate + rework + no commits might indicate frustration.

### 3. Drill into an interesting session
```
# Get full event trace
query_timeline(session_id="abc123")
→ {events: [{tool: "Read", file: "auth.py", ...}, {tool: "Edit", ...}, ...]}

# Get all user messages
get_user_journey(session_id="abc123")
→ {messages: [{content: "Fix the login bug", ...}, ...]}

# Get commit associations
get_session_commits(session_id="abc123")
→ {commits: [{sha: "a1b2c3", time_to_commit_seconds: 1800, is_first_commit: true}]}
```

## Common Patterns

### Understanding tool usage

```
# What tools do I use most?
query_tool_frequency(days=30)

# What bash commands do I run?
query_commands(days=30, prefix="git")  # Just git commands
query_commands(days=30)                 # All commands
```

### Finding workflow sequences

```
# What 2-tool patterns are common?
query_sequences(length=2, min_count=10)
→ [{pattern: "Read → Edit", count: 234}, {pattern: "Grep → Read", count: 156}, ...]

# What 3-tool patterns?
query_sequences(length=3, min_count=5)
→ [{pattern: "Read → Edit → Bash", count: 45}, ...]
```

### Identifying permission gaps

```
# Commands I use frequently but haven't added to settings.json
query_permission_gaps(threshold=5)
→ [{command: "npm test", count: 23, suggestion: "Bash(npm test:*)"}, ...]
```

Add these to your `~/.claude/settings.json` under `permissions.allow`.

### Token usage analysis

```
# Usage by day
query_tokens(days=30, by="day")

# Usage by model
query_tokens(days=30, by="model")

# Usage by session
query_tokens(days=7, by="session")
```

### Timeline exploration

```
# Recent events
query_timeline(hours=24)

# Filter by tool
query_timeline(hours=24, tool="Bash")

# Filter by session
query_timeline(session_id="abc123")
```

### Session classification

```
# Categorize recent sessions by activity type
classify_sessions(days=30)
→ {
    sessions: [
      {session_id: "abc", category: "development", confidence: 0.85},
      {session_id: "def", category: "debugging", confidence: 0.72},
      ...
    ],
    summary: {debugging: 5, development: 12, research: 3, maintenance: 2}
  }
```

Categories:
- **debugging**: High error rate (>15%) or 5+ errors
- **development**: Heavy editing (>30% edits or 3+ writes)
- **maintenance**: Git/build focus without much editing
- **research**: Mostly reading/searching codebase
- **mixed**: No dominant pattern

### Failure analysis

```
# What commands tend to fail?
query_common_failures(days=30, min_count=3)
→ [{tool: "Bash", command: "cargo test", count: 12}, ...]

# Correlate failures with context
query_failure_correlation(days=30)
→ {correlations: [{tool: "Bash", command: "npm install", failure_rate: 0.15}, ...]}
```

### Git integration

```
# Ingest git history from current repo
ingest_git_history(days=30)
→ {commits_found: 45, commits_added: 42, skipped_malformed: 0}

# Link commits to sessions (within 5-min buffer of session)
correlate_git_with_sessions(days=30)
→ {sessions_analyzed: 20, commits_correlated: 38}

# See what commits were made during a session
query_session_commits(session_id="abc123")
→ [{sha: "abc...", message: "Fix auth bug", timestamp: "..."}]
```

### Trend analysis

```
# Compare this week to last week
analyze_trends(days=7, compare_to="previous")
→ {
    metrics: {
      events: {current: 500, previous: 400, change_pct: 25, direction: "up"},
      tokens: {current: 50000, previous: 45000, change_pct: 11, direction: "up"}
    }
  }

# Compare to same week last month
analyze_trends(days=7, compare_to="same_last_month")
```

## Integration with /improve-workflow

The `get_insights` tool returns pre-computed patterns specifically formatted
for the `/improve-workflow` command:

```
get_insights(days=30, refresh=True)
→ {
    tool_frequency: [...],
    command_frequency: [...],
    sequences: [...],
    permission_gaps: [...],
    summary: {has_gaps: true, top_tools: ["Read", "Edit", "Bash"]}
  }
```

This powers data-driven workflow improvement suggestions.

## Best Practices

### Ingestion

1. **Let auto-refresh work** - Queries auto-ingest when data is stale (>5 min)
2. **Use project filter** - `ingest_logs(project="my-repo")` for faster, focused ingestion
3. **Force refresh sparingly** - `force=True` re-parses everything, slower but thorough

### Querying

4. **Start with frequency** - `query_tool_frequency` gives quick overview
5. **Use day filters** - `days=7` for recent trends, `days=30` for patterns
6. **Project filter** - Most queries accept `project` to focus on one repo

### Permission Gaps

7. **Check weekly** - Run `query_permission_gaps(threshold=3)` to catch new patterns
8. **Higher threshold = less noise** - Start with `threshold=10` if overwhelmed
9. **Review before adding** - Some commands shouldn't be auto-approved

### Workflow Improvement

10. **Use /improve-workflow** - It consumes `get_insights` and generates suggestions
11. **Look for sequences** - Repeated patterns might benefit from automation
12. **Track over time** - Compare `days=7` vs `days=30` to see trend changes

## Data Details

### Storage Location

| Item | Path |
|------|------|
| Database | `~/.claude/contrib/analytics/data.db` |
| Source logs | `~/.claude/projects/**/*.jsonl` |

### What's Tracked

Each event includes:
- Timestamp and session ID
- Tool name and entry type
- For Bash: command prefix (e.g., "git", "npm")
- For file ops: file path
- Token counts (input/output)
- Error status

### Incremental Ingestion

The server tracks file mtimes and sizes. Only changed files are re-parsed
on subsequent ingestions, making `ingest_logs` fast for daily use.

## Tips

- Data auto-refreshes on query if stale (>5 min since last ingestion)
- Use `get_status()` to check when data was last refreshed
- The `project` filter uses LIKE matching - partial names work
- `query_sequences` with `length=3` finds more complex patterns but needs more data
- Permission gaps compare your usage against `~/.claude/settings.json`
- Token queries help track API usage costs over time
- The CLI (`session-analytics-cli`) mirrors all MCP tools for terminal use

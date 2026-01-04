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
| `get_permission_gaps(days?, min_count?)` | Commands that should be in settings.json |
| `get_insights(days?, refresh?)` | Pre-computed patterns for /improve-workflow |

### Failure Analysis

| Tool | Purpose |
|------|---------|
| `analyze_failures(days?, project?)` | Failure patterns, rework, and correlations |

### Session Classification

| Tool | Purpose |
|------|---------|
| `classify_sessions(days?, project?)` | Categorize sessions (debugging, development, research, maintenance) |

### Trend Analysis

| Tool | Purpose |
|------|---------|
| `analyze_trends(days?, compare_to?)` | Token/event trends with growth rates |

### User Workflow

| Tool | Purpose |
|------|---------|
| `get_session_messages(days?, project?)` | User messages across sessions chronologically |
| `find_related_sessions(session_id)` | Find sessions with similar patterns |
| `search_messages(query, limit?)` | Full-text search on user messages (FTS5) |

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

## Session Discovery and Drill-In

A common workflow is discovering sessions, getting signals about them, then drilling into interesting ones:

### 1. Discover sessions
```
list_sessions(days=7)
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
get_session_events(session_id="abc123")
→ {events: [{tool: "Read", file: "auth.py", ...}, {tool: "Edit", ...}, ...]}

# Get all user messages
get_session_messages(session_id="abc123")
→ {messages: [{content: "Fix the login bug", ...}, ...]}

# Get commit associations
get_session_commits(session_id="abc123")
→ {commits: [{sha: "a1b2c3", time_to_commit_seconds: 1800, is_first_commit: true}]}
```

## Common Patterns

### Understanding tool usage

```
# What tools do I use most?
get_tool_frequency(days=30)

# What bash commands do I run?
get_command_frequency(days=30, prefix="git")  # Just git commands
get_command_frequency(days=30)                 # All commands
```

### Finding workflow sequences

```
# What 2-tool patterns are common?
get_tool_sequences(length=2, min_count=10)
→ [{pattern: "Read → Edit", count: 234}, {pattern: "Grep → Read", count: 156}, ...]

# What 3-tool patterns?
get_tool_sequences(length=3, min_count=5)
→ [{pattern: "Read → Edit → Bash", count: 45}, ...]
```

### Identifying permission gaps

```
# Commands I use frequently but haven't added to settings.json
get_permission_gaps(min_count=5)
→ [{command: "npm test", count: 23, suggestion: "Bash(npm test:*)"}, ...]
```

Add these to your `~/.claude/settings.json` under `permissions.allow`.

### Token usage analysis

```
# Usage by day
get_token_usage(days=30, by="day")

# Usage by model
get_token_usage(days=30, by="model")

# Usage by session
get_token_usage(days=7, by="session")
```

### Timeline exploration

```
# Recent events (1 day = 24 hours)
get_session_events(days=1)

# Filter by tool
get_session_events(days=1, tool="Bash")

# Filter by session
get_session_events(session_id="abc123")
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
# Analyze failure patterns and rework
analyze_failures(days=30)
→ {total_errors: 45, errors_by_tool: [...], rework_patterns: {...}}
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
get_session_commits(session_id="abc123")
→ [{sha: "abc...", time_to_commit_seconds: 1800, is_first_commit: true}]
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

4. **Start with frequency** - `get_tool_frequency` gives quick overview
5. **Use day filters** - `days=7` for recent trends, `days=30` for patterns
6. **Project filter** - Most queries accept `project` to focus on one repo

### Permission Gaps

7. **Check weekly** - Run `get_permission_gaps(min_count=3)` to catch new patterns
8. **Higher min_count = less noise** - Start with `min_count=10` if overwhelmed
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
- `get_tool_sequences` with `length=3` finds more complex patterns but needs more data
- Permission gaps compare your usage against `~/.claude/settings.json`
- Token queries help track API usage costs over time
- The CLI (`session-analytics-cli`) mirrors all MCP tools for terminal use

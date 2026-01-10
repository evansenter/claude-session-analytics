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
| `get_tool_sequences(days?, min_count?, length?, limit?)` | Common tool chains (e.g., Read → Edit → Bash) |
| `sample_sequences(pattern, limit?, context_events?)` | Random samples of a pattern with surrounding context |
| `get_permission_gaps(days?, min_count?)` | Commands not covered by settings.json (supports glob patterns) |
| `get_insights(days?, refresh?)` | Pre-computed patterns for /improve-workflow |

### Failure Analysis

| Tool | Purpose |
|------|---------|
| `analyze_failures(days?, project?)` | Failure patterns with drill-down to specific commands |
| `get_error_details(days?, tool?, limit?)` | Detailed errors with tool parameters (patterns, commands, files) |

`analyze_failures()` returns:
- `errors_by_tool`: Count of errors per tool
- `error_examples`: Top failing commands (Bash) or files (Edit/Read/Write) for drill-down
- `rework_patterns`: Files edited 3+ times within 10 minutes

`get_error_details()` shows *which specific parameters* caused failures:
- Glob/Grep: The pattern that failed (e.g., `"*"` with 922 errors)
- Bash: The command that failed (e.g., `pwd` with 492 errors)
- Edit/Read/Write: The file path that failed

### Session Classification

| Tool | Purpose |
|------|---------|
| `classify_sessions(days?, project?)` | Categorize sessions with explanation of why |

Each session includes `classification_factors` explaining WHY it was categorized:
- `trigger`: The threshold that was exceeded (e.g., "error_rate > 15%")
- Relevant metrics (error_rate, edit_rate, etc.)

Each session also includes `efficiency` metrics:
- `compaction_count`: Number of context resets
- `total_result_mb`: Total tool result size
- `files_read_multiple_times`: Indicator of rework
- `burn_rate`: "high", "medium", or "low" based on compactions/hour

### Trend Analysis

| Tool | Purpose |
|------|---------|
| `analyze_trends(days?, compare_to?)` | Token/event trends with efficiency metrics |

Returns both core metrics (`events`, `sessions`, `errors`, `tokens`) and `efficiency` metrics:
- `avg_compactions_per_session`: Context resets per session (lower is better)
- `avg_result_mb_per_session`: Context consumption per session
- `files_read_multiple_times`: Rework indicator

### Session Messages

| Tool | Purpose |
|------|---------|
| `get_session_messages(days?, session_id?, entry_types?, max_message_length?)` | Messages across sessions chronologically (user + assistant by default) |
| `search_messages(query, limit?, entry_types?)` | Full-text search across all message types (FTS5) |

**entry_types**: Filter by `["user"]`, `["assistant"]`, `["tool_result"]`, `["summary"]` or any combination.
- `get_session_messages`: Default: `["user", "assistant"]` (conversational context)
- `search_messages`: Default: all types (no filter) for comprehensive search

**max_message_length**: Truncate messages (default: 500, 0=no limit).

### Session Relationships

| Tool | Purpose |
|------|---------|
| `detect_parallel_sessions(days?, min_overlap_minutes?)` | Find simultaneously active sessions |
| `find_related_sessions(session_id)` | Find sessions with similar patterns |

### Git Integration

| Tool | Purpose |
|------|---------|
| `ingest_git_history(days?, repo_path?)` | Parse and store git commits from current repo |
| `ingest_git_history_all_projects(days?)` | Parse commits from all known projects |
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

### Context Efficiency Analysis

| Tool | Purpose |
|------|---------|
| `get_compaction_events(days?, session_id?, limit?, aggregate?)` | List compaction events (context resets) |
| `get_pre_compaction_events(session_id, compaction_timestamp, limit?)` | Events before a compaction for analysis |
| `analyze_pre_compaction_patterns(days?, events_before?, limit?)` | Aggregated patterns before compactions (RFC #81) |
| `get_large_tool_results(days?, min_size_kb?, limit?)` | Find tool results consuming context space |
| `get_session_efficiency(days?, project?, limit?)` | Session efficiency metrics and burn rate |

**Context efficiency** helps identify why sessions hit context limits:
- **Compactions**: Context resets when Claude summarizes conversation
- **Large results**: Tool outputs consuming significant context space
- **Burn rate**: How fast sessions consume their context budget
- **Read/Edit ratio**: High ratio suggests inefficient exploration (should use Task/Explore)
- **Files read multiple times**: Redundant reads indicate opportunity to cache context

### Event-Bus Integration

| Tool | Purpose |
|------|---------|
| `ingest_bus_events(days?)` | Import events from event-bus for cross-session insights |
| `get_bus_events(days?, event_type?, session_id?, repo?, limit?)` | Query event-bus events (gotchas, patterns, help) |

Cross-session events include:
- `gotcha_discovered` - Non-obvious issues found during work
- `pattern_found` - Useful patterns identified
- `help_needed` / `help_response` - Cross-session coordination
- `task_completed` / `task_started` - Work progress tracking

These appear in `get_insights()` under `cross_session_activity` when available.

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
│  get_session_messages(session_id=X) → User+assistant messages   │
│  get_session_commits(session_id=X)  → Work products             │
│  search_messages("query")           → Find across all messages  │
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

### Workflow: Context Efficiency

```
get_compaction_events()               → "When did context resets happen?"
get_compaction_events(aggregate=True) → "Which sessions had most compactions?"
analyze_pre_compaction_patterns()     → "What patterns precede compactions?" (RFC #81)
get_session_efficiency()              → "Which sessions burn context fastest?"
get_large_tool_results()              → "What operations consume the most space?"
get_pre_compaction_events()           → "What led up to a specific reset?"
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

**Notes:**
- Supports glob pattern matching. Patterns like `Bash(make*)` will correctly
  match commands `make`, `make-test`, etc. using fnmatch.
- Automatically filters non-actionable commands (shell builtins like `pwd`, `cd`, `echo`,
  control flow like `for`, `if`, and info commands like `hostname`, `whoami`) to reduce noise.

### Git Integration

Git correlation requires two steps:

```
# Option 1: Ingest from all known projects (recommended)
ingest_git_history_all_projects(days=30)

# Option 2: Ingest from current repo only
ingest_git_history(days=30)

# Then correlate and query
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

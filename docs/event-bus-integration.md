# Event-Bus Integration

Session Analytics can ingest events from [claude-event-bus](https://github.com/evansenter/claude-event-bus) for queryable cross-session insights.

## Overview

The event-bus enables Claude Code sessions to communicate asynchronously. By ingesting these events, you can:

- Track gotchas and patterns discovered across sessions
- See help requests and responses between sessions
- Correlate task progress across parallel work
- Surface cross-session learnings in `/improve-workflow`

## Prerequisites

Install [claude-event-bus](https://github.com/evansenter/claude-event-bus). The integration is optional—session-analytics works without it.

## Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  Event-Bus Database                                             │
│  ~/.claude/contrib/event-bus/data.db                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ events table: id, event_type, channel, session_id, payload ││
│  └─────────────────────────────────────────────────────────────┘│
└───────────────────────────┬─────────────────────────────────────┘
                            │ Read-only connection
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  ingest_bus_events()                                            │
│  - Incremental: tracks last ingested event_id                  │
│  - Extracts repo from channel (e.g., "repo:dotfiles" → "dotfiles")
│  - Stores raw payload unchanged                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Session Analytics Database                                     │
│  ~/.claude/contrib/analytics/data.db                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ bus_events table: event_id, timestamp, event_type, channel,││
│  │                   session_id, repo, payload                 ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Event Types

Common event types from event-bus:

| Event Type | Description |
|------------|-------------|
| `gotcha_discovered` | Non-obvious issues found during work |
| `pattern_found` | Useful patterns identified |
| `help_needed` | Request for assistance from other sessions |
| `help_response` | Response to a help request |
| `task_started` | Work begun on an issue/PR |
| `task_completed` | Work finished (merged, closed) |
| `ci_completed` | CI run finished (pass/fail) |
| `wip_checkpoint` | Work-in-progress snapshot |

## API Reference

### CLI

```bash
# Ingest and query events
session-analytics-cli bus-events --days 7

# Filter by event type
session-analytics-cli bus-events --event-type gotcha_discovered

# Filter by repository
session-analytics-cli bus-events --repo dotfiles

# Limit results
session-analytics-cli bus-events --limit 20

# JSON output
session-analytics-cli bus-events --json
```

### MCP Tools

#### `ingest_bus_events(days?)`

Ingest events from event-bus database. Performs incremental ingestion by tracking the last ingested event ID.

**Parameters:**
- `days` (int, default: 7): Days to look back on first run

**Returns:**
```json
{
  "status": "ok",
  "events_ingested": 42,
  "last_event_id": 1438,
  "oldest_event": "2025-01-01T10:00:00",
  "newest_event": "2025-01-08T01:30:00"
}
```

If event-bus is not installed:
```json
{
  "status": "skipped",
  "reason": "event-bus database not found",
  "path": "~/.claude/contrib/event-bus/data.db"
}
```

#### `get_bus_events(days?, event_type?, session_id?, repo?, limit?)`

Query ingested event-bus events.

**Parameters:**
- `days` (int, default: 7): Time range to query
- `event_type` (str, optional): Filter by event type
- `session_id` (str, optional): Filter by source session
- `repo` (str, optional): Filter by repository name
- `limit` (int, default: 100): Maximum events to return

**Returns:**
```json
{
  "events": [
    {
      "event_id": 1438,
      "timestamp": "2025-01-08T01:30:47",
      "event_type": "task_completed",
      "channel": "repo:dotfiles",
      "session_id": "6cd931c1-929b-4c9c-beb6-507e43e7feec",
      "repo": "dotfiles",
      "payload": "Merged PR #182 - Add named sessions to /parallel-work"
    }
  ],
  "type_breakdown": {
    "task_completed": 15,
    "gotcha_discovered": 8,
    "ci_completed": 42
  },
  "total_events": 65
}
```

## Integration with Insights

When event-bus data is available, `get_insights()` includes cross-session activity:

```json
{
  "cross_session_activity": {
    "gotcha_discovered": 8,
    "pattern_found": 3,
    "help_needed": 2,
    "task_completed": 15
  },
  "summary": {
    "has_bus_events": true
  }
}
```

This enables `/improve-workflow` to surface cross-session learnings.

## Design Principles

This integration follows the project's design philosophy:

1. **Raw signals over interpretation**: Payloads are stored unchanged. The `repo` field is extracted for filtering, but no semantic analysis is performed. The consuming LLM interprets meaning.

2. **Guaranteed drill-down**: `get_bus_events()` returns raw events with full payloads. You can filter by `event_type`, `session_id`, or `repo` to focus on specific signals.

3. **API conformance**: Follows existing patterns:
   - `ingest_bus_events(days)` matches `ingest_logs`, `ingest_git_history`
   - `get_bus_events(...)` matches `get_session_events`, `get_agent_activity`

## Schema

The `bus_events` table (migration v6):

```sql
CREATE TABLE bus_events (
    id INTEGER PRIMARY KEY,
    event_id INTEGER UNIQUE NOT NULL,  -- Original ID from event-bus
    timestamp TIMESTAMP NOT NULL,
    event_type TEXT NOT NULL,
    channel TEXT,
    session_id TEXT,
    repo TEXT,                          -- Extracted from channel
    payload TEXT,                       -- Raw payload unchanged
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX idx_bus_events_timestamp ON bus_events(timestamp);
CREATE INDEX idx_bus_events_type ON bus_events(event_type);
CREATE INDEX idx_bus_events_session ON bus_events(session_id);
CREATE INDEX idx_bus_events_repo ON bus_events(repo);
```

## Troubleshooting

### "event-bus database not found"

Install [claude-event-bus](https://github.com/evansenter/claude-event-bus) and ensure at least one session has registered.

### Events not appearing

1. Check ingestion status: `session-analytics-cli bus-events --json | jq .total_events`
2. Force re-ingest: Events are ingested incrementally; if the event-bus database was recreated, you may need to clear the analytics `bus_events` table.

### Stale data

Data auto-refreshes when queries detect stale data (>5 min). You can also manually trigger ingestion via the MCP tool `ingest_bus_events()`.

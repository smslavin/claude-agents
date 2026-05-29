# 10 — Long-Running Agent with Memory

A REPL assistant that persists notes between sessions and manages its own context
window. When the conversation approaches a token threshold, it compacts the history
by summarizing it and continues from the summary.

## Run

```sh
uv run examples/10-long-running-memory/assistant.py
```

Notes are stored at `~/.claude_agents_notes.json` and persist between runs.
Type `/memory` to see saved notes. Type `exit` to quit.

## What it demonstrates

| Pattern | Where |
|---|---|
| File-system memory tools | `save_note`, `get_note`, `list_notes`, `delete_note` |
| Cross-session persistence | `~/.claude_agents_notes.json` via `load_notes` / `save_notes` |
| Context pressure monitor | `context_bar()` shown after every turn |
| Manual compaction | `compact()` — summarize and reset history when threshold hit |
| Full `response.content` appended | `messages.append({"role": "assistant", "content": response.content})` |
| Compaction event log | Before/after turn count + cost when compaction fires |

## Context pressure monitor

After every turn:

```
Context: ████████░░░░░░░░░░░░░░░░░░░░░░  12,450 / 200,000 (6%)  $0.0623
```

The bar turns yellow at 50%, red at 75%. The token count is cumulative for the
session (not just the last turn), so it reflects actual context window pressure.

## Manual compaction

```python
COMPACTION_THRESHOLD = 40_000  # compact when approaching this many tokens

if session_tokens >= COMPACTION_THRESHOLD:
    messages = compact(client, messages, notes)
```

`compact()` sends the full conversation to Claude and asks for a summary, then
replaces the entire message history with a single summary message. The conversation
continues from the summary — context is reset, but no information is lost.

The compaction itself costs tokens (another API call). That cost is logged
so you can see the trade-off: spend N tokens now to prevent context degradation later.

## Why append `response.content`, not just text

```python
# Correct — preserves thinking blocks, tool_use blocks, etc.
messages.append({"role": "assistant", "content": response.content})

# Wrong — drops everything except text
text = next(b.text for b in response.content if b.type == "text")
messages.append({"role": "assistant", "content": text})
```

If the response includes tool_use blocks and you only append the text, the next
API call will receive a malformed conversation — the tool results won't match any
tool_use block in the history, and the API will reject it.

## Memory design

Memory notes are key-value pairs in a JSON file. The agent is instructed to use
`save_note` proactively for anything the user will want recalled in a future session.
Notes are injected into the conversation at session start, so they're always in context.

This is the simplest possible persistent memory. For larger memory stores, replace
the JSON file with a vector database and retrieve relevant notes per turn rather
than loading all of them.

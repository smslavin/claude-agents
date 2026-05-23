# 04 — Research Agent (Manual Agentic Loop)

A research agent that uses Anthropic's server-side `web_search` and `web_fetch` tools
to answer questions. Every step is logged: what the model searched for, what results
came back, and what it decided to do next.

## Run

```sh
uv run examples/04-research-agent/researcher.py
uv run examples/04-research-agent/researcher.py "How does Python's GIL affect async agents?"
```

## What it demonstrates

| Pattern | Where |
|---|---|
| Manual agentic loop | `while turn in range(MAX_CONTINUATIONS)` |
| Server-side tool use | `web_search_20260209`, `web_fetch_20250910` |
| `pause_turn` handling | Re-submits without new user message |
| `max_continuations` guard | Loop bound prevents infinite runs |
| Per-turn observability | Tool called, query/URL, result count, latency |
| Sources list | Derived from all URLs in search results |

## The loop

```python
for turn in range(1, MAX_CONTINUATIONS + 1):
    response = client.messages.create(tools=TOOLS, messages=messages)
    messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason == "end_turn":
        break
    elif response.stop_reason == "pause_turn":
        # Model hit the per-response server-side tool limit — re-submit to continue.
        # No new user message needed; context is already in messages.
        continue
```

## Server-side tools vs client-defined tools

With **client-defined tools** (like the code review example), `stop_reason == "tool_use"`
means the model wants you to execute a function locally and return the result.

With **server-side tools** (`web_search`, `web_fetch`, `code_execution`), Anthropic's
infrastructure executes the tool. The results appear inline in `response.content` as
`web_search_tool_result` and `web_fetch_tool_result` blocks. The model then uses those
results to generate its next text block. No local execution needed.

`pause_turn` is the server-side tool equivalent of `tool_use` — it means the model
wants to continue but has hit the per-response tool call limit.

## Response content block types

| Block type | When it appears |
|---|---|
| `server_tool_use` | Model is calling a tool (name + input visible) |
| `web_search_tool_result` | Results for a search (list of title/URL/snippet) |
| `web_fetch_tool_result` | Fetched page content or error |
| `thinking` | Model reasoning (suppressed in log, visible with `display: "summarized"`) |
| `text` | Model commentary or final answer |

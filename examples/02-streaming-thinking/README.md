# 02 — Streaming + Thinking Transparency

A technical advisor that streams Claude's response in real time while surfacing its
reasoning process separately from its final answer.

## Run

```sh
uv run examples/02-streaming-thinking/advisor.py
uv run examples/02-streaming-thinking/advisor.py "Should I use Redis or Memcached?"
```

## What it demonstrates

| Pattern | Where |
|---|---|
| `messages.stream()` with raw event iteration | `advisor.py` |
| Separate rendering of thinking vs answer blocks | `content_block_start` → `content_block_delta` dispatch |
| `thinking={"type": "adaptive", "display": "summarized"}` | Opus 4.7 only |
| Cost estimate from `usage` object | After `stream.get_final_message()` |

## Key implementation details

**Streaming with thinking requires iterating raw events**, not `stream.text_stream`.
The `text_stream` helper skips non-text blocks, so thinking deltas are invisible to it.

```python
for event in stream:
    if event.type == "content_block_start":
        current_block_type = event.content_block.type   # "thinking" or "text"
    elif event.type == "content_block_delta":
        if event.delta.type == "thinking_delta":
            print(event.delta.thinking, end="")          # reasoning in real time
        elif event.delta.type == "text_delta":
            print(event.delta.text, end="")              # answer in real time
```

**`display: "summarized"` matters on Opus 4.7.** The default is `"omitted"`, which
means thinking blocks stream but their text is empty — the model appears to pause before
responding. Setting `"summarized"` restores visible progress.

**Cost is calculated after the stream, not during.** The `message_delta` event carries
partial usage but the complete breakdown — including `cache_read_input_tokens` and
`cache_creation_input_tokens` — is only available on the final message.

## Pricing reference (claude-opus-4-7)

| Token type | Cost per 1M |
|---|---|
| Input | $5.00 |
| Output (incl. thinking) | $25.00 |
| Cache read | $0.50 |
| Cache write | $6.25 |

# 01 — Code Review Agent

Two implementations of the same code review task. Run both against `sample.py`
and compare the output, token usage, and timing.

## Single orchestrator

One Claude call does everything: security, style, and performance review in one shot.

```sh
uv run examples/01-code-review/single_orchestrator.py
# or pass your own file:
uv run examples/01-code-review/single_orchestrator.py path/to/your_file.py
```

**Pattern:** single LLM call, broad system prompt, unified output.

## Multi-orchestrator

An orchestrator agent dispatches three specialist subagents concurrently via tool use,
then synthesizes their findings into a prioritized report.

```sh
uv run examples/01-code-review/multi_orchestrator.py
# or pass your own file:
uv run examples/01-code-review/multi_orchestrator.py path/to/your_file.py
```

**Pattern:** orchestrator → tool calls → parallel subagents → synthesis.

## What to observe

| Dimension        | Single             | Multi                              |
|------------------|--------------------|------------------------------------|
| API calls        | 1                  | 1 (orchestrator) + 3 (specialists) |
| Parallelism      | None               | Specialists run concurrently       |
| Specialization   | Broad system prompt | Each agent has a focused persona   |
| Report structure | One agent's view   | Synthesized across 3 viewpoints    |
| Token cost       | Lower              | Higher (4 calls total)             |
| Latency          | Lower              | Specialists offset by concurrency  |

## Key patterns illustrated

- **Tool use as dispatch**: the orchestrator doesn't directly call subagents —
  it emits tool calls, and the harness (`multi_orchestrator.py`) creates the
  subagent instances.
- **Agentic loop**: the orchestrator loop runs until `stop_reason == "end_turn"`,
  which may take multiple turns (tool call → results → synthesis).
- **`asyncio.gather` for concurrency**: all specialist calls are awaited together,
  so total specialist time ≈ the slowest single specialist, not their sum.

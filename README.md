# claude-agents

Learning examples for building multi-agent systems with the [Anthropic Claude API](https://docs.anthropic.com/en/api/getting-started). Each example is self-contained and implements the same task two ways — single orchestrator and multi-orchestrator — so you can directly compare the patterns.

## Setup

```sh
git clone https://github.com/smslavin/claude-agents
cd claude-agents

cp .env.example .env
# add your ANTHROPIC_API_KEY to .env

uv sync
```

## Examples

### [01 — Code Review](examples/01-code-review/)

A code review agent that analyzes Python code for security, style, and performance issues.

| File | Pattern |
|---|---|
| `single_orchestrator.py` | One Claude call, broad system prompt, unified report |
| `multi_orchestrator.py` | Orchestrator dispatches 3 specialist subagents in parallel, then synthesizes |

```sh
uv run examples/01-code-review/single_orchestrator.py
uv run examples/01-code-review/multi_orchestrator.py
```

Both scripts accept an optional path argument to review any Python file:

```sh
uv run examples/01-code-review/single_orchestrator.py path/to/your_file.py
```

## Patterns illustrated

- **Single orchestrator** — simplest baseline; one API call, one system prompt
- **Tool use as dispatch** — orchestrator emits tool calls; the harness creates subagent instances
- **Parallel subagents** — `asyncio.gather` runs specialists concurrently
- **Agentic loop** — orchestrator loop runs until `stop_reason == "end_turn"`
- **Result synthesis** — orchestrator merges specialist outputs into a prioritized report
